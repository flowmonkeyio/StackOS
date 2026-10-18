"""Shared communication processor integration coverage."""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select

from stackos.communications import (
    CommunicationDecision,
    NormalizedInboundEvent,
    NormalizedResourcePatch,
    NormalizedResourceWrite,
    process_inbound_event,
)
from stackos.communications import processor as communication_processor
from stackos.db.connection import make_engine
from stackos.db.models import AgentRequest, Plugin, Project, ProjectPlugin, Resource, ResourceRecord
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.resources import ResourceRepository


def _file_project(path: Path, *, warm_catalog: bool = True) -> tuple[Engine, int]:
    engine = make_engine(path)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        project_id = (
            ProjectRepository(session)
            .create(
                slug=path.stem.lower().replace("_", "-"),
                name="Occurrence proof",
                domain="example.test",
                locale="en-US",
            )
            .data.id
        )
        assert project_id is not None
        if warm_catalog:
            ResourceRepository(session).list_resources(project_id=project_id)
    return engine, project_id


def _occurrence_state(engine: Engine, project_id: int) -> dict[str, object]:
    with Session(engine) as session:
        request_items = AgentRequestRepository(session).list(project_id=project_id).items
        resources = ResourceRepository(session)
        rows = {
            key: resources.query_records(
                project_id=project_id,
                plugin_slug="communications",
                resource_key=key,
            ).items
            for key in (
                "communication-channel",
                "communication-event",
                "communication-message",
                "communication-interaction",
            )
        }
        return {
            "project": [
                item.model_dump(mode="json")
                for item in session.exec(select(Project).where(Project.id == project_id)).all()
            ],
            "requests": [item.model_dump(mode="json") for item in request_items],
            **{
                key: [item.model_dump(mode="json") for item in values]
                for key, values in rows.items()
            },
        }


def _durable_binding_state(engine: Engine, project_id: int) -> dict[str, list[dict[str, object]]]:
    """Capture the canonical rows without repository catalog synchronization."""
    with Session(engine) as session:
        models = (Plugin, ProjectPlugin, Resource, ResourceRecord, AgentRequest)
        state: dict[str, list[dict[str, object]]] = {}
        for model in models:
            statement = select(model)
            if model not in (Plugin, Resource):
                statement = statement.where(model.project_id == project_id)
            rows = session.exec(statement).all()
            state[model.__name__] = [
                row.model_dump(mode="json") for row in sorted(rows, key=lambda item: item.id or 0)
            ]
        return state


def test_communication_processor_dedupes_requests_and_preserves_message_status(
    session: Session,
    project_id: int,
) -> None:
    ResourceRepository(session).upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-interaction",
        external_id="mock-button:1",
        title="Mock outbound button",
        data_json={"status": "sent", "interaction_ref": "mock-button:1"},
        provenance_json={"source": "test"},
    )
    first = process_inbound_event(
        session,
        project_id=project_id,
        event=_event(event_key="evt-1", request_key="provider-message:1", click_patch=True),
        decision=CommunicationDecision(
            store=True,
            create_request=True,
            status="request_created",
            trigger_reason="mention",
        ),
    )
    exact_retry = process_inbound_event(
        session,
        project_id=project_id,
        event=_event(event_key="evt-1", request_key="provider-message:1", click_patch=True),
        decision=CommunicationDecision(
            store=True,
            create_request=True,
            status="request_created",
            trigger_reason="mention",
        ),
    )
    second = process_inbound_event(
        session,
        project_id=project_id,
        event=_event(event_key="evt-2", request_key="provider-message:1"),
        decision=CommunicationDecision(
            store=True,
            create_request=True,
            status="request_created",
            trigger_reason="mention",
        ),
    )

    events = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-event",
    )
    messages = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-message",
    )
    requests = AgentRequestRepository(session).list(project_id=project_id)

    assert first.policy_status == "request_created"
    assert exact_retry.policy_status == "request_deduped"
    assert second.policy_status == "request_deduped"
    assert second.agent_request_id == first.agent_request_id
    assert len(events.items) == 2
    assert len(messages.items) == 1
    assert len(requests.items) == 1
    assert messages.items[0].data_json["policy_status"] == "request_created"
    first_event = next(
        item for item in events.items if item.external_id == "mock-event:support:evt-1"
    )
    assert first_event.data_json["policy_status"] == "request_created"
    assert events.items[1].data_json["deduped_request_id"] == first.agent_request_id
    button = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-interaction",
    )
    outbound_button = next(item for item in button.items if item.external_id == "mock-button:1")
    assert outbound_button.data_json["status"] == "clicked"


