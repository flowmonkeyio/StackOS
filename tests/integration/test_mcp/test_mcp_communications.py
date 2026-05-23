"""MCP parity tests for communication setup operations."""

from __future__ import annotations

import json

from sqlmodel import Session

from content_stack.repositories.projects import IntegrationCredentialRepository

from .conftest import MCPClient


def _seed_telegram_credential(
    mcp: MCPClient,
    project_id: int,
    *,
    profile_key: str = "support",
) -> None:
    engine = mcp.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        IntegrationCredentialRepository(session).set(
            project_id=project_id,
            kind="telegram-bot",
            profile_key=profile_key,
            secret_payload=json.dumps(
                {"bot_token": "123456:ABC", "webhook_secret_token": "telegram-secret"}
            ).encode("utf-8"),
        )


def test_communication_bot_profile_operations_are_registered(mcp_client: MCPClient) -> None:
    tools = {tool["name"] for tool in mcp_client.list_tools()}

    assert {
        "communicationBotProfile.list",
        "communicationBotProfile.get",
        "communicationBotProfile.upsert",
    } <= tools


def test_communication_bot_profile_mcp_lifecycle_has_no_secret_roundtrip(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _seed_telegram_credential(mcp_client, project_id)

    created = mcp_client.call_tool_structured(
        "communicationBotProfile.upsert",
        {
            "project_id": project_id,
            "key": "support-bot",
            "auth_profile_key": "support",
            "bot_username": "support_bot",
            "identity": {
                "display_name": "Support Bot",
                "purpose": "Handle support requests from approved Telegram users.",
                "voice": "Concise and calm.",
            },
            "agent_guidance": {
                "default_instructions": "Triage support requests before replying.",
                "boundaries": "Do not expose secrets.",
            },
            "access_policy": {
                "dm_mode": "allowlist",
                "group_mode": "allowlist",
                "user_mode": "allowlist",
                "allowed_chat_refs": ["telegram-chat:999"],
                "allowed_user_refs": ["telegram-user:555"],
            },
            "reply_to_message_refs": {"telegram-message:999:88": 88},
            "thread_refs": {"telegram-thread:999:default": 1},
            "direct_messages_topic_refs": {"telegram-dm-topic:999:555": 22},
        },
    )
    assert created["data"]["key"] == "support-bot"
    assert created["data"]["auth_profile_key"] == "support"
    assert created["data"]["identity"]["display_name"] == "Support Bot"
    assert created["data"]["reply_to_message_refs"] == {"telegram-message:999:88": 88}

    fetched = mcp_client.call_tool_structured(
        "communicationBotProfile.get",
        {"project_id": project_id, "key": "support-bot"},
    )
    listed = mcp_client.call_tool_structured(
        "communicationBotProfile.list",
        {"project_id": project_id},
    )

    assert fetched["key"] == "support-bot"
    assert fetched["thread_refs"] == {"telegram-thread:999:default": 1}
    assert [item["key"] for item in listed["items"]] == ["support-bot"]
    rendered = json.dumps({"created": created, "fetched": fetched, "listed": listed})
    assert "123456:ABC" not in rendered
    assert "telegram-secret" not in rendered


def test_communication_bot_profile_mcp_requires_project_scoped_credential(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])

    err = mcp_client.call_tool_error(
        "communicationBotProfile.upsert",
        {
            "project_id": project_id,
            "key": "missing-credential",
            "auth_profile_key": "missing",
            "identity": {
                "display_name": "Missing Bot",
                "purpose": "Exercise credential validation.",
                "voice": "Concise.",
            },
            "access_policy": {
                "dm_mode": "allowlist",
                "group_mode": "allowlist",
                "user_mode": "allowlist",
                "allowed_chat_refs": ["telegram-chat:999"],
                "allowed_user_refs": ["telegram-user:555"],
            },
        },
    )

    assert err["code"] == -32602
    assert err["data"]["auth_profile_key"] == "missing"
