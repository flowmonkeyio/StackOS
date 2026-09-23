"""Telegram native ingress through one shared reply and durable receipt lifecycle."""

from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from stackos.actions.telegram import TelegramActionConnector
from stackos.auth_providers import AuthRepository
from stackos.db.models import ActionCall, Credential
from stackos.integrations.telegram_tdlib.updates import process_telegram_update
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.resources import ResourceRepository
from tests.integration.test_repositories.test_telegram_actions import FakeTelegram


def _reply_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "communications.telegram.reply.run",
        "title": "Reply to Telegram request",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "reply",
                    "tool": "communication.reply",
                    "sources": ["telegram"],
                }
            ]
        },
        "steps": [{"id": "reply", "title": "Send Telegram reply"}],
    }


def _store_connected_telegram_account(api: TestClient, project_id: int) -> tuple[str, FakeTelegram]:
    """Seed a verified Account and replace only the typed native action adapter."""
    runtime = FakeTelegram()
    services = api.app.state.operation_services  # type: ignore[attr-defined]
    services["action_connectors"].register(TelegramActionConnector(runtime))
    with Session(api.app.state.engine) as session:  # type: ignore[attr-defined]
        credential_ref = (
            AuthRepository(session)
            .store_credential(
                provider_key="telegram",
                auth_method_key="tdlib-bot-token",
                display_name="Telegram support bot",
                fields={
                    "api_id": 12345,
                    "api_hash": "test-telegram-application-hash",
                    "bot_token": "123456:bot-token",
                    "proxy_enabled": False,
                },
                attach_project_id=project_id,
            )
            .data.credential_ref
        )
        credential = session.exec(
            select(Credential).where(Credential.credential_ref == credential_ref)
        ).one()
        credential.status = "connected"
        session.add(credential)
        session.commit()
    return credential_ref, runtime


def test_telegram_native_ingress_claim_link_and_durable_reply(
    api: TestClient,
    project_id: int,
) -> None:
    credential_ref, native = _store_connected_telegram_account(api, project_id)

    profile = api.post(
        "/api/v1/operations/communicationProfile.upsert/call",
        json={
            "arguments": {
                "project_id": project_id,
                "key": "support-bot",
                "identity": {
                    "display_name": "Support Bot",
                    "purpose": "Handle support requests from approved Telegram users.",
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
                            "guidance": "Triage the request and return the next safe action.",
                        }
                    ],
                },
                "visibility_policy": {
                    "surface_mode": "allowlist",
                    "allowed_surface_refs": ["telegram-chat:999"],
                    "allowed_update_types": ["updateNewMessage"],
                    "store_non_trigger_messages": True,
                },
                "response_policy": {
                    "reply_in_same_chat": True,
                    "origin_required": True,
                    "reply_to_source_message": True,
                    "same_thread": True,
                },
                "provider_facets": {"telegram": {"credential_ref": credential_ref}},
            }
        },
    )
    assert profile.status_code == 200, profile.text

    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        ingress = process_telegram_update(
            session,
            credential_ref=credential_ref,
            update={
                "@type": "updateNewMessage",
                "message": {
                    "@type": "message",
                    "id": 88,
                    "chat_id": 999,
                    "date": 1_779_526_000,
                    "sender_id": {"@type": "messageSenderUser", "user_id": 555},
                    "content": {
                        "@type": "messageText",
                        "text": {"text": "/support check media buying results"},
                    },
                },
            },
        )
        assert ingress[0].policy_status == "request_created"
        assert ingress[0].agent_request_id is not None
        request = AgentRequestRepository(session).get(
            project_id=project_id, request_id=ingress[0].agent_request_id
        )

    prepared = api.post(
        "/api/v1/operations/agentRequest.prepareRunPlan/call",
        json={
            "arguments": {
                "project_id": project_id,
                "request_id": request.id,
                "claimed_by": "codex",
                "idempotency_key": "prepare-native-telegram-reply",
                "run_plan_json": _reply_plan_json(),
                "response_mode": "raw",
            }
        },
    )
    assert prepared.status_code == 200, prepared.text
    run_plan_id = int(prepared.json()["data"]["run_plan"]["id"])

    started = api.post(
        "/api/v1/operations/runPlan.start/call",
        json={"arguments": {"project_id": project_id, "run_plan_id": run_plan_id}},
    )
    assert started.status_code == 200, started.text
    run_token = started.json()["data"]["run_token"]
    run_id = int(started.json()["data"]["run_id"])
    claimed = api.post(
        "/api/v1/operations/runPlan.claimStep/call",
        json={
            "arguments": {"run_plan_id": run_plan_id, "step_id": "reply", "run_token": run_token}
        },
    )
    assert claimed.status_code == 200, claimed.text

    accepted = api.post(
        "/api/v1/operations/communication.reply/call",
        json={
            "arguments": {
                "project_id": project_id,
                "request_id": request.id,
                "text": "Queued the campaign review.",
                "controls": [{"type": "button", "label": "Mark done", "value": "done_89"}],
                "run_token": run_token,
            }
        },
    )
    assert accepted.status_code == 200, accepted.text
    receipt = accepted.json()["data"]
    action_call_id = int(receipt["action_call_id"])
    assert receipt["status"] == "running"
    assert receipt["poll_operation"] == "actionCall.get"
    assert receipt["action_call"]["run_id"] == run_id
    assert receipt["action_call"]["run_plan_id"] == run_plan_id
    assert receipt["action_call"]["run_plan_step_id"] == claimed.json()["data"]["id"]

    # The daemon loop owns normal execution. A direct test dispatch makes the
    # final receipt deterministic without bypassing the ActionCall lease.
    asyncio.run(api.app.state.operation_services["durable_dispatcher"].run_once())  # type: ignore[attr-defined]
    deadline = time.monotonic() + 2
    while True:
        polled = api.post(
            "/api/v1/operations/actionCall.get/call",
            json={"arguments": {"project_id": project_id, "action_call_id": action_call_id}},
        )
        assert polled.status_code == 200, polled.text
        if polled.json()["status"] != "running":
            break
        assert time.monotonic() < deadline, polled.text
        time.sleep(0.02)

    assert polled.json()["status"] == "success"
    assert any(call["@type"] == "sendMessage" for call in native.calls)
    with Session(engine) as session:
        action_call = session.get(ActionCall, action_call_id)
        assert action_call is not None
        assert action_call.run_id == run_id
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
        and item.data_json.get("source_agent_request_id") == request.id
    ]
    assert len(outbound) == 1
    assert outbound[0].data_json["message_ref"] == "telegram-message:999:1048576"
    assert any(item.data_json.get("callback_data") == "done_89" for item in interactions.items)