def test_communication_processor_dedupes_request_retry_after_policy_changes(
    session: Session,
    project_id: int,
) -> None:
    first = process_inbound_event(
        session,
        project_id=project_id,
        event=_event(event_key="evt-policy-change", request_key="provider-message:policy"),
        decision=CommunicationDecision(
            store=True,
            create_request=True,
            status="request_created",
            trigger_reason="mention",
        ),
    )
    retry_after_policy_change = process_inbound_event(
        session,
        project_id=project_id,
        event=_event(event_key="evt-policy-change", request_key="provider-message:policy"),
        decision=CommunicationDecision(
            store=True,
            create_request=False,
            status="stored_ignored",
            trigger_reason="ignored_after_policy_change",
        ),
    )

    events = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-event",
    )
    messages = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-message",
    )
    requests = AgentRequestRepository(session).list(project_id=project_id)

    assert retry_after_policy_change.policy_status == "request_deduped"
    assert retry_after_policy_change.agent_request_id == first.agent_request_id
    assert len(events.items) == 1
    assert len(messages.items) == 1
    assert len(requests.items) == 1
    assert events.items[0].data_json["policy_status"] == "request_created"
    assert events.items[0].data_json["trigger_reason"] == "mention"
    assert messages.items[0].data_json["policy_status"] == "request_created"


def test_communication_processor_stores_related_resources_only_when_policy_allows(
    session: Session,
    project_id: int,
) -> None:
    blocked = process_inbound_event(
        session,
        project_id=project_id,
        event=_event_with_related_resource(),
        decision=CommunicationDecision(
            store=False,
            create_request=False,
            status="surface_blocked",
        ),
    )

    assert blocked.event_record_id is None
    assert (
        not ResourceRepository(session)
        .query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-contact",
        )
        .items
    )

    stored = process_inbound_event(
        session,
        project_id=project_id,
        event=_event_with_related_resource(),
        decision=CommunicationDecision(
            store=True,
            create_request=False,
            status="observed",
        ),
    )

    contacts = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-contact",
    )
    assert stored.event_record_id is not None
    assert len(contacts.items) == 1
    assert contacts.items[0].data_json["contact_ref"] == "mock-user:U111"


def _event_with_related_resource() -> NormalizedInboundEvent:
    event = _event(event_key="evt-related", request_key="provider-message:related")
    return NormalizedInboundEvent(
        provider_key=event.provider_key,
        profile_key=event.profile_key,
        event_key=event.event_key,
        update_type=event.update_type,
        source_kind=event.source_kind,
        request_title=event.request_title,
        body_preview=event.body_preview,
        event=event.event,
        request_key=event.request_key,
        source_message_ref=event.source_message_ref,
        surface=event.surface,
        message=event.message,
        interaction=event.interaction,
        related_resources=(
            NormalizedResourceWrite(
                resource_key="communication-contact",
                external_id="mock-contact:U111",
                title="Mock user",
                data_json={"contact_ref": "mock-user:U111", "display_name": "Mock user"},
                provenance_json={"source": "test"},
            ),
        ),
        state_patches=event.state_patches,
        request_metadata_json=event.request_metadata_json,
        response_json=event.response_json,
    )


def _event(
    *,
    event_key: str,
    request_key: str,
    click_patch: bool = False,
) -> NormalizedInboundEvent:
    return NormalizedInboundEvent(
        provider_key="mock-chat",
        profile_key="support",
        event_key=event_key,
        update_type="message",
        source_kind="mock-message",
        request_key=request_key,
        request_title="Mock message",
        body_preview="Please review this.",
        source_message_ref="mock-message:1",
        surface=NormalizedResourceWrite(
            resource_key="communication-channel",
            external_id="mock-channel:support:general",
            title="general",
            data_json={
                "provider_key": "mock-chat",
                "surface_ref": "mock-channel:general",
                "channel_ref": "mock-channel:general",
                "kind": "mock-channel",
            },
            provenance_json={"source": "test"},
        ),
        event=NormalizedResourceWrite(
            resource_key="communication-event",
            external_id=f"mock-event:support:{event_key}",
            title="Mock event",
            data_json={
                "provider_key": "mock-chat",
                "profile_key": "support",
                "event_key": event_key,
            },
            provenance_json={"source": "test"},
            preserve_existing_on_dedupe=True,
        ),
        message=NormalizedResourceWrite(
            resource_key="communication-message",
            external_id="mock-message:support:1",
            title="Mock message",
            data_json={
                "provider_key": "mock-chat",
                "profile_key": "support",
                "direction": "inbound",
                "message_ref": "mock-message:1",
                "text_preview": "Please review this.",
            },
            provenance_json={"source": "test"},
            preserve_existing_on_dedupe=True,
        ),
        state_patches=(
            [
                NormalizedResourcePatch(
                    resource_key="communication-interaction",
                    external_id="mock-button:1",
                    data_json={"status": "clicked", "last_clicked_by_ref": "mock-user:1"},
                )
            ]
            if click_patch
            else []
        ),
        request_metadata_json={"invoker_ref": "mock-user:1"},
        response_json={"profile_key": "support", "event_key": event_key},
    )


