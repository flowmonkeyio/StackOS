"""Board membership is a projection of existing placement and execution facts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from sqlalchemy import func
from sqlmodel import Session, col, select

from stackos.db.models import ProjectEvent, TrackerTask, TrackerTicket
from stackos.repositories.tracker.schema import TaskDeliveryStateOut

if TYPE_CHECKING:
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


def task_column(task: TrackerTask | TrackerTaskOut, *, has_execution: bool = False) -> BoardColumn:
    if task.status in TERMINAL_COLUMNS:
        return TERMINAL_COLUMNS[task.status]
    delivery = TaskDeliveryStateOut.from_metadata(task.metadata_json)
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


def task_columns(session: Session, tasks: list[TrackerTask]) -> dict[int | None, BoardColumn]:
    # Only queued work needs historical evidence. A workflow's started_at is
    # lifecycle state, not proof that a native host call has ever begun.
    queued = [
        task
        for task in tasks
        if (
            task.status in {"not-started", "in-progress"}
            and (state := TaskDeliveryStateOut.from_metadata(task.metadata_json)) is not None
            and state.status == "queued"
        )
    ]
    executed: set[int | None] = set()
    if queued:
        from stackos.repositories.tracker.agent_stats import _last_execution, invocation_receipts

        for project_id in {task.project_id for task in queued}:
            keys = {task.key: task.id for task in queued if task.project_id == project_id}
            event_keys = session.exec(
                select(func.json_extract(ProjectEvent.metadata_json, "$.task_key"))
                .where(
                    ProjectEvent.project_id == project_id,
                    ProjectEvent.event_type == "tracker.execution.changed",
                    ProjectEvent.source_type == "tracker_ticket",
                    func.json_extract(ProjectEvent.metadata_json, "$.active") == 1,
                    func.json_extract(ProjectEvent.metadata_json, "$.task_key").in_(keys),
                )
                .distinct()
            ).all()
            executed.update(keys[key] for key in event_keys)
        # Older actual invocation receipts remain valid after restart or event
        # retention. Launch/session records alone do not establish execution.
        missing = {task.id for task in queued} - executed
        if missing:
            for task_id, evidence in session.exec(
                select(TrackerTicket.task_id, TrackerTicket.completion_evidence_json).where(
                    col(TrackerTicket.task_id).in_(missing)
                )
            ).all():
                if _last_execution(invocation_receipts(evidence)) is not None:
                    executed.add(task_id)
    return {task.id: task_column(task, has_execution=task.id in executed) for task in tasks}
