"""MCP parity tests for communication setup operations."""

from __future__ import annotations

import json

from sqlmodel import Session

from stackos.db.models import ActionCall
from stackos.operations import communication_platform
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.projects import IntegrationCredentialRepository
from stackos.repositories.resources import ResourceRepository

from .conftest import MCPClient


def _seed_telegram_credential(
    mcp: MCPClient,
    project_id: int,
    *,
    profile_key: str = "support",
    bot_token: str = "123456:ABC",
) -> None:
    engine = mcp.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        IntegrationCredentialRepository(session).set(
            project_id=project_id,
            kind="telegram-bot",
            profile_key=profile_key,
            secret_payload=json.dumps(
                {"bot_token": bot_token, "webhook_secret_token": "telegram-secret"}
            ).encode("utf-8"),
        )


def _seed_smtp_credential(mcp: MCPClient, project_id: int, *, profile_key: str = "primary") -> None:
    engine = mcp.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        IntegrationCredentialRepository(session).set(
            project_id=project_id,
            kind="smtp",
            profile_key=profile_key,
            secret_payload=json.dumps({"password": "smtp-secret"}).encode("utf-8"),
            config_json={
                "auth_method_key": "smtp-password",
                "profile_key": profile_key,
                "host": "smtp.example.test",
                "port": 587,
                "tls_mode": "none",
                "username": "mailer@example.test",
                "from_email": "mailer@example.test",
            },
        )


def _seed_slack_credential(
    mcp: MCPClient, project_id: int, *, profile_key: str = "default"
) -> None:
    engine = mcp.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        IntegrationCredentialRepository(session).set(
            project_id=project_id,
            kind="slack-bot",
            profile_key=profile_key,
            secret_payload=json.dumps({"bot_token": "xoxb-test-token"}).encode("utf-8"),
            config_json={"auth_method_key": "bot-token", "profile_key": profile_key},
        )


def _credential_ref(
    mcp: MCPClient,
    *,
    project_id: int,
    provider_key: str,
    profile_key: str,
) -> str:
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": provider_key},
    )
    for connection in status["connections"]:
        if connection["profile_key"] == profile_key:
            return str(connection["credential_ref"])
    raise AssertionError(f"credential profile {profile_key!r} not found")


class _FakeNgrokResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "endpoints": [
                {
                    "name": "stackos",
                    "url": "https://stackos-local.ngrok.app",
                }
            ]
        }


class _FakeNgrokClient:
    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> _FakeNgrokClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str) -> _FakeNgrokResponse:
        assert url == "http://127.0.0.1:4040/api/endpoints"
        return _FakeNgrokResponse()


def test_communication_profile_operations_are_registered(mcp_client: MCPClient) -> None:
    tools = {tool["name"] for tool in mcp_client.list_tools()}

    assert {
        "ingressEndpoint.configure",
        "ingressEndpoint.refresh",
        "ingressEndpoint.routes",
        "ingressEndpoint.sync",
        "ingressEndpoint.status",
        "localAgentChat.createMessage",
        "communication.send",
        "communication.reply",
        "communicationProfile.list",
        "communicationProfile.get",
        "communicationProfile.upsert",
        "communicationSurface.list",
        "communicationSurface.upsert",
        "communicationContact.list",
        "communicationContact.upsert",
        "communicationMembership.list",
        "communicationMembership.upsert",
        "communicationTarget.list",
        "communicationTarget.resolve",
        "communicationTarget.upsert",
        "communicationRoute.list",
        "communicationRoute.upsert",
        "communicationContext.query",
        "toolProfile.resolve",
    } <= tools


def test_ingress_endpoint_mcp_derives_and_syncs_provider_routes(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _seed_telegram_credential(mcp_client, project_id)

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support",
            "identity": {"display_name": "Support Agent"},
            "provider_facets": {
                "slack-bot": {"auth_profile_key": "default", "bot_user_id": "U123"},
            },
        },
    )
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support-bot",
            "identity": {"display_name": "Support Telegram Bot"},
            "provider_facets": {"telegram-bot": {"auth_profile_key": "support"}},
            "access_policy": {
                "dm_mode": "all",
                "group_mode": "all",
                "user_mode": "all",
            },
        },
    )

    configured = mcp_client.call_tool_structured(
        "ingressEndpoint.configure",
        {
            "project_id": project_id,
            "driver": "public-url",
            "public_base_url": "https://stackos.example.com",
        },
    )
    assert configured["data"]["key"] == "default"

    routes = mcp_client.call_tool_structured(
        "ingressEndpoint.routes",
        {"project_id": project_id},
    )
    assert routes["endpoint"]["public_base_url"] == "https://stackos.example.com"
    assert routes["endpoint"]["driver"] == "public-url"
    assert routes["endpoint"]["driver_config"] == {}
    route_urls = {route["provider_key"]: route["ingress_url"] for route in routes["routes"]}
    assert route_urls["slack-bot"] == (
        f"https://stackos.example.com/api/v1/ingress/slack/{project_id}/support"
    )
    assert route_urls["telegram-bot"] == (
        f"https://stackos.example.com/api/v1/ingress/telegram/{project_id}/support-bot"
    )
    telegram_routes = [
        route for route in routes["routes"] if route["provider_key"] == "telegram-bot"
    ]
    assert len(telegram_routes) == 1
    assert telegram_routes[0]["profile_resource_key"] == "communication-profile"

    synced = mcp_client.call_tool_structured(
        "ingressEndpoint.sync",
        {
            "project_id": project_id,
            "apply_provider_webhooks": False,
        },
    )
    statuses = {
        (result["provider_key"], result["profile_key"]): result["status"]
        for result in synced["data"]["provider_results"]
    }
    assert statuses[("slack-bot", "support")] == "manual_provider_update_required"
    assert statuses[("telegram-bot", "support-bot")] == "profile_updated"
    assert synced["data"]["endpoint"]["last_synced_at"]

    profile = mcp_client.call_tool_structured(
        "communicationProfile.get",
        {"project_id": project_id, "key": "support"},
    )
    assert (
        profile["provider_facets"]["slack-bot"]["ingress_url"]
        == f"https://stackos.example.com/api/v1/ingress/slack/{project_id}/support"
    )

    bot = mcp_client.call_tool_structured(
        "communicationProfile.get",
        {"project_id": project_id, "key": "support-bot"},
    )
    telegram_facet = bot["provider_facets"]["telegram-bot"]
    assert telegram_facet["ingress_mode"] == "webhook"
    assert telegram_facet["webhook_base_url"] == "https://stackos.example.com"
    assert telegram_facet["refs"]["ingress_url"] == (
        f"https://stackos.example.com/api/v1/ingress/telegram/{project_id}/support-bot"
    )
    assert telegram_facet["allowed_webhook_hosts"] == ["stackos.example.com"]

    refreshed = mcp_client.call_tool_structured(
        "ingressEndpoint.refresh",
        {
            "project_id": project_id,
            "public_base_url": "https://fresh.stackos.example.com",
            "sync_profiles": True,
        },
    )
    assert refreshed["data"]["endpoint"]["public_base_url"] == "https://fresh.stackos.example.com"
    assert refreshed["data"]["endpoint"]["driver"] == "public-url"

    refreshed_bot = mcp_client.call_tool_structured(
        "communicationProfile.get",
        {"project_id": project_id, "key": "support-bot"},
    )
    refreshed_telegram_facet = refreshed_bot["provider_facets"]["telegram-bot"]
    assert refreshed_telegram_facet["webhook_base_url"] == "https://fresh.stackos.example.com"
    assert refreshed_telegram_facet["refs"]["ingress_url"] == (
        f"https://fresh.stackos.example.com/api/v1/ingress/telegram/{project_id}/support-bot"
    )
    assert refreshed_telegram_facet["allowed_webhook_hosts"] == [
        "fresh.stackos.example.com",
        "stackos.example.com",
    ]


