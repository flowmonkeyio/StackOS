"""Shared communication processor integration coverage."""

from __future__ import annotations

import hashlib

import pytest
from sqlmodel import Session

from stackos.communications import (
    CommunicationDecision,
    NormalizedInboundEvent,
    NormalizedResourcePatch,
    NormalizedResourceWrite,
    process_inbound_event,
)
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.resources import ResourceRepository


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


def _slack_occurrence_event(*, event_key: str, text: str) -> NormalizedInboundEvent:
    message_ts = "1770000000.000801"
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
