"""MCP parity tests for communication setup operations."""

from __future__ import annotations

import asyncio
import json
import re
import time
from threading import Event

import httpx
import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.actions import ActionRepository
from stackos.actions.telegram import TelegramActionConnector
from stackos.auth_providers import AuthRepository
from stackos.auth_providers.repository.telegram_application import TelegramApplicationRepository
from stackos.db.models import Action, ActionCall, Credential, CredentialAccount, CredentialScope
from stackos.integrations.telegram_tdlib.native import TelegramTdlibNativeError
from stackos.integrations.telegram_tdlib.service import TelegramTdlibServiceError
from stackos.operations import communication_platform
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.provider_refs import ProviderObjectReferenceRepository
from stackos.repositories.resources import ResourceRepository
from tests.integration.account_test_support import seed_test_account
from tests.integration.test_repositories.test_telegram_actions import (
    DownloadTimeoutTelegram,
    FakeTelegram,
)

from .conftest import MCPClient


def _seed_telegram_credential(
    mcp: MCPClient,
    project_id: int,
    *,
    account_name: str = "support",
    account_kind: str = "bot",
    bot_token: str = "123456:ABC",
) -> str:
    _install_fake_telegram_connector(mcp)
    engine = mcp.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        application_fields = (
            {}
            if TelegramApplicationRepository(session).configured()
            else {"api_id": 12345, "api_hash": "test-telegram-application-hash"}
        )
        created = AuthRepository(session).store_credential(
            provider_key="telegram",
            auth_method_key=("tdlib-bot-token" if account_kind == "bot" else "tdlib-user-session"),
            display_name=account_name,
            fields={
                **application_fields,
                **({"bot_token": bot_token} if account_kind == "bot" else {}),
                "proxy_enabled": False,
            },
            attach_project_id=project_id,
        )
        credential_ref = created.data.credential_ref
        credential = session.exec(
            select(Credential).where(Credential.credential_ref == credential_ref)
        ).one()
        credential.status = "connected"
        session.add(credential)
        session.commit()
        return credential_ref


def _seed_smtp_credential(
    mcp: MCPClient,
    project_id: int,
    *,
    account_name: str = "primary",
) -> str:
    engine = mcp.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        return (
            AuthRepository(session)
            .store_credential(
                provider_key="smtp",
                auth_method_key="smtp-password",
                display_name=account_name,
                fields={
                    "password": "smtp-secret",
                    "host": "smtp.example.test",
                    "port": 587,
                    "tls_mode": "none",
                    "username": "mailer@example.test",
                    "from_email": "mailer@example.test",
                },
                attach_project_id=project_id,
            )
            .data.credential_ref
        )


def _seed_slack_credential(
    mcp: MCPClient,
    project_id: int,
    *,
    account_name: str = "default",
) -> str:
    engine = mcp.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        return (
            AuthRepository(session)
            .store_credential(
                provider_key="slack-bot",
                auth_method_key="bot-token",
                display_name=account_name,
                fields={
                    "bot_token": "xoxb-test-token",
                    "signing_secret": "slack-signing-secret",
                },
                attach_project_id=project_id,
            )
            .data.credential_ref
        )


def _seed_hubspot_transactional_credential(
    mcp: MCPClient,
    project_id: int,
    *,
    entitlement_confirmed: bool,
    account_name: str = "default",
    email_verified: bool = True,
) -> tuple[str, str, str]:
    engine = mcp.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        seeded = seed_test_account(
            session,
            project_id=project_id,
            provider_key="hubspot",
            display_name=account_name,
            secret_payload=json.dumps({"access_token": "hubspot-communication-secret"}).encode(),
            config_json={
                "auth_method_key": "oauth2_authorization_code",
                "scope_status": "known",
                "transactional_email_entitlement_confirmed": entitlement_confirmed,
            },
        )
        credential = session.exec(
            select(Credential).where(
                Credential.integration_credential_id == seeded.data.id,
            )
        ).one()
        credential.auth_type = "oauth"
        credential.auth_method_key = "oauth2_authorization_code"
        credential.config_json = {
            **(credential.config_json or {}),
            "scope_status": "known",
            "transactional_email_entitlement_confirmed": entitlement_confirmed,
        }
        session.add(credential)
        assert credential.id is not None
        session.add(
            CredentialAccount(
                credential_id=credential.id,
                provider_account_id="1234567",
                metadata_json={"hub_id": 1234567},
            )
        )
        for scope in (
            "crm.objects.contacts.read",
            "marketing-email",
            "transactional-email",
        ):
            session.add(CredentialScope(credential_id=credential.id, scope=scope))
        refs = ProviderObjectReferenceRepository(session, project_id=project_id)
        contact_ref = refs.upsert(
            credential=credential,
            object_type="contact",
            provider_object_id="8501",
            display_name="Transactional recipient",
        )
        email_ref = refs.upsert(
            credential=credential,
            object_type="marketing-email",
            provider_object_id="8701",
            display_name="Order status template",
            metadata_json={"is_transactional": True} if email_verified else {},
        )
        session.commit()
        return credential.credential_ref, contact_ref, email_ref


def _credential_ref(
    mcp: MCPClient,
    *,
    project_id: int,
    provider_key: str,
    display_name: str,
) -> str:
    status = mcp.call_tool_structured(
        "connection.list",
        {
            "project_id": project_id,
            "provider_key": provider_key,
            "response_mode": "raw",
        },
    )
    for account in status["accounts"]:
        if account["display_name"] == display_name:
            return str(account["credential_ref"])
    raise AssertionError(f"Account {display_name!r} not found")


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


def _install_fake_telegram_connector(mcp: MCPClient) -> FakeTelegram:
    """Replace only the daemon action adapter; Accounts remain daemon-owned state."""
    runtime = FakeTelegram()
    services = mcp.test_client.app.state.operation_services  # type: ignore[attr-defined]
    services["action_connectors"].register(TelegramActionConnector(runtime))
    return runtime


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
        "communication.sendBatch",
        "communication.reply",
        "communicationProfile.accountUsage",
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


def test_ingress_endpoint_mcp_derives_and_syncs_slack_provider_routes(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    slack_credential_ref = _seed_slack_credential(mcp_client, project_id)

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support",
            "identity": {"display_name": "Support Agent"},
            "provider_facets": {
                "slack-bot": {
                    "credential_ref": slack_credential_ref,
                    "bot_user_id": "U123",
                },
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
        {"project_id": project_id, "response_mode": "raw"},
    )
    assert routes["endpoint"]["public_base_url"] == "https://stackos.example.com"
    assert routes["endpoint"]["driver"] == "public-url"
    assert routes["endpoint"]["driver_config"] == {}
    route_urls = {route["provider_key"]: route["ingress_url"] for route in routes["routes"]}
    assert route_urls["slack-bot"] == (
        f"https://stackos.example.com/api/v1/ingress/slack/{project_id}/support"
    )
    slack_routes = [route for route in routes["routes"] if route["provider_key"] == "slack-bot"]
    assert len(slack_routes) == 1
    assert slack_routes[0]["action_required"] is True
    assert slack_routes[0]["next_action"]["kind"] == "manual-provider-update"
    assert slack_routes[0]["next_action"]["url"] == route_urls["slack-bot"]
    assert "Event Subscriptions Request URL" in slack_routes[0]["next_action"]["provider_fields"]
    synced = mcp_client.call_tool_structured(
        "ingressEndpoint.sync",
        {
            "project_id": project_id,
            "response_mode": "raw",
        },
    )
    assert "provider_results" in synced["data"], synced
    statuses = {
        (result["provider_key"], result["profile_key"]): result["status"]
        for result in synced["data"]["provider_results"]
    }
    assert statuses[("slack-bot", "support")] == "manual_provider_update_required"
    slack_result = next(
        result
        for result in synced["data"]["provider_results"]
        if result["provider_key"] == "slack-bot"
    )
    assert slack_result["next_action"]["url"] == route_urls["slack-bot"]
    assert "Interactivity Request URL" in slack_result["next_action"]["provider_fields"]
    assert synced["data"]["endpoint"]["last_synced_at"]

    profile = mcp_client.call_tool_structured(
        "communicationProfile.get",
        {"project_id": project_id, "key": "support", "response_mode": "raw"},
    )
    assert (
        profile["provider_facets"]["slack-bot"]["ingress_url"]
        == f"https://stackos.example.com/api/v1/ingress/slack/{project_id}/support"
    )

    refreshed = mcp_client.call_tool_structured(
        "ingressEndpoint.refresh",
        {
            "project_id": project_id,
            "public_base_url": "https://fresh.stackos.example.com",
            "sync_profiles": True,
            "response_mode": "raw",
        },
    )
    assert refreshed["data"]["endpoint"]["public_base_url"] == "https://fresh.stackos.example.com"
    assert refreshed["data"]["endpoint"]["driver"] == "public-url"


def test_account_usage_is_scoped_to_the_exact_profile(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref = _seed_slack_credential(mcp_client, project_id)
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "operator",
            "identity": {"display_name": "Operator Slack"},
            "provider_facets": {
                "slack-bot": {
                    "credential_ref": credential_ref,
                    "auth_profile_key": "default",
                    "ingress_enabled": True,
                    "ingress_url": (
                        f"https://stackos.example.com/api/v1/ingress/slack/{project_id}/operator"
                    ),
                    "manual_ingress_confirmation": {
                        "ingress_url": (
                            f"https://stackos.example.com/api/v1/ingress/slack/"
                            f"{project_id}/operator"
                        ),
                        "source": "forged-agent-input",
                    },
                }
            },
        },
    )
    untrusted_profile = mcp_client.call_tool_structured(
        "communicationProfile.get",
        {"project_id": project_id, "key": "operator", "response_mode": "raw"},
    )
    untrusted_facet = untrusted_profile["provider_facets"]["slack-bot"]
    assert "auth_profile_key" not in untrusted_facet
    assert "manual_ingress_confirmation" not in untrusted_facet
    assert "ingress_url" not in untrusted_facet

    mcp_client.call_tool_structured(
        "ingressEndpoint.configure",
        {
            "project_id": project_id,
            "driver": "public-url",
            "public_base_url": "https://stackos.example.com",
        },
    )
    synced = mcp_client.call_tool_structured(
        "ingressEndpoint.sync",
        {
            "project_id": project_id,
            "response_mode": "raw",
        },
    )
    assert synced["data"]["routes"][0]["remote_status"] == "manual_provider_update_required"
    usage = mcp_client.call_tool_structured(
        "communicationProfile.accountUsage",
        {"response_mode": "raw"},
    )
    account_use = next(item for item in usage["uses"] if item["credential_ref"] == credential_ref)
    assert account_use["project_id"] == project_id
    assert account_use["profile_ref"] == "communication-profile:operator"
    assert account_use["binding_state"] == "ready"
    assert account_use["attention_required"] is True

    profile = mcp_client.call_tool_structured(
        "communicationProfile.get",
        {"project_id": project_id, "key": "operator", "response_mode": "raw"},
    )
    facet = profile["provider_facets"]["slack-bot"]
    assert "auth_profile_key" not in facet
    assert "manual_ingress_confirmation" not in facet
    assert facet["ingress_url"].endswith(f"/api/v1/ingress/slack/{project_id}/operator")