def test_ingress_endpoint_refresh_discovers_ngrok_agent_endpoints(
    mcp_client: MCPClient,
    seeded_project: dict,
    monkeypatch,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    monkeypatch.setattr(communication_platform.httpx, "AsyncClient", _FakeNgrokClient)

    configured = mcp_client.call_tool_structured(
        "ingressEndpoint.configure",
        {
            "project_id": project_id,
            "driver": "local-tunnel",
            "driver_config": {"provider": "ngrok"},
        },
    )
    assert configured["data"]["driver"] == "local-tunnel"
    assert configured["data"]["driver_config"]["provider"] == "ngrok"
    assert configured["data"]["driver_config"]["discovery_url"] == (
        "http://127.0.0.1:4040/api/endpoints"
    )

    refreshed = mcp_client.call_tool_structured(
        "ingressEndpoint.refresh",
        {"project_id": project_id, "sync_profiles": False},
    )
    endpoint = refreshed["data"]["endpoint"]
    assert endpoint["public_base_url"] == "https://stackos-local.ngrok.app"
    assert endpoint["metadata_json"]["last_refresh"]["resource"] == "endpoints"


def test_provider_neutral_communication_setup_resolves_targets_and_context(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])

    profile = mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support",
            "identity": {
                "display_name": "Support Agent",
                "purpose": "Coordinate customer issues across chat surfaces.",
            },
            "provider_facets": {
                "telegram-bot": {"auth_profile_key": "support"},
                "slack-bot": {"bot_user_id": "U123"},
            },
            "send_policy": {
                "mode": "explicit-targets",
                "allowed_target_refs": ["communication-target:internal-support"],
            },
        },
    )
    surface = mcp_client.call_tool_structured(
        "communicationSurface.upsert",
        {
            "project_id": project_id,
            "surface_ref": "slack-channel:C123",
            "provider_key": "slack-bot",
            "kind": "slack-channel",
            "display_name": "internal-support",
            "capabilities": {"can_read": True, "can_write": True, "can_thread": True},
            "audience": "internal",
            "intent": {
                "category": "support-operations",
                "summary": "Internal operators coordinate customer support issues here.",
            },
            "agent_guidance": {
                "default_instructions": (
                    "Internal coordination surface; do not quote it to customers."
                ),
            },
            "data_scope": {
                "classification": "internal",
                "allowed_share_refs": ["communication-target:internal-support"],
                "restricted_topics": ["secrets"],
            },
            "external_context": {
                "customer": {
                    "safe_ref": "customer:acme",
                    "crm_account_id": "crm-account-123",
                    "primary_email": "ops@acme.example",
                }
            },
        },
    )
    contact = mcp_client.call_tool_structured(
        "communicationContact.upsert",
        {
            "project_id": project_id,
            "key": "customer-acme",
            "display_name": "Acme Inc.",
            "kind": "organization",
            "provider_refs": {
                "telegram": ["telegram-chat:-1001"],
                "slack": ["slack-channel:C123"],
            },
        },
    )
    membership = mcp_client.call_tool_structured(
        "communicationMembership.upsert",
        {
            "project_id": project_id,
            "surface_ref": "slack-channel:C123",
            "member_ref": "communication-profile:support",
            "provider_key": "slack-bot",
            "membership_kind": "profile",
            "status": "joined",
            "roles": ["bot"],
            "permissions": {"can_read": True, "can_write": True},
        },
    )
    target = mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "internal-support",
            "display_name": "Internal support",
            "provider_key": "slack-bot",
            "surface_ref": "slack-channel:C123",
            "action_input_defaults": {"channel_ref": "slack-channel:C123"},
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": ["communication-profile:support"],
                "allowed_source_surface_refs": ["telegram-chat:-1001"],
                "allowed_invoker_refs": ["telegram-user:555"],
                "allowed_target_refs": ["communication-target:internal-support"],
            },
        },
    )
    route = mcp_client.call_tool_structured(
        "communicationRoute.upsert",
        {
            "project_id": project_id,
            "key": "customer-issue-to-internal-support",
            "source_surface_refs": ["telegram-chat:-1001"],
            "target_refs": ["communication-target:internal-support"],
            "allowed_profile_refs": ["communication-profile:support"],
            "requires_approval": False,
        },
    )

    assert profile["data"]["profile_ref"] == "communication-profile:support"
    assert surface["data"]["surface_ref"] == "slack-channel:C123"
    assert surface["data"]["audience"] == "internal"
    assert surface["data"]["intent"]["category"] == "support-operations"
    assert surface["data"]["data_scope"]["classification"] == "internal"
    assert surface["data"]["external_context"]["customer"]["safe_ref"] == "customer:acme"
    assert contact["data"]["provider_refs"]["telegram"] == ["telegram-chat:-1001"]
    assert membership["data"]["permissions"]["can_write"] is True
    assert target["data"]["action_ref"] == "communications.slack-bot.message.send"
    assert route["data"]["target_refs"] == ["communication-target:internal-support"]

    allowed = mcp_client.call_tool_structured(
        "communicationTarget.resolve",
        {
            "project_id": project_id,
            "key": "internal-support",
            "profile_ref": "communication-profile:support",
            "source_surface_ref": "telegram-chat:-1001",
            "invoker_ref": "telegram-user:555",
        },
    )
    allowed_default_actor = mcp_client.call_tool_structured(
        "communicationTarget.resolve",
        {
            "project_id": project_id,
            "key": "internal-support",
            "source_surface_ref": "telegram-chat:-1001",
            "invoker_ref": "telegram-user:555",
        },
    )
    denied = mcp_client.call_tool_structured(
        "communicationTarget.resolve",
        {
            "project_id": project_id,
            "key": "internal-support",
            "profile_ref": "communication-profile:analytics",
            "source_surface_ref": "telegram-chat:-1001",
            "invoker_ref": "telegram-user:555",
        },
    )
    denied_invoker = mcp_client.call_tool_structured(
        "communicationTarget.resolve",
        {
            "project_id": project_id,
            "key": "internal-support",
            "profile_ref": "communication-profile:support",
            "source_surface_ref": "telegram-chat:-1001",
            "invoker_ref": "telegram-user:999",
        },
    )

    assert allowed["allowed"] is True
    assert allowed["policy_profile_ref"] == "communication-profile:support"
    assert allowed_default_actor["allowed"] is True
    assert allowed_default_actor["policy_profile_ref"] == "communication-profile:support"
    assert allowed["action_ref"] == "communications.slack-bot.message.send"
    assert allowed["surface_ref"] == "slack-channel:C123"
    assert allowed["action_input_defaults"]["surface_ref"] == "slack-channel:C123"
    assert denied["allowed"] is False
    assert denied["denial_reason"] == "profile_not_allowed"
    assert denied_invoker["allowed"] is False
    assert denied_invoker["denial_reason"] == "invoker_not_allowed"

    mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "customer-telegram",
            "provider_key": "telegram-bot",
            "surface_ref": "telegram-chat:-1001",
            "profile_ref": "communication-profile:support",
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": ["communication-profile:support"],
                "allowed_invoker_refs": ["telegram-user:555"],
                "allowed_target_refs": ["communication-target:customer-telegram"],
            },
        },
    )
    telegram_allowed = mcp_client.call_tool_structured(
        "communicationTarget.resolve",
        {
            "project_id": project_id,
            "key": "customer-telegram",
            "profile_ref": "communication-profile:support",
            "source_surface_ref": "slack-channel:C123",
            "invoker_ref": "telegram-user:555",
        },
    )
    assert telegram_allowed["allowed"] is True
    assert telegram_allowed["action_ref"] == "communications.telegram-bot.message.send"
    assert telegram_allowed["action_input_defaults"]["chat_ref"] == "telegram-chat:-1001"
    assert telegram_allowed["action_input_defaults"]["profile_key"] == "support"

    default_denied = mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "default-denied",
            "provider_key": "slack-bot",
            "surface_ref": "slack-channel:C999",
        },
    )
    assert default_denied["data"]["send_policy"]["mode"] == "deny"
    default_denied_resolution = mcp_client.call_tool_structured(
        "communicationTarget.resolve",
        {
            "project_id": project_id,
            "key": "default-denied",
            "profile_ref": "communication-profile:support",
        },
    )
    assert default_denied_resolution["allowed"] is False
    assert default_denied_resolution["denial_reason"] == "send_policy_disabled"

    wrong_target_allowlist = mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "wrong-target-allowlist",
            "provider_key": "slack-bot",
            "surface_ref": "slack-channel:C999",
            "send_policy": {
                "mode": "explicit-target",
                "allowed_target_refs": ["communication-target:other"],
            },
        },
    )
    assert wrong_target_allowlist["data"]["send_policy"]["allowed_target_refs"] == [
        "communication-target:other"
    ]
    wrong_target_resolution = mcp_client.call_tool_structured(
        "communicationTarget.resolve",
        {
            "project_id": project_id,
            "key": "wrong-target-allowlist",
            "profile_ref": "communication-profile:support",
        },
    )
    assert wrong_target_resolution["allowed"] is False
    assert wrong_target_resolution["denial_reason"] == "target_not_allowed"

    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        resources = ResourceRepository(session)
        for index, text in enumerate(["Customer reported billing issue", "Support asked for id"]):
            resources.upsert_record(
                project_id=project_id,
                plugin_slug="communications",
                resource_key="communication-message",
                external_id=f"slack-message:C123:{index}",
                title="Slack message",
                data_json={
                    "provider_key": "slack-bot",
                    "profile_ref": "communication-profile:support",
                    "direction": "inbound",
                    "surface_ref": "slack-channel:C123",
                    "channel_ref": "slack-channel:C123",
                    "thread_ref": "slack-thread:C123:1710000000.000100",
                    "message_ref": f"slack-message:C123:{index}",
                    "sender_ref": f"slack-user:U{index}",
                    "text_preview": text,
                    "body_artifact_ref": "artifact:secret-body",
                },
                provenance_json={"source": "test"},
            )

    context = mcp_client.call_tool_structured(
        "communicationContext.query",
        {
            "project_id": project_id,
            "surface_ref": "slack-channel:C123",
            "thread_ref": "slack-thread:C123:1710000000.000100",
            "limit": 10,
            "fields": ["message_ref", "sender_ref", "text_preview"],
        },
    )

    assert [item["fields"]["text_preview"] for item in context["items"]] == [
        "Customer reported billing issue",
        "Support asked for id",
    ]
    rendered = json.dumps(context)
    assert "secret-body" not in rendered

    err = mcp_client.call_tool_error(
        "communicationContext.query",
        {
            "project_id": project_id,
            "surface_ref": "slack-channel:C123",
            "fields": ["raw_artifact_ref"],
        },
    )
    assert err["code"] == -32602
    assert err["data"]["fields"] == ["raw_artifact_ref"]
    assert "text_preview" in err["data"]["allowed_fields"]


