"""Slack Web API connector tests through the generic action executor."""

from __future__ import annotations

import asyncio
import json

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.repositories.base import ConflictError
from stackos.repositories.projects import IntegrationCredentialRepository
from stackos.repositories.resources import ResourceRepository

_TOKEN = "xoxb-1234567890-safe-test-token"
_SIGNING_SECRET = "slack-signing-secret"
_BASE = "https://slack.com/api"


def _slack_credential_ref(
    session: Session,
    project_id: int,
    *,
    profile_key: str = "support-auth",
    config_json: dict | None = None,
) -> str:
    ActionRepository(session).describe(action_ref="communications.slack-bot.identity.get")
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="slack-bot",
        profile_key=profile_key,
        secret_payload=json.dumps(
            {
                "bot_token": _TOKEN,
                "signing_secret": _SIGNING_SECRET,
            }
        ).encode("utf-8"),
        config_json=config_json or {"team_id": "T123"},
    )
    status = AuthRepository(session).status(project_id=project_id, provider_key="slack-bot")
    return status.connections[0].credential_ref


def _slack_communication_profile(
    session: Session,
    project_id: int,
    *,
    profile_key: str = "support-agent",
    auth_profile_key: str = "support-auth",
) -> None:
    ResourceRepository(session).upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-profile",
        external_id=f"communication-profile:{profile_key}",
        title=profile_key,
        data_json={
            "key": profile_key,
            "enabled": True,
            "provider_facets": {
                "slack-bot": {
                    "auth_profile_key": auth_profile_key,
                    "bot_user_id": "U_BOT",
                }
            },
            "identity": {"display_name": "Support Agent"},
            "access_policy": {
                "channel_mode": "all",
                "dm_mode": "all",
                "user_mode": "allowlist",
                "allowed_user_refs": ["slack-user:U111"],
            },
            "send_policy": {
                "mode": "authorized-invoker",
                "allowed_user_refs": ["slack-user:U111"],
            },
            "trigger_policy": {},
            "context_policy": {},
            "response_policy": {},
        },
        provenance_json={"source": "test"},
    )


def test_slack_builtin_actions_are_registered(session: Session) -> None:
    repo = ActionRepository(session)

    for action_ref, operation in {
        "communications.slack-bot.identity.get": "identity.get",
        "communications.slack-bot.message.send": "message.send",
        "communications.slack-bot.conversation.open": "conversation.open",
        "communications.slack-bot.conversation.info": "conversation.info",
        "communications.slack-bot.conversation.list": "conversation.list",
        "communications.slack-bot.conversation.members": "conversation.members",
    }.items():
        described = repo.describe(action_ref=action_ref)

        assert described.connector_registered is True
        assert described.manifest.connector_key == "slack-bot"
        assert described.manifest.operation == operation