def _slack_occurrence_event(
    *, event_key: str, text: str, message_ts: str = "1770000000.000801"
) -> NormalizedInboundEvent:
    message_ref = f"slack-message:C123:{message_ts}"
    occurrence = {
        "schema_version": 1,
        "provider_key": "slack-bot",
        "source_kind": "slack-message",
        "profile_key": "support",
        "team_id": "T123",
        "channel_id": "C123",
        "surface_ref": "slack-channel:C123",
        "message_ts": message_ts,
        "message_ref": message_ref,
        "thread_ref": f"slack-thread:C123:{message_ts}",
        "invoker_ref": "slack-user:U111",
        "content_type": "text",
        "normalized_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    return NormalizedInboundEvent(
        provider_key="slack-bot",
        profile_key="support",
        event_key=event_key,
        update_type="message",
        source_kind="slack-message",
        request_key=f"slack-message-trigger:support:{message_ref}",
        request_title="Slack message in C123",
        body_preview=text[:80],
        source_message_ref=message_ref,
        surface=NormalizedResourceWrite(
            resource_key="communication-channel",
            external_id="slack-channel:support:C123",
            title="C123",
            data_json={"provider_key": "slack-bot", "surface_ref": "slack-channel:C123"},
            provenance_json={"source": "test"},
        ),
        event=NormalizedResourceWrite(
            resource_key="communication-event",
            external_id=f"slack-event:support:{event_key}",
            title="Slack message",
            data_json={"provider_key": "slack-bot", "event_key": event_key},
            provenance_json={"source": "test"},
            preserve_existing_on_dedupe=True,
        ),
        message=NormalizedResourceWrite(
            resource_key="communication-message",
            external_id=f"slack-message:support:C123:{message_ts}",
            title="Slack message",
            data_json={
                "provider_key": "slack-bot",
                "profile_key": "support",
                "team_id": "T123",
                "direction": "inbound",
                "surface_ref": "slack-channel:C123",
                "channel_ref": "slack-channel:C123",
                "thread_ref": occurrence["thread_ref"],
                "message_ref": message_ref,
                "provider_message_ts": message_ts,
                "content_type": "text",
                "text_preview": text[:80],
                "from_ref": "slack-user:U111",
                "source_occurrence_v1": occurrence,
            },
            provenance_json={"source": "slack-ingress"},
            preserve_existing_on_dedupe=True,
        ),
        request_metadata_json={
            "profile_key": "support",
            "surface_ref": "slack-channel:C123",
            "channel_ref": "slack-channel:C123",
            "thread_ref": occurrence["thread_ref"],
            "invoker_ref": "slack-user:U111",
            "source_occurrence_v1": occurrence,
            "source_occurrence_check": "compare",
        },
        response_json={"profile_key": "support", "event_key": event_key, "update_type": "message"},
    )


def test_communication_processor_reports_same_key_source_conflicts(
    session: Session,
    project_id: int,
) -> None:
    first = process_inbound_event(
        session,
        project_id=project_id,
        event=_slack_occurrence_event(event_key="occurrence-first", text="ordinary source"),
        decision=CommunicationDecision(
            store=True,
            create_request=True,
            status="request_created",
            trigger_reason="mention",
        ),
    )
    original_message = (
        ResourceRepository(session)
        .query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-message",
        )
        .items[0]
        .model_dump(mode="json")
    )

    conflicting_event = _slack_occurrence_event(
        event_key="occurrence-conflict", text="different source"
    )
    conflict = process_inbound_event(
        session,
        project_id=project_id,
        event=conflicting_event,
        decision=CommunicationDecision(
            store=True,
            create_request=True,
            status="request_created",
            trigger_reason="mention",
        ),
    )
    repeated_conflict = process_inbound_event(
        session,
        project_id=project_id,
        event=conflicting_event,
        decision=CommunicationDecision(
            store=True,
            create_request=True,
            status="request_created",
            trigger_reason="mention",
        ),
    )

    assert first.policy_status == "request_created"
    assert conflict.ok is True
    assert conflict.policy_status == "source_occurrence_conflict"
    assert conflict.agent_request_id == first.agent_request_id
    assert conflict.message_record_id is None
    assert conflict.interaction_record_id is None
    assert repeated_conflict.policy_status == "source_occurrence_conflict"
    assert conflicting_event.request_metadata_json["source_occurrence_check"] == "compare"
    assert repeated_conflict.event_record_id == conflict.event_record_id
    messages = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-message",
    )
    assert [item.model_dump(mode="json") for item in messages.items] == [original_message]
    diagnostic = ResourceRepository(session).get_record(conflict.event_record_id or 0)
    assert diagnostic.data_json["diagnostic_type"] == "source_occurrence_conflict"
    assert "different source" not in str(diagnostic.data_json)