def test_communication_send_executes_compact_dry_run_through_target(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _seed_slack_credential(mcp_client, project_id)

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "ops-bot",
            "identity": {"display_name": "Ops Bot"},
            "provider_facets": {"slack-bot": {"auth_profile_key": "default"}},
        },
    )
    mcp_client.call_tool_structured(
        "communicationSurface.upsert",
        {
            "project_id": project_id,
            "surface_ref": "slack-channel:CROAD",
            "provider_key": "slack-bot",
            "kind": "slack-channel",
            "display_name": "roadmap",
            "capabilities": {"can_write": True, "can_thread": True, "buttons": True},
        },
    )
    mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "slack-roadmap",
            "provider_key": "slack-bot",
            "surface_ref": "slack-channel:CROAD",
            "profile_ref": "communication-profile:ops-bot",
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": ["communication-profile:ops-bot"],
                "allowed_target_refs": ["communication-target:slack-roadmap"],
            },
        },
    )

    sent = mcp_client.call_tool_structured(
        "communication.send",
        {
            "project_id": project_id,
            "to": "slack-roadmap",
            "text": "Done. The fix shipped.",
            "content": {"controls": [{"type": "button", "label": "Ack", "value": "ack:roadmap:1"}]},
            "dry_run": True,
        },
    )

    assert sent["data"]["ok"] is True
    assert sent["data"]["status"] == "validated"
    assert sent["data"]["action_ref"] == "communications.slack-bot.message.send"
    assert sent["data"]["provider_key"] == "slack-bot"
    assert sent["data"]["target_ref"] == "communication-target:slack-roadmap"
    assert sent["data"]["actor_ref"] == "communication-profile:ops-bot"
    assert sent["data"]["surface_ref"] == "slack-channel:CROAD"
    assert sent["data"]["action_call_id"] > 0
    assert sent["data"]["effects"] == [
        "validated provider payload",
        "created dry-run action_call audit row",
        "did not call provider connector",
    ]
    assert "credential_ref" not in json.dumps(sent)