def test_shared_communication_accounts_allow_outbound_reuse_but_reject_duplicate_ingress_owners(
    mcp_client: MCPClient,
    seeded_project: dict,
    monkeypatch,
) -> None:
    first_project_id = int(seeded_project["data"]["id"])
    second_project = mcp_client.call_tool_structured(
        "project.create",
        {
            "slug": "second-communication-project",
            "name": "Second Communication Project",
            "domain": "second.example",
            "locale": "en-US",
        },
    )
    second_project_id = int(second_project["data"]["id"])

    credential_refs = {
        "slack-bot": _seed_slack_credential(mcp_client, first_project_id),
        "telegram": _seed_telegram_credential(mcp_client, first_project_id),
    }
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        accounts = AuthRepository(session)
        for credential_ref in credential_refs.values():
            attached = accounts.attach_account(
                project_id=second_project_id,
                credential_ref=credential_ref,
            ).data
            assert attached.credential_ref == credential_ref

    attached_to_second_project = mcp_client.call_tool_structured(
        "connection.list",
        {"project_id": second_project_id, "response_mode": "raw"},
    )
    assert {account["credential_ref"] for account in attached_to_second_project["accounts"]} == set(
        credential_refs.values()
    )

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": first_project_id,
            "key": "shared-bot",
            "identity": {"display_name": "Shared bot ingress owner"},
            "provider_facets": {
                "slack-bot": {
                    "credential_ref": credential_refs["slack-bot"],
                    "bot_user_id": "UOWNER",
                    "ingress_enabled": True,
                },
                "telegram": {
                    "credential_ref": credential_refs["telegram"],
                },
            },
        },
    )
    mcp_client.call_tool_structured(
        "ingressEndpoint.configure",
        {
            "project_id": first_project_id,
            "driver": "public-url",
            "public_base_url": "https://owner.stackos.example.com",
        },
    )
    mcp_client.call_tool_structured(
        "ingressEndpoint.sync",
        {
            "project_id": first_project_id,
            "response_mode": "raw",
        },
    )

    for provider_key in ("slack-bot",):
        credential_ref = credential_refs[provider_key]
        for invalid_value in ("false", 0):
            invalid = mcp_client.call_tool_error(
                "communicationProfile.upsert",
                {
                    "project_id": second_project_id,
                    "key": f"invalid-{provider_key}-{str(invalid_value).lower()}",
                    "identity": {"display_name": f"Invalid {provider_key} profile"},
                    "provider_facets": {
                        provider_key: {
                            "credential_ref": credential_ref,
                            "ingress_enabled": invalid_value,
                        }
                    },
                },
            )
            assert invalid["code"] == -32602
            assert invalid["data"]["field"] == "ingress_enabled"
            assert invalid["data"]["expected"] == "boolean"

    for provider_key in ("slack-bot",):
        credential_ref = credential_refs[provider_key]
        created = mcp_client.call_tool_structured(
            "communicationProfile.upsert",
            {
                "project_id": second_project_id,
                "key": f"outbound-{provider_key}",
                "identity": {"display_name": f"Outbound {provider_key} profile"},
                "provider_facets": {
                    provider_key: {
                        "credential_ref": credential_ref,
                        "ingress_enabled": False,
                    }
                },
            },
        )
        assert created["data"]["profile_ref"] == f"communication-profile:outbound-{provider_key}"
        stored = mcp_client.call_tool_structured(
            "communicationProfile.get",
            {
                "project_id": second_project_id,
                "key": f"outbound-{provider_key}",
                "response_mode": "raw",
            },
        )
        assert stored["provider_facets"][provider_key]["ingress_enabled"] is False

    telegram_profile = mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": second_project_id,
            "key": "outbound-telegram",
            "identity": {"display_name": "Outbound Telegram profile"},
            "provider_facets": {"telegram": {"credential_ref": credential_refs["telegram"]}},
        },
    )
    assert telegram_profile["data"]["profile_ref"] == "communication-profile:outbound-telegram"

    mcp_client.call_tool_structured(
        "communicationSurface.upsert",
        {
            "project_id": second_project_id,
            "surface_ref": "slack-channel:COUTBOUND",
            "provider_key": "slack-bot",
            "kind": "slack-channel",
            "display_name": "outbound-only",
            "capabilities": {"can_write": True},
        },
    )
    mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": second_project_id,
            "key": "outbound-slack",
            "provider_key": "slack-bot",
            "surface_ref": "slack-channel:COUTBOUND",
            "profile_ref": "communication-profile:outbound-slack-bot",
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": ["communication-profile:outbound-slack-bot"],
                "allowed_target_refs": ["communication-target:outbound-slack"],
            },
        },
    )
    sent = mcp_client.call_tool_structured(
        "communication.send",
        {
            "project_id": second_project_id,
            "to": "outbound-slack",
            "text": "Outbound Account reuse works.",
            "dry_run": True,
        },
    )
    assert sent["data"]["status"] == "validated"
    assert sent["data"]["actor_ref"] == "communication-profile:outbound-slack-bot"
    assert sent["data"]["credential_ref"] == credential_refs["slack-bot"]

    mcp_client.call_tool_structured(
        "ingressEndpoint.configure",
        {
            "project_id": second_project_id,
            "driver": "public-url",
            "public_base_url": "https://second.stackos.example.com",
        },
    )
    second_routes = mcp_client.call_tool_structured(
        "ingressEndpoint.routes",
        {"project_id": second_project_id, "response_mode": "raw"},
    )
    assert second_routes["routes"] == []

    for project_id in (first_project_id, second_project_id):
        duplicate = mcp_client.call_tool_error(
            "communicationProfile.upsert",
            {
                "project_id": project_id,
                "key": "duplicate-slack-bot",
                "identity": {"display_name": "Duplicate slack-bot profile"},
                "provider_facets": {
                    "slack-bot": {
                        "credential_ref": credential_refs["slack-bot"],
                        "ingress_enabled": True,
                    }
                },
            },
        )
        assert duplicate["code"] == -32008
        assert duplicate["data"]["provider_key"] == "slack-bot"
        assert duplicate["data"]["credential_ref"] == credential_refs["slack-bot"]
        assert duplicate["data"]["owner_project_id"] == first_project_id
        assert duplicate["data"]["owner_profile_ref"] == ("communication-profile:shared-bot")
        assert "Turn off inbound webhook ownership" in duplicate["data"]["next_action"]

    provider_calls: list[dict[str, object]] = []

    async def unexpected_provider_call(self, **kwargs: object) -> object:
        provider_calls.append(kwargs)
        raise AssertionError("provider webhook mutation must not run")

    monkeypatch.setattr(ActionRepository, "execute", unexpected_provider_call)

    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        ResourceRepository(session).upsert_record(
            project_id=second_project_id,
            plugin_slug="communications",
            resource_key="communication-profile",
            external_id="communication-profile:unsafe-legacy",
            title="Unsafe legacy profile",
            data_json={
                "key": "unsafe-legacy",
                "profile_ref": "communication-profile:unsafe-legacy",
                "enabled": True,
                "identity": {"display_name": "Unsafe legacy profile"},
                "provider_facets": {
                    "slack-bot": {
                        "credential_ref": credential_refs["slack-bot"],
                        "bot_user_id": "ULEGACY",
                        "ingress_enabled": True,
                    },
                },
            },
            provenance_json={"source": "legacy-test-bypass"},
        )
        session.commit()

    sync_error = mcp_client.call_tool_error(
        "ingressEndpoint.sync",
        {
            "project_id": second_project_id,
            "response_mode": "raw",
        },
    )
    assert sync_error["code"] == -32008
    assert sync_error["data"]["owner_project_id"] == first_project_id
    assert provider_calls == []


