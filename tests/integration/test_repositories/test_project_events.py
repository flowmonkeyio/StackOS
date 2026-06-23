from __future__ import annotations

from sqlmodel import Session

from stackos.context.repository import ContextRepository
from stackos.events import StackOSEvent, StackOSEventSource
from stackos.repositories.project_events import StackOSEventEmitter


def test_stackos_event_emitter_records_redacted_timeline_event(
    session: Session,
    project_id: int,
) -> None:
    row = StackOSEventEmitter(session).emit(
        StackOSEvent(
            project_id=project_id,
            run_id=None,
            event_type="demo.event.created",
            source=StackOSEventSource(type="demo_source", id=42, ref="demo:42"),
            title="Authorization: Bearer secret-title",
            summary="Operator used api_key=secret-summary",
            tags=["demo", "event", "demo"],
            metadata={
                "safe": "value",
                "access_token": "secret-token",
                "nested": {"client_secret": "secret-nested"},
            },
            actor="codex",
            correlation_id="corr-123",
        )
    )

    assert row.id is not None
    assert row.project_id == project_id
    assert row.source_type == "demo_source"
    assert row.source_id == 42
    assert row.event_type == "demo.event.created"
    assert row.title == "Authorization: Bearer [redacted]"
    assert row.summary == "Operator used api_key=[redacted]"
    assert row.tags_json == ["demo", "event"]
    assert row.metadata_json == {
        "safe": "value",
        "access_token": "[redacted]",
        "nested": {"client_secret": "[redacted]"},
        "source_ref": "demo:42",
        "actor": "codex",
        "correlation_id": "corr-123",
        "schema_version": 1,
    }

    events = (
        ContextRepository(session)
        .timeline(project_id=project_id, event_type="demo.event.created")
        .items
    )
    assert len(events) == 1
    assert events[0].id == row.id
    assert events[0].metadata_json == row.metadata_json


def test_stackos_event_emitter_records_many_in_order(
    session: Session,
    project_id: int,
) -> None:
    rows = StackOSEventEmitter(session).emit_many(
        [
            StackOSEvent(
                project_id=project_id,
                event_type="demo.event.ordered",
                source=StackOSEventSource(type="demo", id=1),
                title="First",
            ),
            StackOSEvent(
                project_id=project_id,
                event_type="demo.event.ordered",
                source=StackOSEventSource(type="demo", id=2),
                title="Second",
            ),
        ]
    )

    assert [row.source_id for row in rows] == [1, 2]
    assert [row.metadata_json for row in rows] == [None, None]
    events = (
        ContextRepository(session)
        .timeline(project_id=project_id, event_type="demo.event.ordered")
        .items
    )
    assert [event.title for event in events] == ["First", "Second"]