def test_communication_send_uses_single_slack_file_upload_for_text_and_files(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _seed_slack_credential(mcp_client, project_id)

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "ops-bot",
            "identity": {"display_name": "Ops Bot"},
            "provider_facets": {"slack-bot": {"auth_profile_key": "default"}},
        },
    )
    mcp_client.call_tool_structured(
        "communicationSurface.upsert",
        {
            "project_id": project_id,
            "surface_ref": "slack-channel:CROAD",
            "provider_key": "slack-bot",
            "kind": "slack-channel",
            "display_name": "roadmap",
            "capabilities": {"can_write": True, "can_thread": True, "images": True},
        },
    )
    mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "slack-roadmap-media",
            "provider_key": "slack-bot",
            "surface_ref": "slack-channel:CROAD",
            "profile_ref": "communication-profile:ops-bot",
            "metadata_json": {"action_mode": "auto"},
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": ["communication-profile:ops-bot"],
                "allowed_target_refs": ["communication-target:slack-roadmap-media"],
            },
        },
    )

    sent = mcp_client.call_tool_structured(
        "communication.send",
        {
            "project_id": project_id,
            "to": "slack-roadmap-media",
            "text": "Customer attached these screenshots.",
            "attachments": [
                {
                    "type": "image",
                    "artifact_ref": "/generated-assets/communication-media/one.png",
                    "filename": "one.png",
                    "mime_type": "image/png",
                },
                {
                    "type": "file",
                    "artifact_ref": "/generated-assets/communication-media/two.log",
                    "filename": "two.log",
                    "mime_type": "text/plain",
                },
            ],
            "context": {"thread_ref": "slack-thread:CROAD:1770000000.000001"},
            "delivery": {"reply_mode": "same_thread"},
            "dry_run": True,
        },
    )

    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    assert "action_call_id" in sent["data"], sent
    with Session(engine) as session:
        call = session.get(ActionCall, sent["data"]["action_call_id"])
        assert call is not None
        request_json = call.request_json or {}
    assert sent["data"]["action_ref"] == "communications.slack-bot.file.upload"
    assert request_json["initial_comment"] == "Customer attached these screenshots."
    assert request_json["thread_ref"] == "slack-thread:CROAD:1770000000.000001"
    assert request_json["delete_after_upload"] is True
    assert "text" not in request_json
    assert "blocks" not in request_json
    assert [item["filename"] for item in request_json["files"]] == ["one.png", "two.log"]


def test_communication_send_rejects_ambiguous_actor_with_repair_context(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])

    mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "multi-bot-roadmap",
            "provider_key": "slack-bot",
            "surface_ref": "slack-channel:CROAD",
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": [
                    "communication-profile:ops-bot",
                    "communication-profile:analytics-bot",
                ],
                "allowed_target_refs": ["communication-target:multi-bot-roadmap"],
            },
        },
    )

    err = mcp_client.call_tool_error(
        "communication.send",
        {
            "project_id": project_id,
            "to": "multi-bot-roadmap",
            "text": "Done.",
            "dry_run": True,
        },
    )

    assert err["code"] == -32602
    detail = err["data"]["error"]
    assert detail["code"] == "COMM_AMBIGUOUS_ACTOR"
    assert detail["effect"] == "none"
    assert detail["same_input_will_fail"] is True
    assert detail["resolved"]["candidate_actor_refs"] == [
        "communication-profile:ops-bot",
        "communication-profile:analytics-bot",
    ]
    assert detail["repair"]["options"][0]["id"] == "pass_from"


def test_communication_send_rejects_unsupported_email_buttons(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _seed_smtp_credential(mcp_client, project_id)

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support-mailer",
            "identity": {"display_name": "Support Mailer"},
            "provider_facets": {"smtp": {"auth_profile_key": "primary"}},
        },
    )
    mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "customer-email",
            "provider_key": "smtp",
            "surface_ref": "email:customer-acme",
            "profile_ref": "communication-profile:support-mailer",
            "action_input_defaults": {
                "recipients": ["customer@example.test"],
                "subject": "Status update",
            },
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": ["communication-profile:support-mailer"],
                "allowed_target_refs": ["communication-target:customer-email"],
            },
        },
    )

    err = mcp_client.call_tool_error(
        "communication.send",
        {
            "project_id": project_id,
            "to": "customer-email",
            "text": "Please approve.",
            "controls": [{"type": "button", "label": "Approve", "value": "approve:1"}],
            "dry_run": True,
        },
    )

    detail = err["data"]["error"]
    assert detail["code"] == "COMM_UNSUPPORTED_CAPABILITY"
    assert detail["effect"] == "none"
    assert detail["failed_paths"][0]["path"] == "/content/controls/0"
    assert detail["failed_paths"][0]["required_capability"] == "control.button.callback"
    assert detail["resolved"]["provider"] == "smtp"
    assert "Do not change communication semantics" in detail["repair"]["do_not"][2]