def test_communication_processor_store_false_returns_without_lookup_or_write(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("store=false must return before comparison or resource access")

    monkeypatch.setattr(AgentRequestRepository, "find_by_key", forbidden)
    monkeypatch.setattr(ResourceRepository, "get_record", forbidden)
    monkeypatch.setattr(ResourceRepository, "upsert_record", forbidden)
    result = process_inbound_event(
        session,
        project_id=project_id,
        event=_slack_occurrence_event(event_key="occurrence-store-false", text="not stored"),
        decision=CommunicationDecision(
            store=False,
            create_request=False,
            status="stored_ignored",
        ),
    )
    assert result.ok is True
    assert result.policy_status == "stored_ignored"
    assert result.event_record_id is None
    assert result.message_record_id is None
    assert result.agent_request_id is None


@pytest.mark.parametrize(
    ("second_text", "second_status"),
    [
        ("same occurrence", "request_deduped"),
        ("different occurrence", "source_occurrence_conflict"),
    ],
)
def test_communication_processor_first_occurrence_two_session_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    second_text: str,
    second_status: str,
) -> None:
    """Two file-backed Sessions serialize the first canonical request/source read."""
    engine, project_id = _file_project(tmp_path / "occurrence-race.sqlite")
    first_event = _slack_occurrence_event(event_key="race-first", text="same occurrence")
    second_event = _slack_occurrence_event(event_key="race-second", text=second_text)
    counterfactual = threading.Barrier(2)
    original_lookup = AgentRequestRepository.find_by_key_in_transaction
    counterfactual_rows: list[AgentRequest | None] = []

    class StopBeforeEffects(Exception):
        pass

    def observe_actual_lookup(self, *, project_id: int, request_key: str):
        row = original_lookup(self, project_id=project_id, request_key=request_key)
        counterfactual_rows.append(row)
        raise StopBeforeEffects

    monkeypatch.setattr(AgentRequestRepository, "find_by_key_in_transaction", observe_actual_lookup)

    def unlocked_processor_lookup(event: NormalizedInboundEvent) -> None:
        with Session(engine) as session:
            prepared = ResourceRepository(session).prepare_inbound_resources(
                project_id=project_id,
                resource_keys=communication_processor._inbound_resource_keys(event),
            )
            counterfactual.wait(timeout=10)
            with pytest.raises(StopBeforeEffects):
                communication_processor._process_inbound_event_impl(
                    session,
                    project_id=project_id,
                    event=event,
                    decision=CommunicationDecision(
                        store=True,
                        create_request=True,
                        status="request_created",
                        trigger_reason="mention",
                    ),
                    prepared=prepared,
                )
            session.rollback()

    with ThreadPoolExecutor(max_workers=2) as pool:
        lookups = [
            pool.submit(unlocked_processor_lookup, event) for event in (first_event, second_event)
        ]
        for result in lookups:
            result.result(timeout=20)
    assert counterfactual_rows == [None, None]

    prelookup = threading.Barrier(2)
    original_guard = communication_processor._require_inbound_writer_session
    local = threading.local()
    protected_lookup_rows: list[tuple[int, str] | None] = []

    def observe_protected_lookup(self, *, project_id: int, request_key: str):
        row = original_lookup(self, project_id=project_id, request_key=request_key)
        protected_lookup_rows.append(
            (row.id, row.request_key) if row is not None and row.id is not None else None
        )
        return row

    monkeypatch.setattr(
        AgentRequestRepository, "find_by_key_in_transaction", observe_protected_lookup
    )

    def pause_before_writer(session: Session) -> None:
        original_guard(session)
        calls = getattr(local, "guard_calls", 0) + 1
        local.guard_calls = calls
        if calls == 2:
            prelookup.wait(timeout=10)

    monkeypatch.setattr(
        communication_processor,
        "_require_inbound_writer_session",
        pause_before_writer,
    )

    def deliver(event: NormalizedInboundEvent):
        with Session(engine) as session:
            return process_inbound_event(
                session,
                project_id=project_id,
                event=event,
                decision=CommunicationDecision(
                    store=True,
                    create_request=True,
                    status="request_created",
                    trigger_reason="mention",
                ),
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(deliver, first_event)
        second = pool.submit(deliver, second_event)
        results = [first.result(timeout=20), second.result(timeout=20)]

    assert sorted(result.policy_status for result in results) == sorted(
        ["request_created", second_status]
    )
    assert len(protected_lookup_rows) == 2
    assert protected_lookup_rows.count(None) == 1
    assert len({row for row in protected_lookup_rows if row is not None}) == 1
    with Session(engine) as session:
        requests = AgentRequestRepository(session).list(project_id=project_id).items
        messages = (
            ResourceRepository(session)
            .query_records(
                project_id=project_id,
                plugin_slug="communications",
                resource_key="communication-message",
            )
            .items
        )
        diagnostics = [
            row
            for row in ResourceRepository(session)
            .query_records(
                project_id=project_id,
                plugin_slug="communications",
                resource_key="communication-event",
            )
            .items
            if str(row.external_id).startswith("slack-occurrence-diagnostic:")
        ]
    assert len(requests) == 1
    assert len(messages) == 1
    assert requests[0].source_resource_record_id == messages[0].id
    assert len(diagnostics) == int(second_status == "source_occurrence_conflict")
    winner = next(result for result in results if result.policy_status == "request_created")
    assert winner.event_key is not None
    winner_text = {
        first_event.event_key: "same occurrence",
        second_event.event_key: second_text,
    }[winner.event_key]
    assert requests[0].body_preview == winner_text
    assert requests[0].source_message_ref == messages[0].data_json["message_ref"]
    assert requests[0].metadata_json is not None
    winner_occurrence = requests[0].metadata_json["source_occurrence_v1"]
    assert (
        winner_occurrence["normalized_text_sha256"]
        == hashlib.sha256(winner_text.encode("utf-8")).hexdigest()
    )
    assert messages[0].data_json["source_occurrence_v1"] == winner_occurrence
    assert messages[0].data_json["text_preview"] == winner_text
    if diagnostics:
        loser_text = second_text if winner.event_key == first_event.event_key else "same occurrence"
        incoming_digest = communication_processor._occurrence_digest(
            _slack_occurrence_event(event_key="loser", text=loser_text).request_metadata_json[
                "source_occurrence_v1"
            ]
        )
        stored_digest = communication_processor._occurrence_digest(winner_occurrence)
        assert diagnostics[0].data_json["incoming_occurrence_sha256"] == incoming_digest
        assert diagnostics[0].data_json["stored_occurrence_sha256"] == stored_digest
    engine.dispose()


@pytest.mark.parametrize("failure_point", ["source", "request", "patch", "before_commit"])
def test_communication_processor_first_occurrence_fault_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: Literal["source", "request", "patch", "before_commit"],
) -> None:
    import stackos.communications.processor as processor

    engine, project_id = _file_project(tmp_path / f"fault-{failure_point}.sqlite")
    with Session(engine) as session:
        ResourceRepository(session).upsert_record(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-interaction",
            external_id="fault-test-interaction",
            title="Original interaction",
            data_json={"status": "sent"},
            provenance_json={"source": "test"},
        )
    before = _occurrence_state(engine, project_id)
    event = _slack_occurrence_event(event_key=f"fault-{failure_point}", text="fault boundary")
    if failure_point == "patch":
        event = replace(
            event,
            state_patches=[
                NormalizedResourcePatch(
                    resource_key="communication-interaction",
                    external_id="fault-test-interaction",
                    data_json={"status": "clicked"},
                )
            ],
        )

    if failure_point == "source":
        original = ResourceRepository.upsert_record_in_transaction

        def fail_after_source(self, prepared, **kwargs):
            row = original(self, prepared, **kwargs)
            if kwargs["resource_key"] == "communication-message":
                raise RuntimeError("injected after source flush")
            return row

        monkeypatch.setattr(ResourceRepository, "upsert_record_in_transaction", fail_after_source)
    elif failure_point == "request":
        original = AgentRequestRepository.create_in_transaction

        def fail_after_request(self, **kwargs):
            original(self, **kwargs)
            raise RuntimeError("injected after request flush")

        monkeypatch.setattr(AgentRequestRepository, "create_in_transaction", fail_after_request)
    elif failure_point == "patch":
        original = processor._apply_patch

        def fail_after_patch(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("injected during selected patch")

        monkeypatch.setattr(processor, "_apply_patch", fail_after_patch)
    else:
        original_pair = ResourceRepository._prepared_pair

        def mark_writer(self, prepared, **kwargs):
            result = original_pair(self, prepared, **kwargs)
            connection = self._s.connection()
            if connection.connection.driver_connection.in_transaction:
                self._s.info["selected_writer_active"] = True
            return result

        original_commit = Session.commit

        def fail_at_commit(session: Session) -> None:
            if session.info.pop("selected_writer_active", False):
                raise RuntimeError("injected before selected commit")
            original_commit(session)

        monkeypatch.setattr(ResourceRepository, "_prepared_pair", mark_writer)
        monkeypatch.setattr(Session, "commit", fail_at_commit)

    with Session(engine) as session, pytest.raises(RuntimeError, match="injected"):
        process_inbound_event(
            session,
            project_id=project_id,
            event=event,
            decision=CommunicationDecision(
                store=True,
                create_request=True,
                status="request_created",
                trigger_reason="mention",
            ),
        )

    assert _occurrence_state(engine, project_id) == before
    engine.dispose()


def test_communication_processor_repository_public_defaults_still_commit(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path / "public-defaults.sqlite")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as writer:
        project_id = (
            ProjectRepository(writer)
            .create(
                slug="public-defaults",
                name="Public Defaults",
                domain="example.test",
                locale="en-US",
            )
            .data.id
        )
        assert project_id is not None
        record = (
            ResourceRepository(writer)
            .upsert_record(
                project_id=project_id,
                plugin_slug="communications",
                resource_key="communication-event",
                external_id="public-default-event",
                title="Committed event",
                data_json={"event": "visible"},
                provenance_json={"source": "test"},
            )
            .data
        )
        request = (
            AgentRequestRepository(writer)
            .create(
                project_id=project_id,
                request_key="public-default-request",
                title="Committed request",
                source_provider="test",
            )
            .data
        )
        anonymous_first = (
            ResourceRepository(writer)
            .upsert_record(
                project_id=project_id,
                plugin_slug="communications",
                resource_key="communication-event",
                data_json={"event": "anonymous one"},
            )
            .data
        )
        anonymous_second = (
            ResourceRepository(writer)
            .upsert_record(
                project_id=project_id,
                plugin_slug="communications",
                resource_key="communication-event",
                data_json={"event": "anonymous two"},
            )
            .data
        )
        assert anonymous_first.id != anonymous_second.id
        with Session(engine) as reader:
            assert reader.get(ResourceRecord, record.id) is not None
            assert reader.get(ResourceRecord, anonymous_first.id) is not None
            assert reader.get(ResourceRecord, anonymous_second.id) is not None
            assert reader.get(AgentRequest, request.id) is not None
    engine.dispose()


@pytest.mark.parametrize("catalog_warm", [False, True])
def test_communication_processor_catalog_preparation_runs_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalog_warm: bool,
) -> None:
    engine, project_id = _file_project(
        tmp_path / f"catalog-{catalog_warm}.sqlite", warm_catalog=catalog_warm
    )
    with Session(engine) as session:
        existing_catalog = ResourceRepository(session)._resource_rows(plugin_slug="communications")
    assert bool(existing_catalog) is catalog_warm
    if catalog_warm:
        with Session(engine) as session:
            ResourceRepository(session).list_resources(project_id=project_id)
    original = ResourceRepository._sync_catalog
    calls = 0
    sync_under_writer: list[bool] = []
    active_writer_commits: list[bool] = []

    def count_sync(self) -> None:
        nonlocal calls
        calls += 1
        sync_under_writer.append(self._s.info.get("selected_writer_active", False))
        original(self)

    original_pair = ResourceRepository._prepared_pair

    def mark_writer(self, prepared, **kwargs):
        result = original_pair(self, prepared, **kwargs)
        if self._s.connection().connection.driver_connection.in_transaction:
            self._s.info["selected_writer_active"] = True
        return result

    original_commit = Session.commit

    def observe_commit(session: Session) -> None:
        active_writer_commits.append(session.info.get("selected_writer_active", False))
        original_commit(session)

    monkeypatch.setattr(ResourceRepository, "_sync_catalog", count_sync)
    monkeypatch.setattr(ResourceRepository, "_prepared_pair", mark_writer)
    monkeypatch.setattr(Session, "commit", observe_commit)
    with Session(engine) as session:
        result = process_inbound_event(
            session,
            project_id=project_id,
            event=_slack_occurrence_event(
                event_key=f"catalog-{catalog_warm}", text="catalog boundary"
            ),
            decision=CommunicationDecision(
                store=True,
                create_request=True,
                status="request_created",
                trigger_reason="mention",
            ),
        )
    assert result.policy_status == "request_created"
    assert calls == 1
    assert sync_under_writer == [False]
    assert active_writer_commits.count(True) == 1
    engine.dispose()


