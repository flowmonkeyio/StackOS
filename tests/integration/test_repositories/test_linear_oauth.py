from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.auth_providers.repository.utils import utcnow
from stackos.config import Settings
from stackos.db.models import Credential, CredentialAccount, CredentialScope, IntegrationCredential
from stackos.repositories.base import ConflictError
from stackos.repositories.projects import IntegrationCredentialRepository

AUTH_DOCUMENT = (
    Path(__file__).parents[3]
    / "plugins"
    / "linear"
    / "graphql"
    / "auth"
    / "viewer-organization.graphql"
)


def _store_linear(repo: AuthRepository, project_id: int) -> str:
    return repo.store_credential(
        project_id=project_id,
        provider_key="linear",
        auth_method_key="oauth2_authorization_code",
        profile_key="primary",
        fields={
            "client_id": "linear-client-id",
            "client_secret": "linear-client-secret",
        },
    ).data.credential_ref


def _start_linear(
    repo: AuthRepository,
    *,
    project_id: int,
    credential_ref: str,
    settings: Settings,
) -> tuple[str, dict[str, list[str]]]:
    started = repo.start(
        project_id=project_id,
        provider_key="linear",
        auth_method_key="oauth2_authorization_code",
        credential_ref=credential_ref,
        settings=settings,
    ).data
    assert started.authorization_url is not None
    parsed = urlparse(started.authorization_url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://linear.app/oauth/authorize"
    )
    query = parse_qs(parsed.query)
    return query["state"][0], query


def test_linear_exposes_oauth_and_personal_api_key_methods(
    session: Session,
    project_id: int,
) -> None:
    providers = AuthRepository(session).list_providers(provider_key="linear")

    assert len(providers) == 1
    assert providers[0].auth_type == "oauth-or-api-key"
    assert providers[0].scopes == ["read", "write"]
    assert [method.key for method in providers[0].auth_methods] == [
        "oauth2_authorization_code",
        "personal_api_key",
    ]
    assert [field.key for field in providers[0].auth_methods[0].fields] == [
        "client_id",
        "client_secret",
    ]
    personal = providers[0].auth_methods[1]
    assert personal.auth_type == "api-key"
    assert personal.payload_format == "raw"
    assert personal.payload_field == "api_key"
    assert [field.key for field in personal.fields] == ["api_key"]
    assert personal.permission_verification is not None
    assert personal.permission_verification.evidence_source == "unavailable"
    assert personal.permission_verification.enforcement == "provider_enforced"


def test_linear_start_requires_s256_pkce_user_actor_and_comma_scopes(
    session: Session,
    project_id: int,
    settings: Settings,
) -> None:
    repo = AuthRepository(session)
    credential_ref = _store_linear(repo, project_id)

    _state, query = _start_linear(
        repo,
        project_id=project_id,
        credential_ref=credential_ref,
        settings=settings,
    )

    assert query["scope"] == ["read,write"]
    assert query["actor"] == ["user"]
    assert query["response_type"] == ["code"]
    assert query["redirect_uri"] == [settings.oauth_callback_uri]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) >= 43


def test_linear_callback_persists_current_scope_string_and_fixed_account_probe(
    session: Session,
    project_id: int,
    settings: Settings,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    credential_ref = _store_linear(repo, project_id)
    state, _query = _start_linear(
        repo,
        project_id=project_id,
        credential_ref=credential_ref,
        settings=settings,
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/oauth/token",
        json={
            "access_token": "linear-access",
            "refresh_token": "linear-refresh",
            "expires_in": 86400,
            "scope": "read write",
            "token_type": "Bearer",
        },
    )

    completed = asyncio.run(
        repo.complete_oauth_callback(state=state, code="linear-code", settings=settings)
    )

    assert completed.status == "connected"
    token_request = httpx_mock.get_requests()[0]
    token_form = parse_qs(token_request.content.decode())
    assert token_form["client_id"] == ["linear-client-id"]
    assert token_form["client_secret"] == ["linear-client-secret"]
    assert token_form["code"] == ["linear-code"]
    assert token_form["code_verifier"]

    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    assert credential.id is not None
    stored_scopes = {
        row.scope
        for row in session.exec(
            select(CredentialScope).where(CredentialScope.credential_id == credential.id)
        ).all()
    }
    assert stored_scopes == {"read", "write"}
    integration = session.get(IntegrationCredential, credential.integration_credential_id)
    assert integration is not None and integration.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(integration.id))
    assert payload["access_token"] == "linear-access"
    assert payload["refresh_token"] == "linear-refresh"
    assert "access_token" not in (integration.config_json or {})
    assert "refresh_token" not in (integration.config_json or {})

    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/graphql",
        json={
            "data": {
                "viewer": {
                    "id": "user-linear-1",
                    "name": "Ada Operator",
                    "organization": {
                        "id": "organization-linear-1",
                        "name": "Example Workspace",
                    },
                }
            }
        },
    )
    tested = asyncio.run(repo.test(project_id=project_id, credential_ref=credential_ref))
    probe_request = httpx_mock.get_requests()[1]
    rendered = json.dumps(tested.data.model_dump(mode="json"))

    assert tested.data.ok is True
    assert tested.data.metadata["organization_id"] == "organization-linear-1"
    assert probe_request.headers["Authorization"] == "Bearer linear-access"
    assert json.loads(probe_request.content) == {"query": AUTH_DOCUMENT.read_text(encoding="utf-8")}
    account = session.exec(
        select(CredentialAccount).where(CredentialAccount.credential_id == credential.id)
    ).one()
    assert account.provider_account_id == "organization-linear-1"
    assert account.display_name == "Example Workspace"
    assert account.metadata_json == {
        "organization_id": "organization-linear-1",
        "organization_name": "Example Workspace",
        "viewer_id": "user-linear-1",
        "viewer_name": "Ada Operator",
    }
    assert "linear-access" not in rendered
    assert "linear-refresh" not in rendered
    assert "linear-client-secret" not in rendered