def test_communication_send_rejects_unsupported_delivery_and_content_shape(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _seed_telegram_credential(mcp_client, project_id, profile_key="support-bot")

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support-bot",
            "identity": {"display_name": "Support Bot"},
            "provider_facets": {"telegram-bot": {"auth_profile_key": "support-bot"}},
        },
    )
    mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "customer-telegram",
            "provider_key": "telegram-bot",
            "surface_ref": "telegram-chat:12345",
            "profile_ref": "communication-profile:support-bot",
            "metadata_json": {"action_mode": "auto"},
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": ["communication-profile:support-bot"],
                "allowed_target_refs": ["communication-target:customer-telegram"],
            },
        },
    )

    private_err = mcp_client.call_tool_error(
        "communication.send",
        {
            "project_id": project_id,
            "to": "customer-telegram",
            "text": "Done.",
            "delivery": {"visibility": "private"},
            "dry_run": True,
        },
    )
    assert private_err["data"]["error"]["code"] == "COMM_UNSUPPORTED_DELIVERY_OPTION"
    assert private_err["data"]["error"]["failed_paths"][0]["path"] == "/delivery/visibility"

    media_controls_err = mcp_client.call_tool_error(
        "communication.send",
        {
            "project_id": project_id,
            "to": "customer-telegram",
            "text": "Images attached.",
            "attachments": [
                {"type": "image", "url": "https://example.test/a.png"},
                {"type": "image", "url": "https://example.test/b.png"},
            ],
            "controls": [{"type": "button", "label": "Ack", "value": "ack:media:1"}],
            "dry_run": True,
        },
    )
    assert media_controls_err["data"]["error"]["code"] == "COMM_UNSUPPORTED_CONTENT_SHAPE"
    assert (
        media_controls_err["data"]["error"]["failed_paths"][0]["path"] == "/content/attachments/1"
    )

    image_sent = mcp_client.call_tool_structured(
        "communication.send",
        {
            "project_id": project_id,
            "to": "customer-telegram",
            "attachments": [{"type": "image", "url": "https://example.test/a.png"}],
            "dry_run": True,
        },
    )
    assert image_sent["data"]["action_ref"] == "communications.telegram-bot.file.upload"
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        call = session.get(ActionCall, image_sent["data"]["action_call_id"])
        assert call is not None
        image_request_json = call.request_json or {}
    assert image_request_json["file"] == {"type": "image", "url": "https://example.test/a.png"}

    multi_sent = mcp_client.call_tool_structured(
        "communication.send",
        {
            "project_id": project_id,
            "to": "customer-telegram",
            "text": "Images attached.",
            "attachments": [
                {"type": "image", "url": "https://example.test/a.png"},
                {"type": "image", "url": "https://example.test/b.png"},
            ],
            "dry_run": True,
        },
    )
    assert multi_sent["data"]["action_ref"] == "communications.telegram-bot.file.upload"

    thread_err = mcp_client.call_tool_error(
        "communication.send",
        {
            "project_id": project_id,
            "to": "customer-telegram",
            "text": "Thread reply.",
            "delivery": {"reply_mode": "same_thread"},
            "dry_run": True,
        },
    )
    assert thread_err["data"]["error"]["code"] == "COMM_DELIVERY_CONTEXT_REQUIRED"
    assert thread_err["data"]["error"]["failed_paths"][0]["required_context"] == "thread_ref"

    long_value = "approve:" + ("x" * 100)
    sent = mcp_client.call_tool_structured(
        "communication.send",
        {
            "project_id": project_id,
            "to": "customer-telegram",
            "text": "Approve this.",
            "controls": [
                {
                    "type": "button",
                    "label": "Approve",
                    "value": long_value,
                    "payload": {"decision": "approve", "case_id": "case-123"},
                }
            ],
            "dry_run": True,
        },
    )
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        call = session.get(ActionCall, sent["data"]["action_call_id"])
        assert call is not None
        request_json = call.request_json or {}
    callback_data = request_json["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert callback_data != long_value
    assert len(callback_data.encode("utf-8")) <= 64
    assert request_json["control_metadata"][callback_data]["payload"] == {
        "decision": "approve",
        "case_id": "case-123",
    }


def test_communication_send_can_infer_actor_from_source_request(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _seed_slack_credential(mcp_client, project_id)

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "ops-bot",
            "identity": {"display_name": "Ops Bot"},
            "provider_facets": {"slack-bot": {"auth_profile_key": "default"}},
        },
    )
    mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "internal-roadmap",
            "provider_key": "slack-bot",
            "surface_ref": "slack-channel:CROAD",
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": [
                    "communication-profile:ops-bot",
                    "communication-profile:analytics-bot",
                ],
                "allowed_target_refs": ["communication-target:internal-roadmap"],
            },
        },
    )
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        request = (
            AgentRequestRepository(session)
            .create(
                project_id=project_id,
                request_key="manual-source:1",
                title="Manual request",
                body_preview="Send roadmap update",
                source_provider="telegram-bot",
                source_kind="telegram_message",
                metadata_json={
                    "profile_ref": "communication-profile:ops-bot",
                    "chat_ref": "telegram-chat:12345",
                    "invoker_ref": "telegram-user:555",
                },
            )
            .data
        )

    sent = mcp_client.call_tool_structured(
        "communication.send",
        {
            "project_id": project_id,
            "to": "internal-roadmap",
            "text": "Done.",
            "context": {"source_request_id": request.id},
            "dry_run": True,
        },
    )

    assert sent["data"]["actor_ref"] == "communication-profile:ops-bot"
    assert sent["data"]["status"] == "validated"