@pytest.mark.parametrize(
    "caller_state", ["new", "dirty", "deleted", "flushing", "physical", "logical"]
)
def test_communication_processor_catalog_and_caller_transaction_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caller_state: str,
) -> None:
    engine, project_id = _file_project(tmp_path / f"caller-{caller_state}.sqlite")
    before = _occurrence_state(engine, project_id)
    counters = {"flush": 0, "commit": 0, "rollback": 0}
    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        if caller_state == "new":
            pending = Project(
                slug="pending-caller",
                name="Pending caller",
                domain="example.test",
                locale="en-US",
            )
            session.add(pending)
        elif caller_state == "dirty":
            project.name = "Caller-owned dirty name"
        elif caller_state == "deleted":
            session.delete(project)
        elif caller_state == "flushing":
            session._flushing = True
        elif caller_state == "physical":
            connection = session.connection()
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            session.exec(select(Project).where(Project.id == project_id)).first()

        if caller_state == "logical":
            result = process_inbound_event(
                session,
                project_id=project_id,
                event=_slack_occurrence_event(event_key="logical-autobegin", text="clean caller"),
                decision=CommunicationDecision(
                    store=True,
                    create_request=True,
                    status="request_created",
                    trigger_reason="mention",
                ),
            )
            assert result.policy_status == "request_created"
            engine.dispose()
            return

        for method_name in counters:
            original = getattr(Session, method_name)

            def observe(target: Session, *args, _name=method_name, _original=original, **kwargs):
                if target is session:
                    counters[_name] += 1
                return _original(target, *args, **kwargs)

            monkeypatch.setattr(Session, method_name, observe)

        with pytest.raises(ConflictError, match=r"caller work|caller transaction"):
            process_inbound_event(
                session,
                project_id=project_id,
                event=_slack_occurrence_event(event_key=f"guard-{caller_state}", text="guard"),
                decision=CommunicationDecision(
                    store=True,
                    create_request=True,
                    status="request_created",
                    trigger_reason="mention",
                ),
            )
        assert counters == {"flush": 0, "commit": 0, "rollback": 0}
        if caller_state == "new":
            assert pending in session.new
        elif caller_state == "dirty":
            assert project in session.dirty and project.name == "Caller-owned dirty name"
        elif caller_state == "deleted":
            assert project in session.deleted
        elif caller_state == "flushing":
            assert session._flushing is True
            session._flushing = False
        elif caller_state == "physical":
            assert session.connection().connection.driver_connection.in_transaction is True

    assert _occurrence_state(engine, project_id) == before
    engine.dispose()


