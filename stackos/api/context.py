"""StackOS project-memory REST routes."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as ModelValidationError
from sqlalchemy.engine import Engine
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from stackos.api.deps import get_engine, get_session
from stackos.api.envelopes import WriteResponse, write_response
from stackos.api.pagination import (
    PageResponse,
    PaginationParams,
    page_response,
    pagination_params,
)
from stackos.api.project_access import authorize_stream_poll, bind_user_session
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
from stackos.context.repository.utils import MAX_CONTEXT_LIMIT
from stackos.host_mcp.service import usage_executions
from stackos.repositories.base import Page
from stackos.repositories.tracker.agent_stats import usage_read_token, usage_refresh
from stackos.repositories.tracker.schema import TrackerMetricDemand, TrackerUsageDemand

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["project-memory"])
global_router = APIRouter(prefix="/api/v1", tags=["project-memory"])

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
    limit: int | None = Field(default=None, ge=1, le=MAX_CONTEXT_LIMIT)
    tags: list[str] | None = None
    domain: str | None = None
    statuses: list[str] | None = None
    plugin_slug: str | None = None
    resource_keys: list[str] | None = None


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
        plugin_slug=body.plugin_slug,
        resource_keys=body.resource_keys,
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
    ui_demand: str | None = Query(default=None, max_length=20000),
    engine: Engine = Depends(get_engine),
) -> StreamingResponse:
    """Stream tracker status timeline updates as Server-Sent Events."""
    return await run_in_threadpool(
        _timeline_stream,
        project_id,
        request,
        task_key,
        after,
        limit,
        poll_ms,
        heartbeat_ms,
        max_events,
        replay,
        engine,
        ui_demand,
    )


@global_router.get("/context/timeline/stream")
async def global_context_timeline_stream(
    request: Request,
    after: int | None = Query(default=None, ge=0),
    ui_demand: str | None = Query(default=None, max_length=20000),
    engine: Engine = Depends(get_engine),
) -> StreamingResponse:
    """The shared tracker transport for Overview's authorized project scope."""
    return await run_in_threadpool(
        _timeline_stream,
        None,
        request,
        None,
        after,
        50,
        1000,
        15_000,
        None,
        False,
        engine,
        ui_demand,
    )