def test_disabled_profile_does_not_retain_shared_account_ingress_ownership(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    first_project_id = int(seeded_project["data"]["id"])
    second_project = mcp_client.call_tool_structured(
        "project.create",
        {
            "slug": "enabled-ingress-owner",
            "name": "Enabled Ingress Owner",
            "domain": "enabled-owner.example",
            "locale": "en-US",
        },
    )
    second_project_id = int(second_project["data"]["id"])
    credential_ref = _seed_slack_credential(mcp_client, first_project_id)
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        AuthRepository(session).attach_account(
            project_id=second_project_id,
            credential_ref=credential_ref,
        )

    disabled = mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": first_project_id,
            "key": "disabled-owner",
            "enabled": False,
            "identity": {"display_name": "Disabled owner"},
            "provider_facets": {
                "slack-bot": {
                    "credential_ref": credential_ref,
                    "ingress_enabled": True,
                }
            },
            "response_mode": "raw",
        },
    )
    assert disabled["data"]["binding_status"] == "disabled"

    enabled = mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": second_project_id,
            "key": "enabled-owner",
            "identity": {"display_name": "Enabled owner"},
            "provider_facets": {
                "slack-bot": {
                    "credential_ref": credential_ref,
                    "ingress_enabled": True,
                }
            },
            "response_mode": "raw",
        },
    )
    assert enabled["data"]["binding_status"] == "ready"


def test_ingress_status_exposes_invalid_account_bindings_and_sync_fails_closed(
    mcp_client: MCPClient,
    seeded_project: dict,
    monkeypatch,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    mcp_client.call_tool_structured(
        "ingressEndpoint.configure",
        {
            "project_id": project_id,
            "driver": "public-url",
            "public_base_url": "https://stackos.example.com",
        },
    )
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        ResourceRepository(session).upsert_record(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-profile",
            external_id="communication-profile:broken-binding",
            title="Broken binding",
            data_json={
                "key": "broken-binding",
                "profile_ref": "communication-profile:broken-binding",
                "enabled": True,
                "identity": {"display_name": "Broken binding"},
                "provider_facets": {
                    "slack-bot": {
                        "credential_ref": "cred_missing_account",
                        "ingress_enabled": True,
                    }
                },
            },
            provenance_json={"source": "legacy-test-bypass"},
        )
        session.commit()

    status = mcp_client.call_tool_structured(
        "ingressEndpoint.status",
        {"project_id": project_id, "response_mode": "raw"},
    )
    assert status["ready"] is False
    assert status["routes"] == []
    assert len(status["blocked_uses"]) == 1
    blocked = status["blocked_uses"][0]
    assert blocked["profile_ref"] == "communication-profile:broken-binding"
    assert blocked["binding_state"] == "missing_account"
    assert "Account" in blocked["repair_message"]

    provider_calls: list[dict[str, object]] = []

    async def unexpected_provider_call(self, **kwargs: object) -> object:
        provider_calls.append(kwargs)
        raise AssertionError("provider webhook mutation must not run")

    monkeypatch.setattr(ActionRepository, "execute", unexpected_provider_call)
    sync_error = mcp_client.call_tool_error(
        "ingressEndpoint.sync",
        {
            "project_id": project_id,
            "response_mode": "raw",
        },
    )
    assert sync_error["message"] == "ValidationError"
    assert sync_error["data"]["blocked_uses"][0]["profile_ref"] == (
        "communication-profile:broken-binding"
    )
    assert provider_calls == []


def test_stale_local_tunnel_blocks_provider_sync_before_mutation(
    mcp_client: MCPClient,
    seeded_project: dict,
    monkeypatch,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref = _seed_telegram_credential(mcp_client, project_id)
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "stale-tunnel-bot",
            "identity": {"display_name": "Stale tunnel bot"},
            "provider_facets": {
                "telegram": {
                    "credential_ref": credential_ref,
                    "ingress_enabled": True,
                }
            },
        },
    )
    mcp_client.call_tool_structured(
        "ingressEndpoint.configure",
        {
            "project_id": project_id,
            "driver": "local-tunnel",
            "public_base_url": "https://stale.stackos.example.com",
            "driver_config": {"provider": "ngrok"},
        },
    )

    status = mcp_client.call_tool_structured(
        "ingressEndpoint.status",
        {"project_id": project_id, "response_mode": "raw"},
    )
    assert status["endpoint_fresh"] is False
    assert status["ready"] is False
    assert any("refresh the local tunnel" in note.lower() for note in status["notes"])

    provider_calls: list[dict[str, object]] = []

    async def unexpected_provider_call(self, **kwargs: object) -> object:
        provider_calls.append(kwargs)
        raise AssertionError("provider webhook mutation must not run")

    monkeypatch.setattr(ActionRepository, "execute", unexpected_provider_call)
    sync_error = mcp_client.call_tool_error(
        "ingressEndpoint.sync",
        {
            "project_id": project_id,
            "response_mode": "raw",
        },
    )
    assert sync_error["message"] == "ValidationError"
    assert "refresh the local tunnel" in str(sync_error["data"]["next_action"]).lower()
    assert provider_calls == []


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
            "response_mode": "raw",
        },
    )
    assert configured["data"]["driver"] == "local-tunnel"
    assert configured["data"]["driver_config"]["provider"] == "ngrok"
    assert configured["data"]["driver_config"]["discovery_url"] == (
        "http://127.0.0.1:4040/api/endpoints"
    )

    refreshed = mcp_client.call_tool_structured(
        "ingressEndpoint.refresh",
        {"project_id": project_id, "sync_profiles": False, "response_mode": "raw"},
    )
    endpoint = refreshed["data"]["endpoint"]
    assert endpoint["public_base_url"] == "https://stackos-local.ngrok.app"
    assert endpoint["metadata_json"]["last_refresh"]["resource"] == "endpoints"