def test_communication_processor_distinct_source_references_remain_independent(
    tmp_path: Path,
) -> None:
    engine, project_id = _file_project(tmp_path / "distinct-references.sqlite")
    with Session(engine) as session:
        plugin = session.exec(select(Plugin).where(Plugin.slug == "communications")).first()
        assert plugin is not None and plugin.id is not None
        session.add(ProjectPlugin(project_id=project_id, plugin_id=plugin.id, enabled=False))
        session.commit()
    with Session(engine) as session:
        results = [
            process_inbound_event(
                session,
                project_id=project_id,
                event=_slack_occurrence_event(
                    event_key=f"independent-{suffix}",
                    message_ts=timestamp,
                    text=f"distinct {suffix}",
                ),
                decision=CommunicationDecision(
                    store=True,
                    create_request=True,
                    status="request_created",
                    trigger_reason="mention",
                ),
            )
            for suffix, timestamp in (
                ("one", "1770000000.000801"),
                ("two", "1770000000.000802"),
            )
        ]
    assert [result.policy_status for result in results] == ["request_created", "request_created"]
    with Session(engine) as session:
        requests = AgentRequestRepository(session).list(project_id=project_id).items
        message_rows = session.exec(
            select(ResourceRecord).where(
                ResourceRecord.project_id == project_id,
                ResourceRecord.external_id.in_(
                    [
                        "slack-message:support:C123:1770000000.000801",
                        "slack-message:support:C123:1770000000.000802",
                    ]
                ),
            )
        ).all()
    assert len(requests) == 2
    assert len(message_rows) == 2
    engine.dispose()


