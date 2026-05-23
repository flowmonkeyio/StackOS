"""Telegram ingress route tests."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlmodel import Session

from content_stack.repositories.agent_requests import AgentRequestRepository
from content_stack.repositories.projects import IntegrationCredentialRepository
from content_stack.repositories.resources import ResourceRepository


def _store_telegram_webhook_credential(api: TestClient, project_id: int) -> None:
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        IntegrationCredentialRepository(session).set(
            project_id=project_id,
            kind="telegram-bot",
            profile_key="main",
            secret_payload=json.dumps(
                {
                    "bot_token": "123456:ABC",
                    "webhook_secret_token": "telegram-secret",
                }
            ).encode("utf-8"),
            config_json={
                "ingestion_mode": "webhook",
                "allowed_updates": "message,callback_query",
                "allowed_chat_refs": "telegram-chat:999",
                "allowed_user_refs": "telegram-user:555",
            },
        )


def test_telegram_ingress_records_callback_and_creates_agent_request_without_bearer(
    api: TestClient,
    project_id: int,
) -> None:
    _store_telegram_webhook_credential(api, project_id)
    update = {
        "update_id": 123,
        "callback_query": {
            "id": "cbq_123",
            "data": "ixn_123",
            "from": {"id": 555, "username": "ada"},
            "message": {
                "message_id": 77,
                "date": 1_779_526_000,
                "chat": {"id": 999, "type": "private", "username": "ada"},
                "text": "Review generated image?",
            },
        },
    }
    original_auth = api.headers.pop("Authorization", None)
    try:
        response = api.post(
            f"/api/v1/ingress/telegram/{project_id}/main",
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
            json=update,
        )
    finally:
        if original_auth is not None:
            api.headers["Authorization"] = original_auth

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["message_record_id"] is not None
    assert body["interaction_record_id"] is not None
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        interactions = ResourceRepository(session).query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-interaction",
        )
        requests = AgentRequestRepository(session).list(project_id=project_id)

    assert interactions.total_estimate == 1
    assert interactions.items[0].data_json["callback_query_id"] == "cbq_123"
    assert interactions.items[0].data_json["callback_data"] == "ixn_123"
    assert requests.total_estimate == 1
    assert requests.items[0].request_key == "telegram-update:123"
    assert requests.items[0].source_resource_key == "communication-interaction"
    rendered = json.dumps(requests.items[0].model_dump(mode="json"))
    assert "telegram-secret" not in rendered
    assert "123456:ABC" not in rendered


def test_telegram_ingress_rejects_wrong_secret_without_writes(
    api: TestClient,
    project_id: int,
) -> None:
    _store_telegram_webhook_credential(api, project_id)
    original_auth = api.headers.pop("Authorization", None)
    try:
        response = api.post(
            f"/api/v1/ingress/telegram/{project_id}/main",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            json={"update_id": 456, "message": {"message_id": 1, "chat": {"id": 999}}},
        )
    finally:
        if original_auth is not None:
            api.headers["Authorization"] = original_auth

    assert response.status_code == 403
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        requests = AgentRequestRepository(session).list(project_id=project_id)
    assert requests.total_estimate == 0


def test_telegram_ingress_rejects_polling_mode_profile_without_writes(
    api: TestClient,
    project_id: int,
) -> None:
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        IntegrationCredentialRepository(session).set(
            project_id=project_id,
            kind="telegram-bot",
            profile_key="polling",
            secret_payload=json.dumps(
                {
                    "bot_token": "123456:ABC",
                    "webhook_secret_token": "telegram-secret",
                }
            ).encode("utf-8"),
            config_json={"ingestion_mode": "polling"},
        )
    original_auth = api.headers.pop("Authorization", None)
    try:
        response = api.post(
            f"/api/v1/ingress/telegram/{project_id}/polling",
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
            json={"update_id": 457, "message": {"message_id": 1, "chat": {"id": 999}}},
        )
    finally:
        if original_auth is not None:
            api.headers["Authorization"] = original_auth

    assert response.status_code == 403
    assert response.json()["detail"] == "Telegram ingress disabled"
    with Session(engine) as session:
        requests = AgentRequestRepository(session).list(project_id=project_id)
    assert requests.total_estimate == 0


def test_telegram_ingress_rejects_disallowed_chat_without_writes(
    api: TestClient,
    project_id: int,
) -> None:
    _store_telegram_webhook_credential(api, project_id)
    original_auth = api.headers.pop("Authorization", None)
    try:
        response = api.post(
            f"/api/v1/ingress/telegram/{project_id}/main",
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
            json={
                "update_id": 458,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 111},
                    "from": {"id": 555},
                    "text": "nope",
                },
            },
        )
    finally:
        if original_auth is not None:
            api.headers["Authorization"] = original_auth

    assert response.status_code == 403
    assert response.json()["detail"] == "Telegram chat blocked"
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        requests = AgentRequestRepository(session).list(project_id=project_id)
    assert requests.total_estimate == 0


def test_telegram_ingress_rejects_unknown_profile_without_leaking_lookup(
    api: TestClient,
    project_id: int,
) -> None:
    original_auth = api.headers.pop("Authorization", None)
    try:
        response = api.post(
            f"/api/v1/ingress/telegram/{project_id}/missing",
            headers={"X-Telegram-Bot-Api-Secret-Token": "anything"},
            json={"update_id": 789, "message": {"message_id": 1, "chat": {"id": 999}}},
        )
    finally:
        if original_auth is not None:
            api.headers["Authorization"] = original_auth

    assert response.status_code == 403
    assert response.json()["detail"] == "invalid Telegram secret"


def test_telegram_ingress_redacts_secret_like_inbound_callback_data(
    api: TestClient,
    project_id: int,
) -> None:
    _store_telegram_webhook_credential(api, project_id)
    original_auth = api.headers.pop("Authorization", None)
    try:
        response = api.post(
            f"/api/v1/ingress/telegram/{project_id}/main",
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
            json={
                "update_id": 900,
                "callback_query": {
                    "id": "cbq_secret",
                    "data": "api_key=secret-value",
                    "from": {"id": 555},
                    "message": {"message_id": 1, "chat": {"id": 999}},
                },
            },
        )
    finally:
        if original_auth is not None:
            api.headers["Authorization"] = original_auth

    assert response.status_code == 202, response.text
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        interactions = ResourceRepository(session).query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-interaction",
        )
        requests = AgentRequestRepository(session).list(project_id=project_id)

    rendered = json.dumps(
        {
            "interactions": [item.model_dump(mode="json") for item in interactions.items],
            "requests": [item.model_dump(mode="json") for item in requests.items],
        }
    )
    assert "secret-value" not in rendered
    assert "api_key=[redacted]" in rendered