def test_provider_neutral_communication_setup_resolves_targets_and_context(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    telegram_credential_ref = _seed_telegram_credential(mcp_client, project_id)
    slack_credential_ref = _seed_slack_credential(mcp_client, project_id)

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
                "telegram": {"credential_ref": telegram_credential_ref},
                "slack-bot": {
                    "credential_ref": slack_credential_ref,
                    "bot_user_id": "U123",
                },
            },
            "send_policy": {
                "mode": "explicit-targets",
                "allowed_target_refs": ["communication-target:internal-support"],
            },
            "response_mode": "raw",
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
            "response_mode": "raw",
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
            "response_mode": "raw",
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
            "response_mode": "raw",
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
            "response_mode": "raw",
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
            "response_mode": "raw",
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
            "response_mode": "raw",
        },
    )
    allowed_default_actor = mcp_client.call_tool_structured(
        "communicationTarget.resolve",
        {
            "project_id": project_id,
            "key": "internal-support",
            "source_surface_ref": "telegram-chat:-1001",
            "invoker_ref": "telegram-user:555",
            "response_mode": "raw",
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
            "response_mode": "raw",
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
            "response_mode": "raw",
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
            "provider_key": "telegram",
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
            "response_mode": "raw",
        },
    )
    assert telegram_allowed["allowed"] is True
    assert telegram_allowed["action_ref"] == "communications.telegram.message.send"
    assert telegram_allowed["action_input_defaults"]["surface_ref"] == "telegram-chat:-1001"
    assert (
        telegram_allowed["action_input_defaults"]["profile_ref"] == "communication-profile:support"
    )

    default_denied = mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "default-denied",
            "provider_key": "slack-bot",
            "surface_ref": "slack-channel:C999",
            "response_mode": "raw",
        },
    )
    assert default_denied["data"]["send_policy"]["mode"] == "deny"
    default_denied_resolution = mcp_client.call_tool_structured(
        "communicationTarget.resolve",
        {
            "project_id": project_id,
            "key": "default-denied",
            "profile_ref": "communication-profile:support",
            "response_mode": "raw",
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
            "response_mode": "raw",
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
            "response_mode": "raw",
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
            "response_mode": "raw",
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


def test_communication_send_executes_raw_dry_run_through_target(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref = _seed_slack_credential(mcp_client, project_id)

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "ops-bot",
            "identity": {"display_name": "Ops Bot"},
            "provider_facets": {"slack-bot": {"credential_ref": credential_ref}},
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
    assert sent["data"]["action_call"]["provider_key"] == "slack-bot"
    assert sent["data"]["action_call"]["run_id"] is None
    assert sent["data"]["action_call"]["run_plan_id"] is None
    assert sent["data"]["action_call"]["run_plan_step_id"] is None
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        action_call = session.get(ActionCall, sent["data"]["action_call_id"])
        assert action_call is not None
        assert action_call.run_id is None
        assert action_call.run_plan_id is None
        assert action_call.run_plan_step_id is None
    assert sent["data"]["output_json"]["dry_run"] is True
    assert sent["data"]["credential_ref"].startswith("cred_")
    assert "token-roadmap" not in json.dumps(sent)


def test_communication_send_hubspot_transactional_resolves_target_and_replays_safely(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref, contact_ref, email_ref = _seed_hubspot_transactional_credential(
        mcp_client,
        project_id,
        entitlement_confirmed=True,
        email_verified=False,
    )
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://api\.hubapi\.com/marketing/emails/2026-03\?.+$"),
        json={
            "results": [
                {
                    "id": "8701",
                    "name": "Order status template",
                    "isPublished": False,
                    "isTransactional": True,
                    "state": "DRAFT",
                }
            ],
            "total": 1,
        },
    )
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        email_ref = asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.marketing.emails.list",
                input_json={"limit": 1, "is_published": False},
                credential_ref=credential_ref,
            )
        ).data.output_json["results"][0]["email_ref"]
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://api.hubapi.com/crm/objects/2026-03/contacts/8501"
            "?properties=email&archived=false"
        ),
        json={
            "id": "8501",
            "properties": {"email": "customer@example.test"},
            "archived": False,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/marketing/transactional/2026-03/single-email/send",
        headers={"x-hubspot-correlation-id": "corr-communication-send"},
        json={
            "status": "PENDING",
            "statusId": "provider-status-9901",
            "eventId": {
                "id": "provider-event-8801",
                "created": "2026-07-22T23:30:00Z",
            },
            "requestedAt": "2026-07-22T23:30:00Z",
        },
    )
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "order-mailer",
            "identity": {
                "display_name": "Order Mailer",
                "purpose": "Send approved customer transaction updates.",
            },
            "provider_facets": {"hubspot": {"credential_ref": credential_ref}},
        },
    )
    mcp_client.call_tool_structured(
        "communicationSurface.upsert",
        {
            "project_id": project_id,
            "surface_ref": contact_ref,
            "provider_key": "hubspot",
            "kind": "hubspot-contact",
            "display_name": "Order recipient",
            "capabilities": {"supported": ["template", "template.data"]},
            "audience": "customer",
            "intent": {"category": "transactional-order-status"},
        },
    )
    mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "customer-order",
            "provider_key": "hubspot",
            "surface_ref": contact_ref,
            "profile_ref": "communication-profile:order-mailer",
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": ["communication-profile:order-mailer"],
                "allowed_target_refs": ["communication-target:customer-order"],
                "transactional_use_confirmed": True,
                "consent_or_relationship_confirmed": True,
                "legal_basis": "contract",
                "legal_basis_explanation": "Customer requested order status updates.",
                "marketing_contact_state": "non-marketing",
            },
        },
    )
    resolved = mcp_client.call_tool_structured(
        "communicationTarget.resolve",
        {
            "project_id": project_id,
            "key": "customer-order",
            "profile_ref": "communication-profile:order-mailer",
            "response_mode": "raw",
        },
    )
    assert resolved["allowed"] is True
    assert resolved["action_ref"] == "gtm.hubspot.transactional.single_email.send"
    assert resolved["surface_ref"] == contact_ref
    arguments = {
        "project_id": project_id,
        "to": "customer-order",
        "content": {
            "template_ref": email_ref,
            "template_data": {"order_number": "SO-202", "item_count": 3},
        },
        "intent_id": "customer-order-SO-202-status",
    }

    first = mcp_client.call_tool_structured("communication.send", arguments)
    replay = mcp_client.call_tool_structured("communication.send", arguments)

    assert "status" in first["data"], first
    assert first["data"]["status"] == "pending"
    assert first["data"]["action_ref"] == ("gtm.hubspot.transactional.single_email.send")
    assert first["data"]["provider_key"] == "hubspot"
    assert first["data"]["target_ref"] == "communication-target:customer-order"
    assert first["data"]["actor_ref"] == "communication-profile:order-mailer"
    assert first["data"]["surface_ref"] == contact_ref
    assert first["data"]["message_ref"].startswith("provider-object:")
    assert first["data"]["output_json"]["contact_ref"] == contact_ref
    assert first["data"]["output_json"]["email_ref"] == email_ref
    assert first["data"]["output_json"]["provider_status"] == "PENDING"
    assert first["data"]["output_json"]["contact_properties_updated"] is False
    assert first["data"]["output_json"]["marketing_contact_state_changed"] is False
    assert first["data"]["output_json"] == replay["data"]["output_json"]
    assert first["data"]["replayed"] is False
    assert replay["data"]["replayed"] is True
    assert replay["data"]["effects"] == ["replayed action result"]
    assert first["data"]["credential_ref"] == credential_ref
    post = httpx_mock.get_request(
        method="POST",
        url="https://api.hubapi.com/marketing/transactional/2026-03/single-email/send",
    )
    assert post is not None
    provider_payload = json.loads(post.content)
    assert provider_payload["message"]["to"] == "customer@example.test"
    assert provider_payload["message"]["sendId"].startswith("stackos:")
    assert provider_payload["contactProperties"] == {}
    assert provider_payload["customProperties"] == {
        "order_number": "SO-202",
        "item_count": 3,
    }
    assert len(httpx_mock.get_requests()) == 3
    rendered = json.dumps({"first": first, "replay": replay})
    for sensitive in (
        "hubspot-communication-secret",
        "customer@example.test",
        "provider-status-9901",
        "provider-event-8801",
        '"8501"',
        '"8701"',
    ):
        assert sensitive not in rendered


def test_communication_send_hubspot_denies_unconfirmed_entitlement(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref, contact_ref, email_ref = _seed_hubspot_transactional_credential(
        mcp_client,
        project_id,
        entitlement_confirmed=False,
    )
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "order-mailer",
            "identity": {"display_name": "Order Mailer"},
            "provider_facets": {"hubspot": {"credential_ref": credential_ref}},
        },
    )
    mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "customer-order",
            "provider_key": "hubspot",
            "surface_ref": contact_ref,
            "profile_ref": "communication-profile:order-mailer",
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": ["communication-profile:order-mailer"],
                "allowed_target_refs": ["communication-target:customer-order"],
                "transactional_use_confirmed": True,
                "consent_or_relationship_confirmed": True,
                "legal_basis": "contract",
                "legal_basis_explanation": "Customer requested order status updates.",
                "marketing_contact_state": "non-marketing",
            },
        },
    )

    err = mcp_client.call_tool_error(
        "communication.send",
        {
            "project_id": project_id,
            "to": "customer-order",
            "content": {"template_ref": email_ref},
            "intent_id": "entitlement-denied",
        },
    )

    detail = err["data"]["error"]
    assert detail["code"] == "COMM_PROVIDER_ENTITLEMENT_REQUIRED"
    assert detail["effect"] == "none"
    assert detail["resolved"]["entitlement"] == "transactional-email-addon"
    assert httpx_mock.get_requests() == []


def test_communication_send_hubspot_denies_incomplete_transactional_policy(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref, contact_ref, email_ref = _seed_hubspot_transactional_credential(
        mcp_client,
        project_id,
        entitlement_confirmed=True,
    )
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "order-mailer",
            "identity": {"display_name": "Order Mailer"},
            "provider_facets": {"hubspot": {"credential_ref": credential_ref}},
        },
    )
    mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "customer-order",
            "provider_key": "hubspot",
            "surface_ref": contact_ref,
            "profile_ref": "communication-profile:order-mailer",
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": ["communication-profile:order-mailer"],
                "allowed_target_refs": ["communication-target:customer-order"],
                "transactional_use_confirmed": True,
                "consent_or_relationship_confirmed": False,
                "legal_basis": "contract",
                "legal_basis_explanation": "Customer relationship is not verified.",
                "marketing_contact_state": "unknown",
            },
        },
    )

    err = mcp_client.call_tool_error(
        "communication.send",
        {
            "project_id": project_id,
            "to": "customer-order",
            "content": {"template_ref": email_ref},
            "intent_id": "policy-denied",
        },
    )

    detail = err["data"]["error"]
    assert detail["code"] == "COMM_HUBSPOT_TRANSACTIONAL_POLICY_REQUIRED"
    assert detail["effect"] == "none"
    fields = {item["policy_field"] for item in detail["failed_paths"]}
    assert fields == {"consent_or_relationship_confirmed", "marketing_contact_state"}
    assert httpx_mock.get_requests() == []


