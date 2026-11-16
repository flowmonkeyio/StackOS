"""Explicit lightweight tracker reads at the existing query/receipt owners."""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, func
from sqlalchemy import select as sa_select
from sqlalchemy.orm import load_only
from sqlmodel import col, select

from stackos.db.models import TrackerTask, TrackerTicket
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.tracker.agent_stats import ReceiptProjection, query_task_agent_stats
from stackos.repositories.tracker.board import task_columns, valid_delivery_json
from stackos.repositories.tracker.schema import (
    TaskDeliveryStateOut,
    TrackerMetricDemand,
    TrackerSnapshotOut,
    TrackerTaskSummaryOut,
    TrackerTicketSummaryOut,
    WorkDurationOut,
)


def task_headers(session, tracker_id: int, *, task_key=None, statuses=None, workflow_key=None):
    """Read scalar columns and allowlisted JSON paths, never whole prompt/evidence JSON."""
    fields = [
        getattr(TrackerTask, name)
        for name in TrackerTaskSummaryOut.model_fields
        if name in TrackerTask.model_fields and not name.endswith("_json")
    ]
    source_paths = ("template_key", "template_version", "run_plan_key", "run_plan_id")
    # Validate the canonical handoff shape without transporting its frozen instructions.
    delivery_paths = ("status", "ticket_key", "reason")
    valid = valid_delivery_json(TrackerTask.metadata_json)
    statement = (
        sa_select(
            TrackerTask,
            *[func.json_extract(TrackerTask.source_json, f"$.{path}") for path in source_paths],
            valid,
            *[
                func.json_extract(TrackerTask.metadata_json, f"$.delivery.{path}")
                for path in delivery_paths
            ],
            case(
                (
                    func.json_type(TrackerTask.metadata_json, "$.delivery_scope.resume_condition")
                    == "text",
                    func.json_extract(
                        TrackerTask.metadata_json, "$.delivery_scope.resume_condition"
                    ),
                ),
                else_=None,
            ),
        )
        .options(load_only(*fields, raiseload=True))
        .where(col(TrackerTask.tracker_id) == tracker_id)
    )
    if task_key is not None:
        statement = statement.where(TrackerTask.key == task_key)
    if statuses:
        statement = statement.where(col(TrackerTask.status).in_(statuses))
    if workflow_key is not None:
        statement = statement.where(
            (func.json_extract(TrackerTask.source_json, "$.template_key") == workflow_key)
            | (func.json_extract(TrackerTask.source_json, "$.run_plan_key") == workflow_key)
        )
    rows = session.execute(
        statement.order_by(col(TrackerTask.order_index), col(TrackerTask.id))
    ).all()
    headers = []
    for task, *values in rows:
        source = {
            key: value
            for key, value in zip(source_paths, values[:4], strict=True)
            if isinstance(value, (str, int))
        }
        valid, status, ticket_key, reason, resume_condition = values[4:]
        delivery = None
        if valid and status in {"queued", "running", "stopped", "complete"}:
            delivery = TaskDeliveryStateOut(
                status=status,
                ticketKey=ticket_key.strip()
                if isinstance(ticket_key, str) and ticket_key.strip()
                else None,
                reason=reason.strip()
                if status == "stopped" and isinstance(reason, str) and reason.strip()
                else None,
            )
        headers.append(
            TrackerTaskSummaryOut(
                **{field.key: getattr(task, field.key) for field in fields},
                source_json=source or None,
                metadata_json={"delivery_scope": {"resume_condition": resume_condition}}
                if isinstance(resume_condition, str)
                else None,
                delivery=delivery,
                board_column="todo",
            )
        )
    return headers


