"""Salesloft credential-probe contract tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos.actions.connectors import ActionConnectorRequest
from stackos.actions.salesloft import SalesloftActionConnector
from stackos.auth_providers.repository.schema import (
    AuthMethodProbeContext,
    PermissionVerificationOut,
    ResolvedCredential,
)
from stackos.db.models import Credential, IntegrationCredential
from stackos.integrations.salesloft import SalesloftIntegration
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
    auth_method_key: str | None,
    project_id: int,
) -> ActionConnectorRequest:
    config = {"auth_method_key": auth_method_key} if auth_method_key is not None else {}
    credential = ResolvedCredential(
        credential=Credential(credential_ref="cred_salesloft", provider_key="salesloft"),
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
        action_key="salesloft.cadence_membership.create",
        action_ref="gtm.salesloft.cadence_membership.create",
        provider_key="salesloft",
        operation="cadence_membership.create",
        input_json={"cadence_ref": "10", "person_ref": "20"},
        config_json={},
        credential=credential,
    )


def test_salesloft_api_key_probe_uses_bearer_without_local_grant_evidence(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    token = "salesloft-api-key-canary"
    httpx_mock.add_response(
        method="GET",
        url="https://api.salesloft.com/v2/me",
        json={"id": 123, "guid": "user-guid", "name": "Ada Operator", "email": "ada@example.com"},
    )

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = SalesloftIntegration(
                payload=json.dumps({"api_key": token}).encode(),
                project_id=project_id,
                http=client,
                probe_context=_context(
                    "api_key",
                    evidence_source="unavailable",
                    enforcement="provider_enforced",
                ),
                qps_override=1000.0,
            )
            return await integration.test_credentials()

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]

    assert request.headers["Authorization"] == f"Bearer {token}"
    assert result == {
        "ok": True,
        "vendor": "salesloft",
        "status": "ok",
        "user_id": "123",
        "user_guid": "user-guid",
        "user_name": "Ada Operator",
        "metadata": {
            "evidence": {
                "account": {
                    "provider_account_id": "user-guid",
                    "display_name": "Ada Operator",
                    "metadata": {
                        "user_id": 123,
                        "user_guid": "user-guid",
                        "user_name": "Ada Operator",
                    },
                }
            }
        },
    }
    assert "grants" not in json.dumps(result)
    assert token not in json.dumps(result)


def test_salesloft_manual_oauth_fails_closed_without_oauth_response_evidence(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = SalesloftIntegration(
                payload=b'{"access_token":"salesloft-oauth-token-canary"}',
                project_id=project_id,
                http=client,
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
        "vendor": "salesloft",
        "status": "permission_evidence_unavailable",
        "summary": (
            "Salesloft manual OAuth tokens have no verified scope evidence; reconnect with OAuth."
        ),
    }
    assert httpx_mock.get_requests() == []


def test_salesloft_rejects_unknown_saved_method_without_calling_the_provider(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = SalesloftIntegration(
                payload=b'{"access_token":"salesloft-token-canary"}',
                project_id=project_id,
                http=client,
                probe_context=_context(
                    "unknown_method",
                    evidence_source="unavailable",
                    enforcement="provider_enforced",
                ),
                qps_override=1000.0,
            )
            return await integration.test_credentials()

    assert asyncio.run(go()) == {
        "ok": False,
        "vendor": "salesloft",
        "status": "unsupported_auth_method",
        "summary": "Salesloft credential test requires a recognized saved auth method.",
    }
    assert httpx_mock.get_requests() == []


def test_salesloft_action_uses_the_saved_method_to_select_the_bearer_value(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(method="POST", json={"id": 30})
    request = _action_request(
        payload={"api_key": "salesloft-api-key-canary", "access_token": "wrong-oauth-token"},
        auth_method_key="api_key",
        project_id=project_id,
    )

    asyncio.run(SalesloftActionConnector().execute(request))
    sent = httpx_mock.get_requests()[0]

    assert sent.headers["Authorization"] == "Bearer salesloft-api-key-canary"
    assert json.loads(sent.content) == {"cadence_id": "10", "person_id": "20"}


def test_salesloft_action_rejects_an_unknown_saved_method_before_http(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    request = _action_request(
        payload={"access_token": "salesloft-oauth-token-canary"},
        auth_method_key=None,
        project_id=project_id,
    )

    with pytest.raises(ValidationError, match="saved auth method"):
        asyncio.run(SalesloftActionConnector().execute(request))

    assert httpx_mock.get_requests() == []
