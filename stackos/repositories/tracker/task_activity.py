"""Persisted task history over ProjectEvent and exact structured revision deltas."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func, or_
from sqlmodel import Session, col, select

from stackos.artifacts import redact_secret_text, redact_secrets
from stackos.context.repository import ContextRepository
from stackos.db.models import ProjectEvent, TrackerRevision
from stackos.repositories.tracker.inspection_schema import TaskActivityItem, TaskActivityOut
from stackos.repositories.tracker.inspection_scope import (
    InspectionScope,
    decode_cursor,
    object_json,
    older_than,
    page_items,
    typed_match,
    validate_limit,
)

_OUTCOME_PATHS = [f"completion_evidence_json.{field}" for field in ("summary", "reason")]
_TASK_PATHS = [
    *(f"metadata_json.delivery.{field}" for field in ("status", "phase", "ticket_key")),
    *_OUTCOME_PATHS,
]
_RECEIPT_PATHS = [
    f"completion_evidence_json.automatic_delivery.{phase}.{field}"
    for phase in ("delivery", "review")
    for field in ("launch.started_at", "validated_outcome", "result.status")
]


def _value(data: Any, path: str) -> Any:
    for key in path.split("."):
        data = object_json(data).get(key)
    return data


def _changed(column_before: Any, column_after: Any, path: str) -> Any:
    return func.coalesce(func.json_extract(column_before, f"$.{path}"), "") != func.coalesce(
        func.json_extract(column_after, f"$.{path}"), ""
    )


class TaskActivityMixin:
    _s: Session

    def task_activity(
        self, *, project_id: int, task_key: str, limit: int = 50, cursor: str | None = None
    ) -> TaskActivityOut:
        validate_limit(limit)
        scope = InspectionScope(self._s, project_id, task_key)
        binding = ["activity", project_id, scope.task.id, task_key]
        last = decode_cursor(cursor, binding)
        event = ProjectEvent
        # Persisted source identity wins over metadata. Invocation events have no
        # row source_id, so their exact task/ticket metadata is validated instead.
        invocation_scope = and_(
            col(event.source_id).is_(None),
            typed_match(event.metadata_json, "task_key", {task_key}),
            typed_match(event.metadata_json, "ticket_key", scope.ticket_keys),
        )
        event_scope = or_(
            and_(col(event.source_type) == "tracker_task", col(event.source_id) == scope.task.id),
            and_(
                col(event.source_type) == "tracker_ticket",
                or_(col(event.source_id).in_(scope.tickets), invocation_scope),
            ),
            and_(
                col(event.source_type).not_in(["tracker_task", "tracker_ticket"]),
                col(event.run_id).in_(scope.runs),
            ),
        )
        events = self._s.exec(
            select(event)
            .where(
                event.project_id == project_id,
                event_scope,
                older_than(event.occurred_at, event.id, "project_event", last),
            )
            .order_by(col(event.occurred_at).desc(), col(event.id).desc())
            .limit(limit + 1)
        ).all()
        items = []
        for row in events:
            assert row.id is not None
            payload = ContextRepository(self._s)._event_out(row)
            metadata = object_json(payload.metadata_json)
            ticket_key = (
                scope.tickets[row.source_id].key
                if row.source_type == "tracker_ticket" and row.source_id in scope.tickets
                else metadata.get("ticket_key")
            )
            items.append(
                TaskActivityItem(
                    source_kind="project_event",
                    source_id=row.id,
                    occurred_at=row.occurred_at,
                    kind=row.event_type,
                    summary=payload.summary or payload.title or row.event_type,
                    task_key=task_key,
                    ticket_key=ticket_key
                    if isinstance(ticket_key, str) and ticket_key in scope.ticket_keys
                    else None,
                    run_id=row.run_id if row.run_id in scope.runs else None,
                    run_plan_id=metadata.get("run_plan_id")
                    if type(metadata.get("run_plan_id")) is int
                    and metadata["run_plan_id"] in scope.plans
                    else None,
                    details=payload.model_dump(),
                )
            )
        revision = TrackerRevision
        revision_scope = or_(
            and_(
                col(revision.entity_kind) == "task",
                col(revision.entity_id) == scope.task.id,
                or_(
                    *(
                        _changed(revision.before_json, revision.after_json, path)
                        for path in _TASK_PATHS
                    )
                ),
            ),
            and_(
                col(revision.entity_kind) == "ticket",
                col(revision.entity_id).in_(scope.tickets),
                or_(
                    *(
                        _changed(revision.before_json, revision.after_json, path)
                        for path in _RECEIPT_PATHS
                    )
                ),
            ),
        )
        # Filter relevant deltas in SQL before applying the merged keyset page.
        revisions = self._s.exec(
            select(revision)
            .where(
                revision.project_id == project_id,
                revision_scope,
                older_than(revision.created_at, revision.id, "tracker_revision", last),
            )
            .order_by(col(revision.created_at).desc(), col(revision.id).desc())
            .limit(limit + 1)
        ).all()
        for rev in revisions:
            assert rev.id is not None
            paths = _TASK_PATHS if rev.entity_kind == "task" else _RECEIPT_PATHS
            changes = [
                {
                    "path": path,
                    "before": _value(rev.before_json, path),
                    "after": _value(rev.after_json, path),
                }
                for path in paths
                if _value(rev.before_json, path) != _value(rev.after_json, path)
            ]
            # Result and validated_outcome are two representations of the same
            # receipt when this writer persists them in one revision.
            changes = [
                change
                for change in changes
                if not (
                    change["path"].endswith("result.status")
                    and any(
                        other["path"]
                        == change["path"].removesuffix("result.status") + "validated_outcome"
                        and other["after"] == change["after"]
                        for other in changes
                    )
                )
            ]
            prefix = "completion_evidence_json.automatic_delivery."
            recorded_ticket = _value(rev.after_json, "metadata_json.delivery.ticket_key")
            phase = _value(rev.after_json, "metadata_json.delivery.phase")
            if rev.entity_kind == "ticket":
                phases = {change["path"].removeprefix(prefix).split(".")[0] for change in changes}
                phase = next(iter(phases)) if len(phases) == 1 else None
            summary = "; ".join(
                f"{change['path'].removeprefix('metadata_json.').removeprefix(prefix)}: "
                f"{change['before']} → {change['after']}"
                for change in changes
            )
            items.append(
                TaskActivityItem(
                    source_kind="tracker_revision",
                    source_id=rev.id,
                    occurred_at=rev.created_at,
                    kind=(
                        "tracker.outcome.changed"
                        if rev.entity_kind == "task"
                        and any(change["path"] in _OUTCOME_PATHS for change in changes)
                        else "tracker.delivery.changed"
                    ),
                    task_key=task_key,
                    ticket_key=scope.tickets[rev.entity_id].key
                    if rev.entity_kind == "ticket"
                    else recorded_ticket
                    if isinstance(recorded_ticket, str) and recorded_ticket in scope.ticket_keys
                    else None,
                    summary=redact_secret_text(summary),
                    phase=phase if isinstance(phase, str) else None,
                    details=redact_secrets(
                        {
                            "rev": rev.rev,
                            "actor": rev.actor,
                            "changes": changes,
                            "reason": _value(rev.after_json, "metadata_json.delivery.reason")
                            if rev.entity_kind == "task"
                            else None,
                        }
                    ),
                )
            )
        page, next_cursor = page_items(items, limit, binding)
        return TaskActivityOut(
            project_id=project_id,
            task_id=scope.task_id,
            task_key=task_key,
            items=page,
            next_cursor=next_cursor,
            warnings=scope.warnings,
        )
