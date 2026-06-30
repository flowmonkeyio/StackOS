"""StackOS project-memory REST routes."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.engine import Engine
from sqlmodel import Session

from stackos.api.deps import get_engine, get_session
from stackos.api.envelopes import WriteResponse, write_response
from stackos.api.pagination import (
    PageResponse,
    PaginationParams,
    page_response,
    pagination_params,
)
from stackos.context import (
    ContextQueryOut,
    ContextRepository,
    ContextSnapshotOut,
    DecisionOut,
    ExperimentObservationOut,
    ExperimentOut,
    LearningOut,
    MetricSnapshotOut,
    ProjectEventOut,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["project-memory"])

TRACKER_STREAM_EVENT = "tracker-status"


class ContextQueryRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "sources": ["learnings", "experiments"],
                "fields": ["statement", "confidence", "status"],
                "limit": 10,
            }
        }
    )

    sources: list[str] | None = None
    fields: list[str] | None = None
    limit: int | None = Field(default=None, ge=1)
    tags: list[str] | None = None
    domain: str | None = None
    statuses: list[str] | None = None


class ContextSnapshotCreateRequest(BaseModel):
    query_json: dict[str, Any] = Field(default_factory=dict)
    selected_sources_json: list[dict[str, Any]] = Field(default_factory=list)
    run_id: int | None = None
    name: str | None = None
    summary_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None


class LearningCreateRequest(BaseModel):
    statement: str
    domain: str | None = None
    confidence: str = "unknown"
    status: str = "active"
    review_state: str = "proposed"
    created_by: str | None = None
    tags: list[str] | None = None
    applies_to_json: dict[str, Any] | None = None
    evidence_json: dict[str, Any] | None = None
    source_snapshot_id: int | None = None
    supersedes_learning_id: int | None = None
    metadata_json: dict[str, Any] | None = None


class LearningUpdateRequest(BaseModel):
    statement: str | None = None
    domain: str | None = None
    confidence: str | None = None
    status: str | None = None
    review_state: str | None = None
    tags: list[str] | None = None
    applies_to_json: dict[str, Any] | None = None
    evidence_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None


class ExperimentVariantRequest(BaseModel):
    key: str
    name: str | None = None
    resources_json: list[dict[str, Any]] | None = None
    metadata_json: dict[str, Any] | None = None


class ExperimentCreateRequest(BaseModel):
    hypothesis: str
    key: str | None = None
    name: str | None = None
    domain: str | None = None
    status: str = "planned"
    linked_template_ids_json: list[str] | None = None
    linked_run_ids_json: list[int] | None = None
    metric_targets_json: dict[str, Any] | None = None
    decision_policy_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    variants: list[ExperimentVariantRequest] | None = None


class ExperimentObservationRequest(BaseModel):
    experiment_id: int
    metrics_json: dict[str, Any] = Field(default_factory=dict)
    variant_key: str | None = None
    run_id: int | None = None
    summary: str | None = None
    observed_at: datetime | None = None
    metadata_json: dict[str, Any] | None = None


class ExperimentDecisionRequest(BaseModel):
    experiment_id: int
    decision: str
    title: str | None = None
    rationale: str | None = None
    status: str = "recorded"
    decided_by: str | None = None
    tags: list[str] | None = None
    evidence_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    run_id: int | None = None
    experiment_status: str | None = None


class DecisionRecordRequest(BaseModel):
    decision: str
    title: str | None = None
    rationale: str | None = None
    status: str = "recorded"
    decided_by: str | None = None
    tags: list[str] | None = None
    evidence_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    run_id: int | None = None
    experiment_id: int | None = None


@router.post("/context/query", response_model=ContextQueryOut)
async def query_context(
    project_id: int,
    body: ContextQueryRequest,
    session: Session = Depends(get_session),
) -> ContextQueryOut:
    """Query bounded, projected, sanitized project context."""
    return ContextRepository(session).query_context(
        project_id=project_id,
        sources=body.sources,
        fields=body.fields,
        limit=body.limit,
        tags=body.tags,
        domain=body.domain,
        statuses=body.statuses,
    )


@router.get("/context/timeline", response_model=PageResponse[ProjectEventOut])
async def context_timeline(
    project_id: int,
    event_type: str | None = Query(default=None),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    page: PaginationParams = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> PageResponse[ProjectEventOut]:
    """Return the project event timeline.

    `order=asc` (default) is oldest-first for forward watermark polling;
    `order=desc` is newest-first for the activity feed.
    """
    return page_response(
        ContextRepository(session).timeline(
            project_id=project_id,
            limit=page.limit,
            after_id=page.after,
            event_type=event_type,
            descending=order == "desc",
        )
    )


@router.get("/context/timeline/stream")
async def context_timeline_stream(
    project_id: int,
    request: Request,
    task_key: str | None = Query(default=None),
    after: int | None = Query(default=None, ge=0),
    limit: int = Query(default=25, ge=1, le=200),
    poll_ms: int = Query(default=1000, ge=250, le=30_000),
    heartbeat_ms: int = Query(default=15_000, ge=1000, le=120_000),
    max_events: int | None = Query(default=None, ge=1, le=200),
    replay: bool = Query(default=False),
    engine: Engine = Depends(get_engine),
) -> StreamingResponse:
    """Stream tracker status timeline updates as Server-Sent Events."""

    async def event_source():
        cursor = after
        emitted = 0
        next_heartbeat = time.monotonic() + heartbeat_ms / 1000
        if cursor is None and not replay:
            with Session(engine) as session:
                cursor = ContextRepository(session).latest_tracker_status_event_id(
                    project_id=project_id,
                    task_key=task_key,
                )
        while True:
            if await request.is_disconnected():
                break

            with Session(engine) as session:
                page = ContextRepository(session).tracker_status_timeline(
                    project_id=project_id,
                    limit=limit,
                    after_id=cursor,
                    task_key=task_key,
                )

            if page.items:
                for item in page.items:
                    cursor = item.id
                    emitted += 1
                    yield sse_frame(
                        event=TRACKER_STREAM_EVENT,
                        data=item.model_dump(mode="json"),
                        event_id=str(item.id),
                    )
                    if max_events is not None and emitted >= max_events:
                        return
                next_heartbeat = time.monotonic() + heartbeat_ms / 1000
            elif time.monotonic() >= next_heartbeat:
                yield sse_frame(event="heartbeat", data={"cursor": cursor})
                next_heartbeat = time.monotonic() + heartbeat_ms / 1000

            await asyncio.sleep(poll_ms / 1000)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def sse_frame(*, event: str, data: dict[str, Any], event_id: str | None = None) -> str:
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    payload = json.dumps(data, separators=(",", ":"))
    for line in payload.splitlines() or [""]:
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


@router.get("/context/snapshots", response_model=PageResponse[ContextSnapshotOut])
async def query_context_snapshots(
    project_id: int,
    run_id: int | None = Query(default=None),
    page: PaginationParams = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> PageResponse[ContextSnapshotOut]:
    """Query sanitized context snapshots for generic project-data views."""
    return page_response(
        ContextRepository(session).query_snapshots(
            project_id=project_id,
            run_id=run_id,
            limit=page.limit,
            after_id=page.after,
        )
    )


@router.post(
    "/context/snapshots",
    response_model=WriteResponse[ContextSnapshotOut],
    status_code=status.HTTP_201_CREATED,
)
async def create_context_snapshot(
    project_id: int,
    body: ContextSnapshotCreateRequest,
    session: Session = Depends(get_session),
) -> WriteResponse[ContextSnapshotOut]:
    """Create a context snapshot through the local/admin REST surface."""
    return write_response(
        ContextRepository(session).create_snapshot(
            project_id=project_id,
            run_id=body.run_id,
            name=body.name,
            query_json=body.query_json,
            selected_sources_json=body.selected_sources_json,
            summary_json=body.summary_json,
            metadata_json=body.metadata_json,
        )
    )


@router.get("/learnings", response_model=PageResponse[LearningOut])
async def query_learnings(
    project_id: int,
    domain: str | None = Query(default=None),
    status_value: str | None = Query(default=None, alias="status"),
    review_state: str | None = Query(default=None),
    tags: list[str] | None = Query(default=None),
    page: PaginationParams = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> PageResponse[LearningOut]:
    """Query project learnings without inferring which ones are true."""
    return page_response(
        ContextRepository(session).query_learnings(
            project_id=project_id,
            domain=domain,
            status=status_value,
            review_state=review_state,
            tags=tags,
            limit=page.limit,
            after_id=page.after,
        )
    )


@router.post(
    "/learnings",
    response_model=WriteResponse[LearningOut],
    status_code=status.HTTP_201_CREATED,
)
async def create_learning(
    project_id: int,
    body: LearningCreateRequest,
    session: Session = Depends(get_session),
) -> WriteResponse[LearningOut]:
    """Record a learning candidate or accepted learning as supplied data."""
    return write_response(
        ContextRepository(session).create_learning(
            project_id=project_id,
            statement=body.statement,
            domain=body.domain,
            confidence=body.confidence,
            status=body.status,
            review_state=body.review_state,
            created_by=body.created_by,
            tags=body.tags,
            applies_to_json=body.applies_to_json,
            evidence_json=body.evidence_json,
            source_snapshot_id=body.source_snapshot_id,
            supersedes_learning_id=body.supersedes_learning_id,
            metadata_json=body.metadata_json,
        )
    )


@router.patch("/learnings/{learning_id}", response_model=WriteResponse[LearningOut])
async def update_learning(
    project_id: int,
    learning_id: int,
    body: LearningUpdateRequest,
    session: Session = Depends(get_session),
) -> WriteResponse[LearningOut]:
    """Update learning review/status fields supplied by an agent or human."""
    return write_response(
        ContextRepository(session).update_learning(
            project_id=project_id,
            learning_id=learning_id,
            statement=body.statement,
            domain=body.domain,
            confidence=body.confidence,
            status=body.status,
            review_state=body.review_state,
            tags=body.tags,
            applies_to_json=body.applies_to_json,
            evidence_json=body.evidence_json,
            metadata_json=body.metadata_json,
        )
    )


@router.get("/experiments", response_model=PageResponse[ExperimentOut])
async def query_experiments(
    project_id: int,
    domain: str | None = Query(default=None),
    status_value: str | None = Query(default=None, alias="status"),
    tags: list[str] | None = Query(default=None),
    page: PaginationParams = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> PageResponse[ExperimentOut]:
    """Query project experiments without declaring a winner."""
    return page_response(
        ContextRepository(session).query_experiments(
            project_id=project_id,
            domain=domain,
            status=status_value,
            tags=tags,
            limit=page.limit,
            after_id=page.after,
        )
    )


@router.post(
    "/experiments",
    response_model=WriteResponse[ExperimentOut],
    status_code=status.HTTP_201_CREATED,
)
async def create_experiment(
    project_id: int,
    body: ExperimentCreateRequest,
    session: Session = Depends(get_session),
) -> WriteResponse[ExperimentOut]:
    """Create an experiment as data supplied by an agent or human."""
    return write_response(
        ContextRepository(session).create_experiment(
            project_id=project_id,
            hypothesis=body.hypothesis,
            key=body.key,
            name=body.name,
            domain=body.domain,
            status=body.status,
            linked_template_ids_json=body.linked_template_ids_json,
            linked_run_ids_json=body.linked_run_ids_json,
            metric_targets_json=body.metric_targets_json,
            decision_policy_json=body.decision_policy_json,
            metadata_json=body.metadata_json,
            variants=[
                variant.model_dump(mode="python", exclude_none=True)
                for variant in body.variants or []
            ],
        )
    )


@router.post("/experiments/observations", response_model=WriteResponse[ExperimentObservationOut])
async def record_experiment_observation(
    project_id: int,
    body: ExperimentObservationRequest,
    session: Session = Depends(get_session),
) -> WriteResponse[ExperimentObservationOut]:
    """Record supplied experiment observation data without interpreting it."""
    return write_response(
        ContextRepository(session).record_observation(
            project_id=project_id,
            experiment_id=body.experiment_id,
            metrics_json=body.metrics_json,
            variant_key=body.variant_key,
            run_id=body.run_id,
            summary=body.summary,
            observed_at=body.observed_at,
            metadata_json=body.metadata_json,
        )
    )


@router.get("/experiments/observations", response_model=PageResponse[ExperimentObservationOut])
async def query_experiment_observations(
    project_id: int,
    experiment_id: int | None = Query(default=None),
    run_id: int | None = Query(default=None),
    page: PaginationParams = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> PageResponse[ExperimentObservationOut]:
    """Query sanitized experiment observations without interpreting them."""
    return page_response(
        ContextRepository(session).query_observations(
            project_id=project_id,
            experiment_id=experiment_id,
            run_id=run_id,
            limit=page.limit,
            after_id=page.after,
        )
    )


@router.post("/experiments/decisions", response_model=WriteResponse[DecisionOut])
async def record_experiment_decision(
    project_id: int,
    body: ExperimentDecisionRequest,
    session: Session = Depends(get_session),
) -> WriteResponse[DecisionOut]:
    """Record an explicit experiment decision supplied by agent or human."""
    return write_response(
        ContextRepository(session).record_experiment_decision(
            project_id=project_id,
            experiment_id=body.experiment_id,
            decision=body.decision,
            title=body.title,
            rationale=body.rationale,
            status=body.status,
            decided_by=body.decided_by,
            tags=body.tags,
            evidence_json=body.evidence_json,
            metadata_json=body.metadata_json,
            run_id=body.run_id,
            experiment_status=body.experiment_status,
        )
    )


@router.get("/decisions", response_model=PageResponse[DecisionOut])
async def query_decisions(
    project_id: int,
    experiment_id: int | None = Query(default=None),
    run_id: int | None = Query(default=None),
    status_value: str | None = Query(default=None, alias="status"),
    tags: list[str] | None = Query(default=None),
    page: PaginationParams = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> PageResponse[DecisionOut]:
    """Query explicit project decisions."""
    return page_response(
        ContextRepository(session).query_decisions(
            project_id=project_id,
            experiment_id=experiment_id,
            run_id=run_id,
            status=status_value,
            tags=tags,
            limit=page.limit,
            after_id=page.after,
        )
    )


@router.post("/decisions", response_model=WriteResponse[DecisionOut])
async def record_decision(
    project_id: int,
    body: DecisionRecordRequest,
    session: Session = Depends(get_session),
) -> WriteResponse[DecisionOut]:
    """Record an explicit decision supplied by an agent or human."""
    return write_response(
        ContextRepository(session).record_decision(
            project_id=project_id,
            decision=body.decision,
            title=body.title,
            rationale=body.rationale,
            status=body.status,
            decided_by=body.decided_by,
            tags=body.tags,
            evidence_json=body.evidence_json,
            metadata_json=body.metadata_json,
            run_id=body.run_id,
            experiment_id=body.experiment_id,
        )
    )


@router.get("/metrics", response_model=PageResponse[MetricSnapshotOut])
async def query_metrics(
    project_id: int,
    metric_key: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    source_id: int | None = Query(default=None),
    page: PaginationParams = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> PageResponse[MetricSnapshotOut]:
    """Query supplied metric snapshots as data, without ranking or decisions."""
    return page_response(
        ContextRepository(session).query_metrics(
            project_id=project_id,
            metric_key=metric_key,
            source_type=source_type,
            source_id=source_id,
            limit=page.limit,
            after_id=page.after,
        )
    )


__all__ = ["router"]
