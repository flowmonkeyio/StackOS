"""Pipedrive credential-probe contract tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos.actions.connectors import ActionConnectorRequest
from stackos.actions.pipedrive import PipedriveActionConnector
from stackos.auth_providers.repository.schema import (
    AuthMethodProbeContext,
    PermissionVerificationOut,
    ResolvedCredential,
)
from stackos.db.models import Credential, IntegrationCredential
from stackos.integrations.pipedrive import PipedriveIntegration
from stackos.repositories.base import ValidationError


def _context(method: str, *, evidence_source: str, enforcement: str) -> AuthMethodProbeContext:
    return AuthMethodProbeContext(
        auth_method_key=method,
        permission_verification=PermissionVerificationOut(
            evidence_source=evidence_source,
            enforcement=enforcement,
        ),
    )


def _action_request(
    *,
    payload: dict[str, str],
    config: dict[str, object],
    project_id: int,
) -> ActionConnectorRequest:
    credential = ResolvedCredential(
        credential=Credential(credential_ref="cred_pipedrive", provider_key="pipedrive"),
        integration=IntegrationCredential(
            encrypted_payload=b"not-used",
            nonce=b"0" * 12,
        ),
        secret_payload=json.dumps(payload).encode(),
        config_json=config,
    )
    return ActionConnectorRequest(
        project_id=project_id,
        plugin_slug="gtm",
        action_key="pipedrive.deals.list",
        action_ref="gtm.pipedrive.deals.list",
        provider_key="pipedrive",
        operation="deals.list",
        input_json={},
        config_json={},
        credential=credential,
    )


def test_pipedrive_api_token_probe_uses_saved_method_transport_without_grant_evidence(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    token = "pipedrive-static-token-canary"
    httpx_mock.add_response(
        method="GET",
        url="https://acme.pipedrive.com/api/v1/users/me",
        json={
            "success": True,
            "data": {
                "id": 123,
                "name": "Ada Operator",
                "company_id": 456,
                "company_name": "Acme",
            },
        },
    )

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = PipedriveIntegration(
                payload=json.dumps({"api_token": token}).encode(),
                project_id=project_id,
                http=client,
                api_domain="https://acme.pipedrive.com",
                probe_context=_context(
                    "api_token",
                    evidence_source="unavailable",
                    enforcement="provider_enforced",
                ),
                qps_override=1000.0,
            )
            return await integration.test_credentials()

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]

    assert request.headers["x-api-token"] == token
    assert "Authorization" not in request.headers
    assert result == {
        "ok": True,
        "vendor": "pipedrive",
        "status": "ok",
        "user_id": "123",
        "user_name": "Ada Operator",
        "company_id": "456",
        "company_name": "Acme",
        "metadata": {
            "evidence": {
                "account": {
                    "provider_account_id": "456",
                    "display_name": "Acme",
                    "metadata": {
                        "user_id": 123,
                        "user_name": "Ada Operator",
                        "company_id": 456,
                        "company_name": "Acme",
                    },
                }
            }
        },
    }
    assert "grants" not in json.dumps(result)
    assert token not in json.dumps(result)


def test_pipedrive_manual_oauth_fails_closed_without_oauth_response_evidence(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = PipedriveIntegration(
                payload=b'{"access_token":"pipedrive-oauth-token-canary"}',
                project_id=project_id,
                http=client,
                api_domain="https://acme.pipedrive.com",
                probe_context=_context(
                    "oauth2_token",
                    evidence_source="unavailable",
                    enforcement="local_required",
                ),
                qps_override=1000.0,
            )
            return await integration.test_credentials()

    assert asyncio.run(go()) == {
        "ok": False,
        "vendor": "pipedrive",
        "status": "permission_evidence_unavailable",
        "summary": (
            "Pipedrive manual OAuth tokens have no verified scope evidence; reconnect with OAuth."
        ),
    }
    assert httpx_mock.get_requests() == []


def test_pipedrive_oauth_probe_uses_bearer_only_when_the_saved_method_is_oauth(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    token = "pipedrive-oauth-token-canary"
    httpx_mock.add_response(
        method="GET",
        url="https://acme.pipedrive.com/api/v1/users/me",
        json={"success": True, "data": {"id": 123, "name": "Ada Operator"}},
    )

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = PipedriveIntegration(
                payload=json.dumps({"access_token": token, "api_token": "wrong-token"}).encode(),
                project_id=project_id,
                http=client,
                api_domain="https://acme.pipedrive.com",
                probe_context=_context(
                    "oauth2_authorization_code",
                    evidence_source="oauth_response",
                    enforcement="local_required",
                ),
                qps_override=1000.0,
            )
            return await integration.test_credentials()

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]
    assert request.headers["Authorization"] == f"Bearer {token}"
    assert "x-api-token" not in request.headers
    assert "grants" not in json.dumps(result)
    assert token not in json.dumps(result)


def test_pipedrive_action_uses_api_token_only_for_the_saved_api_token_method(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(method="GET", json={"data": []})
    request = _action_request(
        payload={"api_token": "pipedrive-api-canary", "access_token": "wrong-oauth-token"},
        config={"auth_method_key": "api_token", "api_domain": "acme.pipedrive.com"},
        project_id=project_id,
    )

    asyncio.run(PipedriveActionConnector().execute(request))
    sent = httpx_mock.get_requests()[0]

    assert sent.headers["x-api-token"] == "pipedrive-api-canary"
    assert "Authorization" not in sent.headers
    assert str(sent.url).startswith("https://acme.pipedrive.com/api/v2/deals")


def test_pipedrive_action_rejects_unknown_method_and_non_pipedrive_domain_before_http(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    connector = PipedriveActionConnector()
    unknown_method = _action_request(
        payload={"api_token": "pipedrive-api-canary"},
        config={"api_domain": "acme.pipedrive.com"},
        project_id=project_id,
    )
    unsafe_domain = _action_request(
        payload={"api_token": "pipedrive-api-canary"},
        config={"auth_method_key": "api_token", "api_domain": "attacker.example"},
        project_id=project_id,
    )
    invalid_port = _action_request(
        payload={"api_token": "pipedrive-api-canary"},
        config={"auth_method_key": "api_token", "api_domain": "https://acme.pipedrive.com:bad"},
        project_id=project_id,
    )

    with pytest.raises(ValidationError, match="saved auth method"):
        asyncio.run(connector.execute(unknown_method))
    with pytest.raises(ValidationError, match=r"tenant .pipedrive.com"):
        asyncio.run(connector.execute(unsafe_domain))
    with pytest.raises(ValidationError, match="HTTPS origin"):
        asyncio.run(connector.execute(invalid_port))

    assert httpx_mock.get_requests() == []
