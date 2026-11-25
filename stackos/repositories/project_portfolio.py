"""Batched, payload-free project portfolio projection over existing records."""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import and_, case, func, or_, select
from sqlmodel import Session, col

from stackos.artifacts import redact_secret_text
from stackos.db.models import Project, TaskTracker, TrackerItemStatus, TrackerTask
from stackos.repositories.base import Page, ValidationError, _normalise_limit
from stackos.repositories.projects import (
    PortfolioTaskOut,
    ProjectPortfolioItemOut,
)
from stackos.repositories.tracker.counts import ticket_counts_projection
from stackos.repositories.tracker.utils import DEFAULT_TRACKER_KEY


def query_portfolio(
    session: Session,
    *,
    project_id: int | None = None,
    is_active: bool | None = True,
    query: str | None = None,
    ticket_status: TrackerItemStatus | None = None,
    sort: Literal["recent", "name"] = "recent",
    limit: int | None = None,
    after_id: int | None = None,
) -> Page[ProjectPortfolioItemOut]:
    size = _normalise_limit(limit)
    if sort not in {"recent", "name"}:
        raise ValidationError("sort must be recent or name")
    tasks = (
        select(
            col(TrackerTask.project_id),
            col(TrackerTask.id).label("task_id"),
            col(TrackerTask.key).label("task_key"),
            col(TrackerTask.title).label("task_title"),
            col(TrackerTask.status).label("task_status"),
            col(TrackerTask.updated_at).label("task_updated_at"),
            func.row_number()
            .over(
                partition_by=col(TrackerTask.project_id),
                order_by=(col(TrackerTask.updated_at).desc(), col(TrackerTask.id).desc()),
            )
            .label("position"),
        )
        .join(TaskTracker, col(TaskTracker.id) == col(TrackerTask.tracker_id))
        .where(col(TaskTracker.key) == DEFAULT_TRACKER_KEY)
        .subquery()
    )
    tickets = ticket_counts_projection(project_id=project_id)
    recent = case(
        (tasks.c.task_updated_at.is_(None), tickets.c.latest_ticket_at),
        (tickets.c.latest_ticket_at.is_(None), tasks.c.task_updated_at),
        else_=func.max(tasks.c.task_updated_at, tickets.c.latest_ticket_at),
    )
    sort_value = recent if sort == "recent" else func.lower(Project.name)
    statement = select(
        Project,
        tasks.c.task_id,
        tasks.c.task_key,
        tasks.c.task_title,
        tasks.c.task_status,
        tasks.c.task_updated_at,
        tickets.c.ticket_count,
        *(tickets.c[status.value] for status in TrackerItemStatus),
        recent.label("last_activity_at"),
        sort_value.label("portfolio_sort_key"),
    ).outerjoin(tasks, and_(tasks.c.project_id == Project.id, tasks.c.position == 1))
    statement = statement.outerjoin(tickets, tickets.c.project_id == Project.id)
    if ticket_status is not None:
        statement = statement.where(tickets.c[TrackerItemStatus(ticket_status).value] > 0)
    if project_id is not None:
        statement = statement.where(col(Project.id) == project_id)
    if is_active is not None:
        statement = statement.where(col(Project.is_active).is_(is_active))
    if query and query.strip():
        term = func.lower(query.strip())
        statement = statement.where(
            or_(
                func.instr(func.lower(Project.name), term) > 0,
                func.instr(func.lower(Project.slug), term) > 0,
                func.instr(func.lower(Project.domain), term) > 0,
            )
        )
    count_statement = select(func.count()).select_from(statement.subquery())
    total = session.execute(count_statement).scalar_one()
    if after_id is not None:
        anchor = session.execute(statement.where(col(Project.id) == after_id)).first()
        if anchor is None:
            raise ValidationError(
                "portfolio cursor no longer matches the filters; refresh the first page"
            )
        key = anchor._mapping["portfolio_sort_key"]
        if sort == "recent" and key is None:
            remaining = and_(sort_value.is_(None), col(Project.id) > after_id)
        else:
            comparison = sort_value < key if sort == "recent" else sort_value > key
            remaining = or_(comparison, and_(sort_value == key, col(Project.id) > after_id))
            if sort == "recent":
                remaining = or_(remaining, sort_value.is_(None))
        statement = statement.where(remaining)
    statement = statement.order_by(
        sort_value.desc() if sort == "recent" else sort_value.asc(), col(Project.id)
    )
    rows = session.execute(statement.limit(size + 1)).all()
    items = [_portfolio_item(row) for row in rows[:size]]
    return Page(
        items=items, total_estimate=total, next_cursor=items[-1].id if len(rows) > size else None
    )


def _portfolio_item(row: Any) -> ProjectPortfolioItemOut:
    project, data = row[0], row._mapping
    task = None
    if data["task_id"] is not None:
        task = PortfolioTaskOut(
            id=data["task_id"],
            key=redact_secret_text(data["task_key"]),
            title=redact_secret_text(data["task_title"]),
            status=data["task_status"],
            updated_at=data["task_updated_at"],
        )
    return ProjectPortfolioItemOut(
        id=project.id,
        slug=project.slug,
        name=project.name,
        domain=project.domain,
        is_active=project.is_active,
        created_at=project.created_at,
        updated_at=project.updated_at,
        last_activity_at=data["last_activity_at"],
        latest_task=task,
        ticket_count=data["ticket_count"] or 0,
        ticket_counts={status.value: data[status.value] or 0 for status in TrackerItemStatus},
    )