def test_communication_send_uses_single_slack_file_upload_for_text_and_files(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref = _seed_slack_credential(mcp_client, project_id)

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "ops-bot",
            "identity": {"display_name": "Ops Bot"},
            "provider_facets": {"slack-bot": {"credential_ref": credential_ref}},
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
    credential_ref = _seed_smtp_credential(mcp_client, project_id)

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support-mailer",
            "identity": {"display_name": "Support Mailer"},
            "provider_facets": {"smtp": {"credential_ref": credential_ref}},
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
    credential_ref = _seed_telegram_credential(
        mcp_client,
        project_id,
        account_name="support-bot",
    )

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support-bot",
            "identity": {"display_name": "Support Bot"},
            "provider_facets": {"telegram": {"credential_ref": credential_ref}},
        },
    )
    mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "customer-telegram",
            "provider_key": "telegram",
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
    assert image_sent["data"]["action_ref"] == "communications.telegram.message.send"
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        call = session.get(ActionCall, image_sent["data"]["action_call_id"])
        assert call is not None
        image_request_json = call.request_json or {}
    assert image_request_json["content"]["kind"] == "photo"
    assert image_request_json["content"]["file"] == {"url": "https://example.test/a.png"}

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
    assert multi_sent["data"]["action_ref"] == "communications.telegram.album.send"

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
    callback_data = request_json["buttons"][0][0]["callback_data"]
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
    credential_ref = _seed_slack_credential(mcp_client, project_id)

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "ops-bot",
            "identity": {"display_name": "Ops Bot"},
            "provider_facets": {"slack-bot": {"credential_ref": credential_ref}},
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
                source_provider="telegram",
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
    slack_credential_ref = _seed_slack_credential(mcp_client, project_id)
    telegram_credential_ref = _seed_telegram_credential(
        mcp_client,
        project_id,
        account_name="telegram",
    )

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "slack-ops",
            "identity": {"display_name": "Slack Ops"},
            "provider_facets": {"slack-bot": {"credential_ref": slack_credential_ref}},
        },
    )
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "telegram",
            "identity": {"display_name": "Telegram Bot"},
            "provider_facets": {"telegram": {"credential_ref": telegram_credential_ref}},
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
                source_provider="telegram",
                metadata_json={
                    "profile_ref": "communication-profile:telegram",
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
    credential_ref = _seed_slack_credential(mcp_client, project_id)

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "ops-bot",
            "identity": {"display_name": "Ops Bot"},
            "provider_facets": {"slack-bot": {"credential_ref": credential_ref}},
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
    claimed = mcp_client.call_tool_structured(
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
    assert sent["data"]["action_call"]["run_id"] == started["data"]["run_id"]
    assert sent["data"]["action_call"]["run_plan_id"] == created["data"]["id"]
    assert sent["data"]["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        action_call = session.get(ActionCall, sent["data"]["action_call_id"])
        assert action_call is not None
        assert action_call.run_id == started["data"]["run_id"]
        assert action_call.run_plan_id == created["data"]["id"]
        assert action_call.run_plan_step_id == claimed["data"]["id"]
    assert denied["code"] == -32007
    assert denied["data"]["tool"] == "communication.send"


def test_communication_send_batch_requires_its_own_grant_and_seals_native_recipients(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    """Recipient-list delivery is a distinct, durable operation and grant."""
    project_id = int(seeded_project["data"]["id"])
    credential_ref = _seed_telegram_credential(mcp_client, project_id, account_name="notices")
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "notices",
            "identity": {"display_name": "Notices"},
            "provider_facets": {"telegram": {"credential_ref": credential_ref}},
        },
    )
    mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "subscribers",
            "provider_key": "telegram",
            "surface_ref": "telegram-chat:1",
            "profile_ref": "communication-profile:notices",
            "metadata_json": {"action_mode": "auto"},
            "send_policy": {
                "mode": "explicit-target",
                "destination_mode": "recipient-list",
                "allowed_profile_refs": ["communication-profile:notices"],
                "allowed_target_refs": ["communication-target:subscribers"],
            },
        },
    )
    recipients = ["telegram-user:501", "telegram-user:502"]

    wrong_grant_plan = {
        "schema_version": "stackos.run-plan.v1",
        "key": "telegram-batch-wrong-grant.run",
        "title": "Telegram batch wrong grant",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "send",
                    "tool": "communication.send",
                    "targets": ["communication-target:subscribers"],
                }
            ]
        },
        "steps": [{"id": "send", "title": "Send"}],
    }
    created = mcp_client.call_tool_structured(
        "runPlan.create", {"project_id": project_id, "run_plan_json": wrong_grant_plan}
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start", {"project_id": project_id, "run_plan_id": created["data"]["id"]}
    )
    wrong_token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {"run_plan_id": created["data"]["id"], "step_id": "send", "run_token": wrong_token},
    )
    denied = mcp_client.call_tool_error(
        "communication.sendBatch",
        {
            "project_id": project_id,
            "to": "subscribers",
            "recipients": recipients,
            "text": "An update is available.",
            "dry_run": True,
            "run_token": wrong_token,
        },
    )
    assert denied["code"] == -32007
    assert denied["data"]["tool"] == "communication.sendBatch"

    batch_plan = {
        "schema_version": "stackos.run-plan.v1",
        "key": "telegram-batch-granted.run",
        "title": "Telegram batch granted",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "send",
                    "tool": "communication.sendBatch",
                    "targets": ["communication-target:subscribers"],
                }
            ]
        },
        "steps": [{"id": "send", "title": "Send"}],
    }
    created = mcp_client.call_tool_structured(
        "runPlan.create", {"project_id": project_id, "run_plan_json": batch_plan}
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start", {"project_id": project_id, "run_plan_id": created["data"]["id"]}
    )
    batch_token = started["data"]["run_token"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {"run_plan_id": created["data"]["id"], "step_id": "send", "run_token": batch_token},
    )
    accepted = mcp_client.call_tool_structured(
        "communication.sendBatch",
        {
            "project_id": project_id,
            "to": "subscribers",
            "recipients": recipients,
            "text": "An update is available.",
            "dry_run": True,
            "run_token": batch_token,
        },
    )
    assert accepted["data"]["status"] == "validated"
    assert accepted["data"]["action_ref"] == "communications.telegram.message.broadcast"
    assert accepted["data"]["action_call"]["run_id"] == started["data"]["run_id"]
    assert accepted["data"]["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        call = session.get(ActionCall, accepted["data"]["action_call_id"])
        assert call is not None
        assert call.request_json is not None
        assert call.request_json["recipients"] == recipients
        assert call.request_json["target_ref"] == "communication-target:subscribers"

    too_many = mcp_client.call_tool_error(
        "communication.sendBatch",
        {
            "project_id": project_id,
            "to": "subscribers",
            "recipients": [f"telegram-user:{index + 1}" for index in range(1001)],
            "text": "This must be rejected before any send.",
            "dry_run": True,
        },
    )
    assert too_many["code"] == -32602


@pytest.mark.parametrize("batch", [False, True])
def test_telegram_communication_seals_schedule_and_rejects_changed_intent(
    mcp_client: MCPClient, seeded_project: dict, batch: bool
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref = _seed_telegram_credential(mcp_client, project_id, account_name="scheduled")
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "scheduled",
            "identity": {"display_name": "Scheduled"},
            "provider_facets": {"telegram": {"credential_ref": credential_ref}},
        },
    )
    mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "scheduled",
            "provider_key": "telegram",
            "surface_ref": "telegram-chat:12345",
            "profile_ref": "communication-profile:scheduled",
            "metadata_json": {"action_mode": "auto"},
            "send_policy": {
                "mode": "explicit-target",
                **({"destination_mode": "recipient-list"} if batch else {}),
                "allowed_profile_refs": ["communication-profile:scheduled"],
                "allowed_target_refs": ["communication-target:scheduled"],
            },
        },
    )
    operation = "communication.sendBatch" if batch else "communication.send"
    arguments = {
        "project_id": project_id,
        "to": "scheduled",
        "text": "A scheduled update.",
        "intent_id": "schedule-proof",
        **({"recipients": ["telegram-user:501", "telegram-user:502"]} if batch else {}),
        "delivery": {
            "due_at": "2099-01-01T12:00:00Z",
            "expires_at": "2099-01-01T13:00:00Z",
            "account_interval_seconds": 5,
            "destination_interval_seconds": 3,
        },
    }
    accepted = mcp_client.call_tool_structured(operation, arguments)
    assert accepted.get("data", {}).get("ok") is True, accepted
    call_id = accepted["data"]["action_call_id"]
    assert accepted["data"]["status"] == "running"
    detail = mcp_client.call_tool_structured(
        "actionCall.items",
        {"project_id": project_id, "action_call_id": call_id, "response_mode": "raw"},
    )
    assert detail["job"]["state"] == "scheduled"
    assert detail["job"]["due_at"].startswith("2099-01-01T12:00:00")
    assert detail["job"]["expires_at"].startswith("2099-01-01T13:00:00")
    assert detail["job"]["pacing_json"] == {
        "account_interval_seconds": 5.0,
        "destination_interval_seconds": 3.0,
    }
    assert all(item["attempt_count"] == 0 for item in detail["items"])
    replay = mcp_client.call_tool_structured(operation, arguments)
    assert replay["data"]["action_call_id"] == call_id
    assert replay["data"]["replayed"] is True
    changed = mcp_client.call_tool_error(operation, {**arguments, "text": "Changed update."})
    assert "different" in changed["message"].lower() or "conflict" in changed["message"].lower()
    changed_timing = mcp_client.call_tool_error(
        operation,
        {**arguments, "delivery": {**arguments["delivery"], "account_interval_seconds": 8}},
    )
    assert (
        "different" in changed_timing["message"].lower()
        or "conflict" in changed_timing["message"].lower()
    )
    if not batch:
        changed_action = mcp_client.call_tool_error(
            operation,
            {
                **arguments,
                "attachments": [
                    {"type": "image", "url": "https://example.test/one.jpg"},
                    {"type": "image", "url": "https://example.test/two.jpg"},
                ],
            },
        )
        assert "conflict" in changed_action["message"].lower()


