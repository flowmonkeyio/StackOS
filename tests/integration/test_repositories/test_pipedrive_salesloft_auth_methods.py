"""Pipedrive and Salesloft auth-method posture contracts."""

from __future__ import annotations

import asyncio
import json

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.db.models import Credential, CredentialAccount, CredentialScope


def test_pipedrive_and_salesloft_publish_their_exact_auth_evidence_postures(
    session: Session,
) -> None:
    providers = {
        provider.key: provider
        for provider in AuthRepository(session).list_providers()
        if provider.key in {"pipedrive", "salesloft"}
    }
    expected = {
        "pipedrive": {
            "oauth2_authorization_code": ("oauth_response", "local_required"),
            "oauth2_token": ("unavailable", "local_required"),
            "api_token": ("unavailable", "provider_enforced"),
        },
        "salesloft": {
            "oauth2_authorization_code": ("oauth_response", "local_required"),
            "oauth2_token": ("unavailable", "local_required"),
            "api_key": ("unavailable", "provider_enforced"),
        },
    }

    for provider_key, method_expectations in expected.items():
        methods = {method.key: method for method in providers[provider_key].auth_methods}
        assert set(methods) == set(method_expectations)
        for method_key, (evidence_source, enforcement) in method_expectations.items():
            posture = methods[method_key].permission_verification
            assert posture is not None
            assert (posture.evidence_source, posture.enforcement) == (
                evidence_source,
                enforcement,
            )

    assert "fails closed" in providers["pipedrive"].auth_methods[1].description
    assert "enforces permissions" in providers["pipedrive"].auth_methods[2].description
    assert "fails closed" in providers["salesloft"].auth_methods[1].description
    assert "enforces permissions" in providers["salesloft"].auth_methods[2].description


@pytest.mark.parametrize(
    ("provider_key", "method_key", "fields", "response", "expected_account_id"),
    [
        (
            "pipedrive",
            "api_token",
            {"api_token": "pipedrive-canary", "company_domain": "acme"},
            {
                "success": True,
                "data": {
                    "id": 123,
                    "name": "Ada Operator",
                    "company_id": 456,
                    "company_name": "Acme",
                },
            },
            "456",
        ),
        (
            "salesloft",
            "api_key",
            {"api_key": "salesloft-canary"},
            {"id": 123, "guid": "user-guid", "name": "Ada Operator"},
            "user-guid",
        ),
    ],
)
def test_provider_enforced_static_probe_persists_account_without_inventing_grants(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    provider_key: str,
    method_key: str,
    fields: dict[str, str],
    response: dict[str, object],
    expected_account_id: str,
) -> None:
    token_canary = next(iter(fields.values()))
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key=provider_key,
        auth_method_key=method_key,
        profile_key=f"{provider_key}-static",
        fields=fields,
    ).data
    httpx_mock.add_response(method="GET", json=response)

    tested = asyncio.run(
        repo.test(project_id=project_id, credential_ref=stored.credential_ref)
    ).data
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == stored.credential_ref)
    ).one()
    assert credential.id is not None
    account = session.exec(
        select(CredentialAccount).where(CredentialAccount.credential_id == credential.id)
    ).one()
    scopes = session.exec(
        select(CredentialScope).where(CredentialScope.credential_id == credential.id)
    ).all()
    rendered = json.dumps(
        {
            "test": tested.model_dump(mode="json"),
            "status": repo.status(
                project_id=project_id,
                provider_key=provider_key,
            ).model_dump(mode="json"),
        }
    )

    assert tested.ok is True
    assert tested.metadata["evidence"]["account"]["provider_account_id"] == expected_account_id
    assert "grants" not in tested.metadata["evidence"]
    assert account.provider_account_id == expected_account_id
    assert scopes == []
    assert token_canary not in rendered
