"""Project memory repositories for StackOS context, learnings, and experiments."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_
from sqlalchemy import select as sa_select
from sqlmodel import Session, col, select

from content_stack.artifacts import redact_secret_text, redact_secrets
from content_stack.db.models import (
    ContextIndexEntry,
    ContextSnapshot,
    Decision,
    Experiment,
    ExperimentObservation,
    ExperimentVariant,
    Learning,
    MetricSnapshot,
    Project,
    ProjectEvent,
    Run,
)
from content_stack.repositories.base import Envelope, NotFoundError, Page, ValidationError

DEFAULT_CONTEXT_LIMIT = 20
MAX_CONTEXT_LIMIT = 50
_FETCH_MULTIPLIER = 5


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _normalise_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_CONTEXT_LIMIT
    if limit < 1:
        raise ValidationError("limit must be >= 1", data={"limit": limit})
    if limit > MAX_CONTEXT_LIMIT:
        raise ValidationError(
            f"limit must be <= {MAX_CONTEXT_LIMIT}",
            data={"limit": limit, "max": MAX_CONTEXT_LIMIT},
        )
    return limit


def _scalar_count(session: Session, statement: Any) -> int:
    raw = session.exec(statement).one()
    if isinstance(raw, tuple):
        return int(raw[0])
    try:
        return int(raw[0])  # type: ignore[index]
    except (KeyError, TypeError):
        pass
    return int(raw)


def _required_id(value: int | None) -> int:
    if value is None:
        raise RuntimeError("expected persisted row id")
    return int(value)


def _safe_text(value: str | None) -> str | None:
    return redact_secret_text(value) if value is not None else None


def _safe_json(value: Any) -> Any:
    return redact_secrets(value)


def _normalise_tags(tags: list[str] | None) -> list[str] | None:
    if tags is None:
        return None
    return sorted({str(tag).strip() for tag in tags if str(tag).strip()})


def _has_all_tags(row_tags: list[str] | None, required: list[str] | None) -> bool:
    if not required:
        return True
    return set(required) <= set(row_tags or [])


class ProjectEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    run_id: int | None
    source_type: str
    source_id: int | None
    event_type: str
    title: str | None
    summary: str | None
    tags: list[str]
    metadata_json: dict[str, Any] | None
    occurred_at: datetime
    created_at: datetime


class ContextIndexEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    source_type: str
    source_id: int | None
    title: str | None
    summary: str | None
    domain: str | None
    provider_key: str | None
    status: str | None
    tags: list[str]
    metadata_json: dict[str, Any] | None
    occurred_at: datetime
    created_at: datetime


class ContextSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    run_id: int | None
    name: str | None
    query_json: dict[str, Any]
    selected_sources_json: list[dict[str, Any]]
    summary_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    created_at: datetime


class ContextItemOut(BaseModel):
    source: str
    id: int
    project_id: int | None
    title: str | None
    occurred_at: datetime | None
    fields: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class ContextQueryOut(BaseModel):
    project_id: int
    sources: list[str]
    fields: list[str]
    limit: int
    items: list[ContextItemOut]
    total_estimate: int


class ContextPageOut(BaseModel):
    items: list[ContextItemOut]
    next_cursor: int | None = None
    total_estimate: int = 0


class LearningOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    source_snapshot_id: int | None
    supersedes_learning_id: int | None
    statement: str
    domain: str | None
    confidence: str
    status: str
    review_state: str
    created_by: str | None
    tags: list[str]
    applies_to_json: dict[str, Any] | None
    evidence_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class ExperimentVariantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    experiment_id: int
    key: str
    name: str | None
    resources_json: list[dict[str, Any]] | None
    metadata_json: dict[str, Any] | None
    created_at: datetime


class ExperimentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    key: str | None
    name: str | None
    domain: str | None
    hypothesis: str
    status: str
    linked_template_ids_json: list[str] | None
    linked_run_ids_json: list[int] | None
    metric_targets_json: dict[str, Any] | None
    decision_policy_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    variants: list[ExperimentVariantOut]
    created_at: datetime
    updated_at: datetime


class ExperimentObservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    experiment_id: int
    run_id: int | None
    variant_key: str | None
    metrics_json: dict[str, Any]
    summary: str | None
    metadata_json: dict[str, Any] | None
    observed_at: datetime
    created_at: datetime


class DecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    experiment_id: int | None
    run_id: int | None
    title: str | None
    decision: str
    rationale: str | None
    status: str
    decided_by: str | None
    tags: list[str]
    evidence_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    created_at: datetime


class MetricSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    source_type: str | None
    source_id: int | None
    metric_key: str
    metric_value: float | None
    dimensions_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    captured_at: datetime
    created_at: datetime


_DEFAULT_FIELDS: dict[str, tuple[str, ...]] = {
    "runs": ("kind", "status", "last_step", "metadata_json"),
    "events": ("event_type", "title", "summary", "tags", "metadata_json"),
    "index": ("source_type", "source_id", "title", "summary", "domain", "status", "tags"),
    "snapshots": ("name", "query_json", "selected_sources_json", "summary_json"),
    "learnings": ("statement", "domain", "confidence", "status", "review_state", "tags"),
    "experiments": ("name", "domain", "hypothesis", "status", "metric_targets_json", "variants"),
    "decisions": ("title", "decision", "rationale", "status", "tags"),
    "metrics": ("metric_key", "metric_value", "dimensions_json", "captured_at"),
}

_FIELD_MAP: dict[str, frozenset[str]] = {
    "runs": frozenset(
        {
            "id",
            "kind",
            "status",
            "started_at",
            "ended_at",
            "last_step",
            "last_step_at",
            "metadata_json",
        }
    ),
    "events": frozenset(_DEFAULT_FIELDS["events"]) | {"occurred_at", "source_type", "source_id"},
    "index": frozenset(_DEFAULT_FIELDS["index"]) | {"occurred_at", "metadata_json"},
    "snapshots": frozenset(_DEFAULT_FIELDS["snapshots"]) | {"run_id", "metadata_json"},
    "learnings": frozenset(_DEFAULT_FIELDS["learnings"])
    | {
        "applies_to_json",
        "evidence_json",
        "metadata_json",
        "created_by",
        "source_snapshot_id",
        "supersedes_learning_id",
    },
    "experiments": frozenset(_DEFAULT_FIELDS["experiments"])
    | {
        "key",
        "linked_template_ids_json",
        "linked_run_ids_json",
        "decision_policy_json",
        "metadata_json",
    },
    "decisions": frozenset(_DEFAULT_FIELDS["decisions"])
    | {"experiment_id", "run_id", "evidence_json", "metadata_json", "decided_by"},
    "metrics": frozenset(_DEFAULT_FIELDS["metrics"])
    | {"source_type", "source_id", "metadata_json"},
}


class ContextRepository:
    """Data-only project memory facade with bounded, sanitized retrieval."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def query_context(
        self,
        *,
        project_id: int,
        sources: list[str] | None = None,
        fields: list[str] | None = None,
        limit: int | None = None,
        tags: list[str] | None = None,
        domain: str | None = None,
        statuses: list[str] | None = None,
    ) -> ContextQueryOut:
        self._require_project(project_id)
        n = _normalise_limit(limit)
        selected_sources = sources or ["runs", "learnings", "experiments", "decisions"]
        for source in selected_sources:
            if source not in _FIELD_MAP:
                raise ValidationError("unknown context source", data={"source": source})
        requested_fields = list(fields or ())
        used_fields: set[str] = set()
        for source in selected_sources:
            source_fields = requested_fields or list(_DEFAULT_FIELDS[source])
            invalid = sorted(set(source_fields) - _FIELD_MAP[source])
            if invalid:
                raise ValidationError(
                    "unsupported fields for context source",
                    data={"source": source, "fields": invalid},
                )
            used_fields.update(source_fields)

        items: list[ContextItemOut] = []
        total = 0
        tag_filter = _normalise_tags(tags)
        for source in selected_sources:
            source_items = self._source_items(
                source=source,
                project_id=project_id,
                fields=requested_fields or list(_DEFAULT_FIELDS[source]),
                limit=n,
                tags=tag_filter,
                domain=domain,
                statuses=statuses,
            )
            total += len(source_items)
            items.extend(source_items)

        items.sort(key=lambda item: item.occurred_at or datetime.min, reverse=True)
        bounded = items[:n]
        return ContextQueryOut(
            project_id=project_id,
            sources=selected_sources,
            fields=sorted(used_fields),
            limit=n,
            items=bounded,
            total_estimate=total,
        )

    def timeline(
        self,
        *,
        project_id: int,
        limit: int | None = None,
        after_id: int | None = None,
        event_type: str | None = None,
    ) -> Page[ProjectEventOut]:
        self._require_project(project_id)
        n = _normalise_limit(limit)
        filters: list[Any] = [col(ProjectEvent.project_id) == project_id]
        if event_type is not None:
            filters.append(col(ProjectEvent.event_type) == event_type)
        total = _scalar_count(
            self._s,
            sa_select(func.count()).select_from(ProjectEvent).where(*filters),
        )
        stmt = select(ProjectEvent).where(*filters)
        if after_id is not None:
            stmt = stmt.where(col(ProjectEvent.id) > after_id)
        rows = list(self._s.exec(stmt.order_by(col(ProjectEvent.id).asc()).limit(n + 1)).all())
        page_rows = rows[:n]
        next_cursor = _required_id(page_rows[-1].id) if len(rows) > n and page_rows else None
        return Page(
            items=[self._event_out(row) for row in page_rows],
            next_cursor=next_cursor,
            total_estimate=total,
        )

    def query_event_context(
        self,
        *,
        project_id: int,
        fields: list[str] | None = None,
        event_type: str | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> ContextPageOut:
        self._require_project(project_id)
        n = _normalise_limit(limit)
        requested = fields or list(_DEFAULT_FIELDS["events"])
        invalid = sorted(set(requested) - _FIELD_MAP["events"])
        if invalid:
            raise ValidationError(
                "unsupported fields for context source",
                data={"source": "events", "fields": invalid},
            )
        filters = [col(ProjectEvent.project_id) == project_id]
        if event_type is not None:
            filters.append(col(ProjectEvent.event_type) == event_type)
        total = _scalar_count(
            self._s,
            sa_select(func.count()).select_from(ProjectEvent).where(*filters),
        )
        stmt = select(ProjectEvent).where(*filters)
        if after_id is not None:
            stmt = stmt.where(col(ProjectEvent.id) > after_id)
        rows = list(self._s.exec(stmt.order_by(col(ProjectEvent.id).asc()).limit(n + 1)).all())
        page_rows = rows[:n]
        next_cursor = _required_id(page_rows[-1].id) if len(rows) > n and page_rows else None
        return ContextPageOut(
            items=[self._item_from_event(row, requested) for row in page_rows],
            next_cursor=next_cursor,
            total_estimate=total,
        )

    def create_snapshot(
        self,
        *,
        project_id: int,
        run_id: int | None = None,
        name: str | None = None,
        query_json: dict[str, Any] | None = None,
        selected_sources_json: list[dict[str, Any]] | None = None,
        summary_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> Envelope[ContextSnapshotOut]:
        self._require_project(project_id)
        if run_id is not None:
            run = self._s.get(Run, run_id)
            if run is None or run.project_id != project_id:
                raise NotFoundError(
                    f"run {run_id} not found in project {project_id}",
                    data={"project_id": project_id, "run_id": run_id},
                )
        row = ContextSnapshot(
            project_id=project_id,
            run_id=run_id,
            name=name,
            query_json=_safe_json(query_json or {}),
            selected_sources_json=_safe_json(selected_sources_json or []),
            summary_json=_safe_json(summary_json) if summary_json is not None else None,
            metadata_json=_safe_json(metadata_json) if metadata_json is not None else None,
        )
        self._s.add(row)
        self._s.flush()
        self._record_event(
            project_id=project_id,
            run_id=run_id,
            source_type="context_snapshot",
            source_id=row.id,
            event_type="context.snapshot",
            title=name or "Context snapshot",
            summary=None,
            tags=None,
            metadata_json={"snapshot_id": row.id},
        )
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._snapshot_out(row), project_id=project_id)

    def query_snapshots(
        self,
        *,
        project_id: int,
        run_id: int | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[ContextSnapshotOut]:
        self._require_project(project_id)
        n = _normalise_limit(limit)
        filters = [col(ContextSnapshot.project_id) == project_id]
        if run_id is not None:
            filters.append(col(ContextSnapshot.run_id) == run_id)
        total = _scalar_count(
            self._s,
            sa_select(func.count()).select_from(ContextSnapshot).where(*filters),
        )
        stmt = select(ContextSnapshot).where(*filters)
        if after_id is not None:
            stmt = stmt.where(col(ContextSnapshot.id) > after_id)
        rows = list(self._s.exec(stmt.order_by(col(ContextSnapshot.id).asc()).limit(n + 1)).all())
        page_rows = rows[:n]
        next_cursor = _required_id(page_rows[-1].id) if len(rows) > n and page_rows else None
        return Page(
            items=[self._snapshot_out(row) for row in page_rows],
            next_cursor=next_cursor,
            total_estimate=total,
        )

    def query_learnings(
        self,
        *,
        project_id: int,
        domain: str | None = None,
        status: str | None = None,
        review_state: str | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[LearningOut]:
        self._require_project(project_id)
        n = _normalise_limit(limit)
        filters = [col(Learning.project_id) == project_id]
        if domain is not None:
            filters.append(col(Learning.domain) == domain)
        if status is not None:
            filters.append(col(Learning.status) == status)
        if review_state is not None:
            filters.append(col(Learning.review_state) == review_state)
        stmt = select(Learning).where(*filters)
        if after_id is not None:
            stmt = stmt.where(col(Learning.id) > after_id)
        tag_filter = _normalise_tags(tags)
        all_rows = list(self._s.exec(stmt.order_by(col(Learning.id).asc())).all())
        filtered = [row for row in all_rows if _has_all_tags(row.tags_json, tag_filter)]
        page_rows = filtered[:n]
        next_cursor = _required_id(page_rows[-1].id) if len(filtered) > n and page_rows else None
        return Page(
            items=[self._learning_out(row) for row in page_rows],
            next_cursor=next_cursor,
            total_estimate=len(filtered),
        )

    def query_learning_context(
        self,
        *,
        project_id: int,
        fields: list[str] | None = None,
        domain: str | None = None,
        status: str | None = None,
        review_state: str | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> ContextPageOut:
        page = self.query_learnings(
            project_id=project_id,
            domain=domain,
            status=status,
            review_state=review_state,
            tags=tags,
            limit=limit,
            after_id=after_id,
        )
        requested = fields or list(_DEFAULT_FIELDS["learnings"])
        invalid = sorted(set(requested) - _FIELD_MAP["learnings"])
        if invalid:
            raise ValidationError(
                "unsupported fields for context source",
                data={"source": "learnings", "fields": invalid},
            )
        rows = [self._get_learning(project_id, item.id) for item in page.items]
        return ContextPageOut(
            items=[self._item_from_learning(row, requested) for row in rows],
            next_cursor=page.next_cursor,
            total_estimate=page.total_estimate,
        )

    def create_learning(
        self,
        *,
        project_id: int,
        statement: str,
        domain: str | None = None,
        confidence: str = "unknown",
        status: str = "active",
        review_state: str = "proposed",
        created_by: str | None = None,
        tags: list[str] | None = None,
        applies_to_json: dict[str, Any] | None = None,
        evidence_json: dict[str, Any] | None = None,
        source_snapshot_id: int | None = None,
        supersedes_learning_id: int | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> Envelope[LearningOut]:
        self._require_project(project_id)
        self._require_snapshot(project_id, source_snapshot_id)
        self._require_learning(project_id, supersedes_learning_id)
        row = Learning(
            project_id=project_id,
            statement=_safe_text(statement) or "",
            domain=domain,
            confidence=confidence,
            status=status,
            review_state=review_state,
            created_by=created_by,
            tags_json=_normalise_tags(tags),
            applies_to_json=_safe_json(applies_to_json) if applies_to_json is not None else None,
            evidence_json=_safe_json(evidence_json) if evidence_json is not None else None,
            source_snapshot_id=source_snapshot_id,
            supersedes_learning_id=supersedes_learning_id,
            metadata_json=_safe_json(metadata_json) if metadata_json is not None else None,
        )
        self._s.add(row)
        self._s.flush()
        self._index_learning(row)
        self._record_event(
            project_id=project_id,
            source_type="learning",
            source_id=row.id,
            event_type="learning.create",
            title="Learning recorded",
            summary=row.statement,
            tags=row.tags_json,
            metadata_json={"review_state": row.review_state, "status": row.status},
        )
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._learning_out(row), project_id=project_id)

    def update_learning(
        self,
        *,
        project_id: int,
        learning_id: int,
        statement: str | None = None,
        domain: str | None = None,
        confidence: str | None = None,
        status: str | None = None,
        review_state: str | None = None,
        tags: list[str] | None = None,
        applies_to_json: dict[str, Any] | None = None,
        evidence_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> Envelope[LearningOut]:
        row = self._get_learning(project_id, learning_id)
        if statement is not None:
            row.statement = _safe_text(statement) or ""
        if domain is not None:
            row.domain = domain
        if confidence is not None:
            row.confidence = confidence
        if status is not None:
            row.status = status
        if review_state is not None:
            row.review_state = review_state
        if tags is not None:
            row.tags_json = _normalise_tags(tags)
        if applies_to_json is not None:
            row.applies_to_json = _safe_json(applies_to_json)
        if evidence_json is not None:
            row.evidence_json = _safe_json(evidence_json)
        if metadata_json is not None:
            row.metadata_json = _safe_json(metadata_json)
        row.updated_at = _utcnow()
        self._s.add(row)
        self._s.flush()
        self._index_learning(row)
        self._record_event(
            project_id=project_id,
            source_type="learning",
            source_id=row.id,
            event_type="learning.update",
            title="Learning updated",
            summary=row.statement,
            tags=row.tags_json,
            metadata_json={"review_state": row.review_state, "status": row.status},
        )
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._learning_out(row), project_id=project_id)

    def query_experiments(
        self,
        *,
        project_id: int,
        domain: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[ExperimentOut]:
        self._require_project(project_id)
        n = _normalise_limit(limit)
        filters = [col(Experiment.project_id) == project_id]
        if domain is not None:
            filters.append(col(Experiment.domain) == domain)
        if status is not None:
            filters.append(col(Experiment.status) == status)
        stmt = select(Experiment).where(*filters)
        if after_id is not None:
            stmt = stmt.where(col(Experiment.id) > after_id)
        tag_filter = _normalise_tags(tags)
        rows = list(self._s.exec(stmt.order_by(col(Experiment.id).asc())).all())
        filtered = [
            row for row in rows if _has_all_tags((row.metadata_json or {}).get("tags"), tag_filter)
        ]
        page_rows = filtered[:n]
        next_cursor = _required_id(page_rows[-1].id) if len(filtered) > n and page_rows else None
        return Page(
            items=[self._experiment_out(row) for row in page_rows],
            next_cursor=next_cursor,
            total_estimate=len(filtered),
        )

    def query_experiment_context(
        self,
        *,
        project_id: int,
        fields: list[str] | None = None,
        domain: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> ContextPageOut:
        page = self.query_experiments(
            project_id=project_id,
            domain=domain,
            status=status,
            tags=tags,
            limit=limit,
            after_id=after_id,
        )
        requested = fields or list(_DEFAULT_FIELDS["experiments"])
        invalid = sorted(set(requested) - _FIELD_MAP["experiments"])
        if invalid:
            raise ValidationError(
                "unsupported fields for context source",
                data={"source": "experiments", "fields": invalid},
            )
        rows = [self._get_experiment(project_id, item.id) for item in page.items]
        return ContextPageOut(
            items=[self._item_from_experiment(row, requested) for row in rows],
            next_cursor=page.next_cursor,
            total_estimate=page.total_estimate,
        )

    def create_experiment(
        self,
        *,
        project_id: int,
        hypothesis: str,
        key: str | None = None,
        name: str | None = None,
        domain: str | None = None,
        status: str = "planned",
        linked_template_ids_json: list[str] | None = None,
        linked_run_ids_json: list[int] | None = None,
        metric_targets_json: dict[str, Any] | None = None,
        decision_policy_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        variants: list[dict[str, Any]] | None = None,
    ) -> Envelope[ExperimentOut]:
        self._require_project(project_id)
        row = Experiment(
            project_id=project_id,
            key=key,
            name=name,
            domain=domain,
            hypothesis=_safe_text(hypothesis) or "",
            status=status,
            linked_template_ids_json=linked_template_ids_json,
            linked_run_ids_json=linked_run_ids_json,
            metric_targets_json=_safe_json(metric_targets_json)
            if metric_targets_json is not None
            else None,
            decision_policy_json=_safe_json(decision_policy_json)
            if decision_policy_json is not None
            else None,
            metadata_json=_safe_json(metadata_json) if metadata_json is not None else None,
        )
        self._s.add(row)
        self._s.flush()
        for variant in variants or []:
            if not variant.get("key"):
                raise ValidationError("experiment variant key is required")
            self._s.add(
                ExperimentVariant(
                    experiment_id=_required_id(row.id),
                    key=str(variant["key"]),
                    name=variant.get("name"),
                    resources_json=_safe_json(variant.get("resources_json"))
                    if variant.get("resources_json") is not None
                    else None,
                    metadata_json=_safe_json(variant.get("metadata_json"))
                    if variant.get("metadata_json") is not None
                    else None,
                )
            )
        self._index_experiment(row)
        self._record_event(
            project_id=project_id,
            source_type="experiment",
            source_id=row.id,
            event_type="experiment.create",
            title=row.name or "Experiment created",
            summary=row.hypothesis,
            tags=(row.metadata_json or {}).get("tags"),
            metadata_json={"status": row.status},
        )
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._experiment_out(row), project_id=project_id)

    def record_observation(
        self,
        *,
        project_id: int,
        experiment_id: int,
        metrics_json: dict[str, Any],
        variant_key: str | None = None,
        run_id: int | None = None,
        summary: str | None = None,
        observed_at: datetime | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> Envelope[ExperimentObservationOut]:
        experiment = self._get_experiment(project_id, experiment_id)
        if run_id is not None:
            run = self._s.get(Run, run_id)
            if run is None or run.project_id != project_id:
                raise NotFoundError(
                    f"run {run_id} not found in project {project_id}",
                    data={"project_id": project_id, "run_id": run_id},
                )
        row = ExperimentObservation(
            project_id=project_id,
            experiment_id=experiment_id,
            run_id=run_id,
            variant_key=variant_key,
            metrics_json=_safe_json(metrics_json),
            summary=_safe_text(summary),
            observed_at=observed_at or _utcnow(),
            metadata_json=_safe_json(metadata_json) if metadata_json is not None else None,
        )
        self._s.add(row)
        self._s.flush()
        self._record_event(
            project_id=project_id,
            run_id=run_id,
            source_type="experiment_observation",
            source_id=row.id,
            event_type="experiment.recordObservation",
            title=f"Observation for {experiment.name or experiment.key or experiment.id}",
            summary=row.summary,
            tags=(experiment.metadata_json or {}).get("tags"),
            metadata_json={"experiment_id": experiment_id, "variant_key": variant_key},
        )
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._observation_out(row), project_id=project_id)

    def query_observations(
        self,
        *,
        project_id: int,
        experiment_id: int | None = None,
        run_id: int | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[ExperimentObservationOut]:
        self._require_project(project_id)
        n = _normalise_limit(limit)
        filters = [col(ExperimentObservation.project_id) == project_id]
        if experiment_id is not None:
            filters.append(col(ExperimentObservation.experiment_id) == experiment_id)
        if run_id is not None:
            filters.append(col(ExperimentObservation.run_id) == run_id)
        total = _scalar_count(
            self._s,
            sa_select(func.count()).select_from(ExperimentObservation).where(*filters),
        )
        stmt = select(ExperimentObservation).where(*filters)
        if after_id is not None:
            stmt = stmt.where(col(ExperimentObservation.id) > after_id)
        rows = list(
            self._s.exec(stmt.order_by(col(ExperimentObservation.id).asc()).limit(n + 1)).all()
        )
        page_rows = rows[:n]
        next_cursor = _required_id(page_rows[-1].id) if len(rows) > n and page_rows else None
        return Page(
            items=[self._observation_out(row) for row in page_rows],
            next_cursor=next_cursor,
            total_estimate=total,
        )

    def record_experiment_decision(
        self,
        *,
        project_id: int,
        experiment_id: int,
        decision: str,
        title: str | None = None,
        rationale: str | None = None,
        status: str = "recorded",
        decided_by: str | None = None,
        tags: list[str] | None = None,
        evidence_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        run_id: int | None = None,
        experiment_status: str | None = None,
    ) -> Envelope[DecisionOut]:
        experiment = self._get_experiment(project_id, experiment_id)
        if experiment_status is not None:
            experiment.status = experiment_status
            experiment.updated_at = _utcnow()
            self._s.add(experiment)
        return self.record_decision(
            project_id=project_id,
            decision=decision,
            title=title or f"Decision for {experiment.name or experiment.key or experiment.id}",
            rationale=rationale,
            status=status,
            decided_by=decided_by,
            tags=tags,
            evidence_json=evidence_json,
            metadata_json=metadata_json,
            run_id=run_id,
            experiment_id=experiment_id,
        )

    def query_decisions(
        self,
        *,
        project_id: int,
        experiment_id: int | None = None,
        run_id: int | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[DecisionOut]:
        self._require_project(project_id)
        n = _normalise_limit(limit)
        filters = [col(Decision.project_id) == project_id]
        if experiment_id is not None:
            filters.append(col(Decision.experiment_id) == experiment_id)
        if run_id is not None:
            filters.append(col(Decision.run_id) == run_id)
        if status is not None:
            filters.append(col(Decision.status) == status)
        stmt = select(Decision).where(*filters)
        if after_id is not None:
            stmt = stmt.where(col(Decision.id) > after_id)
        tag_filter = _normalise_tags(tags)
        rows = list(self._s.exec(stmt.order_by(col(Decision.id).asc())).all())
        filtered = [row for row in rows if _has_all_tags(row.tags_json, tag_filter)]
        page_rows = filtered[:n]
        next_cursor = _required_id(page_rows[-1].id) if len(filtered) > n and page_rows else None
        return Page(
            items=[self._decision_out(row) for row in page_rows],
            next_cursor=next_cursor,
            total_estimate=len(filtered),
        )

    def query_decision_context(
        self,
        *,
        project_id: int,
        fields: list[str] | None = None,
        experiment_id: int | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> ContextPageOut:
        page = self.query_decisions(
            project_id=project_id,
            experiment_id=experiment_id,
            status=status,
            tags=tags,
            limit=limit,
            after_id=after_id,
        )
        requested = fields or list(_DEFAULT_FIELDS["decisions"])
        invalid = sorted(set(requested) - _FIELD_MAP["decisions"])
        if invalid:
            raise ValidationError(
                "unsupported fields for context source",
                data={"source": "decisions", "fields": invalid},
            )
        rows = [self._get_decision(project_id, item.id) for item in page.items]
        return ContextPageOut(
            items=[self._item_from_decision(row, requested) for row in rows],
            next_cursor=page.next_cursor,
            total_estimate=page.total_estimate,
        )

    def query_metrics(
        self,
        *,
        project_id: int,
        metric_key: str | None = None,
        source_type: str | None = None,
        source_id: int | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[MetricSnapshotOut]:
        self._require_project(project_id)
        n = _normalise_limit(limit)
        filters = [col(MetricSnapshot.project_id) == project_id]
        if metric_key is not None:
            filters.append(col(MetricSnapshot.metric_key) == metric_key)
        if source_type is not None:
            filters.append(col(MetricSnapshot.source_type) == source_type)
        if source_id is not None:
            filters.append(col(MetricSnapshot.source_id) == source_id)
        total = _scalar_count(
            self._s,
            sa_select(func.count()).select_from(MetricSnapshot).where(*filters),
        )
        stmt = select(MetricSnapshot).where(*filters)
        if after_id is not None:
            stmt = stmt.where(col(MetricSnapshot.id) > after_id)
        rows = list(self._s.exec(stmt.order_by(col(MetricSnapshot.id).asc()).limit(n + 1)).all())
        page_rows = rows[:n]
        next_cursor = _required_id(page_rows[-1].id) if len(rows) > n and page_rows else None
        return Page(
            items=[self._metric_out(row) for row in page_rows],
            next_cursor=next_cursor,
            total_estimate=total,
        )

    def record_decision(
        self,
        *,
        project_id: int,
        decision: str,
        title: str | None = None,
        rationale: str | None = None,
        status: str = "recorded",
        decided_by: str | None = None,
        tags: list[str] | None = None,
        evidence_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        run_id: int | None = None,
        experiment_id: int | None = None,
    ) -> Envelope[DecisionOut]:
        self._require_project(project_id)
        if experiment_id is not None:
            self._get_experiment(project_id, experiment_id)
        if run_id is not None:
            run = self._s.get(Run, run_id)
            if run is None or run.project_id != project_id:
                raise NotFoundError(
                    f"run {run_id} not found in project {project_id}",
                    data={"project_id": project_id, "run_id": run_id},
                )
        row = Decision(
            project_id=project_id,
            experiment_id=experiment_id,
            run_id=run_id,
            title=_safe_text(title),
            decision=_safe_text(decision) or "",
            rationale=_safe_text(rationale),
            status=status,
            decided_by=decided_by,
            tags_json=_normalise_tags(tags),
            evidence_json=_safe_json(evidence_json) if evidence_json is not None else None,
            metadata_json=_safe_json(metadata_json) if metadata_json is not None else None,
        )
        self._s.add(row)
        self._s.flush()
        self._index_decision(row)
        self._record_event(
            project_id=project_id,
            run_id=run_id,
            source_type="decision",
            source_id=row.id,
            event_type="decision.record",
            title=row.title or "Decision recorded",
            summary=row.decision,
            tags=row.tags_json,
            metadata_json={"status": row.status, "experiment_id": experiment_id},
        )
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._decision_out(row), project_id=project_id)

    def _source_items(
        self,
        *,
        source: str,
        project_id: int,
        fields: list[str],
        limit: int,
        tags: list[str] | None,
        domain: str | None,
        statuses: list[str] | None,
    ) -> list[ContextItemOut]:
        if source == "runs":
            if tags or domain is not None:
                return []
            return self._run_items(
                project_id=project_id,
                fields=fields,
                limit=limit,
                statuses=statuses,
            )
        if source == "events":
            if domain is not None or statuses:
                return []
            rows = self._project_rows(
                ProjectEvent,
                project_id=project_id,
                limit=limit,
                unbounded=bool(tags),
            )
            return [
                self._item_from_event(row, fields)
                for row in rows
                if _has_all_tags(row.tags_json, tags)
            ][: limit * _FETCH_MULTIPLIER]
        if source == "index":
            conditions: list[Any] = []
            if domain is not None:
                conditions.append(col(ContextIndexEntry.domain) == domain)
            if statuses:
                conditions.append(col(ContextIndexEntry.status).in_(statuses))
            rows = self._project_rows(
                ContextIndexEntry,
                project_id=project_id,
                limit=limit,
                conditions=conditions,
                unbounded=bool(tags),
            )
            return [
                self._item_from_index(row, fields)
                for row in rows
                if _has_all_tags(row.tags_json, tags)
            ][: limit * _FETCH_MULTIPLIER]
        if source == "snapshots":
            if tags or domain is not None or statuses:
                return []
            rows = self._project_rows(ContextSnapshot, project_id=project_id, limit=limit)
            return [self._item_from_snapshot(row, fields) for row in rows]
        if source == "learnings":
            conditions = []
            if domain is not None:
                conditions.append(col(Learning.domain) == domain)
            if statuses:
                conditions.append(
                    or_(
                        col(Learning.status).in_(statuses),
                        col(Learning.review_state).in_(statuses),
                    )
                )
            rows = self._project_rows(
                Learning,
                project_id=project_id,
                limit=limit,
                conditions=conditions,
                unbounded=bool(tags),
            )
            return [
                self._item_from_learning(row, fields)
                for row in rows
                if _has_all_tags(row.tags_json, tags)
            ][: limit * _FETCH_MULTIPLIER]
        if source == "experiments":
            conditions = []
            if domain is not None:
                conditions.append(Experiment.domain == domain)
            if statuses:
                conditions.append(Experiment.status.in_(statuses))  # type: ignore[attr-defined]
            rows = self._project_rows(
                Experiment,
                project_id=project_id,
                limit=limit,
                conditions=conditions,
                unbounded=bool(tags),
            )
            return [
                self._item_from_experiment(row, fields)
                for row in rows
                if _has_all_tags((row.metadata_json or {}).get("tags"), tags)
            ][: limit * _FETCH_MULTIPLIER]
        if source == "decisions":
            conditions = []
            if statuses:
                conditions.append(Decision.status.in_(statuses))  # type: ignore[attr-defined]
            rows = self._project_rows(
                Decision,
                project_id=project_id,
                limit=limit,
                conditions=conditions,
                unbounded=bool(tags),
            )
            return [
                self._item_from_decision(row, fields)
                for row in rows
                if _has_all_tags(row.tags_json, tags)
            ][: limit * _FETCH_MULTIPLIER]
        if tags or domain is not None or statuses:
            return []
        rows = self._project_rows(MetricSnapshot, project_id=project_id, limit=limit)
        return [self._item_from_metric(row, fields) for row in rows]

    def _project_rows(
        self,
        model: type[Any],
        *,
        project_id: int,
        limit: int,
        conditions: list[Any] | None = None,
        unbounded: bool = False,
    ) -> list[Any]:
        stmt = (
            select(model)
            .where(col(model.project_id) == project_id, *(conditions or []))
            .order_by(col(model.id).desc())
        )
        if not unbounded:
            stmt = stmt.limit(limit * _FETCH_MULTIPLIER)
        return list(self._s.exec(stmt).all())

    def _run_items(
        self,
        *,
        project_id: int,
        fields: list[str],
        limit: int,
        statuses: list[str] | None,
    ) -> list[ContextItemOut]:
        stmt = select(Run).where(col(Run.project_id) == project_id)
        if statuses:
            stmt = stmt.where(col(Run.status).in_(statuses))
        rows = list(
            self._s.exec(stmt.order_by(col(Run.id).desc()).limit(limit * _FETCH_MULTIPLIER)).all()
        )
        return [self._item_from_run(row, fields) for row in rows]

    def _fields(self, source: str, row: Any, fields: list[str]) -> dict[str, Any]:
        return {
            field: _safe_json(getattr(row, field))
            for field in fields
            if field in _FIELD_MAP[source] and hasattr(row, field)
        }

    def _item_from_run(self, row: Run, fields: list[str]) -> ContextItemOut:
        assert row.id is not None
        data = self._fields("runs", row, fields)
        data["kind"] = str(data["kind"]) if "kind" in data else data.get("kind")
        data["status"] = str(data["status"]) if "status" in data else data.get("status")
        return ContextItemOut(
            source="runs",
            id=row.id,
            project_id=row.project_id,
            title=row.kind.value,
            occurred_at=row.started_at,
            fields=data,
            provenance={"table": "runs", "id": row.id},
        )

    def _item_from_event(self, row: ProjectEvent, fields: list[str]) -> ContextItemOut:
        assert row.id is not None
        data = self._fields("events", row, fields)
        if "tags" in fields:
            data["tags"] = list(row.tags_json or [])
        return ContextItemOut(
            source="events",
            id=row.id,
            project_id=row.project_id,
            title=row.title,
            occurred_at=row.occurred_at,
            fields=data,
            provenance={"table": "project_events", "id": row.id},
        )

    def _item_from_index(self, row: ContextIndexEntry, fields: list[str]) -> ContextItemOut:
        assert row.id is not None
        data = self._fields("index", row, fields)
        if "tags" in fields:
            data["tags"] = list(row.tags_json or [])
        return ContextItemOut(
            source="index",
            id=row.id,
            project_id=row.project_id,
            title=row.title,
            occurred_at=row.occurred_at,
            fields=data,
            provenance={"table": "context_index_entries", "id": row.id},
        )

    def _item_from_snapshot(self, row: ContextSnapshot, fields: list[str]) -> ContextItemOut:
        assert row.id is not None
        return ContextItemOut(
            source="snapshots",
            id=row.id,
            project_id=row.project_id,
            title=row.name,
            occurred_at=row.created_at,
            fields=self._fields("snapshots", row, fields),
            provenance={"table": "context_snapshots", "id": row.id},
        )

    def _item_from_learning(self, row: Learning, fields: list[str]) -> ContextItemOut:
        assert row.id is not None
        data = self._fields("learnings", row, fields)
        if "tags" in fields:
            data["tags"] = list(row.tags_json or [])
        return ContextItemOut(
            source="learnings",
            id=row.id,
            project_id=row.project_id,
            title=row.statement[:120],
            occurred_at=row.updated_at,
            fields=data,
            provenance={"table": "learnings", "id": row.id},
        )

    def _item_from_experiment(self, row: Experiment, fields: list[str]) -> ContextItemOut:
        assert row.id is not None
        data = self._fields("experiments", row, fields)
        if "variants" in fields:
            data["variants"] = [
                variant.model_dump(mode="json") for variant in self._variant_outs(row)
            ]
        return ContextItemOut(
            source="experiments",
            id=row.id,
            project_id=row.project_id,
            title=row.name or row.key,
            occurred_at=row.updated_at,
            fields=data,
            provenance={"table": "experiments", "id": row.id},
        )

    def _item_from_decision(self, row: Decision, fields: list[str]) -> ContextItemOut:
        assert row.id is not None
        data = self._fields("decisions", row, fields)
        if "tags" in fields:
            data["tags"] = list(row.tags_json or [])
        return ContextItemOut(
            source="decisions",
            id=row.id,
            project_id=row.project_id,
            title=row.title,
            occurred_at=row.created_at,
            fields=data,
            provenance={"table": "decisions", "id": row.id},
        )

    def _item_from_metric(self, row: MetricSnapshot, fields: list[str]) -> ContextItemOut:
        assert row.id is not None
        return ContextItemOut(
            source="metrics",
            id=row.id,
            project_id=row.project_id,
            title=row.metric_key,
            occurred_at=row.captured_at,
            fields=self._fields("metrics", row, fields),
            provenance={"table": "metric_snapshots", "id": row.id},
        )

    def _record_event(
        self,
        *,
        project_id: int,
        source_type: str,
        event_type: str,
        source_id: int | None = None,
        run_id: int | None = None,
        title: str | None = None,
        summary: str | None = None,
        tags: list[str] | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> ProjectEvent:
        row = ProjectEvent(
            project_id=project_id,
            run_id=run_id,
            source_type=source_type,
            source_id=source_id,
            event_type=event_type,
            title=_safe_text(title),
            summary=_safe_text(summary),
            tags_json=_normalise_tags(tags),
            metadata_json=_safe_json(metadata_json) if metadata_json is not None else None,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def _upsert_index(
        self,
        *,
        project_id: int,
        source_type: str,
        source_id: int | None,
        title: str | None,
        summary: str | None,
        domain: str | None,
        provider_key: str | None,
        status: str | None,
        tags: list[str] | None,
        metadata_json: dict[str, Any] | None,
        occurred_at: datetime,
    ) -> None:
        row = self._s.exec(
            select(ContextIndexEntry).where(
                ContextIndexEntry.project_id == project_id,
                ContextIndexEntry.source_type == source_type,
                ContextIndexEntry.source_id == source_id,
            )
        ).first()
        if row is None:
            row = ContextIndexEntry(
                project_id=project_id,
                source_type=source_type,
                source_id=source_id,
            )
        row.title = _safe_text(title)
        row.summary = _safe_text(summary)
        row.domain = domain
        row.provider_key = provider_key
        row.status = status
        row.tags_json = _normalise_tags(tags)
        row.metadata_json = _safe_json(metadata_json) if metadata_json is not None else None
        row.occurred_at = occurred_at
        self._s.add(row)

    def _index_learning(self, row: Learning) -> None:
        self._upsert_index(
            project_id=row.project_id,
            source_type="learning",
            source_id=row.id,
            title=row.statement[:120],
            summary=row.statement,
            domain=row.domain,
            provider_key=None,
            status=row.review_state,
            tags=row.tags_json,
            metadata_json={"confidence": row.confidence, "status": row.status},
            occurred_at=row.updated_at,
        )

    def _index_experiment(self, row: Experiment) -> None:
        self._upsert_index(
            project_id=row.project_id,
            source_type="experiment",
            source_id=row.id,
            title=row.name or row.key,
            summary=row.hypothesis,
            domain=row.domain,
            provider_key=None,
            status=row.status,
            tags=(row.metadata_json or {}).get("tags"),
            metadata_json={"metric_targets_json": row.metric_targets_json},
            occurred_at=row.updated_at,
        )

    def _index_decision(self, row: Decision) -> None:
        self._upsert_index(
            project_id=row.project_id,
            source_type="decision",
            source_id=row.id,
            title=row.title,
            summary=row.decision,
            domain=None,
            provider_key=None,
            status=row.status,
            tags=row.tags_json,
            metadata_json={"experiment_id": row.experiment_id, "run_id": row.run_id},
            occurred_at=row.created_at,
        )

    def _event_out(self, row: ProjectEvent) -> ProjectEventOut:
        assert row.id is not None
        return ProjectEventOut(
            id=row.id,
            project_id=row.project_id,
            run_id=row.run_id,
            source_type=row.source_type,
            source_id=row.source_id,
            event_type=row.event_type,
            title=_safe_text(row.title),
            summary=_safe_text(row.summary),
            tags=list(row.tags_json or []),
            metadata_json=_safe_json(row.metadata_json),
            occurred_at=row.occurred_at,
            created_at=row.created_at,
        )

    def _snapshot_out(self, row: ContextSnapshot) -> ContextSnapshotOut:
        assert row.id is not None
        return ContextSnapshotOut(
            id=row.id,
            project_id=row.project_id,
            run_id=row.run_id,
            name=row.name,
            query_json=_safe_json(row.query_json),
            selected_sources_json=_safe_json(row.selected_sources_json),
            summary_json=_safe_json(row.summary_json),
            metadata_json=_safe_json(row.metadata_json),
            created_at=row.created_at,
        )

    def _learning_out(self, row: Learning) -> LearningOut:
        assert row.id is not None
        return LearningOut(
            id=row.id,
            project_id=row.project_id,
            source_snapshot_id=row.source_snapshot_id,
            supersedes_learning_id=row.supersedes_learning_id,
            statement=_safe_text(row.statement) or "",
            domain=row.domain,
            confidence=row.confidence,
            status=row.status,
            review_state=row.review_state,
            created_by=row.created_by,
            tags=list(row.tags_json or []),
            applies_to_json=_safe_json(row.applies_to_json),
            evidence_json=_safe_json(row.evidence_json),
            metadata_json=_safe_json(row.metadata_json),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _variant_outs(self, experiment: Experiment) -> list[ExperimentVariantOut]:
        assert experiment.id is not None
        rows = self._s.exec(
            select(ExperimentVariant)
            .where(col(ExperimentVariant.experiment_id) == experiment.id)
            .order_by(col(ExperimentVariant.id).asc())
        ).all()
        return [
            ExperimentVariantOut(
                id=_required_id(row.id),
                experiment_id=row.experiment_id,
                key=row.key,
                name=row.name,
                resources_json=_safe_json(row.resources_json),
                metadata_json=_safe_json(row.metadata_json),
                created_at=row.created_at,
            )
            for row in rows
        ]

    def _experiment_out(self, row: Experiment) -> ExperimentOut:
        assert row.id is not None
        return ExperimentOut(
            id=row.id,
            project_id=row.project_id,
            key=row.key,
            name=row.name,
            domain=row.domain,
            hypothesis=_safe_text(row.hypothesis) or "",
            status=row.status,
            linked_template_ids_json=row.linked_template_ids_json,
            linked_run_ids_json=row.linked_run_ids_json,
            metric_targets_json=_safe_json(row.metric_targets_json),
            decision_policy_json=_safe_json(row.decision_policy_json),
            metadata_json=_safe_json(row.metadata_json),
            variants=self._variant_outs(row),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _observation_out(self, row: ExperimentObservation) -> ExperimentObservationOut:
        assert row.id is not None
        return ExperimentObservationOut(
            id=row.id,
            project_id=row.project_id,
            experiment_id=row.experiment_id,
            run_id=row.run_id,
            variant_key=row.variant_key,
            metrics_json=_safe_json(row.metrics_json),
            summary=_safe_text(row.summary),
            metadata_json=_safe_json(row.metadata_json),
            observed_at=row.observed_at,
            created_at=row.created_at,
        )

    def _metric_out(self, row: MetricSnapshot) -> MetricSnapshotOut:
        assert row.id is not None
        return MetricSnapshotOut(
            id=row.id,
            project_id=row.project_id,
            source_type=row.source_type,
            source_id=row.source_id,
            metric_key=row.metric_key,
            metric_value=row.metric_value,
            dimensions_json=_safe_json(row.dimensions_json),
            metadata_json=_safe_json(row.metadata_json),
            captured_at=row.captured_at,
            created_at=row.created_at,
        )

    def _decision_out(self, row: Decision) -> DecisionOut:
        assert row.id is not None
        return DecisionOut(
            id=row.id,
            project_id=row.project_id,
            experiment_id=row.experiment_id,
            run_id=row.run_id,
            title=_safe_text(row.title),
            decision=_safe_text(row.decision) or "",
            rationale=_safe_text(row.rationale),
            status=row.status,
            decided_by=row.decided_by,
            tags=list(row.tags_json or []),
            evidence_json=_safe_json(row.evidence_json),
            metadata_json=_safe_json(row.metadata_json),
            created_at=row.created_at,
        )

    def _get_learning(self, project_id: int, learning_id: int) -> Learning:
        row = self._s.get(Learning, learning_id)
        if row is None or row.project_id != project_id:
            raise NotFoundError(
                f"learning {learning_id} not found in project {project_id}",
                data={"project_id": project_id, "learning_id": learning_id},
            )
        return row

    def _get_experiment(self, project_id: int, experiment_id: int) -> Experiment:
        row = self._s.get(Experiment, experiment_id)
        if row is None or row.project_id != project_id:
            raise NotFoundError(
                f"experiment {experiment_id} not found in project {project_id}",
                data={"project_id": project_id, "experiment_id": experiment_id},
            )
        return row

    def _get_decision(self, project_id: int, decision_id: int) -> Decision:
        row = self._s.get(Decision, decision_id)
        if row is None or row.project_id != project_id:
            raise NotFoundError(
                f"decision {decision_id} not found in project {project_id}",
                data={"project_id": project_id, "decision_id": decision_id},
            )
        return row

    def _require_project(self, project_id: int) -> None:
        if self._s.get(Project, project_id) is None:
            raise NotFoundError(f"project {project_id} not found")

    def _require_snapshot(self, project_id: int, snapshot_id: int | None) -> None:
        if snapshot_id is None:
            return
        row = self._s.get(ContextSnapshot, snapshot_id)
        if row is None or row.project_id != project_id:
            raise NotFoundError(
                f"context snapshot {snapshot_id} not found in project {project_id}",
                data={"project_id": project_id, "snapshot_id": snapshot_id},
            )

    def _require_learning(self, project_id: int, learning_id: int | None) -> None:
        if learning_id is None:
            return
        self._get_learning(project_id, learning_id)


__all__ = [
    "ContextIndexEntryOut",
    "ContextItemOut",
    "ContextPageOut",
    "ContextQueryOut",
    "ContextRepository",
    "ContextSnapshotOut",
    "DecisionOut",
    "ExperimentObservationOut",
    "ExperimentOut",
    "ExperimentVariantOut",
    "LearningOut",
    "MetricSnapshotOut",
    "ProjectEventOut",
]
