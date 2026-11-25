"""Bounded scalar projection of the existing atomic tracker revision feed."""

from sqlalchemy import case, func, select
from sqlmodel import Session, col

from stackos.db.models import Project, TaskTracker, TrackerRevision, TrackerTask
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.tracker.board import valid_delivery_json
from stackos.repositories.tracker.schema import (
    TrackerNotificationChangeOut,
    TrackerNotificationChangesOut,
)


def notification_changes(
    session: Session,
    tracker: TaskTracker | None,
    *,
    project_id: int,
    since_rev: int | None,
    limit: int,
) -> TrackerNotificationChangesOut:
    name = session.execute(
        select(col(Project.name)).where(
            col(Project.id) == project_id,
            col(Project.is_active).is_(True),
            (
                func.json_type(Project.cleanup_json).is_(None)
                | (func.json_type(Project.cleanup_json) == "null")
            ),
        )
    ).scalar_one_or_none()
    if name is None:
        raise NotFoundError("project not found or unavailable")
    current = tracker.rev if tracker else 0
    result = TrackerNotificationChangesOut(
        project_id=project_id,
        project_name=name,
        since_rev=since_rev,
        current_rev=current,
    )
    # No historical scan during first load or migration from project-event IDs.
    if since_rev is None:
        return result
    if since_rev < 0 or since_rev > current:
        raise ValidationError("notification revision cursor is outside the current tracker")
    if tracker is None:
        return result
    size = max(1, min(limit, 200))
    revisions = (
        select(
            col(TrackerRevision.rev),
            col(TrackerRevision.entity_kind),
            col(TrackerRevision.entity_id),
            col(TrackerRevision.entity_key),
            col(TrackerRevision.before_json),
            col(TrackerRevision.after_json),
        )
        .where(
            col(TrackerRevision.tracker_id) == tracker.id,
            col(TrackerRevision.project_id) == project_id,
            col(TrackerRevision.rev) > since_rev,
            col(TrackerRevision.rev) <= current,
        )
        .order_by(col(TrackerRevision.rev))
        .limit(size)
        .cte("notification_revisions")
    )

    def delivery(column):
        status = func.json_extract(column, "$.metadata_json.delivery.status")
        return case(
            (
                valid_delivery_json(column, "$.metadata_json.delivery")
                & status.in_(("queued", "running", "stopped", "complete")),
                status,
            ),
            else_=None,
        )

    rows = session.execute(
        select(
            revisions.c.rev,
            col(TrackerTask.id),
            col(TrackerTask.key),
            func.substr(func.json_extract(revisions.c.after_json, "$.title"), 1, 200),
            func.json_extract(revisions.c.before_json, "$.status"),
            func.json_extract(revisions.c.after_json, "$.status"),
            delivery(revisions.c.before_json),
            delivery(revisions.c.after_json),
        )
        .select_from(revisions)
        .outerjoin(
            TrackerTask,
            (
                (revisions.c.entity_kind == "task")
                & (TrackerTask.id == revisions.c.entity_id)
                & (TrackerTask.key == revisions.c.entity_key)
                & (TrackerTask.tracker_id == tracker.id)
                & (TrackerTask.project_id == project_id)
            ),
        )
        .order_by(revisions.c.rev)
    ).all()
    for rev, task_id, key, title, before, after, before_delivery, after_delivery in rows:
        if task_id is not None:
            result.changes.append(
                TrackerNotificationChangeOut(
                    rev=rev,
                    task_id=task_id,
                    task_key=key,
                    task_title=title or key,
                    before_status=before,
                    after_status=after,
                    before_delivery_status=before_delivery,
                    after_delivery_status=after_delivery,
                )
            )
    # Advance over quiet revisions and deleted targets too. New writes after the
    # captured high-water revision belong to the next poll, never this page.
    if rows and rows[-1][0] < current and len(rows) == size:
        result.next_cursor = rows[-1][0]
    return result