def test_communication_send_prefers_target_actor_over_cross_platform_source(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _seed_slack_credential(mcp_client, project_id)
    _seed_telegram_credential(mcp_client, project_id, profile_key="telegram-bot")

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "slack-ops",
            "identity": {"display_name": "Slack Ops"},
            "provider_facets": {"slack-bot": {"auth_profile_key": "default"}},
        },
    )
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "telegram-bot",
            "identity": {"display_name": "Telegram Bot"},
            "provider_facets": {"telegram-bot": {"auth_profile_key": "telegram-bot"}},
        },
    )
    mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "slack-roadmap",
            "provider_key": "slack-bot",
            "surface_ref": "slack-channel:CROAD",
            "profile_ref": "communication-profile:slack-ops",
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": ["communication-profile:slack-ops"],
                "allowed_target_refs": ["communication-target:slack-roadmap"],
            },
        },
    )
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        request = (
            AgentRequestRepository(session)
            .create(
                project_id=project_id,
                request_key="telegram-cross-platform:1",
                title="Telegram request",
                body_preview="Send Slack update",
                source_provider="telegram-bot",
                metadata_json={
                    "profile_ref": "communication-profile:telegram-bot",
                    "chat_ref": "telegram-chat:12345",
                    "invoker_ref": "telegram-user:555",
                },
            )
            .data
        )

    sent = mcp_client.call_tool_structured(
        "communication.send",
        {
            "project_id": project_id,
            "to": "slack-roadmap",
            "text": "Cross-platform update.",
            "context": {"source_request_id": request.id},
            "dry_run": True,
        },
    )

    assert sent["data"]["actor_ref"] == "communication-profile:slack-ops"
    assert sent["data"]["status"] == "validated"


def test_communication_send_can_run_inside_granted_run_plan_step(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _seed_slack_credential(mcp_client, project_id)

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "ops-bot",
            "identity": {"display_name": "Ops Bot"},
            "provider_facets": {"slack-bot": {"auth_profile_key": "default"}},
        },
    )
    mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "ops-alerts",
            "provider_key": "slack-bot",
            "surface_ref": "slack-channel:CALERTS",
            "profile_ref": "communication-profile:ops-bot",
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": ["communication-profile:ops-bot"],
                "allowed_target_refs": ["communication-target:ops-alerts"],
            },
        },
    )
    plan_json = {
        "schema_version": "stackos.run-plan.v1",
        "key": "communication-send.run",
        "title": "Communication send",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "notify",
                    "tool": "communication.send",
                    "targets": ["communication-target:ops-alerts"],
                }
            ]
        },
        "steps": [{"id": "notify", "title": "Notify ops"}],
    }
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": plan_json},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "notify",
            "run_token": run_token,
        },
    )

    sent = mcp_client.call_tool_structured(
        "communication.send",
        {
            "project_id": project_id,
            "to": "ops-alerts",
            "text": "Run-plan scoped notification.",
            "dry_run": True,
            "run_token": run_token,
        },
    )
    denied = mcp_client.call_tool_error(
        "communication.send",
        {
            "project_id": project_id,
            "to": "other-target",
            "text": "Wrong target.",
            "dry_run": True,
            "run_token": run_token,
        },
    )

    assert sent["run_id"] == started["data"]["run_id"]
    assert sent["data"]["status"] == "validated"
    assert denied["code"] == -32007
    assert denied["data"]["tool"] == "communication.send"


def test_communication_reply_requires_matching_run_plan_source_grant(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _seed_telegram_credential(mcp_client, project_id, profile_key="support-bot")

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support-bot",
            "identity": {"display_name": "Support Bot"},
            "provider_facets": {"telegram-bot": {"auth_profile_key": "support-bot"}},
        },
    )
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        request = (
            AgentRequestRepository(session)
            .create(
                project_id=project_id,
                request_key="telegram-run-reply:1",
                title="Telegram request",
                body_preview="Reply from run",
                source_provider="telegram-bot",
                source_kind="telegram_message",
                source_message_ref="telegram-message:12345:11",
                metadata_json={
                    "profile_key": "support-bot",
                    "profile_ref": "communication-profile:support-bot",
                    "chat_ref": "telegram-chat:12345",
                    "invoker_ref": "telegram-user:555",
                },
            )
            .data
        )
    plan_json = {
        "schema_version": "stackos.run-plan.v1",
        "key": "communication-reply.run",
        "title": "Communication reply",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "reply",
                    "tool": "communication.reply",
                    "sources": ["telegram-bot"],
                }
            ]
        },
        "steps": [{"id": "reply", "title": "Reply"}],
    }
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": plan_json},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "reply",
            "run_token": run_token,
        },
    )

    reply = mcp_client.call_tool_structured(
        "communication.reply",
        {
            "project_id": project_id,
            "request_id": request.id,
            "text": "Run-plan reply.",
            "dry_run": True,
            "run_token": run_token,
        },
    )
    assert reply["run_id"] == started["data"]["run_id"]
    assert reply["data"]["status"] == "validated"


def test_communication_reply_enforces_profile_response_policy(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _seed_telegram_credential(mcp_client, project_id, profile_key="support-bot")

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support-bot",
            "identity": {"display_name": "Support Bot"},
            "provider_facets": {"telegram-bot": {"auth_profile_key": "support-bot"}},
            "access_policy": {
                "user_mode": "allowlist",
                "allowed_user_refs": ["telegram-user:555"],
            },
        },
    )
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        request = (
            AgentRequestRepository(session)
            .create(
                project_id=project_id,
                request_key="telegram-update:support-bot:blocked",
                title="Telegram message",
                body_preview="@stackos_bot check this",
                source_provider="telegram-bot",
                source_kind="telegram_message",
                source_message_ref="telegram-message:12345:99",
                metadata_json={
                    "profile_key": "support-bot",
                    "profile_ref": "communication-profile:support-bot",
                    "chat_ref": "telegram-chat:12345",
                    "invoker_ref": "telegram-user:999",
                },
            )
            .data
        )

    err = mcp_client.call_tool_error(
        "communication.reply",
        {
            "project_id": project_id,
            "request_id": request.id,
            "text": "Nope.",
            "dry_run": True,
        },
    )
    assert err["data"]["error"]["code"] == "COMM_REPLY_NOT_ALLOWED"
    assert err["data"]["error"]["failed_paths"][0]["policy_reason"] == "invoker_not_allowed"