def _timeline_stream(
    project_id,
    request,
    task_key,
    after,
    limit,
    poll_ms,
    heartbeat_ms,
    max_events,
    replay,
    engine,
    ui_demand=None,
) -> StreamingResponse:
    demand = None
    if ui_demand is not None:
        if task_key is not None:
            raise HTTPException(
                status_code=422,
                detail="UI demand uses complete project scope; express selected keys in ui_demand",
            )
        try:
            demand = TrackerUsageDemand.model_validate_json(ui_demand)
        except ModelValidationError as exc:
            raise HTTPException(status_code=422, detail="Invalid bounded UI metric demand") from exc
        with Session(engine) as session:
            bind_user_session(session, request, request.app.state.settings)
            _ui_usage_scope(session, project_id, task_key, demand)

    def latest_cursor():
        with Session(engine) as session:
            bind_user_session(session, request, request.app.state.settings)
            return ContextRepository(session).latest_tracker_status_event_id(
                project_id=project_id, task_key=task_key
            )

    def read_poll(cursor, scan_anchor, previous_usage, previous_projects):
        # Construct, use and close each Session in its worker. Only detached
        # event models and a serialized transient snapshot cross the await.
        with Session(engine) as session:
            bind_user_session(session, request, request.app.state.settings)
            read_token = usage_read_token(session, project_id) if demand is not None else None
            repository = ContextRepository(session)
            latest = repository.event_position(project_id=project_id)
            watermark = latest[0] if latest is not None else 0
            if project_id is None and scan_anchor is not None and scan_anchor[0] > (cursor or 0):
                # Project purge can free event IDs for reuse. Validate private
                # progress by its owning project, whose identity is never reused,
                # even if new events already grew past the old maximum ID.
                anchor = latest if watermark == scan_anchor[0] else None
                if watermark > scan_anchor[0]:
                    anchor = repository.event_position(project_id=None, event_id=scan_anchor[0])
                if anchor != scan_anchor:
                    scan_anchor = None
            scan_cursor = cursor if scan_anchor is None else max(cursor or 0, scan_anchor[0])
            page = (
                repository.tracker_status_timeline(
                    project_id=project_id,
                    limit=limit,
                    after_id=scan_cursor,
                    task_key=task_key,
                    include_total=False,
                )
                if scan_cursor is None or watermark > scan_cursor
                else Page[ProjectEventOut](items=[])
            )
            # Exhausted reads also cover unmatched events. Keep that private
            # progress separate from the durable cursor carried by status frames.
            scanned = latest or (0, None)
            if page.items and (page.next_cursor is not None or page.items[-1].id > watermark):
                scanned = (page.items[-1].id, page.items[-1].project_id)

            usage = usage_executions(project_id)
            if request.app.state.settings.process_role == "server" and project_id is None:
                from sqlmodel import select

                from stackos.db.models import Project
                from stackos.repositories.project_access import project_owner_filter

                owned = set(session.exec(select(Project.id).where(project_owner_filter(session))))
                usage = [call for call in usage if call["project_id"] in owned]
            if task_key:
                usage = [call for call in usage if call["task_key"] == task_key]
            projects = None
            if demand is not None and project_id is None:
                projects = _ui_usage_scope(
                    session, project_id, task_key, demand, allow_removed_projects=True
                )
            scope_changed = projects is not None and projects != previous_projects
            # Full replaceable transient snapshot also repairs reconnect/exit state.
            # It has no durable event ID and never advances the timeline cursor.
            frame = None
            if usage != previous_usage or page.items or scope_changed:
                updates = None
                current_demand = demand
                if demand is not None:
                    if projects is None:
                        projects = _ui_usage_scope(session, project_id, task_key, demand)
                    current_demand = TrackerUsageDemand(
                        projects={
                            pid: requested
                            for pid, requested in demand.projects.items()
                            if pid in projects
                        }
                    )
                    scopes = _ui_usage_updates(
                        session,
                        projects,
                        current_demand,
                        include_tasks=bool(demand.projects),
                    )
                    updates = {
                        # A changing source during this poll cannot acknowledge
                        # a concurrent Home read. Null keeps normal invalidation.
                        "read_token": read_token
                        if read_token == usage_read_token(session, project_id)
                        else None,
                        "projection": "ui",
                        "scope_complete": True,
                        "metric_scope": "demand",
                        "replace": True,
                        # Coverage for same-poll status companions, including
                        # legacy writers without a tracker revision. This is
                        # not an SSE id and never advances the durable cursor.
                        "covered_event_id": max((item.id for item in page.items), default=None),
                        "scope": "project" if project_id is not None else "global",
                        "project_id": project_id,
                        "projects": [
                            {key: scope[key] for key in ("project_id", "rev", "metric_demand")}
                            for scope in scopes
                        ],
                        "tasks": [task for scope in scopes for task in scope["tasks"]],
                        "tickets": [ticket for scope in scopes for ticket in scope["tickets"]],
                    }
                elif project_id is not None:
                    affected = [
                        item.metadata_json
                        for item in page.items
                        if isinstance(item.metadata_json, dict)
                        and isinstance(item.metadata_json.get("task_key"), str)
                        and isinstance(item.metadata_json.get("ticket_key"), str)
                    ]
                    updates = usage_refresh(
                        session,
                        project_id,
                        None if previous_usage is None else [*usage, *previous_usage, *affected],
                        task_key=task_key,
                    )
                    updates["replace"] = previous_usage is None
                frame = sse_frame(
                    event="tracker-usage",
                    data={
                        "executions": usage
                        if current_demand is None
                        else [
                            call
                            for call in usage
                            if (
                                (current := current_demand.projects.get(call["project_id"]))
                                is not None
                                and (
                                    call["task_key"] in current.task_keys
                                    or call["ticket_key"] in current.ticket_keys
                                )
                            )
                        ],
                        "updates": updates,
                    },
                )
            return page, usage, projects, frame, scanned

    def authorized(project_ids=()):
        if request.app.state.settings.process_role != "server":
            return True
        with Session(engine) as session:
            return asyncio.run(
                authorize_stream_poll(session, request, project_id, project_ids=project_ids)
            )

    async def event_source():
        cursor = after
        emitted = 0
        next_heartbeat = time.monotonic() + heartbeat_ms / 1000
        previous_usage = None
        previous_projects = None
        if not await run_in_threadpool(authorized):
            return
        if cursor is None and not replay:
            cursor = await run_in_threadpool(latest_cursor)
        scan_anchor = None
        while True:
            if await request.is_disconnected():
                break
            page, usage, projects, frame, scanned = await run_in_threadpool(
                read_poll, cursor, scan_anchor, previous_usage, previous_projects
            )
            # A disconnect during a slow read must not publish its late result
            # or begin another operation. Worker cleanup owns the SQL lifetime.
            if await request.is_disconnected():
                break
            frame_projects = set(projects or ()) | {call["project_id"] for call in usage}
            frame_projects.update(item.project_id for item in page.items)
            if not await run_in_threadpool(authorized, frame_projects):
                return
            scan_anchor = scanned
            if frame is not None:
                yield frame
                previous_usage = usage
                previous_projects = projects

            if page.items:
                for item in page.items:
                    # A consumer can pause between frames. Do not release a
                    # buffered event after logout or an ownership change.
                    if not await run_in_threadpool(authorized, (item.project_id,)):
                        return
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