def test_slack_actions_execute_store_resources_and_redact_secrets(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _slack_credential_ref(session, project_id)
    _slack_communication_profile(session, project_id)
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/auth.test",
        json={
            "ok": True,
            "team_id": "T123",
            "team": "Acme",
            "user_id": "U_BOT",
            "user": "stackos",
            "bot_id": "B123",
            "url": "https://acme.slack.com/",
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/chat.postMessage",
        json={
            "ok": True,
            "channel": "C123",
            "ts": "1770000000.000100",
            "message": {"ts": "1770000000.000100", "text": "Ready?"},
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/conversations.open",
        json={"ok": True, "channel": {"id": "D123", "is_im": True, "user": "U111"}},
    )
    httpx_mock.add_response(
        method="GET",
        json={
            "ok": True,
            "channel": {"id": "C123", "name": "support", "is_private": False},
        },
    )
    httpx_mock.add_response(
        method="GET",
        json={
            "ok": True,
            "channels": [
                {"id": "C123", "name": "support", "is_member": True},
                {"id": "G123", "name": "private-support", "is_private": True},
            ],
            "response_metadata": {"next_cursor": "cursor-2"},
        },
    )
    httpx_mock.add_response(
        method="GET",
        json={"ok": True, "members": ["U111", "U222"], "response_metadata": {}},
    )
    repo = ActionRepository(session)

    identity = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.slack-bot.identity.get",
            input_json={},
            credential_ref=credential_ref,
        )
    ).data
    message = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.slack-bot.message.send",
            input_json={
                "profile_ref": "communication-profile:support-agent",
                "channel_ref": "slack-channel:C123",
                "text": "Ready?",
                "blocks": [
                    {
                        "type": "actions",
                        "block_id": "decision",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Approve"},
                                "action_id": "approve",
                                "value": "approve_177",
                            }
                        ],
                    }
                ],
            },
            credential_ref=credential_ref,
        )
    ).data
    opened = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.slack-bot.conversation.open",
            input_json={
                "profile_ref": "communication-profile:support-agent",
                "users": ["slack-user:U111"],
            },
            credential_ref=credential_ref,
        )
    ).data
    info = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.slack-bot.conversation.info",
            input_json={
                "profile_ref": "communication-profile:support-agent",
                "channel_ref": "slack-channel:C123",
                "include_num_members": True,
            },
            credential_ref=credential_ref,
        )
    ).data
    listed = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.slack-bot.conversation.list",
            input_json={
                "profile_ref": "communication-profile:support-agent",
                "types": ["public_channel", "private_channel"],
                "limit": 100,
            },
            credential_ref=credential_ref,
        )
    ).data
    members = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.slack-bot.conversation.members",
            input_json={
                "profile_ref": "communication-profile:support-agent",
                "channel_ref": "slack-channel:C123",
                "limit": 100,
            },
            credential_ref=credential_ref,
        )
    ).data

    requests = httpx_mock.get_requests()
    post_body = json.loads(requests[1].content.decode("utf-8"))
    rendered = json.dumps(
        {
            "identity": identity.model_dump(mode="json"),
            "message": message.model_dump(mode="json"),
            "opened": opened.model_dump(mode="json"),
            "info": info.model_dump(mode="json"),
            "listed": listed.model_dump(mode="json"),
            "members": members.model_dump(mode="json"),
        }
    )

    assert [request.url.path for request in requests] == [
        "/api/auth.test",
        "/api/chat.postMessage",
        "/api/conversations.open",
        "/api/conversations.info",
        "/api/conversations.list",
        "/api/conversations.members",
    ]
    assert requests[1].headers["authorization"] == f"Bearer {_TOKEN}"
    assert post_body["channel"] == "C123"
    assert post_body["text"] == "Ready?"
    assert "profile_ref" not in post_body
    assert message.output_json["message_ref"] == "slack-message:C123:1770000000.000100"
    assert listed.output_json["next_cursor"] == "cursor-2"
    assert members.output_json["member_refs"] == ["slack-user:U111", "slack-user:U222"]
    assert _TOKEN not in rendered
    assert _SIGNING_SECRET not in rendered

    messages = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-message",
    )
    outbound = [item for item in messages.items if item.data_json.get("direction") == "outbound"]
    assert len(outbound) == 1
    assert outbound[0].external_id == "slack-message:support-agent:C123:1770000000.000100"
    assert outbound[0].data_json["profile_key"] == "support-agent"
    assert outbound[0].data_json["auth_profile_key"] == "support-auth"

    interactions = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-interaction",
    )
    buttons = [
        item
        for item in interactions.items
        if item.data_json.get("interaction_type") == "outbound_block_button"
    ]
    assert len(buttons) == 1
    assert buttons[0].external_id.startswith("slack-button:support-agent:")
    assert buttons[0].data_json["button_value"] == "approve_177"

    memberships = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-membership",
    )
    assert {item.data_json["member_ref"] for item in memberships.items} == {
        "slack-user:U111",
        "slack-user:U222",
    }
    assert {item.data_json["profile_key"] for item in memberships.items} == {"support-agent"}
    channels = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-channel",
    )
    assert {item.external_id for item in channels.items} >= {
        "slack-channel:support-agent:C123",
        "slack-channel:support-agent:D123",
        "slack-channel:support-agent:G123",
    }
    assert not any(
        item.external_id and item.external_id.startswith("slack-channel:support-auth:")
        for item in channels.items
    )


def test_slack_validation_rejects_secret_like_button_values(
    session: Session,
    project_id: int,
) -> None:
    credential_ref = _slack_credential_ref(session, project_id)
    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="communications.slack-bot.message.send",
        input_json={
            "channel_ref": "slack-channel:C123",
            "text": "Pick one",
            "blocks": [
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Bad"},
                            "action_id": "bad",
                            "value": "xoxb-this-looks-like-a-token",
                        }
                    ],
                }
            ],
        },
        credential_ref=credential_ref,
    )

    assert validation.valid is False
    assert any(item.code == "secret_like" for item in validation.issues)


def test_slack_send_rejects_unbound_communication_profile(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _slack_credential_ref(session, project_id)
    _slack_communication_profile(
        session,
        project_id,
        profile_key="wrong-agent",
        auth_profile_key="other-auth",
    )

    with pytest.raises(ConflictError, match="action connector failed") as exc:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="communications.slack-bot.message.send",
                input_json={
                    "profile_ref": "communication-profile:wrong-agent",
                    "channel_ref": "slack-channel:C123",
                    "text": "hello",
                },
                credential_ref=credential_ref,
            )
        )

    assert "auth_profile_key does not match credential profile" in exc.value.data["error"]
    assert httpx_mock.get_requests() == []


def test_slack_send_rejects_missing_communication_profile(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _slack_credential_ref(session, project_id)

    with pytest.raises(ConflictError, match="action connector failed") as exc:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="communications.slack-bot.message.send",
                input_json={
                    "profile_ref": "communication-profile:missing-agent",
                    "channel_ref": "slack-channel:C123",
                    "text": "hello",
                },
                credential_ref=credential_ref,
            )
        )

    assert "communication profile not found" in exc.value.data["error"]
    assert httpx_mock.get_requests() == []


def test_slack_provider_error_redacts_token_text(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _slack_credential_ref(session, project_id)
    _slack_communication_profile(session, project_id)
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/chat.postMessage",
        status_code=401,
        text=f"invalid_auth for Authorization: Bearer {_TOKEN}",
    )

    with pytest.raises(ConflictError, match="action connector failed") as exc:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="communications.slack-bot.message.send",
                input_json={
                    "profile_ref": "communication-profile:support-agent",
                    "channel_ref": "slack-channel:C123",
                    "text": "hello",
                },
                credential_ref=credential_ref,
            )
        )

    assert _TOKEN not in exc.value.data["error"]
    assert "Bearer [redacted]" in exc.value.data["error"]
