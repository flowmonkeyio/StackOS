"""Slack signed HTTP ingress route tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, col, select

from stackos.artifacts import redact_secret_text
from stackos.auth_providers import AuthRepository
from stackos.db.models import AgentRequest, Plugin, Resource, ResourceRecord
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.base import ConflictError
from stackos.repositories.resources import ResourceRepository

_TOKEN = "xoxb-1234567890-safe-test-token"
_SIGNING_SECRET = "slack-signing-secret"


@pytest.mark.parametrize(
    "offset,accepted", [(-300, True), (300, True), (-301, False), (301, False)]
)
def test_slack_replay_window_stays_in_host_before_protocol_verification(
    api: TestClient, project_id: int, monkeypatch, offset: int, accepted: bool
) -> None:
    from stackos.api import slack_ingress

    _store_slack_profile(api, project_id)
    now = 1_800_000_000
    monkeypatch.setattr(slack_ingress.time, "time", lambda: now)
    raw = b'{"type":"url_verification","challenge":"synthetic-challenge"}'
    timestamp = str(now + offset)
    signature = hmac.new(
        _SIGNING_SECRET.encode(), b"v0:" + timestamp.encode() + b":" + raw, hashlib.sha256
    ).hexdigest()
    calls = []
    verify = slack_ingress.verify_signature_v0

    def checked(*args):
        calls.append(args)
        return verify(*args)

    monkeypatch.setattr(slack_ingress, "verify_signature_v0", checked)
    response = _post_without_bearer(
        api,
        f"/api/v1/ingress/slack/{project_id}/support-agent",
        raw_body=raw,
        headers={"X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": f"v0={signature}"},
    )
    assert response.status_code == (200 if accepted else 403)
    assert len(calls) == int(accepted)
    if accepted:
        assert calls[0] == (_SIGNING_SECRET, timestamp, raw, f"v0={signature}")
    with Session(api.app.state.engine) as session:
        assert AgentRequestRepository(session).list(project_id=project_id).total_estimate == 0


@pytest.mark.parametrize("timestamp", [None, "", "invalid"])
def test_slack_invalid_timestamp_never_reaches_protocol_helper(
    api: TestClient, project_id: int, monkeypatch, timestamp: str | None
) -> None:
    from stackos.api import slack_ingress

    _store_slack_profile(api, project_id)

    def forbidden(*args):
        raise AssertionError("invalid timestamp must fail at the host boundary")

    monkeypatch.setattr(slack_ingress, "verify_signature_v0", forbidden)
    headers = {"X-Slack-Signature": "v0=invalid"}
    if timestamp is not None:
        headers["X-Slack-Request-Timestamp"] = timestamp
    response = _post_without_bearer(
        api, f"/api/v1/ingress/slack/{project_id}/support-agent", raw_body=b"{}", headers=headers
    )
    assert response.status_code == 403


def _store_slack_profile(
    api: TestClient,
    project_id: int,
    *,
    profile_key: str = "support-agent",
    credential_ref: str | None = None,
    access_policy: dict | None = None,
    trigger_policy: dict | None = None,
    visibility_policy: dict | None = None,
    ingress_enabled: bool = True,
) -> str:
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        if credential_ref is None:
            credential_ref = (
                AuthRepository(session)
                .store_credential(
                    provider_key="slack-bot",
                    auth_method_key="bot-token",
                    display_name=f"Slack - {profile_key}",
                    fields={
                        "bot_token": _TOKEN,
                        "signing_secret": _SIGNING_SECRET,
                    },
                    attach_project_id=project_id,
                )
                .data.credential_ref
            )
        ResourceRepository(session).upsert_record(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-profile",
            external_id=f"communication-profile:{profile_key}",
            title="Support Agent",
            data_json={
                "key": profile_key,
                "enabled": True,
                "provider_facets": {
                    "slack-bot": {
                        "credential_ref": credential_ref,
                        "bot_user_id": "U_BOT",
                        "ingress_enabled": ingress_enabled,
                    }
                },
                "identity": {
                    "display_name": "Support Agent",
                    "purpose": "Handle approved Slack support requests.",
                    "voice": "Concise and calm.",
                },
                "agent_guidance": {
                    "default_instructions": "Triage the Slack request before replying.",
                    "boundaries": "Do not expose secrets.",
                },
                "access_policy": access_policy
                or {
                    "dm_mode": "all",
                    "channel_mode": "all",
                    "user_mode": "allowlist",
                    "allowed_user_refs": ["slack-user:U111"],
                },
                "visibility_policy": visibility_policy or {"store_non_trigger_messages": True},
                "trigger_policy": trigger_policy
                or {
                    "dm_trigger": "always",
                    "channel_trigger": "mention_or_command",
                    "commands": [
                        {
                            "command": "/support",
                            "description": "Handle a support request.",
                            "guidance": "Triage and reply with the next safe action.",
                        }
                    ],
                    "mention_patterns": ["<@U_BOT>"],
                },
                "context_policy": {"include_last_messages": 25},
                "response_policy": {"reply_in_same_thread": True},
            },
            provenance_json={"source": "test"},
        )
    return credential_ref


def _store_outbound_button(
    api: TestClient,
    project_id: int,
    *,
    profile_key: str = "support-agent",
    message_ref: str = "slack-message:C123:1770000000.000100",
    action_id: str = "approve",
    value: str = "approve_177",
    block_id: str = "decision",
    allowed_user_refs: list[str] | None = None,
) -> None:
    digest = hashlib.sha256(
        f"{message_ref}\0{block_id}\0{action_id}\0{value}".encode()
    ).hexdigest()[:24]
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        credential_ref = (
            AuthRepository(session)
            .status(
                project_id=project_id,
                provider_key="slack-bot",
            )
            .accounts[0]
            .credential_ref
        )
        data_json = {
            "provider_key": "slack-bot",
            "profile_key": profile_key,
            "credential_ref": credential_ref,
            "interaction_type": "outbound_block_button",
            "surface_ref": "slack-channel:C123",
            "message_ref": message_ref,
            "block_id": block_id,
            "action_id": action_id,
            "button_value": value,
            "status": "active",
        }
        if allowed_user_refs is not None:
            data_json["allowed_user_refs"] = allowed_user_refs
        ResourceRepository(session).upsert_record(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-interaction",
            external_id=f"slack-button:{profile_key}:{digest}",
            title="Approve",
            data_json=data_json,
            provenance_json={"source": "test"},
        )


def _signed_headers(raw_body: bytes, *, secret: str = _SIGNING_SECRET) -> dict[str, str]:
    timestamp = str(int(time.time()))
    basestring = b"v0:" + timestamp.encode("utf-8") + b":" + raw_body
    signature = hmac.new(secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": f"v0={signature}",
    }


def _post_without_bearer(
    api: TestClient,
    url: str,
    *,
    raw_body: bytes,
    headers: dict[str, str],
) -> object:
    original_auth = api.headers.pop("Authorization", None)
    try:
        return api.post(url, content=raw_body, headers=headers)
    finally:
        if original_auth is not None:
            api.headers["Authorization"] = original_auth


def test_slack_ingress_records_app_mention_and_creates_agent_request_without_bearer(
    api: TestClient,
    project_id: int,
) -> None:
    _store_slack_profile(api, project_id)
    payload = {
        "type": "event_callback",
        "team_id": "T123",
        "event_id": "Ev123",
        "event": {
            "type": "app_mention",
            "user": "U111",
            "channel": "C123",
            "channel_type": "channel",
            "text": "<@U_BOT> summarize this customer issue",
            "ts": "1770000000.000200",
            "event_ts": "1770000000.000200",
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")
    response = _post_without_bearer(
        api,
        f"/api/v1/ingress/slack/{project_id}/support-agent",
        raw_body=raw_body,
        headers={**_signed_headers(raw_body), "Content-Type": "application/json"},
    )

    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["ok"] is True
    assert body["policy_status"] == "request_created"
    assert body["message_record_id"] is not None
    assert body["agent_request_id"] is not None

    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        messages = ResourceRepository(session).query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-message",
        )
        requests = AgentRequestRepository(session).list(project_id=project_id)

    assert len(messages.items) == 1
    assert messages.items[0].data_json["direction"] == "inbound"
    assert messages.items[0].data_json["attention_status"] == "unread"
    assert requests.items[0].source_provider == "slack-bot"
    assert requests.items[0].metadata_json["profile_key"] == "support-agent"
    occurrence = requests.items[0].metadata_json["source_occurrence_v1"]
    assert occurrence["team_id"] == "T123"
    assert (
        occurrence["normalized_text_sha256"]
        == hashlib.sha256(payload["event"]["text"].encode("utf-8")).hexdigest()
    )
    assert messages.items[0].data_json["source_occurrence_v1"] == occurrence
    assert (
        requests.items[0].metadata_json["agent_guidance"]["boundaries"] == "Do not expose secrets."
    )


def test_slack_ingress_self_message_echo_does_not_overwrite_outbound_history(
    api: TestClient,
    project_id: int,
) -> None:
    _store_slack_profile(api, project_id)
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        credential_ref = (
            AuthRepository(session)
            .status(
                project_id=project_id,
                provider_key="slack-bot",
            )
            .accounts[0]
            .credential_ref
        )
        ResourceRepository(session).upsert_record(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-message",
            external_id="slack-message:support-agent:C123:1770000000.000400",
            title="Slack outbound message",
            data_json={
                "provider_key": "slack-bot",
                "profile_key": "support-agent",
                "credential_ref": credential_ref,
                "direction": "outbound",
                "surface_ref": "slack-channel:C123",
                "channel_ref": "slack-channel:C123",
                "thread_ref": "slack-thread:C123:1770000000.000400",
                "message_ref": "slack-message:C123:1770000000.000400",
                "provider_message_ts": "1770000000.000400",
                "content_type": "text",
                "text_preview": "StackOS sent this.",
                "transport_status": "accepted",
                "attention_status": "sent",
            },
            provenance_json={"source": "slack-bot-action"},
        )
    payload = {
        "type": "event_callback",
        "team_id": "T123",
        "event_id": "EvSelfEcho",
        "event": {
            "type": "message",
            "user": "U_BOT",
            "channel": "C123",
            "channel_type": "channel",
            "text": "StackOS sent this.",
            "ts": "1770000000.000400",
            "event_ts": "1770000000.000400",
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")
    response = _post_without_bearer(
        api,
        f"/api/v1/ingress/slack/{project_id}/support-agent",
        raw_body=raw_body,
        headers={**_signed_headers(raw_body), "Content-Type": "application/json"},
    )

    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["policy_status"] == "self_message_ignored"
    assert body["event_record_id"] is not None
    assert body["message_record_id"] is None
    assert body["agent_request_id"] is None

    with Session(engine) as session:
        messages = ResourceRepository(session).query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-message",
        )
        requests = AgentRequestRepository(session).list(project_id=project_id)

    assert messages.total_estimate == 1
    assert messages.items[0].data_json["direction"] == "outbound"
    assert messages.items[0].data_json["attention_status"] == "sent"
    assert messages.items[0].provenance_json == {"source": "slack-bot-action"}
    assert requests.total_estimate == 0


def test_slack_ingress_stores_disallowed_actor_mention_without_request(
    api: TestClient,
    project_id: int,
) -> None:
    _store_slack_profile(api, project_id)
    payload = {
        "type": "event_callback",
        "team_id": "T123",
        "event_id": "EvBlockedActor",
        "event": {
            "type": "app_mention",
            "user": "U222",
            "channel": "C123",
            "channel_type": "channel",
            "text": "<@U_BOT> do this",
            "ts": "1770000000.000202",
            "event_ts": "1770000000.000202",
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")
    response = _post_without_bearer(
        api,
        f"/api/v1/ingress/slack/{project_id}/support-agent",
        raw_body=raw_body,
        headers={**_signed_headers(raw_body), "Content-Type": "application/json"},
    )

    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["policy_status"] == "invoker_blocked"
    assert body["message_record_id"] is not None
    assert body["agent_request_id"] is None

    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        messages = ResourceRepository(session).query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-message",
        )
        requests = AgentRequestRepository(session).list(project_id=project_id)

    assert messages.total_estimate == 1
    assert messages.items[0].data_json["from_ref"] == "slack-user:U222"
    assert messages.items[0].data_json["policy_status"] == "invoker_blocked"
    assert requests.total_estimate == 0


def test_slack_ingress_allows_approved_actor_in_any_visible_channel(
    api: TestClient,
    project_id: int,
) -> None:
    _store_slack_profile(
        api,
        project_id,
        access_policy={
            "dm_mode": "allowlist",
            "channel_mode": "allowlist",
            "allowed_channel_refs": ["slack-channel:C999"],
            "user_mode": "allowlist",
            "allowed_user_refs": ["slack-user:U111"],
        },
    )
    payload = {
        "type": "event_callback",
        "team_id": "T123",
        "event_id": "EvApprovedAnyChannel",
        "event": {
            "type": "app_mention",
            "user": "U111",
            "channel": "C123",
            "channel_type": "channel",
            "text": "<@U_BOT> summarize this",
            "ts": "1770000000.000203",
            "event_ts": "1770000000.000203",
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")
    response = _post_without_bearer(
        api,
        f"/api/v1/ingress/slack/{project_id}/support-agent",
        raw_body=raw_body,
        headers={**_signed_headers(raw_body), "Content-Type": "application/json"},
    )

    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["policy_status"] == "request_created"
    assert body["agent_request_id"] is not None


def test_slack_ingress_dedupes_message_and_app_mention_for_same_source_message(
    api: TestClient,
    project_id: int,
) -> None:
    _store_slack_profile(api, project_id)
    event = {
        "user": "U111",
        "channel": "C123",
        "channel_type": "channel",
        "text": "<@U_BOT> summarize this customer issue",
        "ts": "1770000000.000201",
        "event_ts": "1770000000.000201",
    }
    first_payload = {
        "type": "event_callback",
        "team_id": "T123",
        "event_id": "EvMessage",
        "event_time": 1_770_000_001,
        "event": {"type": "message", **event},
    }
    second_payload = {
        "type": "event_callback",
        "team_id": "T123",
        "event_id": "EvMention",
        "event_time": 1_770_000_099,
        "event": {"type": "app_mention", **event},
    }

    first_raw_body = json.dumps(first_payload).encode("utf-8")
    first = _post_without_bearer(
        api,
        f"/api/v1/ingress/slack/{project_id}/support-agent",
        raw_body=first_raw_body,
        headers={**_signed_headers(first_raw_body), "Content-Type": "application/json"},
    )
    second_raw_body = json.dumps(second_payload).encode("utf-8")
    second = _post_without_bearer(
        api,
        f"/api/v1/ingress/slack/{project_id}/support-agent",
        raw_body=second_raw_body,
        headers={
            **_signed_headers(second_raw_body),
            "Content-Type": "application/json",
            "X-Slack-Retry-Num": "1",
            "X-Slack-Retry-Reason": "http_timeout",
        },
    )

    assert first.status_code == 200, first.text  # type: ignore[attr-defined]
    assert second.status_code == 200, second.text  # type: ignore[attr-defined]
    first_body = first.json()  # type: ignore[attr-defined]
    second_body = second.json()  # type: ignore[attr-defined]
    assert first_body["policy_status"] == "request_created"
    assert second_body["policy_status"] == "request_deduped"
    assert second_body["agent_request_id"] == first_body["agent_request_id"]

    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
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

    assert len(events.items) == 2
    assert len(messages.items) == 1
    assert len(requests.items) == 1
    assert requests.items[0].request_key == (
        "slack-message-trigger:support-agent:slack-message:C123:1770000000.000201"
    )
    assert messages.items[0].data_json["policy_status"] == "request_created"
    assert (
        messages.items[0].data_json["source_occurrence_v1"]
        == requests.items[0].metadata_json["source_occurrence_v1"]
    )


def _send_occurrence_message(
    api: TestClient,
    project_id: int,
    *,
    event_id: str,
    message_ts: str,
    text: Any,
    team_id: Any = "T123",
    event_type: str = "message",
    subtype: str | None = None,
) -> object:
    event = {
        "type": event_type,
        "user": "U111",
        "channel": "C123",
        "channel_type": "channel",
        "text": text,
        "ts": message_ts,
        "event_ts": message_ts,
    }
    if subtype is not None:
        event["subtype"] = subtype
    payload = {
        "type": "event_callback",
        "event_id": event_id,
        "event_time": int(time.time()) + 10,
        "event": event,
    }
    if team_id is not None:
        payload["team_id"] = team_id
    raw_body = json.dumps(payload).encode("utf-8")
    return _post_without_bearer(
        api,
        f"/api/v1/ingress/slack/{project_id}/support-agent",
        raw_body=raw_body,
        headers={**_signed_headers(raw_body), "Content-Type": "application/json"},
    )


def _occurrence_snapshot(api: TestClient, project_id: int) -> dict[str, list[dict]]:
    with Session(api.app.state.engine) as session:  # type: ignore[attr-defined]
        requests = AgentRequestRepository(session).list(project_id=project_id).items
        resources = ResourceRepository(session)
        records = {
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
        "requests": [item.model_dump(mode="json") for item in requests],
        **{
            key: [item.model_dump(mode="json") for item in values]
            for key, values in records.items()
        },
    }


def _seed_occurrence_request(
    api: TestClient,
    project_id: int,
    *,
    message_ts: str,
    text: str,
    team_id: str | None = "T123",
) -> int:
    _store_slack_profile(api, project_id)
    response = _send_occurrence_message(
        api,
        project_id,
        event_id="EvOccurrenceOriginal",
        message_ts=message_ts,
        text=text,
        team_id=team_id,
    )
    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    assert response.json()["policy_status"] == "request_created"  # type: ignore[attr-defined]
    with Session(api.app.state.engine) as session:  # type: ignore[attr-defined]
        request = AgentRequestRepository(session).list(project_id=project_id).items[0]
    return request.id


@pytest.mark.parametrize(
    "case_id",
    [
        "trailing-suffix",
        "redaction-collision",
        "missing-source-link",
        "wrong-record-project",
        "wrong-plugin",
        "wrong-resource-key",
        "wrong-external-id",
        "request-link-disagreement",
        "malformed-native-type",
        "one-sided-digest",
        "legacy-provider-invalid-incoming",
        "legacy-content-type-invalid-incoming",
        "legacy-message-ref-invalid-incoming",
    ],
)
def test_slack_ingress_reports_same_key_source_conflicts_without_request_mutation(
    api: TestClient,
    project_id: int,
    case_id: str,
) -> None:
    message_ts = "1770000000.000701"
    original_text = (
        "<@U_BOT> token=alpha" if case_id == "redaction-collision" else "<@U_BOT> " + "a" * 5000
    )
    if case_id == "missing-source-link":
        _store_slack_profile(api, project_id)
        request_key = f"slack-message-trigger:support-agent:slack-message:C123:{message_ts}"
        with Session(api.app.state.engine) as session:  # type: ignore[attr-defined]
            request = (
                AgentRequestRepository(session)
                .create(
                    project_id=project_id,
                    request_key=request_key,
                    title="Manual same-key request",
                    body_preview="Operator-authored request",
                )
                .data
            )
        request_id = request.id
    else:
        request_id = _seed_occurrence_request(
            api, project_id, message_ts=message_ts, text=original_text
        )

    if case_id not in {"trailing-suffix", "redaction-collision", "missing-source-link"}:
        if case_id == "wrong-record-project":
            foreign = api.post(
                "/api/v1/projects",
                json={
                    "slug": "occurrence-foreign",
                    "name": "Occurrence Foreign",
                    "domain": "example.test",
                },
            )
            assert foreign.status_code == 201, foreign.text
            foreign_project_id = int(foreign.json()["data"]["id"])
        with Session(api.app.state.engine) as session:  # type: ignore[attr-defined]
            request = session.get(AgentRequest, request_id)
            assert request is not None
            record = session.get(ResourceRecord, request.source_resource_record_id)
            assert record is not None
            if case_id == "wrong-record-project":
                record.project_id = foreign_project_id
            elif case_id == "wrong-plugin":
                resource_plugin = session.exec(
                    select(Resource, Plugin)
                    .join(Plugin, col(Resource.plugin_id) == col(Plugin.id))
                    .where(Plugin.slug != "communications")
                ).first()
                assert resource_plugin is not None
                resource, _plugin = resource_plugin
                foreign_record = ResourceRecord(
                    project_id=project_id,
                    resource_id=resource.id,
                    external_id="occurrence-wrong-plugin",
                    title="Wrong plugin record",
                    data_json={},
                    provenance_json={"source": "test"},
                )
                session.add(foreign_record)
                session.flush()
                request.source_resource_record_id = foreign_record.id
            elif case_id == "wrong-resource-key":
                channel = (
                    ResourceRepository(session)
                    .query_records(
                        project_id=project_id,
                        plugin_slug="communications",
                        resource_key="communication-channel",
                    )
                    .items[0]
                )
                request.source_resource_record_id = channel.id
            elif case_id == "wrong-external-id":
                record.external_id = "slack-message:other:C123:1770000000.000701"
            elif case_id == "request-link-disagreement":
                request.source_message_ref = "slack-message:C123:other"
            elif case_id == "malformed-native-type":
                request_metadata = dict(request.metadata_json or {})
                occurrence = dict(request_metadata["source_occurrence_v1"])
                occurrence["schema_version"] = True
                request_metadata["source_occurrence_v1"] = occurrence
                request.metadata_json = request_metadata
                flag_modified(request, "metadata_json")
                message_data = dict(record.data_json)
                message_data.pop("thread_ref")
                record.data_json = message_data
                flag_modified(record, "data_json")
            elif case_id == "one-sided-digest":
                message_data = dict(record.data_json)
                occurrence = dict(message_data["source_occurrence_v1"])
                occurrence.pop("normalized_text_sha256")
                message_data["source_occurrence_v1"] = occurrence
                record.data_json = message_data
                flag_modified(record, "data_json")
            elif case_id.startswith("legacy-"):
                request_metadata = dict(request.metadata_json or {})
                request_metadata.pop("source_occurrence_v1")
                request.metadata_json = request_metadata
                flag_modified(request, "metadata_json")
                message_data = dict(record.data_json)
                message_data.pop("source_occurrence_v1")
                if case_id == "legacy-provider-invalid-incoming":
                    message_data["provider_key"] = "other-provider"
                elif case_id == "legacy-content-type-invalid-incoming":
                    message_data["content_type"] = "other-kind"
                else:
                    message_data["message_ref"] = "slack-message:C123:other"
                record.data_json = message_data
                flag_modified(record, "data_json")
            session.commit()

    baseline = _occurrence_snapshot(api, project_id)
    if case_id.startswith("legacy-"):
        stored_request = next(item for item in baseline["requests"] if item["id"] == request_id)
        stored_message = next(
            item
            for item in baseline["communication-message"]
            if item["id"] == stored_request["source_resource_record_id"]
        )
        assert "source_occurrence_v1" not in stored_request["metadata_json"]
        assert "source_occurrence_v1" not in stored_message["data_json"]
        if case_id == "legacy-provider-invalid-incoming":
            assert stored_message["data_json"]["provider_key"] == "other-provider"
        elif case_id == "legacy-content-type-invalid-incoming":
            assert stored_message["data_json"]["content_type"] == "other-kind"
        else:
            assert stored_message["data_json"]["message_ref"] == "slack-message:C123:other"
    incoming_text = original_text
    if case_id == "trailing-suffix":
        incoming_text = original_text + "-distinct-trailing-suffix"
        assert redact_secret_text(original_text)[:4000] == redact_secret_text(incoming_text)[:4000]
    elif case_id == "redaction-collision":
        incoming_text = "<@U_BOT> token=beta"
        assert redact_secret_text(original_text) == redact_secret_text(incoming_text)
    elif case_id.startswith("legacy-"):
        incoming_text = {"invalid": "non-string source text"}
    privacy_witness = (
        incoming_text
        if isinstance(incoming_text, str)
        else json.dumps(incoming_text, sort_keys=True, separators=(",", ":"))
    )
    response = _send_occurrence_message(
        api,
        project_id,
        event_id=f"EvConflict-{case_id}",
        message_ts=message_ts,
        text=incoming_text,
    )

    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["ok"] is True
    assert body["policy_status"] == "source_occurrence_conflict"
    assert body["agent_request_id"] == request_id
    assert body["message_record_id"] is None
    assert body["interaction_record_id"] is None
    assert set(body) == {
        "ok",
        "profile_key",
        "event_key",
        "update_type",
        "policy_status",
        "event_record_id",
        "message_record_id",
        "interaction_record_id",
        "agent_request_id",
    }
    assert privacy_witness not in response.text  # type: ignore[attr-defined]
    current = _occurrence_snapshot(api, project_id)
    assert current["requests"] == baseline["requests"]
    assert current["communication-channel"] == baseline["communication-channel"]
    assert current["communication-message"] == baseline["communication-message"]
    assert current["communication-interaction"] == baseline["communication-interaction"]
    prior_events = [
        item
        for item in current["communication-event"]
        if not str(item["external_id"]).startswith("slack-occurrence-diagnostic:")
    ]
    assert prior_events == baseline["communication-event"]
    diagnostics = [
        item
        for item in current["communication-event"]
        if str(item["external_id"]).startswith("slack-occurrence-diagnostic:")
    ]
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic["id"] == body["event_record_id"]
    assert diagnostic["title"] == "Slack source occurrence diagnostic"
    assert diagnostic["data_json"]["diagnostic_type"] == "source_occurrence_conflict"
    assert set(diagnostic["data_json"]) == {
        "schema_version",
        "diagnostic_type",
        "provider_key",
        "profile_key",
        "agent_request_id",
        "event_key_sha256",
        "request_key_sha256",
        "incoming_occurrence_sha256",
        "stored_occurrence_sha256",
        "mismatch_fields",
    }
    assert set(diagnostic["data_json"]["mismatch_fields"]).issubset(
        {
            "source_provider",
            "source_kind",
            "source_message_ref",
            "source_resource_key",
            "source_resource_record_id",
            "resource_record.project_id",
            "resource.plugin_slug",
            "resource.key",
            "resource_record.external_id",
            *{
                f"source_occurrence_v1.{field}"
                for field in (
                    "schema_version",
                    "provider_key",
                    "source_kind",
                    "profile_key",
                    "team_id",
                    "channel_id",
                    "surface_ref",
                    "message_ts",
                    "message_ref",
                    "thread_ref",
                    "invoker_ref",
                    "content_type",
                    "normalized_text_sha256",
                )
            },
        }
    )
    assert privacy_witness not in json.dumps(diagnostic["data_json"], sort_keys=True)


@pytest.mark.parametrize("case_id", ["short-preview", "capped-preview"])
def test_slack_ingress_holds_legacy_request_without_digest(
    api: TestClient,
    project_id: int,
    case_id: str,
) -> None:
    message_ts = "1770000000.000702"
    text = "<@U_BOT> legacy" if case_id == "short-preview" else "<@U_BOT> " + "z" * 5000
    request_id = _seed_occurrence_request(api, project_id, message_ts=message_ts, text=text)
    with Session(api.app.state.engine) as session:  # type: ignore[attr-defined]
        request = session.get(AgentRequest, request_id)
        assert request is not None
        metadata = dict(request.metadata_json or {})
        metadata.pop("source_occurrence_v1")
        request.metadata_json = metadata
        flag_modified(request, "metadata_json")
        message = session.get(ResourceRecord, request.source_resource_record_id)
        assert message is not None
        data = dict(message.data_json)
        data.pop("source_occurrence_v1")
        message.data_json = data
        flag_modified(message, "data_json")
        session.commit()
    baseline = _occurrence_snapshot(api, project_id)
    response = _send_occurrence_message(
        api,
        project_id,
        event_id=f"EvLegacy-{case_id}",
        message_ts=message_ts,
        text=text,
    )
    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["policy_status"] == "source_occurrence_unverifiable"
    assert body["agent_request_id"] == request_id
    current = _occurrence_snapshot(api, project_id)
    assert current["requests"] == baseline["requests"]
    assert current["communication-message"] == baseline["communication-message"]
    assert current["communication-channel"] == baseline["communication-channel"]
    assert current["communication-interaction"] == baseline["communication-interaction"]
    old_events = [
        item
        for item in current["communication-event"]
        if not str(item["external_id"]).startswith("slack-occurrence-diagnostic:")
    ]
    assert old_events == baseline["communication-event"]
    diagnostic = next(
        item
        for item in current["communication-event"]
        if str(item["external_id"]).startswith("slack-occurrence-diagnostic:")
    )
    assert diagnostic["data_json"]["diagnostic_type"] == "source_occurrence_unverifiable"


def test_slack_ingress_preserves_message_subtype_update_without_occurrence_conflict(
    api: TestClient,
    project_id: int,
) -> None:
    message_ts = "1770000000.000703"
    request_id = _seed_occurrence_request(
        api, project_id, message_ts=message_ts, text="<@U_BOT> original"
    )
    before = _occurrence_snapshot(api, project_id)
    response = _send_occurrence_message(
        api,
        project_id,
        event_id="EvMessageChanged",
        message_ts=message_ts,
        text="edited event that remains outside this comparator",
        subtype="message_changed",
    )
    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["policy_status"] == "request_deduped"
    assert body["agent_request_id"] == request_id
    after = _occurrence_snapshot(api, project_id)
    assert after["requests"] == before["requests"]
    assert after["communication-message"] == before["communication-message"]
    assert after["communication-interaction"] == before["communication-interaction"]
    assert not any(
        str(item["external_id"]).startswith("slack-occurrence-diagnostic:")
        for item in after["communication-event"]
    )

    # Outside the selected comparator, baseline display and team normalization
    # remain unchanged even for non-string text and a missing team binding.
    display_text = {"message": "<@U_BOT> keep baseline"}
    response = _send_occurrence_message(
        api,
        project_id,
        event_id="EvSubtypeBaselineNormalization",
        message_ts="1770000000.000707",
        text=display_text,
        team_id=None,
        subtype="message_changed",
    )
    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    assert response.json()["policy_status"] == "request_created"  # type: ignore[attr-defined]
    after_display = _occurrence_snapshot(api, project_id)
    display_message = next(
        item
        for item in after_display["communication-message"]
        if item["external_id"].endswith("1770000000.000707")
    )
    assert display_message["data_json"]["team_id"] == ""
    assert display_message["data_json"]["text_preview"] == redact_secret_text(str(display_text))
    assert "source_occurrence_v1" not in display_message["data_json"]
    display_request = next(
        item
        for item in after_display["requests"]
        if item["source_message_ref"] == display_message["data_json"]["message_ref"]
    )
    assert "source_occurrence_v1" not in display_request["metadata_json"]


def test_slack_ingress_distinct_message_ref_creates_independent_requests(
    api: TestClient,
    project_id: int,
) -> None:
    _store_slack_profile(api, project_id)
    first = _send_occurrence_message(
        api,
        project_id,
        event_id="EvDistinctOne",
        message_ts="1770000000.000704",
        text="<@U_BOT> same body",
    )
    second = _send_occurrence_message(
        api,
        project_id,
        event_id="EvDistinctTwo",
        message_ts="1770000000.000705",
        text="<@U_BOT> same body",
        team_id=None,
    )
    assert first.status_code == 200, first.text  # type: ignore[attr-defined]
    assert second.status_code == 200, second.text  # type: ignore[attr-defined]
    assert first.json()["policy_status"] == "request_created"  # type: ignore[attr-defined]
    assert second.json()["policy_status"] == "request_created"  # type: ignore[attr-defined]
    snapshot = _occurrence_snapshot(api, project_id)
    assert len(snapshot["requests"]) == 2
    assert len(snapshot["communication-message"]) == 2
    request_by_ref = {item["source_message_ref"]: item for item in snapshot["requests"]}
    assert set(request_by_ref) == {
        "slack-message:C123:1770000000.000704",
        "slack-message:C123:1770000000.000705",
    }
    for message in snapshot["communication-message"]:
        assert message["data_json"]["message_ref"] in request_by_ref
        linked = request_by_ref[message["data_json"]["message_ref"]]
        assert linked["source_resource_record_id"] == message["id"]
    second_message = next(
        item
        for item in snapshot["communication-message"]
        if item["data_json"]["message_ref"].endswith("1770000000.000705")
    )
    assert second_message["data_json"]["source_occurrence_v1"]["team_id"] is None


@pytest.mark.parametrize(
    "supplied_team,expected_status",
    [
        (False, "source_occurrence_unverifiable"),
        (0, "source_occurrence_unverifiable"),
        ("", "source_occurrence_conflict"),
    ],
)
def test_slack_ingress_distinguishes_malformed_team_from_nullable_team_replay(
    api: TestClient,
    project_id: int,
    supplied_team: Any,
    expected_status: str,
) -> None:
    message_ts = "1770000000.000709"
    request_id = _seed_occurrence_request(
        api,
        project_id,
        message_ts=message_ts,
        text="<@U_BOT> original",
        team_id=None,
    )
    before = _occurrence_snapshot(api, project_id)
    stored_request = next(item for item in before["requests"] if item["id"] == request_id)
    stored_message = next(
        item
        for item in before["communication-message"]
        if item["id"] == stored_request["source_resource_record_id"]
    )
    assert stored_message["data_json"]["team_id"] is None
    assert stored_message["data_json"]["source_occurrence_v1"]["team_id"] is None

    response = _send_occurrence_message(
        api,
        project_id,
        event_id=f"EvMalformedTeam-{type(supplied_team).__name__}-{supplied_team!r}",
        message_ts=message_ts,
        text="<@U_BOT> original",
        team_id=supplied_team,
    )
    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["policy_status"] == expected_status
    assert body["agent_request_id"] == request_id
    after = _occurrence_snapshot(api, project_id)
    assert after["requests"] == before["requests"]
    assert after["communication-channel"] == before["communication-channel"]
    assert after["communication-message"] == before["communication-message"]
    assert after["communication-interaction"] == before["communication-interaction"]


def test_slack_ingress_diagnostic_write_failure_does_not_return_success_ack(
    api: TestClient,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message_ts = "1770000000.000706"
    _seed_occurrence_request(api, project_id, message_ts=message_ts, text="<@U_BOT> original")
    baseline = _occurrence_snapshot(api, project_id)
    original_upsert = ResourceRepository.upsert_record

    def fail_diagnostic(self, **kwargs):
        if kwargs.get("title") == "Slack source occurrence diagnostic":
            raise ConflictError("synthetic diagnostic persistence failure")
        return original_upsert(self, **kwargs)

    monkeypatch.setattr(ResourceRepository, "upsert_record", fail_diagnostic)
    response = _send_occurrence_message(
        api,
        project_id,
        event_id="EvDiagnosticFailure",
        message_ts=message_ts,
        text="<@U_BOT> contradictory source",
    )
    assert response.status_code != 200  # type: ignore[attr-defined]
    after = _occurrence_snapshot(api, project_id)
    assert after == baseline


def test_slack_ingress_rejects_boolean_diagnostic_replay_without_overwrite_or_ack(
    api: TestClient,
    project_id: int,
) -> None:
    message_ts = "1770000000.000708"
    _seed_occurrence_request(api, project_id, message_ts=message_ts, text="<@U_BOT> original")
    first = _send_occurrence_message(
        api,
        project_id,
        event_id="EvDiagnosticBoolCollision",
        message_ts=message_ts,
        text="<@U_BOT> contradictory",
    )
    assert first.status_code == 200, first.text  # type: ignore[attr-defined]
    diagnostic_id = first.json()["event_record_id"]  # type: ignore[attr-defined]
    assert diagnostic_id is not None
    with Session(api.app.state.engine) as session:  # type: ignore[attr-defined]
        diagnostic = session.get(ResourceRecord, diagnostic_id)
        assert diagnostic is not None
        data = dict(diagnostic.data_json)
        data["schema_version"] = True
        diagnostic.data_json = data
        flag_modified(diagnostic, "data_json")
        session.commit()
    before_replay = _occurrence_snapshot(api, project_id)
    saved = next(
        item for item in before_replay["communication-event"] if item["id"] == diagnostic_id
    )
    assert saved["data_json"]["schema_version"] is True

    replay = _send_occurrence_message(
        api,
        project_id,
        event_id="EvDiagnosticBoolCollision",
        message_ts=message_ts,
        text="<@U_BOT> contradictory",
    )
    assert replay.status_code != 200  # type: ignore[attr-defined]
    assert _occurrence_snapshot(api, project_id) == before_replay


def test_slack_ingress_records_known_button_click_and_does_not_store_transient_secrets(
    api: TestClient,
    project_id: int,
) -> None:
    _store_slack_profile(api, project_id)
    _store_outbound_button(api, project_id)
    payload = {
        "type": "block_actions",
        "team": {"id": "T123"},
        "user": {"id": "U111"},
        "channel": {"id": "C123", "type": "channel"},
        "container": {
            "type": "message",
            "channel_id": "C123",
            "message_ts": "1770000000.000100",
        },
        "message": {"ts": "1770000000.000100", "text": "Ready?"},
        "actions": [
            {
                "type": "button",
                "block_id": "decision",
                "action_id": "approve",
                "value": "approve_177",
            }
        ],
        "response_url": "https://hooks.slack.com/actions/secret-path",
        "trigger_id": "111.222.secret-trigger",
    }
    raw_body = urlencode({"payload": json.dumps(payload)}).encode("utf-8")
    response = _post_without_bearer(
        api,
        f"/api/v1/ingress/slack/{project_id}/support-agent",
        raw_body=raw_body,
        headers={
            **_signed_headers(raw_body),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["policy_status"] == "request_created"
    assert body["interaction_record_id"] is not None
    assert body["agent_request_id"] is not None

    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        interactions = ResourceRepository(session).query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-interaction",
        )
        records = [item.model_dump(mode="json") for item in interactions.items]

    outbound = [
        item
        for item in interactions.items
        if item.data_json.get("interaction_type") == "outbound_block_button"
    ]
    incoming = [
        item
        for item in interactions.items
        if item.data_json.get("interaction_type") == "block_actions"
    ]
    rendered = json.dumps(records)
    assert len(outbound) == 1
    assert outbound[0].data_json["status"] == "clicked"
    assert outbound[0].data_json["last_clicked_by_ref"] == "slack-user:U111"
    assert len(incoming) == 1
    assert incoming[0].data_json["button_value"] == "approve_177"
    assert "hooks.slack.com/actions" not in rendered
    assert "secret-trigger" not in rendered


def test_slack_ingress_blocks_button_click_from_unissued_user(
    api: TestClient,
    project_id: int,
) -> None:
    _store_slack_profile(
        api,
        project_id,
        access_policy={
            "dm_mode": "all",
            "channel_mode": "all",
            "user_mode": "all",
        },
    )
    _store_outbound_button(api, project_id, allowed_user_refs=["slack-user:U111"])
    payload = {
        "type": "block_actions",
        "team": {"id": "T123"},
        "user": {"id": "U222"},
        "channel": {"id": "C123", "type": "channel"},
        "container": {
            "type": "message",
            "channel_id": "C123",
            "message_ts": "1770000000.000100",
        },
        "message": {"ts": "1770000000.000100", "text": "Ready?"},
        "actions": [
            {
                "type": "button",
                "block_id": "decision",
                "action_id": "approve",
                "value": "approve_177",
            }
        ],
    }
    raw_body = urlencode({"payload": json.dumps(payload)}).encode("utf-8")
    response = _post_without_bearer(
        api,
        f"/api/v1/ingress/slack/{project_id}/support-agent",
        raw_body=raw_body,
        headers={
            **_signed_headers(raw_body),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["policy_status"] == "interaction_blocked"
    assert body["agent_request_id"] is None

    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        interactions = ResourceRepository(session).query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-interaction",
        )
        requests = AgentRequestRepository(session).list(project_id=project_id)
    outbound = [
        item
        for item in interactions.items
        if item.data_json.get("interaction_type") == "outbound_block_button"
    ]
    assert outbound[0].data_json["status"] == "active"
    assert requests.total_estimate == 0


def test_slack_ingress_rejects_bad_signature_before_storing(
    api: TestClient,
    project_id: int,
) -> None:
    _store_slack_profile(api, project_id)
    payload = {
        "type": "event_callback",
        "team_id": "T123",
        "event_id": "EvBad",
        "event": {
            "type": "message",
            "user": "U111",
            "channel": "C123",
            "channel_type": "im",
            "text": "hello",
            "ts": "1770000000.000300",
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")
    response = _post_without_bearer(
        api,
        f"/api/v1/ingress/slack/{project_id}/support-agent",
        raw_body=raw_body,
        headers={
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=bad",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 403, response.text  # type: ignore[attr-defined]
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        events = ResourceRepository(session).query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-event",
        )
    assert events.items == []


def test_slack_ingress_rejects_outbound_only_profile_without_writes(
    api: TestClient,
    project_id: int,
) -> None:
    _store_slack_profile(api, project_id, profile_key="outbound-only", ingress_enabled=False)
    raw_body = json.dumps(
        {
            "type": "event_callback",
            "team_id": "T123",
            "event_id": "EvOutboundOnly",
            "event": {
                "type": "app_mention",
                "user": "U111",
                "channel": "C123",
                "text": "<@U_BOT> should not be accepted",
                "ts": "1770000000.000999",
            },
        },
        separators=(",", ":"),
    ).encode()
    response = _post_without_bearer(
        api,
        f"/api/v1/ingress/slack/{project_id}/outbound-only",
        raw_body=raw_body,
        headers={**_signed_headers(raw_body), "Content-Type": "application/json"},
    )

    assert response.status_code == 403, response.text  # type: ignore[attr-defined]
    assert response.json()["detail"] == "invalid Slack signature"  # type: ignore[attr-defined]
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        events = ResourceRepository(session).query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-event",
        )
        requests = AgentRequestRepository(session).list(project_id=project_id)
    assert events.items == []
    assert requests.total_estimate == 0


def test_slack_ingress_rejects_ambiguous_account_owner_without_writes(
    api: TestClient,
    project_id: int,
) -> None:
    credential_ref = _store_slack_profile(api, project_id, profile_key="primary")
    _store_slack_profile(
        api,
        project_id,
        profile_key="duplicate",
        credential_ref=credential_ref,
    )
    raw_body = json.dumps(
        {
            "type": "event_callback",
            "team_id": "T123",
            "event_id": "EvAmbiguousOwner",
            "event": {
                "type": "app_mention",
                "user": "U111",
                "channel": "C123",
                "text": "<@U_BOT> should fail closed",
                "ts": "1770000000.001000",
            },
        },
        separators=(",", ":"),
    ).encode()

    for profile_key in ("primary", "duplicate"):
        response = _post_without_bearer(
            api,
            f"/api/v1/ingress/slack/{project_id}/{profile_key}",
            raw_body=raw_body,
            headers={**_signed_headers(raw_body), "Content-Type": "application/json"},
        )
        assert response.status_code == 403, response.text  # type: ignore[attr-defined]
        assert response.json()["detail"] == "invalid Slack signature"  # type: ignore[attr-defined]

    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        events = ResourceRepository(session).query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-event",
        )
        requests = AgentRequestRepository(session).list(project_id=project_id)
    assert events.items == []
    assert requests.total_estimate == 0


def test_slack_url_verification_returns_challenge_without_resource_writes(
    api: TestClient,
    project_id: int,
) -> None:
    _store_slack_profile(api, project_id)
    raw_body = json.dumps({"type": "url_verification", "challenge": "challenge-value"}).encode(
        "utf-8"
    )
    response = _post_without_bearer(
        api,
        f"/api/v1/ingress/slack/{project_id}/support-agent",
        raw_body=raw_body,
        headers={**_signed_headers(raw_body), "Content-Type": "application/json"},
    )

    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    assert response.json() == {"challenge": "challenge-value"}  # type: ignore[attr-defined]
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        events = ResourceRepository(session).query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-event",
        )
    assert events.items == []
