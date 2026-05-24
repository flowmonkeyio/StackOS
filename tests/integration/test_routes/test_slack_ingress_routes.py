"""Slack signed HTTP ingress route tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient
from sqlmodel import Session

from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.projects import IntegrationCredentialRepository
from stackos.repositories.resources import ResourceRepository

_TOKEN = "xoxb-1234567890-safe-test-token"
_SIGNING_SECRET = "slack-signing-secret"


def _store_slack_profile(
    api: TestClient,
    project_id: int,
    *,
    profile_key: str = "support-agent",
    auth_profile_key: str = "support-auth",
    access_policy: dict | None = None,
    trigger_policy: dict | None = None,
    visibility_policy: dict | None = None,
) -> None:
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        IntegrationCredentialRepository(session).set(
            project_id=project_id,
            kind="slack-bot",
            profile_key=auth_profile_key,
            secret_payload=json.dumps(
                {
                    "bot_token": _TOKEN,
                    "signing_secret": _SIGNING_SECRET,
                }
            ).encode("utf-8"),
            config_json={"team_id": "T123"},
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
                        "auth_profile_key": auth_profile_key,
                        "bot_user_id": "U_BOT",
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


def _store_outbound_button(
    api: TestClient,
    project_id: int,
    *,
    profile_key: str = "support-agent",
    message_ref: str = "slack-message:C123:1770000000.000100",
    action_id: str = "approve",
    value: str = "approve_177",
    block_id: str = "decision",
) -> None:
    digest = hashlib.sha256(
        f"{message_ref}\0{block_id}\0{action_id}\0{value}".encode()
    ).hexdigest()[:24]
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        ResourceRepository(session).upsert_record(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-interaction",
            external_id=f"slack-button:{profile_key}:{digest}",
            title="Approve",
            data_json={
                "provider_key": "slack-bot",
                "profile_key": profile_key,
                "auth_profile_key": "support-auth",
                "interaction_type": "outbound_block_button",
                "surface_ref": "slack-channel:C123",
                "message_ref": message_ref,
                "block_id": block_id,
                "action_id": action_id,
                "button_value": value,
                "status": "active",
            },
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
    assert (
        requests.items[0].metadata_json["agent_guidance"]["boundaries"] == "Do not expose secrets."
    )


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