def test_communication_reply_requires_matching_run_plan_source_grant(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref = _seed_telegram_credential(
        mcp_client,
        project_id,
        account_name="support-bot",
    )

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support-bot",
            "identity": {"display_name": "Support Bot"},
            "provider_facets": {"telegram": {"credential_ref": credential_ref}},
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
                source_provider="telegram",
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
                    "sources": ["telegram"],
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
    claimed = mcp_client.call_tool_structured(
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
    assert reply["data"]["action_call"]["run_id"] == started["data"]["run_id"]
    assert reply["data"]["action_call"]["run_plan_id"] == created["data"]["id"]
    assert reply["data"]["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        action_call = session.get(ActionCall, reply["data"]["action_call_id"])
        assert action_call is not None
        assert action_call.run_id == started["data"]["run_id"]
        assert action_call.run_plan_id == created["data"]["id"]
        assert action_call.run_plan_step_id == claimed["data"]["id"]


@pytest.mark.parametrize("provider_succeeds", [True, False])
def test_background_communication_send_blocks_run_plan_step_until_terminal(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    provider_succeeds: bool,
) -> None:
    """A background delivery remains owned by its granted workflow step."""
    project_id = int(seeded_project["data"]["id"])
    credential_ref = _seed_slack_credential(mcp_client, project_id)

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "ops-bot",
            "identity": {"display_name": "Ops Bot"},
            "provider_facets": {"slack-bot": {"credential_ref": credential_ref}},
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
        "key": "background-communication-send.run",
        "title": "Background communication send",
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
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "notify",
            "run_token": run_token,
        },
    )

    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        action = session.exec(select(Action).where(Action.key == "slack-bot.message.send")).one()
        action.config_json = {**(action.config_json or {}), "execution_mode": "background"}
        session.add(action)
        session.commit()

    provider_started = Event()
    release_provider = Event()

    async def delayed_response(request: httpx.Request) -> httpx.Response:
        provider_started.set()
        assert await asyncio.to_thread(release_provider.wait, 5), "test provider was not released"
        return httpx.Response(
            200,
            json=(
                {
                    "ok": True,
                    "channel": "CALERTS",
                    "ts": "1770000000.000100",
                    "message": {"ts": "1770000000.000100", "text": "Queued notification."},
                }
                if provider_succeeds
                else {"ok": False, "error": "channel_not_found"}
            ),
            request=request,
        )

    httpx_mock.add_callback(
        delayed_response,
        method="POST",
        url="https://slack.com/api/chat.postMessage",
    )

    accepted: dict | None = None
    try:
        accepted = mcp_client.call_tool_structured(
            "communication.send",
            {
                "project_id": project_id,
                "to": "ops-alerts",
                "text": "Queued notification.",
                "intent_id": "queued-notification",
                "run_token": run_token,
            },
        )
        action_call = accepted["data"]["action_call"]
        assert action_call["status"] == "running"
        assert action_call["run_id"] == started["data"]["run_id"]
        assert action_call["run_plan_id"] == created["data"]["id"]
        assert action_call["run_plan_step_id"] == claimed["data"]["id"]
        assert accepted["data"]["status"] == "running"
        assert accepted["data"]["poll_operation"] == "actionCall.get"
        assert accepted["data"]["poll_arguments"] == {"action_call_id": action_call["id"]}
        assert accepted["data"]["next_poll_after_ms"] > 0
        assert "accepted background action" in accepted["data"]["effects"]
        assert "called provider connector" not in accepted["data"]["effects"]
        assert provider_started.wait(2)
        replay = mcp_client.call_tool_structured(
            "communication.send",
            {
                "project_id": project_id,
                "to": "ops-alerts",
                "text": "Queued notification.",
                "intent_id": "queued-notification",
                "run_token": run_token,
            },
        )
        assert replay["data"]["action_call_id"] == action_call["id"]
        assert replay["data"]["status"] == "running"
        assert replay["data"]["replayed"] is True
        assert replay["data"]["poll_arguments"] == accepted["data"]["poll_arguments"]
        assert replay["data"]["effects"] == ["replayed action result"]

        blocked = mcp_client.call_tool_error(
            "runPlan.recordStep",
            {
                "project_id": project_id,
                "run_plan_id": created["data"]["id"],
                "step_id": "notify",
                "status": "success",
                "run_token": run_token,
            },
        )
        assert blocked["message"] == "ValidationError", blocked
        assert blocked["data"]["action_call_ids"] == [action_call["id"]]
        assert blocked["data"]["pending_actions"] == [
            {
                "action_call_id": action_call["id"],
                "poll_operation": "actionCall.get",
                "poll_arguments": {"action_call_id": action_call["id"]},
            }
        ]
    finally:
        release_provider.set()

    assert accepted is not None
    deadline = time.monotonic() + 5
    while True:
        terminal = mcp_client.call_tool_structured(
            "actionCall.get",
            {
                "project_id": project_id,
                "action_call_id": accepted["data"]["action_call"]["id"],
                "response_mode": "raw",
            },
        )
        if terminal["status"] != "running":
            break
        assert time.monotonic() < deadline, terminal
        Event().wait(0.02)

    assert terminal["status"] == ("success" if provider_succeeds else "failed")
    terminal_replay = mcp_client.call_tool_structured(
        "communication.send",
        {
            "project_id": project_id,
            "to": "ops-alerts",
            "text": "Queued notification.",
            "intent_id": "queued-notification",
            "run_token": run_token,
        },
    )
    assert terminal_replay["data"]["action_call_id"] == action_call["id"]
    assert terminal_replay["data"]["status"] == ("sent" if provider_succeeds else "failed")
    assert terminal_replay["data"]["ok"] is provider_succeeds
    assert terminal_replay["data"]["replayed"] is True
    assert terminal_replay["data"]["poll_operation"] is None
    assert len(httpx_mock.get_requests(url="https://slack.com/api/chat.postMessage")) == 1
    completed = mcp_client.call_tool_structured(
        "runPlan.recordStep",
        {
            "project_id": project_id,
            "run_plan_id": created["data"]["id"],
            "step_id": "notify",
            "status": "success" if provider_succeeds else "failed",
            "run_token": run_token,
        },
    )
    assert completed["data"]["status"] == ("completed" if provider_succeeds else "failed")


def test_communication_reply_enforces_profile_response_policy(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref = _seed_telegram_credential(
        mcp_client,
        project_id,
        account_name="support-bot",
    )

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support-bot",
            "identity": {"display_name": "Support Bot"},
            "provider_facets": {"telegram": {"credential_ref": credential_ref}},
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
                source_provider="telegram",
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
    credential_ref = _seed_telegram_credential(
        mcp_client,
        project_id,
        account_name="support-bot",
    )

    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support-bot",
            "identity": {"display_name": "Support Bot"},
            "provider_facets": {"telegram": {"credential_ref": credential_ref}},
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
                source_provider="telegram",
                source_kind="telegram_message",
                source_message_ref="telegram-message:12345:77",
                metadata_json={
                    "profile_key": "support-bot",
                    "profile_ref": "communication-profile:support-bot",
                    "chat_ref": "telegram-chat:12345",
                    "thread_ref": "telegram-thread:12345:1",
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
    assert reply["data"]["action_ref"] == "communications.telegram.message.send"
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
            "response_mode": "raw",
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
            "response_mode": "raw",
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
            "response_mode": "raw",
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
    credential_ref = _seed_telegram_credential(mcp_client, project_id)

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
            "provider_facets": {"telegram": {"credential_ref": credential_ref}},
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
            "response_mode": "raw",
        },
    )
    assert created["data"]["key"] == "support-bot"
    telegram_facet = created["data"]["provider_facets"]["telegram"]
    assert telegram_facet["credential_ref"] == credential_ref
    assert created["data"]["identity"]["display_name"] == "Support Bot"

    fetched = mcp_client.call_tool_structured(
        "communicationProfile.get",
        {"project_id": project_id, "key": "support-bot", "response_mode": "raw"},
    )
    listed = mcp_client.call_tool_structured(
        "communicationProfile.list",
        {"project_id": project_id, "response_mode": "raw"},
    )

    assert fetched["key"] == "support-bot"
    assert [item["key"] for item in listed["items"]] == ["support-bot"]
    rendered = json.dumps({"created": created, "fetched": fetched, "listed": listed})
    assert "123456:ABC" not in rendered
    assert "telegram-secret" not in rendered


def test_telegram_retention_selector_schema_and_validation_are_discoverable(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref = _seed_telegram_credential(mcp_client, project_id)
    tool = next(
        item for item in mcp_client.list_tools() if item["name"] == "communicationProfile.upsert"
    )
    selector = tool["inputSchema"]["properties"]["visibility_policy"]
    guidance = selector["x-telegram-retention"]
    properties = selector["properties"]
    assert "updateNewMessage" in properties["allowed_update_types"]["items"]["enum"]
    assert properties["surface_mode"]["enum"] == [
        "allowlist",
        "all",
        "denylist",
        "disabled",
    ]
    assert properties["allowed_surface_refs"]["items"]["anyOf"][0]["pattern"] == (
        r"^telegram-chat:-?[1-9][0-9]*$"
    )
    assert "updateNewMessage" in guidance["chat_scoped_update_types"]
    assert guidance["account_scoped_update_types"] == ["updateUser", "updateFile"]
    assert guidance["account_scoped_surface_mode"] == "all"
    assert guidance["surface_ref_pattern"] == r"^telegram-chat:-?[1-9][0-9]*$"
    assert "telegram.chat.list" in selector["description"]
    assert "not a phone number" in selector["description"]
    assert "Account-scoped updateUser and updateFile" in selector["description"]

    arguments = {
        "project_id": project_id,
        "key": "selected-updates",
        "identity": {"display_name": "Selected Telegram updates"},
        "provider_facets": {"telegram": {"credential_ref": credential_ref}},
    }
    invalid_type = mcp_client.call_tool_error(
        "communicationProfile.upsert",
        {
            **arguments,
            "visibility_policy": {
                "allowed_surface_refs": ["telegram-chat:123"],
                "allowed_update_types": ["new-message"],
            },
        },
    )
    assert invalid_type["code"] == -32602
    assert invalid_type["data"]["field"] == "visibility_policy.allowed_update_types"
    assert "updateNewMessage" in invalid_type["data"]["allowed_update_types"]

    invalid_surface = mcp_client.call_tool_error(
        "communicationProfile.upsert",
        {
            **arguments,
            "visibility_policy": {
                "allowed_surface_refs": ["123"],
                "allowed_update_types": ["updateNewMessage"],
            },
        },
    )
    assert invalid_surface["code"] == -32602
    assert invalid_surface["data"]["field"] == "visibility_policy.allowed_surface_refs"
    assert "telegram-chat:" in invalid_surface["data"]["format"]

    account_scope = mcp_client.call_tool_error(
        "communicationProfile.upsert",
        {
            **arguments,
            "visibility_policy": {
                "surface_mode": "allowlist",
                "allowed_surface_refs": ["telegram-chat:123"],
                "allowed_update_types": ["updateUser"],
            },
        },
    )
    assert account_scope["code"] == -32602
    assert account_scope["data"]["field"] == "visibility_policy.surface_mode"
    assert account_scope["data"]["required_value"] == "all"

    created = mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            **arguments,
            "response_mode": "raw",
            "visibility_policy": {
                "allowed_surface_refs": ["telegram-chat:123"],
                "allowed_update_types": ["updateNewMessage"],
            },
        },
    )
    assert created["data"]["visibility_policy"]["allowed_surface_refs"] == ["telegram-chat:123"]


