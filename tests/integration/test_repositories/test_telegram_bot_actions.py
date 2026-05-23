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
from content_stack.repositories.base import ConflictError
from content_stack.repositories.projects import IntegrationCredentialRepository

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
        secret_payload=json.dumps({"bot_token": _TOKEN}).encode("utf-8"),
        config_json=config_json or {},
    )
    status = AuthRepository(session).status(project_id=project_id, provider_key="telegram-bot")
    return status.connections[0].credential_ref


def test_telegram_builtin_actions_are_registered(session: Session) -> None:
    repo = ActionRepository(session)

    for action_ref, operation in {
        "communications.telegram-bot.identity.get": "identity.get",
        "communications.telegram-bot.message.send": "message.send",
        "communications.telegram-bot.photo.send": "photo.send",
        "communications.telegram-bot.callback.answer": "callback.answer",
        "communications.telegram-bot.updates.poll": "updates.poll",
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
        config_json={
            "allowed_chat_refs": "main,12345",
            "allowed_updates": "message,callback_query",
            "refs": {"main": "12345"},
        },
    )
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
                "chat_ref": "main",
                "text": "Ready to review?",
                "reply_markup": {
                    "inline_keyboard": [
                        [{"text": "Review", "callback_data": "ixn_123"}],
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
            input_json={"callback_query_id": "cbq_1", "text": "Queued"},
            credential_ref=credential_ref,
        )
    ).data
    updates = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.telegram-bot.updates.poll",
            input_json={
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
    assert message_body["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "ixn_123"
    assert callback_body == {"callback_query_id": "cbq_1", "text": "Queued"}
    assert updates_body["allowed_updates"] == ["message", "callback_query"]
    assert message.action_call.connector_key == "telegram-bot"
    assert callback.output_json["body"]["result"] is True
    assert updates.output_json["body"]["result"][0]["callback_query"]["data"] == "ixn_123"
    assert _TOKEN not in rendered


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
        config_json={"allowed_chat_refs": "main", "refs": {"main": "12345"}},
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
            "chat_ref": "12345",
            "photo": {"file_id": "file_1", "url": "http://example.com/a.jpg"},
        },
        credential_ref=credential_ref,
    )

    assert unsafe_button.valid is False
    assert any(issue.code == "secret_like" for issue in unsafe_button.issues)
    assert bad_photo.valid is False
    assert any(issue.code == "one_of" for issue in bad_photo.issues)


def test_telegram_polling_rejects_webhook_mode_without_override(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _telegram_credential_ref(
        session,
        project_id,
        config_json={"ingestion_mode": "webhook"},
    )

    with pytest.raises(ConflictError, match="action connector failed") as exc:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="communications.telegram-bot.updates.poll",
                input_json={"limit": 1, "timeout_s": 0, "allowed_updates": ["message"]},
                credential_ref=credential_ref,
            )
        )

    assert "webhook ingestion" in exc.value.data["error"]
    assert httpx_mock.get_requests() == []