@pytest.mark.parametrize("bad_reference", ["session", "project", "resource_id", "plugin_id"])
def test_prepared_resource_references_reject_foreign_or_stale_bindings(
    tmp_path: Path,
    bad_reference: str,
) -> None:
    engine, project_id = _file_project(tmp_path / f"prepared-{bad_reference}.sqlite")
    with Session(engine) as prepared_session:
        repository = ResourceRepository(prepared_session)
        prepared = repository.prepare_inbound_resources(
            project_id=project_id,
            resource_keys={"communication-message"},
        )
        key = ("communications", "communication-message")
        plugin_id, resource_id = prepared.resource_ids[key]
        if bad_reference == "session":
            with (
                Session(engine) as other_session,
                pytest.raises(ConflictError, match="another Session or project"),
            ):
                ResourceRepository(other_session).find_record_in_transaction(
                    prepared,
                    project_id=project_id,
                    plugin_slug=key[0],
                    resource_key=key[1],
                    external_id="missing",
                )
        elif bad_reference == "project":
            with pytest.raises(ConflictError, match="another Session or project"):
                repository.find_record_in_transaction(
                    prepared,
                    project_id=project_id + 1,
                    plugin_slug=key[0],
                    resource_key=key[1],
                    external_id="missing",
                )
        else:
            with Session(engine) as updater:
                if bad_reference == "plugin_id":
                    plugin = updater.get(Plugin, plugin_id)
                    assert plugin is not None
                    plugin.slug = "communications-renamed"
                    updater.add(plugin)
                else:
                    resource = updater.get(Resource, resource_id)
                    assert resource is not None
                    resource.key = "communication-message-renamed"
                    updater.add(resource)
                updater.commit()
            after_fixture_mutation = _durable_binding_state(engine, project_id)
            with pytest.raises(ConflictError, match="no longer current"):
                repository.upsert_record_in_transaction(
                    prepared,
                    project_id=project_id,
                    plugin_slug=key[0],
                    resource_key=key[1],
                    external_id="stale-binding-write",
                    data_json={"provider_key": "slack-bot", "direction": "inbound"},
                )
            assert _durable_binding_state(engine, project_id) == after_fixture_mutation
    engine.dispose()