def ui_snapshot(
    repo: Any,
    *,
    project_id,
    task_key,
    task_statuses,
    workflow_key,
    include_graph,
    task_index_only,
    demand: TrackerMetricDemand,
    receipt_projection: ReceiptProjection | None = None,
) -> TrackerSnapshotOut:
    ProjectRepository(repo._s).require_available(project_id)
    tracker = repo._tracker_or_none(project_id=project_id)
    if tracker is None:
        if task_key is not None:
            raise NotFoundError(f"Task {task_key!r} not found")
        if demand.task_keys or demand.ticket_keys:
            raise ValidationError("Metric demand is outside the selected scope")
        return TrackerSnapshotOut(
            tracker=repo._empty_tracker_out(project_id=project_id),
            lanes=repo._default_lane_out(),
            priorities=repo._default_priority_out(),
            tasks=[],
            tickets=[],
            dependencies=[],
            links=[],
            projection="ui",
            metric_scope="demand",
            metric_demand=demand,
        )
    tasks = task_headers(
        repo._s, tracker.id, task_key=task_key, statuses=task_statuses, workflow_key=workflow_key
    )
    if task_key is not None and not tasks:
        raise NotFoundError(f"Task {task_key!r} not found in selected scope")
    tasks_by_key = {task.key: task for task in tasks}
    if not set(demand.task_keys) <= tasks_by_key.keys():
        raise ValidationError("Metric task demand is outside the selected scope")
    # A project index never hydrates the ticket archive. A graph is explicitly selected.
    if not task_index_only and task_key is None:
        raise ValidationError("UI ticket/graph projection requires a selected task_key")
    ticket_fields = [
        getattr(TrackerTicket, name)
        for name in TrackerTicketSummaryOut.model_fields
        if name in TrackerTicket.model_fields and not name.endswith("_json")
    ]
    ticket_rows = (
        list(
            repo._s.execute(
                select(
                    TrackerTicket,
                    func.json_extract(TrackerTicket.metadata_json, "$.resume_condition"),
                )
                .where(
                    TrackerTicket.tracker_id == tracker.id,
                    col(TrackerTicket.task_id).in_({task.id for task in tasks}),
                    col(TrackerTicket.key).in_(demand.ticket_keys) if task_index_only else True,
                )
                .options(load_only(*ticket_fields, raiseload=True))
                .order_by(col(TrackerTicket.order_index), col(TrackerTicket.id))
            )
        )
        if not task_index_only or demand.ticket_keys
        else []
    )
    tickets = [row[0] for row in ticket_rows]
    resume_conditions = {row[0].id: row[1] for row in ticket_rows}
    ticket_ids_by_key = {ticket.key: ticket.id for ticket in tickets}
    if not set(demand.ticket_keys) <= ticket_ids_by_key.keys():
        raise ValidationError("Metric ticket demand is outside the selected graph scope")
    projection = receipt_projection or ReceiptProjection(repo._s)
    # Shared global demand was authorized and loaded once by exact composite keys.
    metric_tracker_id = None if receipt_projection is not None else tracker.id
    task_ids = {tasks_by_key[key].id for key in demand.task_keys}
    ticket_ids = {ticket_ids_by_key[key] for key in demand.ticket_keys}
    # Exactly one receipt batch combines demanded task and ticket scopes.
    projection.load(task_ids=task_ids, ticket_ids=ticket_ids, tracker_id=metric_tracker_id)
    durations: dict[int, WorkDurationOut] = {}
    metrics = query_task_agent_stats(
        repo._s,
        tracker_id=metric_tracker_id,
        task_ids=task_ids,
        work_durations=durations,
        task_keys={task.id: task.key for task in tasks if task.id in task_ids},
        projection=projection,
    )
    columns = task_columns(repo._s, tasks, lightweight=True)
    summaries = repo._task_ticket_summaries(tracker.id, tasks)
    from stackos.repositories.tracker.queries import _execution_out

    for task in tasks:
        task.board_column = columns[task.id]
        task.ticket_summary = summaries.get(task.id)
        if task.id in task_ids:
            task.agent_stats = metrics[task.id]
            task.work_duration = durations[task.id]
            task.active_execution = _execution_out(project_id, task.key)
    ticket_out = repo._ticket_out_many(
        tickets, summary=True, metric_ticket_ids=ticket_ids, projection=projection
    )
    for ticket in ticket_out:
        condition = resume_conditions.get(ticket.id)
        ticket.metadata_json = (
            {"resume_condition": condition} if isinstance(condition, str) else None
        )
    dependencies = repo._dependency_out_for_tickets(tickets)
    links = (
        repo._link_out_for_scope(
            tracker.id,
            {task.id for task in tasks},
            {ticket.id for ticket in tickets},
            summary=True,
        )
        if not task_index_only
        else []
    )
    graph = (
        repo._graph_out(tasks, ticket_out, dependencies, links, summary=True)
        if include_graph
        else None
    )
    return TrackerSnapshotOut(
        tracker=repo._tracker_out(tracker).model_copy(update={"config_json": None}),
        lanes=repo._lane_out(tracker.id),
        priorities=repo._priority_out(tracker.id),
        tasks=tasks,
        tickets=ticket_out,
        dependencies=dependencies,
        links=links,
        graph=graph,
        projection="ui",
        metric_scope="demand",
        metric_demand=demand,
    )