def test_communication_reply_resolves_origin_without_provider_payload(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _seed_telegram_credential(mcp_client, project_id, profile_key="support-bot")

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support-bot",
            "identity": {"display_name": "Support Bot"},
            "provider_facets": {"telegram-bot": {"auth_profile_key": "support-bot"}},
        },
    )
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        request = (
            AgentRequestRepository(session)
            .create(
                project_id=project_id,
                request_key="telegram-update:support-bot:500",
                title="Telegram message",
                body_preview="@stackos_bot check this",
                source_provider="telegram-bot",
                source_kind="telegram_message",
                source_message_ref="telegram-message:12345:77",
                metadata_json={
                    "profile_key": "support-bot",
                    "profile_ref": "communication-profile:support-bot",
                    "chat_ref": "telegram-chat:12345",
                    "thread_ref": "telegram-thread:12345:default",
                    "invoker_ref": "telegram-user:555",
                },
            )
            .data
        )

    reply = mcp_client.call_tool_structured(
        "communication.reply",
        {
            "project_id": project_id,
            "request_id": request.id,
            "text": "Done. I checked it.",
            "dry_run": True,
        },
    )

    assert reply["data"]["status"] == "validated"
    assert reply["data"]["action_ref"] == "communications.telegram-bot.message.send"
    assert reply["data"]["actor_ref"] == "communication-profile:support-bot"
    assert reply["data"]["surface_ref"] == "telegram-chat:12345"
    assert reply["data"]["resolved"]["request_id"] == request.id


def test_local_agent_chat_mcp_creates_message_and_request(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])

    created = mcp_client.call_tool_structured(
        "localAgentChat.createMessage",
        {
            "project_id": project_id,
            "thread_key": "support",
            "message_key": "msg-001",
            "sender_ref": "local-user:operator",
            "sender_display_name": "Operator",
            "text": "Review campaign status.",
            "create_request": True,
        },
    )
    replayed = mcp_client.call_tool_structured(
        "localAgentChat.createMessage",
        {
            "project_id": project_id,
            "thread_key": "support",
            "message_key": "msg-001",
            "sender_ref": "local-user:operator",
            "sender_display_name": "Operator",
            "text": "Review campaign status.",
            "create_request": True,
        },
    )

    assert created["data"]["thread_ref"] == "local-agent-chat:thread:support"
    assert created["data"]["message_ref"] == "local-agent-chat:message:support:msg-001"
    assert created["data"]["agent_request"]["source_provider"] == "local-agent-chat"
    assert replayed["data"]["agent_request"]["id"] == created["data"]["agent_request"]["id"]

    response = mcp_client.call_tool_structured(
        "localAgentChat.createMessage",
        {
            "project_id": project_id,
            "thread_key": "support",
            "message_key": "msg-002",
            "direction": "outbound",
            "sender_ref": "agent:codex",
            "text": "I reviewed it and opened the follow-up plan.",
            "create_request": False,
        },
    )
    assert response["data"]["thread_ref"] == "local-agent-chat:thread:support"
    assert response["data"]["message_ref"] == "local-agent-chat:message:support:msg-002"
    assert response["data"]["agent_request"] is None


def test_communication_profile_mcp_lifecycle_has_no_secret_roundtrip(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _seed_telegram_credential(mcp_client, project_id)

    created = mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support-bot",
            "identity": {
                "display_name": "Support Bot",
                "purpose": "Handle support requests from approved Telegram users.",
                "voice": "Concise and calm.",
            },
            "provider_facets": {
                "telegram-bot": {
                    "auth_profile_key": "support",
                    "bot_username": "support_bot",
                    "reply_to_message_refs": {"telegram-message:999:88": 88},
                    "thread_refs": {"telegram-thread:999:default": 1},
                    "direct_messages_topic_refs": {"telegram-dm-topic:999:555": 22},
                }
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
        },
    )
    assert created["data"]["key"] == "support-bot"
    telegram_facet = created["data"]["provider_facets"]["telegram-bot"]
    assert telegram_facet["auth_profile_key"] == "support"
    assert created["data"]["identity"]["display_name"] == "Support Bot"
    assert telegram_facet["reply_to_message_refs"] == {"telegram-message:999:88": 88}

    fetched = mcp_client.call_tool_structured(
        "communicationProfile.get",
        {"project_id": project_id, "key": "support-bot"},
    )
    listed = mcp_client.call_tool_structured(
        "communicationProfile.list",
        {"project_id": project_id},
    )

    assert fetched["key"] == "support-bot"
    assert fetched["provider_facets"]["telegram-bot"]["thread_refs"] == {
        "telegram-thread:999:default": 1
    }
    assert [item["key"] for item in listed["items"]] == ["support-bot"]
    rendered = json.dumps({"created": created, "fetched": fetched, "listed": listed})
    assert "123456:ABC" not in rendered
    assert "telegram-secret" not in rendered


def test_tool_profile_resolve_mcp_reports_missing_telegram_credential(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])

    resolved = mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "missing-credential",
            "identity": {
                "display_name": "Missing Bot",
                "purpose": "Exercise credential validation.",
                "voice": "Concise.",
            },
            "provider_facets": {"telegram-bot": {"auth_profile_key": "missing"}},
            "access_policy": {
                "dm_mode": "allowlist",
                "group_mode": "allowlist",
                "user_mode": "allowlist",
                "allowed_chat_refs": ["telegram-chat:999"],
                "allowed_user_refs": ["telegram-user:555"],
            },
        },
    )

    assert resolved["data"]["provider_facets"]["telegram-bot"]["auth_profile_key"] == "missing"
    missing = mcp_client.call_tool_structured(
        "toolProfile.resolve",
        {
            "project_id": project_id,
            "provider_key": "telegram-bot",
            "tool_profile_key": "missing-credential",
        },
    )
    assert missing["ready"] is False
    assert missing["tool_profile"]["auth_profile_key"] == "missing"
    assert "credential" in missing["missing"]


