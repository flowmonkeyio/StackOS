"""Persisted Board placement; visual order never changes execution priority."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, update
from sqlmodel import Session, col, select

from stackos.db.models import (
    ProjectEvent,
    TaskTracker,
    TrackerItemStatus,
    TrackerRevision,
    TrackerTask,
)
from stackos.repositories.tracker.utils import _utcnow

NORMALIZATION_KIND = "board-arrivals-normalization"


def next_task_position(
    session: Session,
    tracker_id: int,
    status: TrackerItemStatus,
    *,
    exclude_task_id: int | None = None,
) -> int:
    """Reserve the writer, then prepend to the complete destination column.

    The reservation also serializes concurrent creations before either reads
    the minimum rank. It does not advance revisions or touch project settings.
    The caller owns the transaction, status event and audit receipt.
    """
    session.execute(
        update(TaskTracker)
        .where(col(TaskTracker.id) == tracker_id)
        .values(rev=col(TaskTracker.rev))
        .execution_options(synchronize_session=False)
    )
    statement = select(func.min(TrackerTask.order_index)).where(
        TrackerTask.tracker_id == tracker_id, TrackerTask.status == status
    )
    if exclude_task_id is not None:
        statement = statement.where(TrackerTask.id != exclude_task_id)
    minimum = session.exec(statement).one()
    return minimum - 1 if minimum is not None else 0


def normalize_default_columns(session: Session) -> list[dict[str, Any]]:
    """One-time upgrade of provably untouched columns, in the migration transaction.

    Complete revision history and recorded ranks establish the default/manual
    distinction. Unknown history is preserved and named in the existing audit,
    never guessed. Status-entry events win; only missing entry events fall back
    to creation time/ID. Neither ordinary edits nor updated_at order arrivals.
    """
    from stackos.repositories.tracker.repository import TrackerRepository

    repo = TrackerRepository(session)
    reports = []
    for tracker in session.exec(select(TaskTracker).order_by(col(TaskTracker.id))).all():
        revisions = session.exec(
            select(TrackerRevision)
            .where(TrackerRevision.tracker_id == tracker.id)
            .order_by(col(TrackerRevision.rev))
        ).all()
        if any(revision.change_kind == NORMALIZATION_KIND for revision in revisions):
            continue
        tasks = session.exec(
            select(TrackerTask).where(
                TrackerTask.tracker_id == tracker.id, TrackerTask.project_id == tracker.project_id
            )
        ).all()
        if not tasks:
            continue
        manual: dict[str, list[int]] = {}
        ranks = {}
        created = set()
        unknown = (
            "incomplete revision history"
            if len(revisions) != tracker.rev
            or any(revision.rev != index for index, revision in enumerate(revisions, start=1))
            else None
        )
        for revision in revisions:
            after = revision.after_json or {}
            if revision.project_id != tracker.project_id:
                unknown = "revision project mismatch"
            if revision.entity_kind == "task" and "order_index" in after:
                ranks[revision.entity_key] = after["order_index"]
                if revision.change_kind in {"create", "mirror"}:
                    created.add(revision.entity_key)
            if (revision.metadata_json or {}).get(
                "operation"
            ) == "tracker.reorderTask" and "order" not in after:
                unknown = f"order revision {revision.rev} has no saved order"
            if "order" in after:
                status = (revision.patch_json or {}).get("expected_status")
                if status not in {item.value for item in TrackerItemStatus}:
                    unknown = f"order revision {revision.rev} has no column identity"
                else:
                    manual.setdefault(status, []).append(revision.rev)
                order = after["order"]
                if not isinstance(order, list) or any(
                    not isinstance(item, dict) or not {"task_key", "order_index"} <= item.keys()
                    for item in order
                ):
                    unknown = f"order revision {revision.rev} has incomplete rank evidence"
                    continue
                for item in order:
                    ranks[item["task_key"]] = item["order_index"]
        events = session.exec(
            select(ProjectEvent)
            .where(
                ProjectEvent.project_id == tracker.project_id,
                ProjectEvent.source_type == "tracker_task",
                ProjectEvent.event_type == "tracker.task.status_changed",
            )
            .order_by(col(ProjectEvent.occurred_at), col(ProjectEvent.id))
        ).all()
        latest = {event.source_id: event for event in events}
        report: dict[str, Any] = {
            "tracker_id": tracker.id,
            "history_through_rev": tracker.rev,
            "normalized": [],
            "preserved": [],
            "creation_fallback": [],
        }
        before_order: list[dict[str, Any]] = []
        after_order: list[dict[str, Any]] = []
        for status in sorted({task.status for task in tasks}):
            column = [task for task in tasks if task.status == status]
            reason = unknown
            if status in manual:
                reason = "saved manual ordering"
            elif any(
                task.key not in created or ranks.get(task.key) != task.order_index
                for task in column
            ):
                reason = reason or "missing or contradictory rank history"
            elif any(
                task.id in latest
                and (latest[task.id].metadata_json or {}).get("new_status") != status
                for task in column
            ):
                reason = reason or "status event contradicts current column"
            if reason:
                report["preserved"].append(
                    {
                        "status": status,
                        "reason": reason,
                        "task_keys": [task.key for task in column],
                        "manual_revisions": manual.get(status, []),
                    }
                )
                continue
            before_order.extend(
                {"task_key": task.key, "order_index": task.order_index} for task in column
            )
            column.sort(
                key=lambda task: (
                    latest[task.id].occurred_at if task.id in latest else task.created_at,
                    latest[task.id].id if task.id in latest else task.id,
                    task.id,
                ),
                reverse=True,
            )
            for index, task in enumerate(column):
                if task.id not in latest:
                    report["creation_fallback"].append(task.key)
                if task.order_index != index:
                    task.order_index = index
                    task.updated_at = max(_utcnow(), task.updated_at + timedelta(microseconds=1))
                    session.add(task)
                    repo._record_tracker_task_change_event(task, ["order_index"], actor="migration")
            after_order.extend(
                {"task_key": task.key, "order_index": task.order_index} for task in column
            )
            report["normalized"].append(status)
        repo._record_revision(
            tracker,
            actor="migration",
            change_kind=NORMALIZATION_KIND,
            entity_kind="tracker",
            entity_id=tracker.id,
            entity_key=tracker.key,
            summary=(
                "Normalized untouched Board columns to newest arrival first; "
                "preserved manual or ambiguous columns."
            ),
            before_json={"order": before_order},
            after_json={"order": after_order},
            metadata_json=report,
        )
        reports.append(report)
    return reports
