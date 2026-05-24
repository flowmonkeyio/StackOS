"""Shared communication processor integration coverage."""

from __future__ import annotations

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