def test_linear_personal_api_key_probe_uses_raw_authorization_without_renewal(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    credential_ref = repo.store_credential(
        project_id=project_id,
        provider_key="linear",
        auth_method_key="personal_api_key",
        profile_key="personal",
        fields={"api_key": "linear-personal-key-sentinel"},
    ).data.credential_ref
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/graphql",
        json={
            "data": {
                "viewer": {
                    "id": "user-linear-personal",
                    "name": "Personal Operator",
                    "organization": {
                        "id": "organization-linear-personal",
                        "name": "Personal Workspace",
                    },
                }
            }
        },
    )

    tested = asyncio.run(repo.test(project_id=project_id, credential_ref=credential_ref))

    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(tested.data.model_dump(mode="json"))
    assert credential.auth_type == "api-key"
    assert credential.auth_method_key == "personal_api_key"
    assert tested.data.ok is True
    assert tested.data.metadata["organization_id"] == "organization-linear-personal"
    assert request.headers["Authorization"] == "linear-personal-key-sentinel"
    assert request.headers["Authorization"] != "Bearer linear-personal-key-sentinel"
    assert len(httpx_mock.get_requests()) == 1
    account = session.exec(
        select(CredentialAccount).where(CredentialAccount.credential_id == credential.id)
    ).one()
    assert account.provider_account_id == "organization-linear-personal"
    assert account.display_name == "Personal Workspace"
    assert "linear-personal-key-sentinel" not in rendered


