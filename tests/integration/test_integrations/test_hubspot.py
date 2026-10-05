"""HubSpot credential-probe contract tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock
from stackos_connectors.connectors.hubspot.integration import HubSpotIntegration
from stackos_connectors.errors import IntegrationDownError
from stackos_connectors.probe import AuthMethodProbeContext


def _context(method: str) -> AuthMethodProbeContext:
    return AuthMethodProbeContext(auth_method_key=method)


def test_hubspot_private_app_probe_records_only_safe_scope_and_account_evidence(
    httpx_mock: HTTPXMock,
    project_id: int,
    host_account_probe,
) -> None:
    token = "hubspot-private-token-canary"
    response_token = "hubspot-provider-token-canary"
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/oauth/v2/private-apps/get/access-token-info",
        is_reusable=True,
        json={
            "userId": 123,
            "hubId": 456,
            "appId": 789,
            "scopes": ["crm.objects.contacts.read", "oauth", "crm.objects.contacts.read"],
            "tokenKey": response_token,
        },
    )

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = HubSpotIntegration(
                payload=json.dumps({"access_token": token}).encode(),
                http=client,
                probe_context=_context(
                    "private_app_token",
                ),
                qps_override=1000.0,
            )
            return await integration.test_credentials()

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(result)

    assert json.loads(request.content) == {"tokenKey": token}
    assert result == {
        "ok": True,
        "vendor": "hubspot",
        "status": "ok",
        "metadata": {
            "evidence": {
                "grants": ["crm.objects.contacts.read", "oauth"],
                "account": {
                    "provider_account_id": "456",
                    "display_name": None,
                    "metadata": {"hub_id": 456, "app_id": 789, "user_id": 123},
                },
            }
        },
    }
    assert token not in rendered
    assert response_token not in rendered
    host_result, audit = asyncio.run(
        host_account_probe(
            "hubspot",
            "private_app_token",
            {"access_token": token},
        )
    )
    assert host_result.ok is True
    assert host_result.metadata["evidence"] == result["metadata"]["evidence"]
    assert audit["result"]["metadata"]["evidence"] == result["metadata"]["evidence"]
    assert "tokenKey" not in json.dumps(audit)
    assert token not in json.dumps(audit)
    assert response_token not in json.dumps(audit)
    assert len(httpx_mock.get_requests()) == 2  # One native probe and one real host probe.


def test_hubspot_private_app_probe_redacts_token_canaries_from_failure_audit(
    httpx_mock: HTTPXMock,
    project_id: int,
    host_account_probe,
) -> None:
    token = "hubspot-private-token-canary"
    echoed_token = "hubspot-provider-token-canary"
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/oauth/v2/private-apps/get/access-token-info",
        status_code=401,
        is_reusable=True,
        json={"tokenKey": echoed_token, "message": f"token={token}"},
    )

    async def go() -> None:
        async with httpx.AsyncClient() as client:
            integration = HubSpotIntegration(
                payload=json.dumps({"access_token": token}).encode(),
                http=client,
                probe_context=_context(
                    "private_app_token",
                ),
                qps_override=1000.0,
            )
            await integration.test_credentials()

    with pytest.raises(IntegrationDownError) as exc_info:
        asyncio.run(go())

    rendered = json.dumps({"detail": str(exc_info.value), "data": exc_info.value.data})
    host_result, audit_row = asyncio.run(
        host_account_probe(
            "hubspot",
            "private_app_token",
            {"access_token": token},
        )
    )
    assert host_result.ok is False
    assert audit_row["ok"] is False
    audit = json.dumps(audit_row)
    assert token not in rendered
    assert echoed_token not in rendered
    assert token not in audit
    assert echoed_token not in audit
    assert len(httpx_mock.get_requests()) == 2


def test_hubspot_oauth_probe_is_read_only_and_does_not_claim_scopes(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    token = "hubspot-oauth-token-canary"
    httpx_mock.add_response(
        method="GET",
        url="https://api.hubapi.com/integrations/v1/me",
        json={
            "portalId": 456,
            "timeZone": "America/Los_Angeles",
            "currency": "USD",
            "utcOffset": "-07:00",
            "echo": token,
        },
    )

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = HubSpotIntegration(
                payload=json.dumps({"access_token": token}).encode(),
                http=client,
                probe_context=_context(
                    "oauth2_authorization_code",
                ),
                qps_override=1000.0,
            )
            return await integration.test_credentials()

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]

    assert request.headers["Authorization"] == f"Bearer {token}"
    assert result == {
        "ok": True,
        "vendor": "hubspot",
        "status": "ok",
        "portal_id": "456",
        "timezone": "America/Los_Angeles",
        "currency": "USD",
        "metadata": {
            "evidence": {
                "account": {
                    "provider_account_id": "456",
                    "display_name": None,
                    "metadata": {
                        "portal_id": 456,
                        "timezone": "America/Los_Angeles",
                        "currency": "USD",
                    },
                }
            }
        },
    }
    assert "grants" not in json.dumps(result)
    assert token not in json.dumps(result)


def test_hubspot_private_app_probe_rejects_an_unreviewed_posture_without_a_request(
    httpx_mock, host_probe_preflight
):
    result = host_probe_preflight(
        "hubspot",
        "private_app_token",
        evidence_source="unavailable",
        enforcement="provider_enforced",
    )
    assert result == {
        "ok": False,
        "vendor": "hubspot",
        "status": "unsupported_permission_verification",
        "summary": (
            "HubSpot private-app scope evidence requires the reviewed provider probe posture."
        ),
    }
    assert httpx_mock.get_requests() == []