def test_telegram_live_read_after_explicit_disconnect_returns_connect_repair(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref = _seed_telegram_credential(mcp_client, project_id)
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "navigation",
            "identity": {"display_name": "Navigation"},
            "provider_facets": {"telegram": {"credential_ref": credential_ref}},
        },
    )
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        credential = session.exec(
            select(Credential).where(Credential.credential_ref == credential_ref)
        ).one()
        credential.status = "disconnected"
        credential.config_json = {
            **(credential.config_json or {}),
            "telegram_desired_connected": False,
        }
        session.add(credential)
        session.commit()

    blocked = mcp_client.call_tool_error(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "communications.telegram.chat.list",
            "credential_ref": credential_ref,
            "input_json": {"profile_ref": "communication-profile:navigation"},
            "response_mode": "raw",
        },
    )
    assert blocked["code"] == -32602
    assert blocked["data"]["status"] == "not_connected"
    assert blocked["data"]["next_action"] == "account.session.connect"
    assert blocked["data"]["provider_executed"] is False
    assert blocked["data"]["credential_ref"] == credential_ref


def test_telegram_transient_read_repairs_missing_tdlib_runtime_without_storing_result(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref = _seed_telegram_credential(mcp_client, project_id, account_kind="user")
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "navigation",
            "identity": {"display_name": "Navigation"},
            "provider_facets": {"telegram": {"credential_ref": credential_ref}},
        },
    )

    class MissingSessionRuntime:
        async def request(self, *_args, **_kwargs):
            raise TelegramTdlibServiceError("TDLib session is not configured")

    services = mcp_client.test_client.app.state.operation_services  # type: ignore[attr-defined]
    services["action_connectors"].register(TelegramActionConnector(MissingSessionRuntime()))
    blocked = mcp_client.call_tool_error(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "communications.telegram.chat.list",
            "credential_ref": credential_ref,
            "input_json": {"profile_ref": "communication-profile:navigation"},
            "response_mode": "raw",
        },
    )
    assert blocked["data"]["status"] == "not_connected"
    assert blocked["data"]["next_action"] == "account.session.connect"
    assert blocked["data"]["provider_executed"] is False
    assert blocked["data"]["credential_ref"] == credential_ref

    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        calls = session.exec(select(ActionCall).where(ActionCall.project_id == project_id)).all()
    assert len(calls) == 1
    assert calls[0].response_json["output_mode"] == "transient"
    assert "next_action" not in calls[0].response_json


def test_telegram_transient_native_timeout_has_safe_retry_without_auditing_body(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref = _seed_telegram_credential(mcp_client, project_id, account_kind="user")
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "navigation",
            "identity": {"display_name": "Navigation"},
            "provider_facets": {"telegram": {"credential_ref": credential_ref}},
        },
    )

    class TimedOutRuntime:
        async def request(self, *_args, **_kwargs):
            raise TelegramTdlibNativeError("TDLib request timed out awaiting a response.")

    services = mcp_client.test_client.app.state.operation_services  # type: ignore[attr-defined]
    services["action_connectors"].register(TelegramActionConnector(TimedOutRuntime()))
    blocked = mcp_client.call_tool_error(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "communications.telegram.chat.list",
            "credential_ref": credential_ref,
            "input_json": {"profile_ref": "communication-profile:navigation"},
            "response_mode": "raw",
        },
    )
    assert blocked["data"]["status"] == "retryable_timeout"
    assert blocked["data"]["retry_safe"] is True
    assert blocked["data"]["next_action"] == "communications.telegram.chat.list"
    assert "TDLib request timed out" not in str(blocked)
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        calls = session.exec(select(ActionCall).where(ActionCall.project_id == project_id)).all()
    assert len(calls) == 1
    assert calls[0].response_json["output_mode"] == "transient"
    assert "TDLib request timed out" not in str(calls[0].response_json)
    assert "next_action" not in calls[0].response_json


def test_telegram_background_download_timeout_is_terminal_with_retry_guidance(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref = _seed_telegram_credential(mcp_client, project_id)
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "navigation",
            "identity": {"display_name": "Navigation"},
            "provider_facets": {"telegram": {"credential_ref": credential_ref}},
        },
    )
    services = mcp_client.test_client.app.state.operation_services  # type: ignore[attr-defined]
    services["action_connectors"].register(TelegramActionConnector(DownloadTimeoutTelegram()))
    accepted = mcp_client.call_tool_structured(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "communications.telegram.file.download",
            "credential_ref": credential_ref,
            "input_json": {
                "profile_ref": "communication-profile:navigation",
                "file_ref": f"telegram-file:{credential_ref}:1522",
            },
            "response_mode": "raw",
        },
    )
    assert accepted["data"]["status"] == "running"
    action_call_id = accepted["data"]["action_call_id"]
    deadline = time.monotonic() + 5
    while True:
        terminal = mcp_client.call_tool_structured(
            "actionCall.get",
            {
                "project_id": project_id,
                "action_call_id": action_call_id,
                "response_mode": "raw",
            },
        )
        if terminal["status"] != "running":
            break
        assert time.monotonic() < deadline, terminal
        Event().wait(0.02)
    assert terminal["status"] == "failed"
    assert terminal["output_json"]["status"] == "retryable_timeout"
    assert terminal["output_json"]["retry_safe"] is True
    assert terminal["output_json"]["file_ref"] == f"telegram-file:{credential_ref}:1522"
    assert terminal["output_json"]["next_action"] == "communications.telegram.file.download"


@pytest.mark.parametrize("account_kind", ["bot", "user"])
def test_telegram_dry_runs_use_selected_account_capabilities(
    mcp_client: MCPClient,
    seeded_project: dict,
    account_kind: str,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref = _seed_telegram_credential(
        mcp_client,
        project_id,
        account_name=f"capability-{account_kind}",
        account_kind=account_kind,
    )
    profile_ref = "communication-profile:capability"
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "capability",
            "identity": {"display_name": "Capability"},
            "provider_facets": {"telegram": {"credential_ref": credential_ref}},
        },
    )
    mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "capability",
            "provider_key": "telegram",
            "surface_ref": "telegram-chat:12345",
            "profile_ref": profile_ref,
            "action_input_defaults": {"options": {"protect_content": True}},
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": [profile_ref],
                "allowed_target_refs": ["communication-target:capability"],
            },
        },
    )
    base_input = {
        "profile_ref": profile_ref,
        "surface_ref": "telegram-chat:12345",
        "content": {"kind": "text", "text": "A status update"},
    }
    for extra in (
        {"buttons": [[{"text": "Open", "url": "https://example.test"}]]},
        {"options": {"protect_content": True}},
    ):
        validation = mcp_client.call_tool_structured(
            "action.validate",
            {
                "project_id": project_id,
                "action_ref": "communications.telegram.message.send",
                "credential_ref": credential_ref,
                "input_json": {**base_input, **extra},
                "response_mode": "raw",
            },
        )
        assert validation["valid"] is (account_kind == "bot")
        if account_kind == "user":
            assert validation["issues"]

    button_send = {
        "project_id": project_id,
        "to": "capability",
        "text": "A status update",
        "controls": [{"type": "button", "label": "Open", "url": "https://example.test"}],
        "dry_run": True,
    }
    protected_send = {
        "project_id": project_id,
        "to": "capability",
        "text": "A status update",
        "dry_run": True,
    }
    if account_kind == "bot":
        for arguments in (button_send, protected_send):
            accepted = mcp_client.call_tool_structured("communication.send", arguments)
            assert accepted["data"]["status"] == "validated"
    else:
        button_error = mcp_client.call_tool_error("communication.send", button_send)
        assert button_error["data"]["error"]["code"] == "COMM_UNSUPPORTED_CAPABILITY"
        assert (
            button_error["data"]["error"]["failed_paths"][0]["required_capability"]
            == "control.button.url"
        )
        protected_error = mcp_client.call_tool_error("communication.send", protected_send)
        assert protected_error["data"]["issues"]
        assert protected_error["data"]["action_ref"] == ("communications.telegram.message.send")