def test_tool_profile_resolve_mcp_resolves_telegram_profile_and_credential(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _seed_telegram_credential(mcp_client, project_id)
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support-bot",
            "identity": {
                "display_name": "Support Bot",
                "purpose": "Handle approved support requests.",
                "voice": "Concise.",
            },
            "provider_facets": {
                "telegram-bot": {
                    "auth_profile_key": "support",
                    "bot_username": "support_bot",
                }
            },
            "agent_guidance": {"default_instructions": "Triage before replying."},
            "access_policy": {
                "dm_mode": "allowlist",
                "group_mode": "allowlist",
                "user_mode": "allowlist",
                "allowed_chat_refs": ["telegram-chat:999"],
                "allowed_user_refs": ["telegram-user:555"],
            },
        },
    )

    resolved = mcp_client.call_tool_structured(
        "toolProfile.resolve",
        {
            "project_id": project_id,
            "provider_key": "telegram-bot",
            "tool_profile_key": "support-bot",
        },
    )

    rendered = json.dumps(resolved)
    assert resolved["ready"] is True
    assert resolved["provider"]["provider_key"] == "telegram-bot"
    assert resolved["provider"]["setup_required"] is False
    assert resolved["tool_profile"]["key"] == "support-bot"
    assert resolved["tool_profile"]["auth_profile_key"] == "support"
    assert resolved["tool_profile"]["access_policy"]["allowed_user_refs"] == ["telegram-user:555"]
    assert resolved["credential"]["credential_ref"].startswith("cred_")
    assert resolved["credential"]["profile_key"] == "support"
    assert resolved["missing"] == []
    assert "123456:ABC" not in rendered
    assert "telegram-secret" not in rendered


def test_tool_profile_resolve_mcp_rejects_profile_credential_mismatch(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _seed_telegram_credential(mcp_client, project_id, profile_key="support")
    _seed_telegram_credential(
        mcp_client,
        project_id,
        profile_key="analytics",
        bot_token="654321:XYZ",
    )
    analytics_ref = _credential_ref(
        mcp_client,
        project_id=project_id,
        provider_key="telegram-bot",
        profile_key="analytics",
    )
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support-bot",
            "identity": {
                "display_name": "Support Bot",
                "purpose": "Handle approved support requests.",
                "voice": "Concise.",
            },
            "provider_facets": {"telegram-bot": {"auth_profile_key": "support"}},
            "access_policy": {
                "dm_mode": "allowlist",
                "group_mode": "allowlist",
                "user_mode": "allowlist",
                "allowed_chat_refs": ["telegram-chat:999"],
                "allowed_user_refs": ["telegram-user:555"],
            },
        },
    )

    err = mcp_client.call_tool_error(
        "toolProfile.resolve",
        {
            "project_id": project_id,
            "provider_key": "telegram-bot",
            "tool_profile_key": "support-bot",
            "credential_ref": analytics_ref,
        },
    )

    assert err["code"] == -32602
    assert err["message"] == "ValidationError"
    assert err["data"]["credential_profile_key"] == "analytics"
    assert err["data"]["requested_auth_profile_key"] == "support"


def test_tool_profile_resolve_mcp_redacts_profile_sections(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _seed_telegram_credential(mcp_client, project_id)
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support-bot",
            "identity": {
                "display_name": "Support Bot",
                "purpose": "Handle support with api_key=profile-secret",
                "voice": "Concise.",
            },
            "provider_facets": {
                "telegram-bot": {
                    "auth_profile_key": "support",
                    "refs": {
                        "safe_ref": "telegram-chat:999",
                    },
                }
            },
            "context_policy": {
                "note": "authorization: bearer hidden-token",
                "nested": {"password": "nested-secret"},
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

    resolved = mcp_client.call_tool_structured(
        "toolProfile.resolve",
        {
            "project_id": project_id,
            "provider_key": "telegram-bot",
            "tool_profile_key": "support-bot",
        },
    )

    rendered = json.dumps(resolved)
    assert "profile-secret" not in rendered
    assert "hidden-token" not in rendered
    assert "nested-secret" not in rendered
    assert (
        resolved["tool_profile"]["identity"]["purpose"] == "Handle support with api_key=[redacted]"
    )
    assert resolved["tool_profile"]["context_policy"]["nested"]["password"] == "[redacted]"
    assert resolved["tool_profile"]["refs"]["safe_ref"] == "telegram-chat:999"


def test_communication_profile_upsert_mcp_rejects_secret_like_setup_fields(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])

    err = mcp_client.call_tool_error(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support-bot",
            "identity": {"display_name": "Support Bot"},
            "provider_facets": {
                "telegram-bot": {
                    "auth_profile_key": "support",
                    "api_key": "raw-secret",
                }
            },
        },
    )

    assert err["code"] == -32602
    assert err["message"] == "ValidationError"
    assert "must not contain secrets" in err["data"]["detail"]
    assert "raw-secret" not in json.dumps(err)


def test_tool_profile_resolve_mcp_resolves_generic_credential_profile(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _seed_smtp_credential(mcp_client, project_id)

    resolved = mcp_client.call_tool_structured(
        "toolProfile.resolve",
        {
            "project_id": project_id,
            "provider_key": "smtp",
            "auth_profile_key": "primary",
        },
    )

    rendered = json.dumps(resolved)
    assert resolved["ready"] is True
    assert resolved["provider"]["setup_required"] is False
    assert resolved["tool_profile"] is None
    assert resolved["credential"]["provider_key"] == "smtp"
    assert resolved["credential"]["profile_key"] == "primary"
    assert resolved["credential"]["credential_ref"].startswith("cred_")
    assert resolved["next_action"] is None
    assert "smtp-secret" not in rendered


def test_tool_profile_resolve_mcp_rejects_generic_credential_profile_mismatch(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _seed_smtp_credential(mcp_client, project_id, profile_key="primary")
    _seed_smtp_credential(mcp_client, project_id, profile_key="secondary")
    secondary_ref = _credential_ref(
        mcp_client,
        project_id=project_id,
        provider_key="smtp",
        profile_key="secondary",
    )

    err = mcp_client.call_tool_error(
        "toolProfile.resolve",
        {
            "project_id": project_id,
            "provider_key": "smtp",
            "auth_profile_key": "primary",
            "credential_ref": secondary_ref,
        },
    )

    assert err["code"] == -32602
    assert err["message"] == "ValidationError"
    assert err["data"]["credential_profile_key"] == "secondary"
    assert err["data"]["requested_auth_profile_key"] == "primary"
