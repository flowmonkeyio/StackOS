"""End-to-end Telegram setup, ingress, run-plan, and action route slice."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from pytest_httpx import HTTPXMock
from sqlmodel import Session

from stackos.repositories.resources import ResourceRepository


def _telegram_reply_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "communications.telegram.reply.run",
        "title": "Reply to Telegram request",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "reply",
                    "tool": "action.execute",
                    "action_refs": ["communications.telegram-bot.message.send"],
                }
            ]
        },
        "steps": [
            {
                "id": "reply",
                "title": "Send Telegram reply",
                "action_refs": ["communications.telegram-bot.message.send"],
            }
        ],
    }


def _store_credential(api: TestClient, project_id: int) -> str:
    response = api.post(
        f"/api/v1/projects/{project_id}/auth/telegram-bot/credentials",
        json={
            "auth_method_key": "bot-token",
            "profile_key": "support",
            "label": "Support Bot",
            "fields": {
                "bot_token": "123456:ABC",
                "webhook_secret_token": "telegram-secret",
            },
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["data"]["credential_ref"])


def _post_without_daemon_auth(
    api: TestClient,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict,
) -> object:
    original_auth = api.headers.pop("Authorization", None)
    try:
        return api.post(url, headers=headers, json=json_body)
    finally:
        if original_auth is not None:
            api.headers["Authorization"] = original_auth


def test_telegram_setup_ingress_claim_link_and_reply_action(
    api: TestClient,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _store_credential(api, project_id)

    profile = api.post(
        "/api/v1/operations/communicationProfile.upsert/call",
        json={
            "arguments": {
                "project_id": project_id,
                "key": "support-bot",
                "identity": {
                    "display_name": "Support Bot",
                    "purpose": "Handle support requests from approved Telegram users.",
                    "voice": "Concise and calm.",
                },
                "agent_guidance": {
                    "default_instructions": (
                        "Use StackOS context before replying to support requests."
                    ),
                    "boundaries": "Do not mutate external systems unless the run plan grants it.",
                },
                "access_policy": {
                    "dm_mode": "all",
                    "group_mode": "all",
                    "user_mode": "allowlist",
                    "allowed_chat_refs": ["telegram-chat:999"],
                    "allowed_user_refs": ["telegram-user:555"],
                },
                "trigger_policy": {
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
                    "reply_to_bot_triggers": True,
                },
                "visibility_policy": {"store_non_trigger_messages": True},
                "response_policy": {
                    "reply_in_same_chat": True,
                    "origin_required": True,
                    "reply_to_source_message": True,
                    "same_thread": True,
                },
                "provider_facets": {
                    "telegram-bot": {
                        "auth_profile_key": "support",
                        "bot_username": "support_bot",
                        "ingress_mode": "webhook",
                        "allowed_updates": ["message", "callback_query"],
                        "reply_to_message_refs": {"telegram-message:999:88": 88},
                        "thread_refs": {"telegram-thread:999:default": 1},
                    }
                },
            }
        },
    )
    assert profile.status_code == 200, profile.text

    ingress = _post_without_daemon_auth(
        api,
        f"/api/v1/ingress/telegram/{project_id}/support-bot",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json_body={
            "update_id": 901,
            "message": {
                "message_id": 88,
                "date": 1_779_526_000,
                "from": {"id": 555, "username": "ada"},
                "chat": {"id": 999, "type": "private", "username": "ada"},
                "text": "/support check media buying results",
            },
        },
    )
    assert ingress.status_code == 202, ingress.text
    agent_request_id = int(ingress.json()["agent_request_id"])

    prepared = api.post(
        "/api/v1/operations/agentRequest.prepareRunPlan/call",
        json={
            "arguments": {
                "project_id": project_id,
                "request_id": agent_request_id,
                "claimed_by": "codex",
                "idempotency_key": "prepare-telegram-setup-to-action",
                "run_plan_json": _telegram_reply_plan_json(),
                "response_mode": "raw",
            }
        },
    )
    assert prepared.status_code == 200, prepared.text
    claim_token = prepared.json()["data"]["claim_token"]
    assert claim_token
    run_plan_id = int(prepared.json()["data"]["run_plan"]["id"])
    assert prepared.json()["data"]["request"]["run_plan_id"] == run_plan_id

    started = api.post(
        "/api/v1/operations/runPlan.start/call",
        json={"arguments": {"project_id": project_id, "run_plan_id": run_plan_id}},
    )
    assert started.status_code == 200, started.text
    run_token = started.json()["data"]["run_token"]
    run_id = int(started.json()["data"]["run_id"])

    claimed_step = api.post(
        "/api/v1/operations/runPlan.claimStep/call",
        json={
            "arguments": {
                "run_plan_id": run_plan_id,
                "step_id": "reply",
                "run_token": run_token,
            }
        },
    )
    assert claimed_step.status_code == 200, claimed_step.text
    step_id = int(claimed_step.json()["data"]["id"])

    httpx_mock.add_response(
        method="POST",
        url="https://api.telegram.org/bot123456:ABC/sendMessage",
        json={"ok": True, "result": {"message_id": 89, "chat": {"id": 999}}},
    )
    executed = api.post(
        "/api/v1/operations/action.execute/call",
        json={
            "arguments": {
                "project_id": project_id,
                "run_token": run_token,
                "credential_ref": credential_ref,
                "action_ref": "communications.telegram-bot.message.send",
                "input_json": {
                    "profile_key": "support-bot",
                    "chat_ref": "telegram-chat:999",
                    "source_agent_request_id": agent_request_id,
                    "reply_to_message_ref": "telegram-message:999:88",
                    "thread_ref": "telegram-thread:999:default",
                    "text": "Queued the campaign review.",
                    "reply_markup": {
                        "inline_keyboard": [[{"text": "Mark done", "callback_data": "done_89"}]]
                    },
                },
                "output_policy_json": {"mode": "inline"},
                "response_mode": "raw",
            }
        },
    )
    assert executed.status_code == 200, executed.text
    body = executed.json()["data"]
    provider_body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    rendered = json.dumps(executed.json())

    assert provider_body["chat_id"] == 999
    assert provider_body["reply_to_message_id"] == 88
    assert provider_body["message_thread_id"] == 1
    assert provider_body["text"] == "Queued the campaign review."
    assert body["action_call"]["run_id"] == run_id
    assert body["action_call"]["run_plan_id"] == run_plan_id
    assert body["action_call"]["run_plan_step_id"] == step_id
    assert body["action_call"]["provider_key"] == "telegram-bot"
    assert body["output_json"]["body"]["result"]["message_id"] == 89
    assert "123456:ABC" not in rendered
    assert "telegram-secret" not in rendered

    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        messages = ResourceRepository(session).query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-message",
        )
        interactions = ResourceRepository(session).query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-interaction",
        )

    outbound = [
        item
        for item in messages.items
        if item.data_json.get("direction") == "outbound"
        and item.data_json.get("source_agent_request_id") == agent_request_id
    ]
    buttons = [
        item for item in interactions.items if item.data_json.get("callback_data") == "done_89"
    ]
    assert len(outbound) == 1
    assert outbound[0].data_json["message_ref"] == "telegram-message:999:89"
    assert len(buttons) == 1
    assert buttons[0].data_json["allowed_user_refs"] == ["telegram-user:555"]