def _ui_usage_updates(
    session,
    projects,
    demand: TrackerUsageDemand,
    *,
    include_tasks: bool | None = None,
) -> list[dict[str, Any]]:
    """One authorized receipt batch across the global stream's composite demand."""
    from sqlalchemy import false, or_
    from sqlmodel import col, select

    from stackos.db.models import TaskTracker, TrackerTask, TrackerTicket
    from stackos.repositories.tracker.agent_stats import ReceiptProjection
    from stackos.repositories.tracker.utils import DEFAULT_TRACKER_KEY

    if not projects:
        return []
    should_include_tasks = bool(demand.projects) if include_tasks is None else include_tasks
    if not should_include_tasks:
        # Empty UI demand (Home/Work invalidation transport) only needs each
        # project's current tracker revision; task/ticket hydration is skipped.
        revs = dict(
            session.exec(
                select(TaskTracker.project_id, TaskTracker.rev).where(
                    TaskTracker.key == DEFAULT_TRACKER_KEY,
                    col(TaskTracker.project_id).in_(projects),
                )
            ).all()
        )
        empty_demand = TrackerMetricDemand().model_dump(mode="json")
        return [
            {
                "project_id": pid,
                "rev": revs.get(pid, 0),
                "metric_demand": empty_demand,
                "tasks": [],
                "tickets": [],
            }
            for pid in projects
        ]

    task_ids = (
        set(
            session.exec(
                select(TrackerTask.id)
                .join(TaskTracker, col(TaskTracker.id) == col(TrackerTask.tracker_id))
                .where(
                    TaskTracker.key == DEFAULT_TRACKER_KEY,
                    or_(
                        false(),
                        *[
                            (col(TrackerTask.project_id) == pid)
                            & col(TrackerTask.key).in_(requested.task_keys)
                            for pid, requested in demand.projects.items()
                        ],
                    ),
                )
            )
        )
        if any(d.task_keys for d in demand.projects.values())
        else set()
    )
    ticket_ids = (
        set(
            session.exec(
                select(TrackerTicket.id)
                .join(TaskTracker, col(TaskTracker.id) == col(TrackerTicket.tracker_id))
                .where(
                    TaskTracker.key == DEFAULT_TRACKER_KEY,
                    or_(
                        false(),
                        *[
                            (col(TrackerTicket.project_id) == pid)
                            & col(TrackerTicket.key).in_(requested.ticket_keys)
                            for pid, requested in demand.projects.items()
                        ],
                    ),
                )
            )
        )
        if any(d.ticket_keys for d in demand.projects.values())
        else set()
    )
    projection = ReceiptProjection(session)
    projection.load(task_ids=task_ids, ticket_ids=ticket_ids)
    return [
        usage_refresh(
            session,
            pid,
            None,
            metric_demand=demand.projects.get(pid, TrackerMetricDemand()),
            projection=projection,
        )
        for pid in projects
    ]


def _ui_usage_scope(
    session,
    project_id,
    task_key,
    demand: TrackerUsageDemand,
    *,
    allow_removed_projects: bool = False,
) -> list[int]:
    """Validate admission; an admitted global stream may drop unavailable projects."""
    from sqlmodel import col, select

    from stackos.db.json_expressions import json_kind
    from stackos.db.models import Project, TaskTracker, TrackerTask, TrackerTicket
    from stackos.repositories.project_access import project_owner_filter
    from stackos.repositories.projects import ProjectRepository
    from stackos.repositories.tracker.utils import DEFAULT_TRACKER_KEY

    if project_id is not None:
        ProjectRepository(session).require_available(project_id)
        projects = [project_id]
    else:
        projects = list(
            session.exec(
                select(Project.id)
                .where(
                    project_owner_filter(session),
                    col(Project.is_active).is_(True),
                    json_kind(col(Project.cleanup_json)).is_(None)
                    | (json_kind(col(Project.cleanup_json)) == "null"),
                )
                .order_by(col(Project.id))
            )
        )
    if not demand.projects.keys() <= set(projects) and not (
        project_id is None and allow_removed_projects
    ):
        raise HTTPException(
            status_code=422, detail="UI metric demand is outside the selected project scope"
        )
    for pid in projects:
        requested = demand.projects.get(pid, TrackerMetricDemand())
        if not (requested.task_keys or requested.ticket_keys or task_key is not None):
            continue
        tasks = dict(
            session.exec(
                select(TrackerTask.key, TrackerTask.id)
                .join(TaskTracker, col(TaskTracker.id) == col(TrackerTask.tracker_id))
                .where(
                    TrackerTask.project_id == pid,
                    TaskTracker.key == DEFAULT_TRACKER_KEY,
                    TrackerTask.key == task_key if task_key is not None else True,
                )
            ).all()
        )
        if task_key is not None and task_key not in tasks:
            raise HTTPException(status_code=404, detail="Task not found in selected project")
        if not set(requested.task_keys) <= tasks.keys():
            raise HTTPException(
                status_code=422, detail="UI metric task demand is outside the selected scope"
            )
        tickets = (
            set(
                session.exec(
                    select(TrackerTicket.key).where(
                        TrackerTicket.project_id == pid,
                        col(TrackerTicket.task_id).in_(tasks.values()),
                        col(TrackerTicket.key).in_(requested.ticket_keys),
                    )
                )
            )
            if requested.ticket_keys
            else set()
        )
        if tickets != set(requested.ticket_keys):
            raise HTTPException(
                status_code=422, detail="UI metric ticket demand is outside the selected scope"
            )
    return projects


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
