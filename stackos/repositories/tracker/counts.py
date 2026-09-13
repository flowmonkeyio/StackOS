"""Payload-free default-tracker ticket projections shared by counts and portfolio."""

from datetime import UTC, datetime

from sqlalchemy import case, func, select
from sqlalchemy.sql.selectable import Subquery
from sqlmodel import Session, col

from stackos.db.models import Project, TaskTracker, TrackerItemStatus, TrackerTicket
from stackos.repositories.tracker.schema import TrackerTicketCountsOut
from stackos.repositories.tracker.utils import DEFAULT_TRACKER_KEY


def ticket_counts_projection(*, project_id: int | None = None) -> Subquery:
    statement = (
        select(
            col(TrackerTicket.project_id),
            func.count(col(TrackerTicket.id)).label("ticket_count"),
            func.max(col(TrackerTicket.updated_at)).label("latest_ticket_at"),
            *(
                func.sum(case((col(TrackerTicket.status) == status, 1), else_=0)).label(
                    status.value
                )
                for status in TrackerItemStatus
            ),
        )
        .join(TaskTracker, col(TaskTracker.id) == col(TrackerTicket.tracker_id))
        .where(col(TaskTracker.key) == DEFAULT_TRACKER_KEY)
        .group_by(col(TrackerTicket.project_id))
    )
    if project_id is not None:
        statement = statement.where(col(TrackerTicket.project_id) == project_id)
    return statement.subquery()


def query_ticket_counts(
    session: Session, *, project_id: int | None = None, is_active: bool | None = None
) -> TrackerTicketCountsOut:
    counts = ticket_counts_projection(project_id=project_id)
    statement = select(
        *(func.coalesce(func.sum(counts.c[status.value]), 0) for status in TrackerItemStatus)
    ).select_from(counts)
    if is_active is not None:
        statement = statement.join(Project, col(Project.id) == counts.c.project_id).where(
            col(Project.is_active).is_(is_active)
        )
    row = session.execute(statement).one()
    by_status = {
        status.value: int(value) for status, value in zip(TrackerItemStatus, row, strict=True)
    }
    return TrackerTicketCountsOut(
        project_id=project_id,
        is_active=is_active,
        as_of=datetime.now(UTC),
        total_count=sum(by_status.values()),
        ticket_counts=by_status,
    )