def test_linear_callback_accepts_legacy_scope_array(
    session: Session,
    project_id: int,
    settings: Settings,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    credential_ref = _store_linear(repo, project_id)
    state, _query = _start_linear(
        repo,
        project_id=project_id,
        credential_ref=credential_ref,
        settings=settings,
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/oauth/token",
        json={
            "access_token": "legacy-access",
            "refresh_token": "legacy-refresh",
            "expires_in": 86400,
            "scope": ["read", "write"],
            "token_type": "Bearer",
        },
    )

    completed = asyncio.run(
        repo.complete_oauth_callback(state=state, code="legacy-code", settings=settings)
    )

    assert completed.status == "connected"
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    assert credential.id is not None
    assert {
        row.scope
        for row in session.exec(
            select(CredentialScope).where(CredentialScope.credential_id == credential.id)
        ).all()
    } == {"read", "write"}


def test_linear_refresh_rotates_access_and_refresh_tokens(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    stored = (
        IntegrationCredentialRepository(session)
        .set(
            project_id=project_id,
            kind="linear",
            profile_key="primary",
            secret_payload=json.dumps(
                {
                    "client_id": "linear-client-id",
                    "client_secret": "linear-client-secret",
                    "access_token": "expired-linear-access",
                    "refresh_token": "old-linear-refresh",
                }
            ).encode(),
            config_json={
                "auth_method_key": "oauth2_authorization_code",
                "scope_status": "known",
            },
            expires_at=utcnow() - timedelta(minutes=5),
        )
        .data
    )
    repo = AuthRepository(session)
    credential_ref = (
        repo.status(project_id=project_id, provider_key="linear").connections[0].credential_ref
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/oauth/token",
        json={
            "access_token": "renewed-linear-access",
            "refresh_token": "rotated-linear-refresh",
            "expires_in": 86400,
            "scope": "read write",
            "token_type": "Bearer",
        },
    )

    resolved = asyncio.run(
        repo.resolve_for_execution(
            project_id=project_id,
            provider_key="linear",
            credential_ref=credential_ref,
            operation="test.linear.refresh",
            required_scopes=["read"],
        )
    )

    request = httpx_mock.get_requests()[0]
    form = parse_qs(request.content.decode())
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["old-linear-refresh"]
    assert form["client_id"] == ["linear-client-id"]
    assert form["client_secret"] == ["linear-client-secret"]
    payload = json.loads(resolved.secret_payload)
    assert payload["access_token"] == "renewed-linear-access"
    assert payload["refresh_token"] == "rotated-linear-refresh"
    row = session.get(IntegrationCredential, stored.id)
    assert row is not None and row.expires_at is not None
    assert row.expires_at > utcnow() + timedelta(hours=23)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("token_type", None),
        ("token_type", "MAC"),
        ("scope", None),
        ("scope", "read"),
    ],
)
def test_linear_callback_rejects_non_bearer_or_incomplete_scope_responses(
    session: Session,
    project_id: int,
    settings: Settings,
    httpx_mock: HTTPXMock,
    field: str,
    invalid_value: object,
) -> None:
    repo = AuthRepository(session)
    credential_ref = _store_linear(repo, project_id)
    state, _query = _start_linear(
        repo,
        project_id=project_id,
        credential_ref=credential_ref,
        settings=settings,
    )
    response: dict[str, object] = {
        "access_token": "rejected-linear-access",
        "refresh_token": "rejected-linear-refresh",
        "expires_in": 86400,
        "scope": "read write",
        "token_type": "Bearer",
    }
    if invalid_value is None:
        response.pop(field)
    else:
        response[field] = invalid_value
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/oauth/token",
        json=response,
    )

    completed = asyncio.run(
        repo.complete_oauth_callback(state=state, code="rejected-code", settings=settings)
    )

    assert completed.status == "repair-required"
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    integration = session.get(IntegrationCredential, credential.integration_credential_id)
    assert integration is not None and integration.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(integration.id))
    assert "access_token" not in payload
    assert "refresh_token" not in payload


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("token_type", None),
        ("token_type", "MAC"),
        ("scope", None),
        ("scope", "read"),
    ],
)
def test_linear_refresh_rejects_non_bearer_or_incomplete_scope_responses(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    field: str,
    invalid_value: object,
) -> None:
    stored = (
        IntegrationCredentialRepository(session)
        .set(
            project_id=project_id,
            kind="linear",
            profile_key="primary",
            secret_payload=json.dumps(
                {
                    "client_id": "linear-client-id",
                    "client_secret": "linear-client-secret",
                    "access_token": "expired-linear-access",
                    "refresh_token": "old-linear-refresh",
                }
            ).encode(),
            config_json={
                "auth_method_key": "oauth2_authorization_code",
                "scope_status": "known",
            },
            expires_at=utcnow() - timedelta(minutes=5),
        )
        .data
    )
    repo = AuthRepository(session)
    credential_ref = (
        repo.status(project_id=project_id, provider_key="linear").connections[0].credential_ref
    )
    response: dict[str, object] = {
        "access_token": "rejected-renewed-access",
        "refresh_token": "rejected-rotated-refresh",
        "expires_in": 86400,
        "scope": "read write",
        "token_type": "Bearer",
    }
    if invalid_value is None:
        response.pop(field)
    else:
        response[field] = invalid_value
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/oauth/token",
        json=response,
    )

    with pytest.raises(ConflictError, match="credential renewal failed"):
        asyncio.run(
            repo.resolve_for_execution(
                project_id=project_id,
                provider_key="linear",
                credential_ref=credential_ref,
                operation="test.linear.rejected-refresh",
                required_scopes=["read"],
            )
        )

    row = session.get(IntegrationCredential, stored.id)
    assert row is not None and row.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id))
    assert payload["access_token"] == "expired-linear-access"
    assert payload["refresh_token"] == "old-linear-refresh"


def test_linear_local_revoke_makes_no_provider_request_while_remote_revoke_is_deferred(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    credential_ref = _store_linear(repo, project_id)

    revoked = repo.revoke(project_id=project_id, credential_ref=credential_ref).data

    assert revoked.provider_key == "linear"
    assert httpx_mock.get_requests() == []
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    assert credential.status == "revoked"
    assert credential.integration_credential_id is None