def test_telegram_action_validate_passes_account_ref_without_resolving_secrets(
    mcp_client: MCPClient,
    seeded_project: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref = _seed_telegram_credential(mcp_client, project_id)
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "validation",
            "identity": {"display_name": "Validation"},
            "provider_facets": {"telegram": {"credential_ref": credential_ref}},
        },
    )
    observed: list[tuple[str | None, bool]] = []

    class CapturingTelegramConnector(TelegramActionConnector):
        def validate(self, request):
            observed.append((request.credential_ref, request.credential is None))
            return super().validate(request)

    async def reject_secret_resolution(*_args, **_kwargs):
        raise AssertionError("action.validate must not resolve provider secrets")

    services = mcp_client.test_client.app.state.operation_services  # type: ignore[attr-defined]
    services["action_connectors"].register(CapturingTelegramConnector(FakeTelegram()))
    monkeypatch.setattr(AuthRepository, "resolve_for_execution", reject_secret_resolution)
    validated = mcp_client.call_tool_structured(
        "action.validate",
        {
            "project_id": project_id,
            "action_ref": "communications.telegram.message.send",
            "credential_ref": credential_ref,
            "input_json": {
                "profile_ref": "communication-profile:validation",
                "surface_ref": "telegram-chat:12345",
                "content": {"kind": "text", "text": "Safe validation"},
            },
            "response_mode": "raw",
        },
    )
    assert validated["valid"] is True
    assert observed == [(credential_ref, True)]
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        calls = session.exec(select(ActionCall).where(ActionCall.project_id == project_id)).all()
    assert calls == []


def test_telegram_named_target_sender_ref_reaches_typed_dry_run_payload(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref = _seed_telegram_credential(mcp_client, project_id)
    profile_ref = "communication-profile:channel-voice"
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "channel-voice",
            "identity": {"display_name": "Channel voice"},
            "provider_facets": {"telegram": {"credential_ref": credential_ref}},
        },
    )
    mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "channel-voice",
            "provider_key": "telegram",
            "surface_ref": "telegram-chat:-1001",
            "profile_ref": profile_ref,
            "action_input_defaults": {"sender_ref": "telegram-chat:-900"},
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": [profile_ref],
                "allowed_target_refs": ["communication-target:channel-voice"],
            },
        },
    )
    accepted = mcp_client.call_tool_structured(
        "communication.send",
        {
            "project_id": project_id,
            "to": "channel-voice",
            "text": "A channel update",
            "dry_run": True,
        },
    )
    assert accepted["data"]["status"] == "validated"
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        call = session.get(ActionCall, accepted["data"]["action_call_id"])
        assert call is not None
        payload = dict(call.request_json or {})
    assert payload["sender_ref"] == "telegram-chat:-900"
    assert payload["surface_ref"] == "telegram-chat:-1001"
    assert payload["profile_ref"] == profile_ref
    assert payload["content"] == {"kind": "text", "text": "A channel update", "format": "plain"}
    validated = mcp_client.call_tool_structured(
        "action.validate",
        {
            "project_id": project_id,
            "action_ref": "communications.telegram.message.send",
            "credential_ref": credential_ref,
            "input_json": payload,
            "response_mode": "raw",
        },
    )
    assert validated["valid"] is True


def test_communication_profile_upsert_rejects_missing_telegram_account(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])

    missing = mcp_client.call_tool_error(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "missing-credential",
            "identity": {
                "display_name": "Missing Bot",
                "purpose": "Exercise credential validation.",
                "voice": "Concise.",
            },
            "provider_facets": {"telegram": {"credential_ref": "cred_missing"}},
            "access_policy": {
                "dm_mode": "allowlist",
                "group_mode": "allowlist",
                "user_mode": "allowlist",
                "allowed_chat_refs": ["telegram-chat:999"],
                "allowed_user_refs": ["telegram-user:555"],
            },
            "response_mode": "raw",
        },
    )

    assert missing["code"] == -32004
    assert missing["data"]["credential_ref"] == "cred_missing"


def test_communication_profile_upsert_rejects_provider_mismatched_account(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    slack_credential_ref = _seed_slack_credential(mcp_client, project_id)

    mismatch = mcp_client.call_tool_error(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "provider-mismatch",
            "identity": {
                "display_name": "Provider mismatch",
            },
            "provider_facets": {
                "telegram": {
                    "credential_ref": slack_credential_ref,
                }
            },
        },
    )

    assert mismatch["code"] == -32602
    assert mismatch["data"]["credential_ref"] == slack_credential_ref
    assert mismatch["data"]["credential_provider"] == "slack-bot"
    assert mismatch["data"]["provider_key"] == "telegram"


def test_tool_profile_resolve_mcp_resolves_telegram_profile_and_credential(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref = _seed_telegram_credential(mcp_client, project_id)
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
            "provider_facets": {"telegram": {"credential_ref": credential_ref}},
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
            "provider_key": "telegram",
            "tool_profile_key": "support-bot",
            "response_mode": "raw",
        },
    )

    rendered = json.dumps(resolved)
    assert resolved["ready"] is True
    assert resolved["provider"]["provider_key"] == "telegram"
    assert resolved["provider"]["setup_required"] is False
    assert resolved["tool_profile"]["key"] == "support-bot"
    assert resolved["tool_profile"]["credential_ref"] == credential_ref
    assert resolved["tool_profile"]["access_policy"]["allowed_user_refs"] == ["telegram-user:555"]
    assert resolved["credential"]["credential_ref"] == credential_ref
    assert resolved["credential"]["display_name"] == "support"
    assert resolved["missing"] == []
    assert "123456:ABC" not in rendered
    assert "telegram-secret" not in rendered


def test_tool_profile_resolve_mcp_rejects_profile_credential_mismatch(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    support_ref = _seed_telegram_credential(
        mcp_client,
        project_id,
        account_name="support",
    )
    analytics_ref = _seed_telegram_credential(
        mcp_client,
        project_id,
        account_name="analytics",
        bot_token="654321:XYZ",
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
            "provider_facets": {"telegram": {"credential_ref": support_ref}},
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
            "provider_key": "telegram",
            "tool_profile_key": "support-bot",
            "credential_ref": analytics_ref,
        },
    )

    assert err["code"] == -32602
    assert err["message"] == "ValidationError"
    assert err["data"]["profile_credential_ref"] == support_ref
    assert err["data"]["requested_credential_ref"] == analytics_ref


def test_tool_profile_resolve_mcp_redacts_profile_sections(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    credential_ref = _seed_telegram_credential(mcp_client, project_id)
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
                "telegram": {
                    "credential_ref": credential_ref,
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
            "provider_key": "telegram",
            "tool_profile_key": "support-bot",
            "response_mode": "raw",
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
    credential_ref = _seed_telegram_credential(mcp_client, project_id)

    err = mcp_client.call_tool_error(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "support-bot",
            "identity": {"display_name": "Support Bot"},
            "provider_facets": {
                "telegram": {
                    "credential_ref": credential_ref,
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
    credential_ref = _seed_smtp_credential(mcp_client, project_id)

    resolved = mcp_client.call_tool_structured(
        "toolProfile.resolve",
        {
            "project_id": project_id,
            "provider_key": "smtp",
            "credential_ref": credential_ref,
            "response_mode": "raw",
        },
    )

    rendered = json.dumps(resolved)
    assert resolved["ready"] is True
    assert resolved["provider"]["setup_required"] is False
    assert resolved["tool_profile"] is None
    assert resolved["credential"]["provider_key"] == "smtp"
    assert resolved["credential"]["display_name"] == "primary"
    assert resolved["credential"]["credential_ref"] == credential_ref
    assert resolved["next_action"] is None
    assert "smtp-secret" not in rendered


def test_tool_profile_resolve_mcp_selects_exact_generic_account(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    _seed_smtp_credential(mcp_client, project_id, account_name="primary")
    secondary_ref = _seed_smtp_credential(
        mcp_client,
        project_id,
        account_name="secondary",
    )

    resolved = mcp_client.call_tool_structured(
        "toolProfile.resolve",
        {
            "project_id": project_id,
            "provider_key": "smtp",
            "credential_ref": secondary_ref,
            "response_mode": "raw",
        },
    )

    assert resolved["ready"] is True
    assert resolved["credential"]["credential_ref"] == secondary_ref
    assert resolved["credential"]["display_name"] == "secondary"
