"""Telegram ingress route tests."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlmodel import Session

from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.projects import IntegrationCredentialRepository
from stackos.repositories.resources import ResourceRepository


def _store_telegram_bot_profile(
    api: TestClient,
    project_id: int,
    *,
    bot_profile_key: str = "support-bot",
    auth_profile_key: str = "support-credential",
    access_policy: dict | None = None,
    trigger_policy: dict | None = None,
    visibility_policy: dict | None = None,
    ingress_mode: str = "webhook",
) -> None:
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        IntegrationCredentialRepository(session).set(
            project_id=project_id,
            kind="telegram-bot",
            profile_key=auth_profile_key,
            secret_payload=json.dumps(
                {
                    "bot_token": "123456:ABC",
                    "webhook_secret_token": "telegram-secret",
                }
            ).encode("utf-8"),
            config_json={"api_base_url": "http://127.0.0.1:8081"},
        )
        ResourceRepository(session).upsert_record(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-bot-profile",
            external_id=f"telegram-bot-profile:{bot_profile_key}",
            title=bot_profile_key,
            data_json={
                "key": bot_profile_key,
                "provider_key": "telegram-bot",
                "auth_profile_key": auth_profile_key,
                "enabled": True,
                "bot_username": "support_bot",
                "ingress_mode": ingress_mode,
                "allowed_updates": ["message", "callback_query"],
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
                    "dm_mode": "all",
                    "group_mode": "all",
                    "user_mode": "allowlist",
                    "allowed_user_refs": ["telegram-user:555"],
                },
                "visibility_policy": visibility_policy or {"store_non_trigger_messages": True},
                "trigger_policy": trigger_policy
                or {
                    "dm_trigger": "always",
                    "group_trigger": "mention_or_command",
                    "commands": [
                        {
                            "command": "/support",
                            "description": "Handle a support request.",
                            "guidance": "Triage the request and reply with the next safe action.",
                        }
                    ],
                    "mention_patterns": ["@support_bot"],
                },
                "context_policy": {"include_last_messages": 50},
                "response_policy": {"reply_in_same_chat": True},
            },
            provenance_json={"source": "test"},
        )


def _store_outbound_button(
    api: TestClient,
    project_id: int,
    *,
    bot_profile_key: str = "support-bot",
    callback_data: str = "ixn_123",
    message_ref: str = "telegram-message:999:77",
    allowed_user_refs: list[str] | None = None,
    allowed_chat_refs: list[str] | None = None,
) -> None:
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        ResourceRepository(session).upsert_record(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-interaction",
            external_id=f"telegram-button:{bot_profile_key}:{message_ref}:{callback_data}",
            title="Review",
            data_json={
                "provider_key": "telegram-bot",
                "bot_profile_key": bot_profile_key,
                "interaction_type": "outbound_inline_button",
                "callback_data": callback_data,
                "message_ref": message_ref,
                "allowed_user_refs": allowed_user_refs or ["telegram-user:555"],
                "allowed_chat_refs": allowed_chat_refs or ["telegram-chat:999"],
                "status": "active",
            },
            provenance_json={"source": "test"},
        )


def _store_outbound_message(
    api: TestClient,
    project_id: int,
    *,
    bot_profile_key: str = "support-bot",
    chat_id: int = 999,
    message_id: int = 77,
) -> None:
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        ResourceRepository(session).upsert_record(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-message",
            external_id=f"telegram-message:{bot_profile_key}:{chat_id}:{message_id}",
            title="Telegram outbound message",
            data_json={
                "provider_key": "telegram-bot",
                "bot_profile_key": bot_profile_key,
                "direction": "outbound",
                "channel_ref": f"telegram-chat:{chat_id}",
                "message_ref": f"telegram-message:{chat_id}:{message_id}",
                "provider_message_id": str(message_id),
                "content_type": "text",
                "text_preview": "Review generated image?",
                "attention_status": "sent",
            },
            provenance_json={"source": "test"},
        )


def test_telegram_ingress_records_callback_and_creates_agent_request_without_bearer(
    api: TestClient,
    project_id: int,
) -> None:
    _store_telegram_bot_profile(api, project_id)
    _store_outbound_message(api, project_id)
    _store_outbound_button(api, project_id)
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
            f"/api/v1/ingress/telegram/{project_id}/support-bot",
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
            json=update,
        )
    finally:
        if original_auth is not None:
            api.headers["Authorization"] = original_auth

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["message_record_id"] is None
    assert body["interaction_record_id"] is not None
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        interactions = ResourceRepository(session).query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-interaction",
        )
        messages = ResourceRepository(session).query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-message",
        )
        requests = AgentRequestRepository(session).list(project_id=project_id)

    incoming = [
        item
        for item in interactions.items
        if item.data_json.get("interaction_type") == "inline_callback"
    ]
    outbound = [
        item
        for item in interactions.items
        if item.data_json.get("interaction_type") == "outbound_inline_button"
    ]
    assert len(incoming) == 1
    assert incoming[0].data_json["callback_query_id"] == "cbq_123"
    assert incoming[0].data_json["callback_data"] == "ixn_123"
    assert len(outbound) == 1
    assert outbound[0].data_json["status"] == "clicked"
    assert outbound[0].data_json["last_callback_query_id"] == "cbq_123"
    assert messages.total_estimate == 1
    assert messages.items[0].external_id == "telegram-message:support-bot:999:77"
    assert messages.items[0].data_json["direction"] == "outbound"
    assert messages.items[0].data_json["attention_status"] == "sent"
    assert requests.total_estimate == 1
    assert requests.items[0].request_key == "telegram-update:support-bot:123"
    assert requests.items[0].source_resource_key == "communication-interaction"
    assert requests.items[0].metadata_json["bot_profile_key"] == "support-bot"
    assert requests.items[0].metadata_json["identity"]["display_name"] == "Support Bot"
    assert (
        requests.items[0].metadata_json["agent_guidance"]["boundaries"] == "Do not expose secrets."
    )
    rendered = json.dumps(requests.items[0].model_dump(mode="json"))
    assert "telegram-secret" not in rendered
    assert "123456:ABC" not in rendered


def test_telegram_ingress_rejects_wrong_secret_without_writes(
    api: TestClient,
    project_id: int,
) -> None:
    _store_telegram_bot_profile(api, project_id)
    original_auth = api.headers.pop("Authorization", None)
    try:
        response = api.post(
            f"/api/v1/ingress/telegram/{project_id}/support-bot",
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


def test_telegram_ingress_rejects_disabled_ingress_profile_without_writes(
    api: TestClient,
    project_id: int,
) -> None:
    _store_telegram_bot_profile(
        api,
        project_id,
        bot_profile_key="disabled",
        ingress_mode="disabled",
    )
    original_auth = api.headers.pop("Authorization", None)
    try:
        response = api.post(
            f"/api/v1/ingress/telegram/{project_id}/disabled",
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
            json={"update_id": 457, "message": {"message_id": 1, "chat": {"id": 999}}},
        )
    finally:
        if original_auth is not None:
            api.headers["Authorization"] = original_auth

    assert response.status_code == 202
    assert response.json()["policy_status"] == "ingress_disabled"
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        requests = AgentRequestRepository(session).list(project_id=project_id)
    assert requests.total_estimate == 0


def test_telegram_ingress_rejects_disallowed_chat_without_writes(
    api: TestClient,
    project_id: int,
) -> None:
    _store_telegram_bot_profile(
        api,
        project_id,
        access_policy={
            "dm_mode": "allowlist",
            "group_mode": "allowlist",
            "user_mode": "allowlist",
            "allowed_chat_refs": ["telegram-chat:999"],
            "allowed_user_refs": ["telegram-user:555"],
        },
    )
    original_auth = api.headers.pop("Authorization", None)
    try:
        response = api.post(
            f"/api/v1/ingress/telegram/{project_id}/support-bot",
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

    assert response.status_code == 202
    assert response.json()["policy_status"] == "chat_blocked"
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        requests = AgentRequestRepository(session).list(project_id=project_id)
    assert requests.total_estimate == 0


def test_telegram_ingress_observes_allowed_group_message_without_triggering_request(
    api: TestClient,
    project_id: int,
) -> None:
    _store_telegram_bot_profile(api, project_id)
    original_auth = api.headers.pop("Authorization", None)
    try:
        response = api.post(
            f"/api/v1/ingress/telegram/{project_id}/support-bot",
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
            json={
                "update_id": 459,
                "message": {
                    "message_id": 11,
                    "chat": {"id": 999, "type": "supergroup", "title": "Ops"},
                    "from": {"id": 555, "username": "ada"},
                    "text": "general chatter",
                },
            },
        )
    finally:
        if original_auth is not None:
            api.headers["Authorization"] = original_auth

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["policy_status"] == "observed"
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
    assert messages.items[0].data_json["policy_status"] == "observed"
    assert requests.total_estimate == 0


def test_telegram_ingress_allowed_group_mention_creates_agent_request(
    api: TestClient,
    project_id: int,
) -> None:
    _store_telegram_bot_profile(api, project_id)
    original_auth = api.headers.pop("Authorization", None)
    try:
        response = api.post(
            f"/api/v1/ingress/telegram/{project_id}/support-bot",
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
            json={
                "update_id": 461,
                "message": {
                    "message_id": 13,
                    "chat": {"id": 999, "type": "supergroup", "title": "Ops"},
                    "from": {"id": 555, "username": "ada"},
                    "text": "@support_bot summarize the last incidents",
                },
            },
        )
    finally:
        if original_auth is not None:
            api.headers["Authorization"] = original_auth

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["policy_status"] == "request_created"
    assert body["agent_request_id"] is not None
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        requests = AgentRequestRepository(session).list(project_id=project_id)
    assert requests.total_estimate == 1
    assert requests.items[0].metadata_json["trigger_reason"] == "mention"
    assert requests.items[0].metadata_json["chat_ref"] == "telegram-chat:999"


def test_telegram_ingress_allows_dm_by_user_allowlist_without_chat_ref(
    api: TestClient,
    project_id: int,
) -> None:
    _store_telegram_bot_profile(
        api,
        project_id,
        access_policy={
            "dm_mode": "allowlist",
            "group_mode": "disabled",
            "user_mode": "allowlist",
            "allowed_user_refs": ["telegram-user:555"],
        },
    )
    original_auth = api.headers.pop("Authorization", None)
    try:
        response = api.post(
            f"/api/v1/ingress/telegram/{project_id}/support-bot",
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
            json={
                "update_id": 462,
                "message": {
                    "message_id": 14,
                    "chat": {"id": 555, "type": "private", "username": "ada"},
                    "from": {"id": 555, "username": "ada"},
                    "text": "Can you check the campaign?",
                },
            },
        )
    finally:
        if original_auth is not None:
            api.headers["Authorization"] = original_auth

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["policy_status"] == "request_created"
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        requests = AgentRequestRepository(session).list(project_id=project_id)
    assert requests.total_estimate == 1
    assert requests.items[0].metadata_json["trigger_reason"] == "dm"
    assert requests.items[0].metadata_json["invoker_ref"] == "telegram-user:555"


def test_telegram_ingress_non_trigger_can_be_no_store(
    api: TestClient,
    project_id: int,
) -> None:
    _store_telegram_bot_profile(
        api,
        project_id,
        visibility_policy={"store_non_trigger_messages": False},
    )
    original_auth = api.headers.pop("Authorization", None)
    try:
        response = api.post(
            f"/api/v1/ingress/telegram/{project_id}/support-bot",
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
            json={
                "update_id": 463,
                "message": {
                    "message_id": 15,
                    "chat": {"id": 999, "type": "supergroup", "title": "Ops"},
                    "from": {"id": 555, "username": "ada"},
                    "text": "general chatter",
                },
            },
        )
    finally:
        if original_auth is not None:
            api.headers["Authorization"] = original_auth

    assert response.status_code == 202, response.text
    assert response.json()["policy_status"] == "not_triggered"
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        messages = ResourceRepository(session).query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-message",
        )
        requests = AgentRequestRepository(session).list(project_id=project_id)
    assert messages.total_estimate == 0
    assert requests.total_estimate == 0


def test_telegram_ingress_blocks_disallowed_invoker_but_keeps_observed_context(
    api: TestClient,
    project_id: int,
) -> None:
    _store_telegram_bot_profile(api, project_id)
    original_auth = api.headers.pop("Authorization", None)
    try:
        response = api.post(
            f"/api/v1/ingress/telegram/{project_id}/support-bot",
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
            json={
                "update_id": 460,
                "message": {
                    "message_id": 12,
                    "chat": {"id": 999, "type": "supergroup", "title": "Ops"},
                    "from": {"id": 777, "username": "eve"},
                    "text": "@support_bot run this",
                },
            },
        )
    finally:
        if original_auth is not None:
            api.headers["Authorization"] = original_auth

    assert response.status_code == 202, response.text
    body = response.json()
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
    assert messages.items[0].data_json["from_ref"] == "telegram-user:777"
    assert messages.items[0].data_json["policy_status"] == "invoker_blocked"
    assert requests.total_estimate == 0


def test_telegram_ingress_blocks_callback_from_unissued_user(
    api: TestClient,
    project_id: int,
) -> None:
    _store_telegram_bot_profile(
        api,
        project_id,
        access_policy={
            "dm_mode": "allowlist",
            "group_mode": "allowlist",
            "user_mode": "all",
            "allowed_chat_refs": ["telegram-chat:999"],
        },
    )
    _store_outbound_button(api, project_id, allowed_user_refs=["telegram-user:555"])
    original_auth = api.headers.pop("Authorization", None)
    try:
        response = api.post(
            f"/api/v1/ingress/telegram/{project_id}/support-bot",
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
            json={
                "update_id": 464,
                "callback_query": {
                    "id": "cbq_wrong_user",
                    "data": "ixn_123",
                    "from": {"id": 777, "username": "eve"},
                    "message": {
                        "message_id": 77,
                        "chat": {"id": 999, "type": "supergroup", "title": "Ops"},
                        "text": "Review generated image?",
                    },
                },
            },
        )
    finally:
        if original_auth is not None:
            api.headers["Authorization"] = original_auth

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["policy_status"] == "callback_blocked"
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
        if item.data_json.get("interaction_type") == "outbound_inline_button"
    ]
    assert outbound[0].data_json["status"] == "active"
    assert requests.total_estimate == 0


def test_telegram_ingress_scopes_same_update_id_per_bot_profile(
    api: TestClient,
    project_id: int,
) -> None:
    _store_telegram_bot_profile(
        api,
        project_id,
        bot_profile_key="support-bot",
        auth_profile_key="support-credential",
    )
    _store_telegram_bot_profile(
        api,
        project_id,
        bot_profile_key="analytics-bot",
        auth_profile_key="analytics-credential",
    )
    original_auth = api.headers.pop("Authorization", None)
    try:
        for bot_profile_key in ("support-bot", "analytics-bot"):
            response = api.post(
                f"/api/v1/ingress/telegram/{project_id}/{bot_profile_key}",
                headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
                json={
                    "update_id": 465,
                    "message": {
                        "message_id": 16,
                        "chat": {"id": 999, "type": "private", "username": "ada"},
                        "from": {"id": 555, "username": "ada"},
                        "text": "hello",
                    },
                },
            )
            assert response.status_code == 202, response.text
            assert response.json()["policy_status"] == "request_created"
    finally:
        if original_auth is not None:
            api.headers["Authorization"] = original_auth

    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        requests = AgentRequestRepository(session).list(project_id=project_id)
        events = ResourceRepository(session).query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-event",
        )
    assert sorted(item.request_key for item in requests.items) == [
        "telegram-update:analytics-bot:465",
        "telegram-update:support-bot:465",
    ]
    assert events.total_estimate == 2


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
    _store_telegram_bot_profile(api, project_id)
    original_auth = api.headers.pop("Authorization", None)
    try:
        response = api.post(
            f"/api/v1/ingress/telegram/{project_id}/support-bot",
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
