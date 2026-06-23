"""Tracker status event helpers."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from stackos.db.models import ProjectEvent, TrackerItemStatus, TrackerTask, TrackerTicket
from stackos.events import StackOSEvent, StackOSEventSource
from stackos.repositories.project_events import StackOSEventEmitter

TRACKER_TASK_STATUS_CHANGED = "tracker.task.status_changed"
TRACKER_TICKET_STATUS_CHANGED = "tracker.ticket.status_changed"


def _status_value(status: TrackerItemStatus | str | None) -> str | None:
    if status is None:
        return None
    return status.value if isinstance(status, TrackerItemStatus) else str(status)


def _tracker_task_path(task: TrackerTask) -> str:
    return f"/projects/{task.project_id}/tasks?task={quote(task.key, safe='')}"


class TrackerEventMixin:
    """Append tracker status changes to the project timeline."""

    def _record_tracker_task_status_event(
        self,
        task: TrackerTask,
        old_status: TrackerItemStatus | str | None,
        new_status: TrackerItemStatus | str | None,
        *,
        actor: str | None = None,
        reason: str | None = None,
        source: str | None = None,
        trigger_ticket: TrackerTicket | None = None,
    ) -> ProjectEvent | None:
        old_value = _status_value(old_status)
        new_value = _status_value(new_status)
        if old_value == new_value:
            return None

        metadata: dict[str, Any] = {
            "entity_kind": "task",
            "task_id": task.id,
            "task_key": task.key,
            "task_title": task.title,
            "old_status": old_value,
            "new_status": new_value,
            "url_path": _tracker_task_path(task),
        }
        if actor:
            metadata["actor"] = actor
        if reason:
            metadata["reason"] = reason
        if source:
            metadata["source"] = source
        if trigger_ticket is not None:
            metadata["trigger_ticket"] = {
                "ticket_id": trigger_ticket.id,
                "ticket_key": trigger_ticket.key,
                "ticket_title": trigger_ticket.title,
            }

        return StackOSEventEmitter(self._s).emit(
            StackOSEvent(
                project_id=task.project_id,
                source=StackOSEventSource(type="tracker_task", id=task.id),
                event_type=TRACKER_TASK_STATUS_CHANGED,
                title=f"Task {task.key} is {new_value}",
                summary=f"Task {task.key} changed from {old_value} to {new_value}.",
                tags=["tracker", "task", "status"],
                metadata=metadata,
                actor=actor,
            )
        )

    def _record_tracker_ticket_status_event(
        self,
        ticket: TrackerTicket,
        old_status: TrackerItemStatus | str | None,
        new_status: TrackerItemStatus | str | None,
        *,
        task: TrackerTask | None = None,
        actor: str | None = None,
        reason: str | None = None,
        source: str | None = None,
    ) -> ProjectEvent | None:
        old_value = _status_value(old_status)
        new_value = _status_value(new_status)
        if old_value == new_value:
            return None

        task_key = task.key if task is not None else None
        metadata: dict[str, Any] = {
            "entity_kind": "ticket",
            "ticket_id": ticket.id,
            "ticket_key": ticket.key,
            "ticket_title": ticket.title,
            "task_id": ticket.task_id,
            "task_key": task_key,
            "old_status": old_value,
            "new_status": new_value,
        }
        if task is not None:
            metadata["url_path"] = _tracker_task_path(task)
        if actor:
            metadata["actor"] = actor
        if reason:
            metadata["reason"] = reason
        if source:
            metadata["source"] = source

        return StackOSEventEmitter(self._s).emit(
            StackOSEvent(
                project_id=ticket.project_id,
                run_id=ticket.run_id,
                source=StackOSEventSource(type="tracker_ticket", id=ticket.id),
                event_type=TRACKER_TICKET_STATUS_CHANGED,
                title=f"Ticket {ticket.key} is {new_value}",
                summary=f"Ticket {ticket.key} changed from {old_value} to {new_value}.",
                tags=["tracker", "ticket", "status"],
                metadata=metadata,
                actor=actor,
            )
        )


__all__ = [
    "TRACKER_TASK_STATUS_CHANGED",
    "TRACKER_TICKET_STATUS_CHANGED",
    "TrackerEventMixin",
]
