"""AIGNC credential setup and non-generation probes use shared auth storage."""

from __future__ import annotations

import asyncio
import json

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.db.models import Credential, CredentialScope, CredentialUsageEvent
from stackos.repositories.projects import IntegrationCredentialRepository

_API_KEY = "fixture-aignc-auth-private-key"


def test_aignc_static_auth_declares_provider_enforced_permissions(session: Session) -> None:
    provider = next(
        provider for provider in AuthRepository(session).list_providers() if provider.key == "aignc"
    )
    assert len(provider.auth_methods) == 1
    method = provider.auth_methods[0]
    assert method.key == "api_key"
    assert method.permission_verification is not None
    assert method.permission_verification.evidence_source == "unavailable"
    assert method.permission_verification.enforcement == "provider_enforced"
    assert len(method.fields) == 1
    assert method.fields[0].key == "api_key"
    assert method.fields[0].secret is True


@pytest.mark.parametrize("probe", ("success", "unauthorized", "malformed"))
def test_aignc_auth_probe_persists_safe_diagnostics_without_inventing_capabilities(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    probe: str,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key="aignc",
        auth_method_key="api_key",
        display_name="AIGNC fixture",
        fields={"api_key": _API_KEY},
    ).data
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == stored.credential_ref)
    ).one()
    assert credential.integration_credential_id is not None
    assert (
        IntegrationCredentialRepository(session).get_decrypted(credential.integration_credential_id)
        == _API_KEY.encode()
    )
    assert _API_KEY not in json.dumps(credential.config_json)
    if probe == "success":
        payload = {"data": [{"id": "gemini-3.8-flash", "pricing": {"input": 999}}]}
    elif probe == "unauthorized":
        payload = {"error": {"code": "unauthorized", "message": f"Rejected {_API_KEY}"}}
    else:
        payload = {"models": "undocumented shape"}
    httpx_mock.add_response(
        method="GET",
        url="https://cli-api.f2nd.com/v1/models",
        status_code=401 if probe == "unauthorized" else 200,
        json=payload,
        headers={"cf-aig-log-id": "auth-probe-fixture", "X-Model-Pricing": "ignored-pricing"},
    )
    tested = asyncio.run(
        repo.test(project_id=project_id, credential_ref=stored.credential_ref)
    ).data
    assert tested.ok is (probe == "success")
    if probe == "success":
        assert tested.metadata["model_count"] == 1
        assert tested.metadata["generation_verified"] is False
    else:
        assert tested.status == "failed"
        assert "failed" in tested.summary
    assert len(httpx_mock.get_requests()) == 1
    request = httpx_mock.get_requests()[0]
    assert request.headers["Authorization"] == f"Bearer {_API_KEY}"
    assert request.method == "GET"
    assert not request.content
    assert not session.exec(
        select(CredentialScope).where(CredentialScope.credential_id == credential.id)
    ).all()
    events = session.exec(
        select(CredentialUsageEvent).where(CredentialUsageEvent.credential_id == credential.id)
    ).all()
    assert any(event.operation == "account.test" for event in events)
    rendered = json.dumps(
        {
            "stored": stored.model_dump(mode="json"),
            "tested": tested.model_dump(mode="json"),
            "status": repo.status(project_id=project_id, provider_key="aignc").model_dump(
                mode="json"
            ),
            "events": [event.model_dump(mode="json") for event in events],
        }
    )
    assert _API_KEY not in rendered
    assert "ignored-pricing" not in rendered
    assert '"pricing"' not in rendered
