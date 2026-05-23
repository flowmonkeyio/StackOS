"""Telegram Bot API connector tests through the generic action executor."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session

from content_stack.actions import ActionRepository
from content_stack.auth_providers import AuthRepository
from content_stack.repositories.agent_requests import AgentRequestRepository
from content_stack.repositories.base import ConflictError, NotFoundError
from content_stack.repositories.projects import IntegrationCredentialRepository
from content_stack.repositories.resources import ResourceRepository

_TOKEN = "123456:ABC"
_BASE = f"https://api.telegram.org/bot{_TOKEN}"


def _telegram_credential_ref(
    session: Session,
    project_id: int,
    *,
    config_json: dict | None = None,
) -> str:
    ActionRepository(session).describe(action_ref="communications.telegram-bot.identity.get")
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="telegram-bot",
        secret_payload=json.dumps(
            {"bot_token": _TOKEN, "webhook_secret_token": "telegram-secret"}
        ).encode("utf-8"),
        config_json=config_json or {},
    )
    status = AuthRepository(session).status(project_id=project_id, provider_key="telegram-bot")
    return status.connections[0].credential_ref


def _telegram_bot_profile(
    session: Session,
    project_id: int,
    *,
    key: str = "support-bot",
    auth_profile_key: str = "default",
    access_policy: dict | None = None,
    response_policy: dict | None = None,
    profile_overrides: dict | None = None,
) -> None:
    data_json = {
        "key": key,
        "provider_key": "telegram-bot",
        "auth_profile_key": auth_profile_key,
        "enabled": True,
        "bot_username": "stackos_bot",
        "allowed_updates": ["message", "callback_query"],
        "refs": {"main": "12345"},
        "identity": {
            "display_name": "Support Bot",
            "purpose": "Handle support requests from approved Telegram users.",
            "voice": "Concise and calm.",
        },
        "agent_guidance": {
            "default_instructions": "Triage support requests before replying.",
            "boundaries": "Do not expose secrets.",
        },
        "access_policy": access_policy
        or {
            "dm_mode": "allowlist",
            "group_mode": "allowlist",
            "user_mode": "all",
            "allowed_chat_refs": ["main", "telegram-chat:12345"],
        },
        "response_policy": response_policy or {},
    }
    if profile_overrides:
        data_json.update(profile_overrides)
    ResourceRepository(session).upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-bot-profile",
        external_id=f"telegram-bot-profile:{key}",
        title=key,
        data_json=data_json,
        provenance_json={"source": "test"},
    )


def test_telegram_builtin_actions_are_registered(session: Session) -> None:
    repo = ActionRepository(session)

    for action_ref, operation in {
        "communications.telegram-bot.identity.get": "identity.get",
        "communications.telegram-bot.message.send": "message.send",
        "communications.telegram-bot.photo.send": "photo.send",
        "communications.telegram-bot.callback.answer": "callback.answer",
        "communications.telegram-bot.updates.poll": "updates.poll",
        "communications.telegram-bot.webhook.set": "webhook.set",
        "communications.telegram-bot.webhook.delete": "webhook.delete",
        "communications.telegram-bot.webhook.info": "webhook.info",
    }.items():
        described = repo.describe(action_ref=action_ref)

        assert described.connector_registered is True
        assert described.manifest.connector_key == "telegram-bot"
        assert described.manifest.operation == operation


def test_telegram_identity_send_message_callback_and_poll_execute_without_secret_leak(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _telegram_credential_ref(
        session,
        project_id,
    )
    _telegram_bot_profile(session, project_id)
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/getMe",
        json={"ok": True, "result": {"id": 42, "username": "stackos_bot"}},
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/sendMessage",
        json={"ok": True, "result": {"message_id": 7, "chat": {"id": 12345}}},
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/answerCallbackQuery",
        json={"ok": True, "result": True},
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/getUpdates",
        json={
            "ok": True,
            "result": [
                {
                    "update_id": 99,
                    "callback_query": {
                        "id": "cbq_1",
                        "data": "ixn_123",
                        "message": {"message_id": 7, "chat": {"id": 12345}},
                    },
                }
            ],
        },
    )
    repo = ActionRepository(session)

    identity = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.telegram-bot.identity.get",
            input_json={},
            credential_ref=credential_ref,
        )
    ).data
    message = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.telegram-bot.message.send",
            input_json={
                "bot_profile_key": "support-bot",
                "chat_ref": "main",
                "text": "Ready to review?",
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {
                                "text": "Review",
                                "callback_data": "ixn_123",
                                "allowed_user_refs": ["telegram-user:555"],
                                "allowed_chat_refs": ["telegram-chat:12345"],
                            }
                        ],
                        [{"text": "Open", "url": "https://example.com/review"}],
                    ]
                },
            },
            credential_ref=credential_ref,
        )
    ).data
    callback = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.telegram-bot.callback.answer",
            input_json={
                "bot_profile_key": "support-bot",
                "callback_query_id": "cbq_1",
                "text": "Queued",
            },
            credential_ref=credential_ref,
        )
    ).data
    updates = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.telegram-bot.updates.poll",
            input_json={
                "bot_profile_key": "support-bot",
                "offset": 100,
                "limit": 10,
                "timeout_s": 0,
                "allowed_updates": ["message", "callback_query"],
            },
            credential_ref=credential_ref,
        )
    ).data

    requests = httpx_mock.get_requests()
    message_body = json.loads(requests[1].content.decode("utf-8"))
    callback_body = json.loads(requests[2].content.decode("utf-8"))
    updates_body = json.loads(requests[3].content.decode("utf-8"))
    rendered = json.dumps(
        {
            "identity": identity.model_dump(mode="json"),
            "message": message.model_dump(mode="json"),
            "callback": callback.model_dump(mode="json"),
            "updates": updates.model_dump(mode="json"),
        }
    )

    assert message_body["chat_id"] == "12345"
    sent_button = message_body["reply_markup"]["inline_keyboard"][0][0]
    assert sent_button["callback_data"] == "ixn_123"
    assert "allowed_user_refs" not in sent_button
    assert "allowed_chat_refs" not in sent_button
    assert callback_body == {"callback_query_id": "cbq_1", "text": "Queued"}
    assert updates_body["allowed_updates"] == ["message", "callback_query"]
    assert message.action_call.connector_key == "telegram-bot"
    assert callback.output_json["body"]["result"] is True
    assert updates.output_json["body"]["result"][0]["callback_query"]["data"] == "ixn_123"
    assert _TOKEN not in rendered
    interactions = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-interaction",
    )
    buttons = [
        item
        for item in interactions.items
        if item.external_id == "telegram-button:support-bot:telegram-message:12345:7:ixn_123"
    ]
    assert len(buttons) == 1
    assert buttons[0].data_json["allowed_user_refs"] == ["telegram-user:555"]
    assert buttons[0].data_json["allowed_chat_refs"] == ["telegram-chat:12345"]
    messages = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-message",
    )
    outbound = [item for item in messages.items if item.data_json.get("direction") == "outbound"]
    assert len(outbound) == 1
    assert outbound[0].data_json["message_ref"] == "telegram-message:12345:7"


def test_telegram_photo_uploads_generated_asset_ref(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    tmp_path: Path,
) -> None:
    asset = tmp_path / "openai-images" / "sample.webp"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"fake-webp-bytes")
    credential_ref = _telegram_credential_ref(
        session,
        project_id,
    )
    _telegram_bot_profile(
        session,
        project_id,
        access_policy={
            "dm_mode": "allowlist",
            "group_mode": "allowlist",
            "user_mode": "all",
            "allowed_chat_refs": ["main"],
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/sendPhoto",
        json={"ok": True, "result": {"message_id": 8, "photo": [{"file_id": "file_1"}]}},
    )

    out = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="communications.telegram-bot.photo.send",
            input_json={
                "bot_profile_key": "support-bot",
                "chat_ref": "main",
                "photo": {"artifact_ref": "/generated-assets/openai-images/sample.webp"},
                "caption": "Generated option",
            },
            credential_ref=credential_ref,
        )
    ).data

    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(out.model_dump(mode="json"))
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b"fake-webp-bytes" in request.content
    assert out.output_json["body"]["result"]["photo"][0]["file_id"] == "file_1"
    assert _TOKEN not in rendered


def test_telegram_validation_rejects_unsafe_buttons_and_photo_sources(
    session: Session,
    project_id: int,
) -> None:
    credential_ref = _telegram_credential_ref(session, project_id)
    repo = ActionRepository(session)

    unsafe_button = repo.validate(
        project_id=project_id,
        action_ref="communications.telegram-bot.message.send",
        input_json={
            "bot_profile_key": "support-bot",
            "chat_ref": "12345",
            "text": "Pick one",
            "reply_markup": {
                "inline_keyboard": [[{"text": "Bad", "callback_data": "contains-secret-word"}]]
            },
        },
        credential_ref=credential_ref,
    )
    bad_photo = repo.validate(
        project_id=project_id,
        action_ref="communications.telegram-bot.photo.send",
        input_json={
            "bot_profile_key": "support-bot",
            "chat_ref": "12345",
            "photo": {"file_id": "file_1", "url": "http://example.com/a.jpg"},
        },
        credential_ref=credential_ref,
    )

    assert unsafe_button.valid is False
    assert any(issue.code == "secret_like" for issue in unsafe_button.issues)
    assert bad_photo.valid is False
    assert any(issue.code == "one_of" for issue in bad_photo.issues)


def test_telegram_webhook_actions_execute_against_local_bot_api_server(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _telegram_credential_ref(
        session,
        project_id,
        config_json={"api_base_url": "http://127.0.0.1:8081"},
    )
    _telegram_bot_profile(session, project_id)
    httpx_mock.add_response(
        method="POST",
        url=f"http://127.0.0.1:8081/bot{_TOKEN}/setWebhook",
        json={"ok": True, "result": True},
    )
    httpx_mock.add_response(
        method="POST",
        url=f"http://127.0.0.1:8081/bot{_TOKEN}/getWebhookInfo",
        json={
            "ok": True,
            "result": {"url": "http://127.0.0.1:5180/api/v1/ingress/telegram/1/support-bot"},
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=f"http://127.0.0.1:8081/bot{_TOKEN}/deleteWebhook",
        json={"ok": True, "result": True},
    )
    repo = ActionRepository(session)

    set_out = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.telegram-bot.webhook.set",
            input_json={
                "bot_profile_key": "support-bot",
                "webhook_url": "http://127.0.0.1:5180/api/v1/ingress/telegram/1/support-bot",
                "allowed_updates": ["message", "callback_query"],
                "drop_pending_updates": True,
            },
            credential_ref=credential_ref,
        )
    ).data
    info_out = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.telegram-bot.webhook.info",
            input_json={"bot_profile_key": "support-bot"},
            credential_ref=credential_ref,
        )
    ).data
    delete_out = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.telegram-bot.webhook.delete",
            input_json={"bot_profile_key": "support-bot", "drop_pending_updates": True},
            credential_ref=credential_ref,
        )
    ).data

    requests = httpx_mock.get_requests()
    set_body = json.loads(requests[0].content.decode("utf-8"))
    delete_body = json.loads(requests[2].content.decode("utf-8"))
    rendered = json.dumps(
        {
            "set": set_out.model_dump(mode="json"),
            "info": info_out.model_dump(mode="json"),
            "delete": delete_out.model_dump(mode="json"),
        }
    )

    assert set_body["secret_token"] == "telegram-secret"
    assert set_body["allowed_updates"] == ["message", "callback_query"]
    assert delete_body == {"drop_pending_updates": True}
    assert set_out.output_json["body"]["result"] is True
    assert info_out.output_json["body"]["result"]["url"].endswith("/support-bot")
    assert delete_out.output_json["body"]["result"] is True
    assert _TOKEN not in rendered
    assert "telegram-secret" not in rendered


def test_telegram_send_rejects_bot_profile_credential_mismatch(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _telegram_credential_ref(session, project_id)
    _telegram_bot_profile(
        session,
        project_id,
        key="support-bot",
        auth_profile_key="other-profile",
    )

    with pytest.raises(ConflictError, match="action connector failed") as exc:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="communications.telegram-bot.message.send",
                input_json={
                    "bot_profile_key": "support-bot",
                    "chat_ref": "telegram-chat:12345",
                    "text": "hello",
                },
                credential_ref=credential_ref,
            )
        )

    assert "does not match credential profile" in exc.value.data["error"]
    assert httpx_mock.get_requests() == []


def test_telegram_send_rejects_global_credential_for_project_bot_profile(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    global_credential = (
        IntegrationCredentialRepository(session)
        .set(
            project_id=None,
            kind="telegram-bot",
            secret_payload=json.dumps(
                {"bot_token": _TOKEN, "webhook_secret_token": "telegram-secret"}
            ).encode("utf-8"),
        )
        .data
    )
    status = AuthRepository(session).status(
        project_id=project_id,
        provider_key="telegram-bot",
    )
    assert status.connections == []
    credential = AuthRepository(session).sync_credential_for_integration(global_credential.id)
    _telegram_bot_profile(session, project_id)

    with pytest.raises(NotFoundError):
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="communications.telegram-bot.message.send",
                input_json={
                    "bot_profile_key": "support-bot",
                    "chat_ref": "main",
                    "text": "hello",
                },
                credential_ref=credential.credential_ref,
            )
        )

    assert httpx_mock.get_requests() == []


def test_telegram_webhook_set_rejects_wrong_project_or_profile_url(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _telegram_credential_ref(
        session,
        project_id,
        config_json={"api_base_url": "http://127.0.0.1:8081"},
    )
    _telegram_bot_profile(session, project_id)

    with pytest.raises(ConflictError, match="action connector failed") as exc:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="communications.telegram-bot.webhook.set",
                input_json={
                    "bot_profile_key": "support-bot",
                    "webhook_url": (
                        f"http://127.0.0.1:5180/api/v1/ingress/telegram/{project_id}/other-bot"
                    ),
                },
                credential_ref=credential_ref,
            )
        )

    assert "must target this project bot profile" in exc.value.data["error"]

    with pytest.raises(ConflictError, match="action connector failed") as host_exc:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="communications.telegram-bot.webhook.set",
                input_json={
                    "bot_profile_key": "support-bot",
                    "webhook_url": (
                        f"https://evil.example/api/v1/ingress/telegram/{project_id}/support-bot"
                    ),
                },
                credential_ref=credential_ref,
            )
        )
    assert "configured StackOS webhook host" in host_exc.value.data["error"]
    assert httpx_mock.get_requests() == []


def test_telegram_response_policy_requires_source_origin_and_exact_reply(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _telegram_credential_ref(session, project_id)
    _telegram_bot_profile(
        session,
        project_id,
        response_policy={
            "origin_required": True,
            "reply_to_source_message": True,
            "same_thread": True,
        },
        profile_overrides={
            "reply_to_message_refs": {"telegram-message:12345:77": 77},
            "thread_refs": {"telegram-thread:12345:default": 1},
        },
    )
    source = (
        AgentRequestRepository(session)
        .create(
            project_id=project_id,
            request_key="telegram-update:support-bot:500",
            title="Telegram message",
            body_preview="@stackos_bot review",
            source_provider="telegram-bot",
            source_kind="telegram_message",
            source_message_ref="telegram-message:12345:77",
            metadata_json={
                "bot_profile_key": "support-bot",
                "chat_ref": "telegram-chat:12345",
                "thread_ref": "telegram-thread:12345:default",
                "invoker_ref": "telegram-user:555",
            },
        )
        .data
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/sendMessage",
        json={"ok": True, "result": {"message_id": 78, "chat": {"id": 12345}}},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="communications.telegram-bot.message.send",
            input_json={
                "bot_profile_key": "support-bot",
                "chat_ref": "main",
                "source_agent_request_id": source.id,
                "reply_to_message_ref": "telegram-message:12345:77",
                "thread_ref": "telegram-thread:12345:default",
                "text": "Queued the review.",
                "reply_markup": {
                    "inline_keyboard": [[{"text": "Done", "callback_data": "done_78"}]]
                },
            },
            credential_ref=credential_ref,
        )
    ).data
    request_body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))

    assert out.output_json["body"]["result"]["message_id"] == 78
    assert request_body["reply_to_message_id"] == 77
    assert request_body["message_thread_id"] == 1
    interactions = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-interaction",
    )
    button = next(
        item
        for item in interactions.items
        if item.external_id == "telegram-button:support-bot:telegram-message:12345:78:done_78"
    )
    assert button.data_json["allowed_user_refs"] == ["telegram-user:555"]
    assert button.data_json["allowed_chat_refs"] == ["telegram-chat:12345"]

    with pytest.raises(ConflictError, match="action connector failed") as exc:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="communications.telegram-bot.message.send",
                input_json={
                    "bot_profile_key": "support-bot",
                    "chat_ref": "main",
                    "source_agent_request_id": source.id,
                    "reply_to_message_ref": "telegram-message:12345:99",
                    "thread_ref": "telegram-thread:12345:default",
                    "text": "Wrong source.",
                },
                credential_ref=credential_ref,
            )
        )
    assert "must reply to the source message" in exc.value.data["error"]


def test_telegram_response_policy_rejects_malformed_non_telegram_source(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _telegram_credential_ref(session, project_id)
    _telegram_bot_profile(
        session,
        project_id,
        response_policy={"origin_required": True},
    )
    source = (
        AgentRequestRepository(session)
        .create(
            project_id=project_id,
            request_key="manual-request:1",
            title="Manual task",
            source_provider="manual",
            source_kind="manual",
            metadata_json={"bot_profile_key": "support-bot"},
        )
        .data
    )

    with pytest.raises(ConflictError, match="action connector failed") as exc:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="communications.telegram-bot.message.send",
                input_json={
                    "bot_profile_key": "support-bot",
                    "chat_ref": "main",
                    "source_agent_request_id": source.id,
                    "text": "This should not send.",
                },
                credential_ref=credential_ref,
            )
        )

    assert "must be a Telegram agent request" in exc.value.data["error"]
    assert httpx_mock.get_requests() == []


def test_telegram_provider_error_redacts_bot_token_url(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _telegram_credential_ref(session, project_id)
    _telegram_bot_profile(session, project_id)
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/sendMessage",
        status_code=401,
        text=f"unauthorized at https://api.telegram.org/bot{_TOKEN}/sendMessage",
    )

    with pytest.raises(ConflictError, match="action connector failed") as exc:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="communications.telegram-bot.message.send",
                input_json={
                    "bot_profile_key": "support-bot",
                    "chat_ref": "main",
                    "text": "hello",
                },
                credential_ref=credential_ref,
            )
        )

    assert _TOKEN not in exc.value.data["error"]
    assert "/bot[redacted]/sendMessage" in exc.value.data["error"]
