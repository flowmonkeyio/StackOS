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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stackos_connectors.connectors.pipedrive.integration import PipedriveIntegration
    from stackos_connectors.connectors.salesloft.integration import SalesloftIntegration

    native_class = PipedriveIntegration if provider_key == "pipedrive" else SalesloftIntegration
    original_probe = native_class.test_credentials
    native_calls = []

    async def check_native_context(instance):
        native_calls.append(instance)
        assert instance.probe_context.auth_method_key == method_key
        assert instance.probe_context.permission_verification is None
        return await original_probe(instance)

    monkeypatch.setattr(native_class, "test_credentials", check_native_context)
    token_canary = next(iter(fields.values()))
    repo = AuthRepository(session)
    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key=provider_key,
        auth_method_key=method_key,
        display_name=f"{provider_key}-static",
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
    assert len(native_calls) == 1
    request = httpx_mock.get_requests()[0]
    assert request.method == "GET" and request.content == b""
    if provider_key == "pipedrive":
        assert str(request.url) == "https://acme.pipedrive.com/api/v1/users/me"
        assert request.headers["x-api-token"] == token_canary
        assert "authorization" not in request.headers
    else:
        assert str(request.url) == "https://api.salesloft.com/v2/me"
        assert request.headers["authorization"] == f"Bearer {token_canary}"
    assert tested.metadata["evidence"]["account"]["provider_account_id"] == expected_account_id
    assert "grants" not in tested.metadata["evidence"]
    assert account.provider_account_id == expected_account_id
    assert scopes == []
    assert token_canary not in rendered


@pytest.mark.parametrize("provider_key", ["pipedrive", "salesloft"])
@pytest.mark.parametrize("unsupported_posture", [False, True])
def test_host_probe_policy_denies_before_native_request(
    session, project_id, httpx_mock, monkeypatch, provider_key, unsupported_posture
):
    from stackos.auth_providers.repository.schema import PermissionVerificationOut

    repo = AuthRepository(session)
    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key=provider_key,
        auth_method_key="oauth2_token",
        display_name="manual-token",
        fields={
            "access_token": "manual-canary",
            **({"company_domain": "acme"} if provider_key == "pipedrive" else {}),
        },
    ).data
    if unsupported_posture:
        get_method = repo._get_auth_method

        def changed_method(*args, **kwargs):
            method = get_method(*args, **kwargs)
            return method.model_copy(
                update={
                    "permission_verification": PermissionVerificationOut(
                        evidence_source="provider_probe", enforcement="local_required"
                    )
                }
            )

        monkeypatch.setattr(repo, "_get_auth_method", changed_method)
    tested = asyncio.run(
        repo.test(project_id=project_id, credential_ref=stored.credential_ref)
    ).data
    assert tested.ok is False
    title = "Pipedrive" if provider_key == "pipedrive" else "Salesloft"
    assert tested.status == (
        "unsupported_permission_verification"
        if unsupported_posture
        else "permission_evidence_unavailable"
    )
    assert tested.summary == (
        f"{title} credential test requires the saved method's reviewed posture."
        if unsupported_posture
        else f"{title} manual OAuth tokens have no verified scope evidence; reconnect with OAuth."
    )
    assert httpx_mock.get_requests() == []
    assert "manual-canary" not in tested.model_dump_json()


@pytest.mark.parametrize("provider_key", ["pipedrive", "salesloft"])
def test_native_http_failure_uses_existing_repository_diagnostics(
    session, project_id, httpx_mock, provider_key
):
    repo = AuthRepository(session)
    pipedrive = provider_key == "pipedrive"
    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key=provider_key,
        auth_method_key="api_token" if pipedrive else "api_key",
        display_name="rejected-token",
        fields=(
            {"api_token": "auth-canary", "company_domain": "acme"}
            if pipedrive
            else {"api_key": "auth-canary"}
        ),
    ).data
    httpx_mock.add_response(
        status_code=401,
        json={"error": "Authorization: Bearer auth-canary", "access_token": "auth-canary"},
    )
    tested = asyncio.run(
        repo.test(project_id=project_id, credential_ref=stored.credential_ref)
    ).data
    assert tested.ok is False and tested.retryable is False
    assert tested.metadata["provider_status_code"] == 401
    assert tested.metadata["reason_code"] == "authentication_failed"
    assert "auth-canary" not in tested.model_dump_json()
    assert len(httpx_mock.get_requests()) == 1
