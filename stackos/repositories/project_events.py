"""Durable StackOS event emitter backed by the project timeline."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sqlmodel import Session

from stackos.artifacts import redact_secret_text, redact_secrets
from stackos.db.models import ProjectEvent
from stackos.events import StackOSEvent


class ProjectEventSink(Protocol):
    """Sink contract for durable StackOS events."""

    def write(self, event: StackOSEvent) -> ProjectEvent:
        """Persist one event and return the stored row."""
        ...


def _redact_text(value: str | None) -> str | None:
    return redact_secret_text(value) if value is not None else None


def _metadata_for_event(event: StackOSEvent) -> dict[str, object] | None:
    metadata = dict(event.metadata)
    if event.source.ref:
        metadata["source_ref"] = event.source.ref
    if event.actor:
        metadata["actor"] = event.actor
    if event.correlation_id:
        metadata["correlation_id"] = event.correlation_id
    if metadata and event.schema_version is not None:
        metadata["schema_version"] = event.schema_version
    return redact_secrets(metadata) if metadata else None


class ProjectEventTimelineSink:
    """Persist StackOS events into the append-only project timeline."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def write(self, event: StackOSEvent) -> ProjectEvent:
        row = ProjectEvent(
            project_id=event.project_id,
            run_id=event.run_id,
            source_type=event.source.type,
            source_id=event.source.id,
            event_type=event.event_type,
            title=_redact_text(event.title),
            summary=_redact_text(event.summary),
            tags_json=event.tags or None,
            metadata_json=_metadata_for_event(event),
        )
        if event.occurred_at is not None:
            row.occurred_at = event.occurred_at
        self._s.add(row)
        self._s.flush()
        return row


class StackOSEventEmitter:
    """Emit durable StackOS events through a configured sink."""

    def __init__(self, session: Session, sink: ProjectEventSink | None = None) -> None:
        self._sink = sink or ProjectEventTimelineSink(session)

    def emit(self, event: StackOSEvent) -> ProjectEvent:
        return self._sink.write(event)

    def emit_many(self, events: Sequence[StackOSEvent]) -> list[ProjectEvent]:
        return [self.emit(event) for event in events]


__all__ = ["ProjectEventSink", "ProjectEventTimelineSink", "StackOSEventEmitter"]
