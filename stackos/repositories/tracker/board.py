"""Board membership is a projection of existing placement and execution facts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from sqlalchemy import case, func
from sqlmodel import Session, col, select

from stackos.db.json_expressions import json_document, json_kind, json_value
from stackos.db.models import ProjectEvent, TrackerReceiptProjection, TrackerTask, TrackerTicket
from stackos.repositories.tracker.schema import TaskDeliveryStateOut, TrackerTaskSummaryOut

if TYPE_CHECKING:
    from stackos.repositories.tracker.agent_stats import ReceiptProjection
    from stackos.repositories.tracker.schema import TrackerTaskOut

BoardColumn = Literal[
    "backlog", "todo", "in-progress", "stopped", "done", "aborted", "failed", "skipped"
]
TERMINAL_COLUMNS: dict[str, BoardColumn] = {
    "complete": "done",
    "aborted": "aborted",
    "failed": "failed",
    "skipped": "skipped",
    "deferred": "backlog",
}


def nonempty_json_text(column, path: str):
    """SQL counterpart of canonical string.strip validation, without returning text."""
    whitespace = (
        " \t\n\v\f\r\x1c\x1d\x1e\x1f\x85\xa0\u1680\u2000\u2001\u2002\u2003"
        "\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
    )
    keys = path.removeprefix("$.").split(".")
    return (json_kind(column, *keys) == "text") & (
        func.trim(json_value(column, *keys).as_string(), whitespace) != ""
    )


def valid_delivery_json(column, prefix: str = "$.delivery"):
    """Validate the canonical handoff without reading frozen instructions into Python."""
    paths = (
        "job_ref",
        "delivery_agent.name",
        "delivery_agent.instructions",
        "review_agent.name",
        "review_agent.instructions",
    )
    valid = nonempty_json_text(column, f"{prefix}.{paths[0]}")
    for path in paths[1:]:
        valid = valid & nonempty_json_text(column, f"{prefix}.{path}")
    return valid


def task_column(task: TrackerTask | TrackerTaskOut, *, has_execution: bool = False) -> BoardColumn:
    if task.status in TERMINAL_COLUMNS:
        return TERMINAL_COLUMNS[task.status]
    delivery = (
        task.delivery
        if isinstance(task, TrackerTaskSummaryOut)
        else TaskDeliveryStateOut.from_metadata(task.metadata_json)
    )
    if delivery is not None:
        if delivery.status == "stopped":
            return "stopped"
        if delivery.status == "queued":
            return "in-progress" if has_execution else "todo"
        if delivery.status == "running":
            return "in-progress"
    if task.status == "in-progress":
        return "in-progress"
    return "backlog" if task.lane_key == "backlog" else "todo"


def task_columns(
    session: Session,
    tasks: list[TrackerTask],
    *,
    projection: ReceiptProjection | None = None,
    lightweight: bool = False,
) -> dict[int | None, BoardColumn]:
    # Only queued work needs historical evidence. A workflow's started_at is
    # lifecycle state, not proof that a native host call has ever begun.
    queued = [
        task
        for task in tasks
        if (
            task.status in {"not-started", "in-progress"}
            and (
                state := (
                    task.delivery
                    if isinstance(task, TrackerTaskSummaryOut)
                    else TaskDeliveryStateOut.from_metadata(task.metadata_json)
                )
            )
            is not None
            and state.status == "queued"
        )
    ]
    executed: set[int | None] = set()
    if queued:
        from stackos.repositories.tracker.agent_stats import ReceiptProjection, _last_execution

        for project_id in {task.project_id for task in queued}:
            keys = {task.key: task.id for task in queued if task.project_id == project_id}
            event_keys = session.exec(
                select(json_value(ProjectEvent.metadata_json, "task_key").as_string())
                .where(
                    ProjectEvent.project_id == project_id,
                    ProjectEvent.event_type == "tracker.execution.changed",
                    ProjectEvent.source_type == "tracker_ticket",
                    json_kind(ProjectEvent.metadata_json, "active") == "true",
                    json_value(ProjectEvent.metadata_json, "task_key").as_string().in_(keys),
                )
                .distinct()
            ).all()
            executed.update(keys[key] for key in event_keys)
        # Older actual invocation receipts remain valid after restart or event
        # retention. Launch/session records alone do not establish execution.
        missing = {task.id for task in queued} - executed
        if missing and lightweight:
            # Board membership needs only actual invocation identity. Never load or
            # decode receipt/history payloads merely to discover a queued column.
            receipt = json_document(TrackerReceiptProjection.receipt_json)
            has_execution = (json_kind(receipt, "execution") == "object") & (
                json_value(receipt, "execution").as_string() != "{}"
            )
            fields = ("agent", "host_key", "model", "started_at", "invocation_id", "ticket_key")
            rows = session.execute(
                select(
                    TrackerTicket.task_id,
                    TrackerTicket.key,
                    *[
                        case(
                            (has_execution, json_value(receipt, "execution", field).as_string()),
                            else_=json_value(receipt, "launch", field).as_string(),
                        )
                        for field in fields
                    ],
                )
                .join(
                    TrackerReceiptProjection,
                    col(TrackerReceiptProjection.ticket_id) == col(TrackerTicket.id),
                )
                .where(
                    col(TrackerTicket.task_id).in_(missing),
                    TrackerReceiptProjection.inventory == "usage",
                    has_execution
                    | (json_kind(receipt, "exit_code") == "integer")
                    | nonempty_json_text(receipt, "$.thread_id")
                    | (json_kind(receipt, "result") == "object"),
                )
            ).all()
            for task_id, key, *values in rows:
                identity = dict(zip(fields, values, strict=True))
                if _last_execution([{"execution": identity, "ticket_key": key}]) is not None:
                    executed.add(task_id)
            missing = set()  # Scalar discovery is complete; never fall through to metrics.
        if missing:
            if projection is None:
                projection = ReceiptProjection(session)
                projection.load(task_ids={tid for tid in missing if tid is not None})
            for ticket in projection.tickets.values():
                if (
                    ticket.task_id in missing
                    and _last_execution([fact.receipt for fact in ticket.durable_receipts])
                    is not None
                ):
                    executed.add(ticket.task_id)
    return {task.id: task_column(task, has_execution=task.id in executed) for task in tasks}