def test_prepared_resource_schema_binding_uses_fresh_catalog_row(tmp_path: Path) -> None:
    engine, project_id = _file_project(tmp_path / "prepared-schema.sqlite")
    with Session(engine) as prepared_session:
        repository = ResourceRepository(prepared_session)
        prepared = repository.prepare_inbound_resources(
            project_id=project_id,
            resource_keys={"communication-message"},
        )
        _, resource_id = prepared.resource_ids[("communications", "communication-message")]
        stale_resource = prepared_session.get(Resource, resource_id)
        assert stale_resource is not None
        original_schema = stale_resource.schema_data
        with Session(engine) as updater:
            resource = updater.get(Resource, resource_id)
            assert resource is not None
            resource.schema_data = {
                "type": "object",
                "required": ["fresh_required"],
                "properties": {"fresh_required": {"type": "string"}},
            }
            updater.add(resource)
            updater.commit()
        assert stale_resource.schema_data == original_schema
        after_fixture_mutation = _durable_binding_state(engine, project_id)
        with pytest.raises(ValidationError, match="does not satisfy"):
            repository.upsert_record_in_transaction(
                prepared,
                project_id=project_id,
                plugin_slug="communications",
                resource_key="communication-message",
                external_id="fresh-schema-test",
                data_json={"provider_key": "slack-bot"},
            )
        assert stale_resource.schema_data["required"] == ["fresh_required"]
        assert _durable_binding_state(engine, project_id) == after_fixture_mutation
        with Session(engine) as reader:
            assert (
                reader.exec(
                    select(ResourceRecord).where(
                        ResourceRecord.project_id == project_id,
                        ResourceRecord.external_id == "fresh-schema-test",
                    )
                ).first()
                is None
            )
    engine.dispose()


def test_prepared_resource_flush_is_invisible_until_outer_commit(tmp_path: Path) -> None:
    engine, project_id = _file_project(tmp_path / "prepared-visibility.sqlite")
    with Session(engine) as writer:
        repository = ResourceRepository(writer)
        prepared = repository.prepare_inbound_resources(
            project_id=project_id,
            resource_keys={"communication-message"},
        )
        connection = writer.connection()
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        repository.upsert_record_in_transaction(
            prepared,
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-message",
            external_id="uncommitted-private-source",
            data_json={"provider_key": "slack-bot", "direction": "inbound"},
        )
        with Session(engine) as reader:
            assert (
                reader.exec(
                    select(ResourceRecord).where(
                        ResourceRecord.project_id == project_id,
                        ResourceRecord.external_id == "uncommitted-private-source",
                    )
                ).first()
                is None
            )
        writer.commit()
    with Session(engine) as reader:
        assert (
            reader.exec(
                select(ResourceRecord).where(
                    ResourceRecord.project_id == project_id,
                    ResourceRecord.external_id == "uncommitted-private-source",
                )
            ).first()
            is not None
        )
    engine.dispose()
