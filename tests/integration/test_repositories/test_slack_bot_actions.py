"""Slack Web API connector tests through the generic action executor."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.repositories.agent_requests import AgentRequestRepository
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
        "communications.slack-bot.file.upload": "file.upload",
        "communications.slack-bot.reaction.add": "reaction.add",
        "communications.slack-bot.message.delete": "message.delete",
        "communications.slack-bot.conversation.open": "conversation.open",
        "communications.slack-bot.conversation.info": "conversation.info",
        "communications.slack-bot.conversation.list": "conversation.list",
        "communications.slack-bot.conversation.members": "conversation.members",
        "communications.slack-bot.conversation.history": "conversation.history",
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
        url=f"{_BASE}/reactions.add",
        json={"ok": True},
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/chat.delete",
        json={"ok": True, "channel": "C123", "ts": "1770000000.000100"},
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
    source = (
        AgentRequestRepository(session)
        .create(
            project_id=project_id,
            request_key="slack-message-trigger:support-agent:C123:1770000000.000001",
            title="Slack message",
            body_preview="<@U_BOT> review",
            source_provider="slack-bot",
            source_kind="slack-message",
            source_message_ref="slack-message:C123:1770000000.000001",
            metadata_json={
                "profile_key": "support-agent",
                "surface_ref": "slack-channel:C123",
                "channel_ref": "slack-channel:C123",
                "thread_ref": "slack-thread:C123:1770000000.000001",
                "invoker_ref": "slack-user:U111",
            },
        )
        .data
    )

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
                "source_agent_request_id": source.id,
                "control_metadata": {
                    "approve_177": {
                        "action": "approve",
                        "payload": {"decision": "approve", "ticket_id": "T-177"},
                    }
                },
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
    reaction = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.slack-bot.reaction.add",
            input_json={
                "profile_ref": "communication-profile:support-agent",
                "message_ref": "slack-message:C123:1770000000.000100",
                "name": "white_check_mark",
            },
            credential_ref=credential_ref,
        )
    ).data
    deleted = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.slack-bot.message.delete",
            input_json={
                "profile_ref": "communication-profile:support-agent",
                "message_ref": "slack-message:C123:1770000000.000100",
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
    reaction_body = json.loads(requests[2].content.decode("utf-8"))
    delete_body = json.loads(requests[3].content.decode("utf-8"))
    rendered = json.dumps(
        {
            "identity": identity.model_dump(mode="json"),
            "message": message.model_dump(mode="json"),
            "reaction": reaction.model_dump(mode="json"),
            "deleted": deleted.model_dump(mode="json"),
            "opened": opened.model_dump(mode="json"),
            "info": info.model_dump(mode="json"),
            "listed": listed.model_dump(mode="json"),
            "members": members.model_dump(mode="json"),
        }
    )

    assert [request.url.path for request in requests] == [
        "/api/auth.test",
        "/api/chat.postMessage",
        "/api/reactions.add",
        "/api/chat.delete",
        "/api/conversations.open",
        "/api/conversations.info",
        "/api/conversations.list",
        "/api/conversations.members",
    ]
    assert requests[1].headers["authorization"] == f"Bearer {_TOKEN}"
    assert post_body["channel"] == "C123"
    assert post_body["text"] == "Ready?"
    assert "profile_ref" not in post_body
    assert reaction_body == {
        "channel": "C123",
        "timestamp": "1770000000.000100",
        "name": "white_check_mark",
    }
    assert delete_body == {"channel": "C123", "ts": "1770000000.000100"}
    assert message.output_json["message_ref"] == "slack-message:C123:1770000000.000100"
    assert reaction.output_json["status"] == "reacted"
    assert reaction.output_json["message_ref"] == "slack-message:C123:1770000000.000100"
    assert deleted.output_json["status"] == "deleted"
    assert deleted.output_json["message_ref"] == "slack-message:C123:1770000000.000100"
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
    assert outbound[0].data_json["transport_status"] == "deleted"
    assert outbound[0].data_json["attention_status"] == "deleted"

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
    reactions = [
        item
        for item in interactions.items
        if item.data_json.get("interaction_type") == "reaction_added"
    ]
    assert len(buttons) == 1
    assert len(reactions) == 1
    assert reactions[0].external_id.startswith("slack-reaction:support-agent:")
    assert reactions[0].data_json["message_ref"] == "slack-message:C123:1770000000.000100"
    assert reactions[0].data_json["reaction_name"] == "white_check_mark"
    assert reactions[0].data_json["status"] == "sent"
    assert buttons[0].external_id.startswith("slack-button:support-agent:")
    assert buttons[0].data_json["button_value"] == "approve_177"
    assert buttons[0].data_json["control_action"] == "approve"
    assert buttons[0].data_json["control_payload"] == {
        "decision": "approve",
        "ticket_id": "T-177",
    }
    assert buttons[0].data_json["allowed_user_refs"] == ["slack-user:U111"]
    assert buttons[0].data_json["allowed_channel_refs"] == ["slack-channel:C123"]

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


def test_slack_file_upload_keeps_comment_and_files_in_one_provider_call(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    tmp_path,
) -> None:
    credential_ref = _slack_credential_ref(session, project_id)
    _slack_communication_profile(session, project_id)
    asset = tmp_path / "communication-media" / "issue.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"image-bytes")
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/files.getUploadURLExternal",
        json={"ok": True, "upload_url": "https://files.slack.test/upload/F123", "file_id": "F123"},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://files.slack.test/upload/F123",
        text="OK",
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/files.completeUploadExternal",
        json={
            "ok": True,
            "files": [
                {
                    "id": "F123",
                    "name": "issue.png",
                    "title": "Issue screenshot",
                    "mimetype": "image/png",
                    "size": 11,
                    "shares": {
                        "private": {
                            "C123": [{"ts": "1770000000.000200"}],
                        }
                    },
                }
            ],
        },
    )

    out = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="communications.slack-bot.file.upload",
            input_json={
                "profile_ref": "communication-profile:support-agent",
                "channel_ref": "slack-channel:C123",
                "thread_ref": "slack-thread:C123:1770000000.000100",
                "initial_comment": "Customer attached this screenshot.",
                "files": [
                    {
                        "artifact_ref": "/generated-assets/communication-media/issue.png",
                        "filename": "issue.png",
                        "title": "Issue screenshot",
                        "mime_type": "image/png",
                    }
                ],
                "delete_after_upload": True,
            },
            credential_ref=credential_ref,
        )
    ).data

    requests = httpx_mock.get_requests()
    assert [request.url.path for request in requests] == [
        "/api/files.getUploadURLExternal",
        "/upload/F123",
        "/api/files.completeUploadExternal",
    ]
    assert not any(request.url.path == "/api/chat.postMessage" for request in requests)
    upload_url_body = parse_qs(requests[0].content.decode("utf-8"))
    assert upload_url_body == {"filename": ["issue.png"], "length": ["11"]}
    complete_body = json.loads(requests[2].content.decode("utf-8"))
    assert complete_body["channel_id"] == "C123"
    assert complete_body["initial_comment"] == "Customer attached this screenshot."
    assert complete_body["thread_ts"] == "1770000000.000100"
    assert complete_body["files"] == [{"id": "F123", "title": "Issue screenshot"}]
    assert requests[1].content == b"image-bytes"
    assert asset.exists() is False
    assert out.output_json["message_ref"] == "slack-message:C123:1770000000.000200"
    assert out.output_json["thread_ref"] == "slack-thread:C123:1770000000.000100"
    assert out.output_json["file_refs"] == ["slack-file:F123"]
    assert out.output_json["local_artifact_deleted"] is True

    messages = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-message",
    )
    uploads = [
        item for item in messages.items if item.data_json.get("content_type") == "file_upload"
    ]
    assert len(uploads) == 1
    assert uploads[0].data_json["text_preview"] == "Customer attached this screenshot."
    assert uploads[0].data_json["attachments"][0]["file_ref"] == "slack-file:F123"


def test_slack_file_upload_missing_scope_reports_repair_context(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    tmp_path,
) -> None:
    credential_ref = _slack_credential_ref(session, project_id)
    _slack_communication_profile(session, project_id)
    asset = tmp_path / "communication-media" / "issue.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"image-bytes")
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/files.getUploadURLExternal",
        json={
            "ok": False,
            "error": "missing_scope",
            "needed": "files:write",
            "provided": "chat:write,reactions:write",
        },
    )

    with pytest.raises(ConflictError, match="action connector failed") as exc:
        asyncio.run(
            ActionRepository(session, asset_dir=tmp_path).execute(
                project_id=project_id,
                action_ref="communications.slack-bot.file.upload",
                input_json={
                    "profile_ref": "communication-profile:support-agent",
                    "channel_ref": "slack-channel:C123",
                    "initial_comment": "Customer attached this screenshot.",
                    "file": {
                        "artifact_ref": "/generated-assets/communication-media/issue.png",
                        "filename": "issue.png",
                        "title": "Issue screenshot",
                        "mime_type": "image/png",
                    },
                },
                credential_ref=credential_ref,
            )
        )

    assert "missing_scope" in exc.value.data["error"]
    assert "needed=files:write" in exc.value.data["error"]
    assert "provided=chat:write,reactions:write" in exc.value.data["error"]
    assert asset.exists() is True
    assert [request.url.path for request in httpx_mock.get_requests()] == [
        "/api/files.getUploadURLExternal"
    ]


def test_slack_conversation_history_returns_message_and_file_refs(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _slack_credential_ref(session, project_id)
    _slack_communication_profile(session, project_id)
    httpx_mock.add_response(
        method="GET",
        url=f"{_BASE}/conversations.history?channel=D123&limit=5",
        json={
            "ok": True,
            "messages": [
                {
                    "type": "message",
                    "subtype": "file_share",
                    "ts": "1770000000.000300",
                    "thread_ts": "1770000000.000300",
                    "bot_id": "B123",
                    "text": "Customer feedback forwarded from Telegram",
                    "files": [{"id": "F123"}],
                }
            ],
            "has_more": False,
        },
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="communications.slack-bot.conversation.history",
            input_json={
                "profile_ref": "communication-profile:support-agent",
                "surface_ref": "slack-channel:D123",
                "limit": 5,
            },
            credential_ref=credential_ref,
        )
    ).data

    assert out.output_json["message_refs"] == ["slack-message:D123:1770000000.000300"]
    assert out.output_json["messages"][0]["thread_ref"] == ("slack-thread:D123:1770000000.000300")
    assert out.output_json["messages"][0]["file_refs"] == ["slack-file:F123"]
