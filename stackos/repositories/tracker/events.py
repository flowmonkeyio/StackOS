"""Tracker status event helpers."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, case, func
from sqlalchemy import select as sa_select
from sqlmodel import Session, col

from stackos.db.models import ProjectEvent, TrackerItemStatus, TrackerTask, TrackerTicket
from stackos.events import StackOSEvent, StackOSEventSource
from stackos.host_mcp.service import ActiveInvocation
from stackos.repositories.project_events import StackOSEventEmitter
from stackos.repositories.tracker.agent_stats import (
    invocation_receipts,
    invocation_timing_facts,
    summarize_receipts,
)
from stackos.repositories.tracker.ordering import next_task_position

TRACKER_TASK_STATUS_CHANGED = "tracker.task.status_changed"
TRACKER_TASK_CHANGED = "tracker.task.changed"
TRACKER_TASKS_CHANGED = "tracker.tasks.changed"
TRACKER_TICKET_STATUS_CHANGED = "tracker.ticket.status_changed"
TRACKER_EXECUTION_CHANGED = "tracker.execution.changed"
TRACKER_USAGE_CHANGED = "tracker.usage.changed"


class TrackerTaskChange(BaseModel):
    """One exact member of a committed Board batch."""

    model_config = ConfigDict(extra="forbid")

    task_id: int = Field(gt=0)
    task_key: str
    changed_fields: list[str]
    url_path: str


class TrackerTasksChanged(BaseModel):
    """Metadata for one reorder, at the revision accepted by its transaction."""

    model_config = ConfigDict(extra="forbid")

    tracker_id: int = Field(gt=0)
    rev: int = Field(gt=0)
    affected_task_keys: list[str]
    affected_tasks: list[TrackerTaskChange]
    changed_fields: list[str]


def tracker_batch_task_filter(*, task_key: str, task_id: int | None = None) -> Any:
    """Match structured members with EXISTS, so one event remains one row.

    Activity additionally verifies the persisted task ID. Guard JSON shape so
    nested lookalikes, strings and malformed member values cannot establish scope.
    """
    members = func.json_each(ProjectEvent.metadata_json, "$.affected_tasks").table_valued(
        "value", "type"
    )
    member = case((members.c.type == "object", members.c.value), else_="{}")
    filters = [
        func.json_type(member, "$.task_key") == "text",
        func.json_extract(member, "$.task_key") == task_key,
        func.json_type(member, "$.task_id") == "integer",
    ]
    if task_id is not None:
        filters.append(func.json_extract(member, "$.task_id") == task_id)
    return and_(
        col(ProjectEvent.event_type) == TRACKER_TASKS_CHANGED,
        col(ProjectEvent.source_type) == "tracker",
        func.json_type(ProjectEvent.metadata_json, "$.affected_tasks") == "array",
        sa_select(1).select_from(members).where(*filters).exists(),
    )


def record_execution_change(session: Session, call: ActiveInvocation, active: bool) -> None:
    """Wake the existing tracker stream even when persisted status is unchanged."""
    StackOSEventEmitter(session).emit(
        StackOSEvent(
            project_id=call.scope.project_id,
            source=StackOSEventSource(type="tracker_ticket"),
            event_type=TRACKER_EXECUTION_CHANGED,
            title="Agent call started" if active else "Agent call finished",
            tags=["tracker", "execution"],
            metadata={
                "task_key": call.scope.task_key,
                "ticket_key": call.scope.ticket_key,
                "invocation_id": call.invocation_id,
                "active": active,
            },
        )
    )
    # This notification owns its short transaction; there is no accompanying
    # tracker status mutation to commit it for the independent SSE reader.
    session.commit()


def _status_value(status: TrackerItemStatus | str | None) -> str | None:
    if status is None:
        return None
    return status.value if isinstance(status, TrackerItemStatus) else str(status)


def _tracker_task_path(task: TrackerTask) -> str:
    return f"/projects/{task.project_id}/tasks?task={quote(task.key, safe='')}"


class TrackerEventMixin:
    """Append tracker status changes to the project timeline."""

    _s: Session

    def _record_tracker_tasks_change_event(
        self,
        *,
        project_id: int,
        tracker_id: int,
        rev: int,
        changes: list[TrackerTaskChange],
        actor: str | None = None,
    ) -> None:
        metadata = TrackerTasksChanged(
            tracker_id=tracker_id,
            rev=rev,
            affected_task_keys=[change.task_key for change in changes],
            affected_tasks=changes,
            changed_fields=sorted({field for change in changes for field in change.changed_fields}),
        )
        StackOSEventEmitter(self._s).emit(
            StackOSEvent(
                project_id=project_id,
                source=StackOSEventSource(type="tracker", id=tracker_id),
                event_type=TRACKER_TASKS_CHANGED,
                title="Task Board order updated",
                actor=actor,
                tags=["tracker"],
                metadata=metadata.model_dump(),
            )
        )

    def _record_tracker_task_change_event(
        self,
        task: TrackerTask,
        changed_fields: list[str],
        *,
        actor: str | None = None,
    ) -> None:
        StackOSEventEmitter(self._s).emit(
            StackOSEvent(
                project_id=task.project_id,
                source=StackOSEventSource(type="tracker_task", id=task.id),
                event_type=TRACKER_TASK_CHANGED,
                title="Task Board state updated",
                actor=actor,
                tags=["tracker"],
                metadata={
                    "task_id": task.id,
                    "task_key": task.key,
                    "changed_fields": changed_fields,
                    "url_path": _tracker_task_path(task),
                    "actor": actor,
                },
            )
        )

    def _record_tracker_usage_event(
        self, ticket: TrackerTicket, task: TrackerTask, before_evidence: Any
    ) -> None:
        before = summarize_receipts(invocation_receipts(before_evidence), include_history=True)
        after = summarize_receipts(
            invocation_receipts(ticket.completion_evidence_json), include_history=True
        )
        if before == after and invocation_timing_facts(before_evidence) == invocation_timing_facts(
            ticket.completion_evidence_json
        ):
            return
        StackOSEventEmitter(self._s).emit(
            StackOSEvent(
                project_id=ticket.project_id,
                source=StackOSEventSource(type="tracker_ticket", id=ticket.id),
                event_type=TRACKER_USAGE_CHANGED,
                title="Agent usage updated",
                tags=["tracker", "usage"],
                metadata={"task_key": task.key, "ticket_key": ticket.key},
            )
        )

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

        # Every canonical task status writer reaches this hook once, before
        # recording its snapshot. Same-status writes leave manual order intact.
        task.order_index = next_task_position(
            self._s, task.tracker_id, task.status, exclude_task_id=task.id
        )
        self._s.add(task)

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
