"""Red-first integration coverage for the daemon-owned OAuth lifecycle."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.auth_providers.repository.utils import utcnow
from stackos.config import Settings
from stackos.db.models import (
    Credential,
    CredentialRefreshEvent,
    CredentialScope,
    IntegrationCredential,
    OAuthState,
)
from stackos.repositories.base import ConflictError
from stackos.repositories.projects import IntegrationCredentialRepository


def _interactive_google_profile(repo: AuthRepository, project_id: int) -> str:
    stored = repo.store_credential(
        project_id=project_id,
        provider_key="google-search-console",
        auth_method_key="oauth2_authorization_code",
        profile_key="primary",
        fields={
            "client_id": "google-client-id",
            "client_secret": "google-client-secret",
            "default_site_url": "https://example.test/",
        },
    ).data
    assert stored.status == "pending"
    return stored.credential_ref


def _start_google(
    repo: AuthRepository,
    *,
    project_id: int,
    credential_ref: str,
    settings: Settings,
) -> tuple[object, str, dict[str, list[str]]]:
    started = repo.start(
        project_id=project_id,
        provider_key="google-search-console",
        auth_method_key="oauth2_authorization_code",
        credential_ref=credential_ref,
        settings=settings,
    ).data
    assert started.authorization_url is not None
    query = parse_qs(urlparse(started.authorization_url).query)
    state = query["state"][0]
    return started, state, query


@pytest.mark.parametrize(
    ("provider_key", "fields", "expected_scopes"),
    [
        (
            "google-ads",
            {
                "client_id": "google-client-id",
                "client_secret": "google-client-secret",
                "developer_token": "google-developer-token",
            },
            {"https://www.googleapis.com/auth/adwords"},
        ),
        (
            "google-workspace",
            {
                "client_id": "google-client-id",
                "client_secret": "google-client-secret",
            },
            {
                "https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/calendar.events",
            },
        ),
        (
            "google-search-console",
            {
                "client_id": "google-client-id",
                "client_secret": "google-client-secret",
            },
            {"https://www.googleapis.com/auth/webmasters.readonly"},
        ),
        (
            "google-analytics",
            {
                "client_id": "google-client-id",
                "client_secret": "google-client-secret",
            },
            {"https://www.googleapis.com/auth/analytics.readonly"},
        ),
        (
            "google-tag-manager",
            {
                "client_id": "google-client-id",
                "client_secret": "google-client-secret",
            },
            {"https://www.googleapis.com/auth/tagmanager.readonly"},
        ),
    ],
)
def test_google_family_profiles_start_the_shared_interactive_flow(
    session: Session,
    project_id: int,
    settings: Settings,
    provider_key: str,
    fields: dict[str, str],
    expected_scopes: set[str],
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key=provider_key,
        auth_method_key="oauth2_authorization_code",
        profile_key="primary",
        fields=fields,
    ).data

    started = repo.start(
        project_id=project_id,
        provider_key=provider_key,
        auth_method_key="oauth2_authorization_code",
        credential_ref=stored.credential_ref,
        settings=settings,
    ).data

    assert started.status == "authorization-pending"
    assert started.authorization_url is not None
    query = parse_qs(urlparse(started.authorization_url).query)
    assert set(query["scope"][0].split()) == expected_scopes
    assert query["redirect_uri"] == [settings.oauth_callback_uri]


@pytest.mark.parametrize(
    (
        "provider_key",
        "expected_authorization_endpoint",
        "expected_scopes",
        "uses_pkce",
        "includes_response_type",
    ),
    [
        (
            "meta-ads",
            "https://www.facebook.com/v25.0/dialog/oauth",
            {"ads_management", "ads_read", "business_management"},
            False,
            True,
        ),
        (
            "salesforce",
            "https://login.salesforce.com/services/oauth2/authorize",
            {"api", "refresh_token"},
            True,
            True,
        ),
        (
            "pipedrive",
            "https://oauth.pipedrive.com/oauth/authorize",
            set(),
            False,
            False,
        ),
        (
            "outreach",
            "https://api.outreach.io/oauth/authorize",
            {"sequenceStates.write"},
            False,
            True,
        ),
        (
            "salesloft",
            "https://accounts.salesloft.com/oauth/authorize",
            set(),
            False,
            True,
        ),
        (
            "microsoft-365",
            "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            {
                "offline_access",
                "https://graph.microsoft.com/Mail.Send",
                "https://graph.microsoft.com/Calendars.ReadWrite",
            },
            True,
            True,
        ),
    ],
)
def test_enterprise_profiles_start_provider_correct_interactive_flow(
    session: Session,
    project_id: int,
    settings: Settings,
    provider_key: str,
    expected_authorization_endpoint: str,
    expected_scopes: set[str],
    uses_pkce: bool,
    includes_response_type: bool,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key=provider_key,
        auth_method_key="oauth2_authorization_code",
        profile_key="primary",
        fields={
            "client_id": f"{provider_key}-client-id",
            "client_secret": f"{provider_key}-client-secret",
        },
    ).data

    started = repo.start(
        project_id=project_id,
        provider_key=provider_key,
        auth_method_key="oauth2_authorization_code",
        credential_ref=stored.credential_ref,
        settings=settings,
    ).data

    assert started.authorization_url is not None
    parsed = urlparse(started.authorization_url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == expected_authorization_endpoint
    query = parse_qs(parsed.query)
    assert query["redirect_uri"] == [settings.oauth_callback_uri]
    assert ("response_type" in query) is includes_response_type
    assert ("code_challenge" in query) is uses_pkce
    if expected_scopes:
        assert set(query["scope"][0].split()) == expected_scopes
    else:
        assert "scope" not in query


@pytest.mark.parametrize(
    ("provider_key", "token_url", "response_metadata", "config_key", "config_value"),
    [
        (
            "salesforce",
            "https://login.salesforce.com/services/oauth2/token",
            {
                "scope": "api refresh_token",
                "instance_url": "https://example.my.salesforce.com",
                "id": "https://login.salesforce.com/id/org/user",
            },
            "instance_url",
            "https://example.my.salesforce.com",
        ),
        (
            "pipedrive",
            "https://oauth.pipedrive.com/oauth/token",
            {
                "scope": "deals:read,search:read",
                "api_domain": "https://example.pipedrive.com",
            },
            "base_url",
            "https://example.pipedrive.com",
        ),
    ],
)
def test_provider_callback_normalizes_execution_base_url(
    session: Session,
    project_id: int,
    settings: Settings,
    httpx_mock: HTTPXMock,
    provider_key: str,
    token_url: str,
    response_metadata: dict[str, str],
    config_key: str,
    config_value: str,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key=provider_key,
        auth_method_key="oauth2_authorization_code",
        fields={
            "client_id": f"{provider_key}-client-id",
            "client_secret": f"{provider_key}-client-secret",
        },
    ).data
    started = repo.start(
        project_id=project_id,
        provider_key=provider_key,
        auth_method_key="oauth2_authorization_code",
        credential_ref=stored.credential_ref,
        settings=settings,
    ).data
    assert started.authorization_url is not None
    state = parse_qs(urlparse(started.authorization_url).query)["state"][0]
    httpx_mock.add_response(
        method="POST",
        url=token_url,
        json={
            "access_token": f"{provider_key}-access",
            "refresh_token": f"{provider_key}-refresh",
            "expires_in": 3600,
            **response_metadata,
        },
    )

    completed = asyncio.run(
        repo.complete_oauth_callback(state=state, code="provider-code", settings=settings)
    )

    assert completed.status == "connected"
    row = session.exec(
        select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.kind == provider_key,
        )
    ).one()
    assert row.config_json is not None
    assert row.config_json[config_key] == config_value
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == stored.credential_ref)
    ).one()
    assert credential.id is not None
    scopes = {
        item.scope
        for item in session.exec(
            select(CredentialScope).where(CredentialScope.credential_id == credential.id)
        ).all()
    }
    assert scopes == set(response_metadata["scope"].replace(",", " ").split())


def test_meta_callback_exchanges_short_token_for_long_lived_token(
    session: Session,
    project_id: int,
    settings: Settings,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key="meta-ads",
        auth_method_key="oauth2_authorization_code",
        fields={"client_id": "meta-client", "client_secret": "meta-secret"},
    ).data
    started = repo.start(
        project_id=project_id,
        provider_key="meta-ads",
        auth_method_key="oauth2_authorization_code",
        credential_ref=stored.credential_ref,
        settings=settings,
    ).data
    assert started.authorization_url is not None
    state = parse_qs(urlparse(started.authorization_url).query)["state"][0]
    httpx_mock.add_response(
        method="POST",
        url="https://graph.facebook.com/v25.0/oauth/access_token",
        json={"access_token": "meta-short", "expires_in": 3600},
    )
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://graph.facebook.com/v25.0/oauth/access_token"
            "?grant_type=fb_exchange_token&client_id=meta-client"
            "&client_secret=meta-secret&fb_exchange_token=meta-short"
        ),
        json={"access_token": "meta-long", "token_type": "bearer", "expires_in": 5_184_000},
    )

    completed = asyncio.run(
        repo.complete_oauth_callback(state=state, code="meta-code", settings=settings)
    )

    assert completed.status == "connected"
    row = session.exec(
        select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.kind == "meta-ads",
        )
    ).one()
    assert row.id is not None and row.expires_at is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id))
    assert payload["access_token"] == "meta-long"
    assert row.expires_at > utcnow() + timedelta(days=50)
    assert len(httpx_mock.get_requests()) == 2


def test_provider_callback_rejects_untrusted_execution_base(
    session: Session,
    project_id: int,
    settings: Settings,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key="pipedrive",
        auth_method_key="oauth2_authorization_code",
        fields={"client_id": "pipedrive-client", "client_secret": "pipedrive-secret"},
    ).data
    started = repo.start(
        project_id=project_id,
        provider_key="pipedrive",
        auth_method_key="oauth2_authorization_code",
        credential_ref=stored.credential_ref,
        settings=settings,
    ).data
    assert started.authorization_url is not None
    state = parse_qs(urlparse(started.authorization_url).query)["state"][0]
    httpx_mock.add_response(
        method="POST",
        url="https://oauth.pipedrive.com/oauth/token",
        json={
            "access_token": "pipedrive-access",
            "refresh_token": "pipedrive-refresh",
            "expires_in": 3600,
            "scope": "deals:read",
            "api_domain": "https://attacker.example",
        },
    )

    completed = asyncio.run(
        repo.complete_oauth_callback(state=state, code="provider-code", settings=settings)
    )

    assert completed.status == "repair-required"
    row = session.exec(
        select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.kind == "pipedrive",
        )
    ).one()
    assert "base_url" not in (row.config_json or {})
    assert row.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id))
    assert "access_token" not in payload


@pytest.mark.parametrize(
    ("provider_key", "auth_method_key", "fields", "required_scope", "payload_key"),
    [
        (
            "pipedrive",
            "api_token",
            {"api_token": "pipedrive-static", "company_domain": "example"},
            "deals:read",
            "api_token",
        ),
        (
            "salesloft",
            "api_key",
            {"api_key": "salesloft-static"},
            "cadences:write",
            "api_key",
        ),
    ],
)
def test_static_token_alternative_does_not_require_oauth_scope_records(
    session: Session,
    project_id: int,
    provider_key: str,
    auth_method_key: str,
    fields: dict[str, str],
    required_scope: str,
    payload_key: str,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key=provider_key,
        auth_method_key=auth_method_key,
        fields=fields,
    ).data

    resolved = asyncio.run(
        repo.resolve_for_execution(
            project_id=project_id,
            provider_key=provider_key,
            credential_ref=stored.credential_ref,
            operation=f"test.{provider_key}.static-token",
            required_scopes=[required_scope],
        )
    )

    assert json.loads(resolved.secret_payload)[payload_key] == fields[payload_key]


@pytest.mark.parametrize(
    ("provider_key", "token_url", "scope", "client_auth_style", "response_metadata"),
    [
        (
            "salesforce",
            "https://login.salesforce.com/services/oauth2/token",
            "api",
            "body",
            {"instance_url": "https://example.my.salesforce.com"},
        ),
        (
            "pipedrive",
            "https://oauth.pipedrive.com/oauth/token",
            "deals:read",
            "basic",
            {"api_domain": "https://example.pipedrive.com"},
        ),
        (
            "outreach",
            "https://api.outreach.io/oauth/token",
            "sequenceStates.write",
            "body",
            {},
        ),
        (
            "salesloft",
            "https://accounts.salesloft.com/oauth/token",
            "cadences:write",
            "body",
            {},
        ),
        (
            "microsoft-365",
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            "https://graph.microsoft.com/Mail.Send",
            "body",
            {},
        ),
    ],
)
def test_enterprise_refresh_uses_the_provider_contract_and_rotates_tokens(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    provider_key: str,
    token_url: str,
    scope: str,
    client_auth_style: str,
    response_metadata: dict[str, str],
) -> None:
    stored = (
        IntegrationCredentialRepository(session)
        .set(
            project_id=project_id,
            kind=provider_key,
            secret_payload=json.dumps(
                {
                    "client_id": f"{provider_key}-client",
                    "client_secret": f"{provider_key}-secret",
                    "access_token": "expired-access",
                    "refresh_token": "old-refresh",
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
        repo.status(
            project_id=project_id,
            provider_key=provider_key,
        )
        .connections[0]
        .credential_ref
    )
    httpx_mock.add_response(
        method="POST",
        url=token_url,
        json={
            "access_token": "renewed-access",
            "refresh_token": "rotated-refresh",
            "expires_in": 3600,
            "scope": scope,
            **response_metadata,
        },
    )

    resolved = asyncio.run(
        repo.resolve_for_execution(
            project_id=project_id,
            provider_key=provider_key,
            credential_ref=credential_ref,
            operation=f"test.{provider_key}.refresh",
            required_scopes=[scope],
        )
    )

    request = httpx_mock.get_requests()[0]
    form = parse_qs(request.content.decode())
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["old-refresh"]
    if client_auth_style == "basic":
        assert request.headers["Authorization"].startswith("Basic ")
        assert "client_id" not in form
    else:
        assert form["client_id"] == [f"{provider_key}-client"]
        assert form["client_secret"] == [f"{provider_key}-secret"]
    payload = json.loads(resolved.secret_payload)
    assert payload["access_token"] == "renewed-access"
    assert payload["refresh_token"] == "rotated-refresh"
    row = session.get(IntegrationCredential, stored.id)
    assert row is not None and row.expires_at is not None
    assert row.expires_at > utcnow() + timedelta(minutes=50)


@pytest.mark.parametrize(
    "provider_key",
    ["meta-ads", "salesforce", "pipedrive", "outreach", "salesloft", "microsoft-365"],
)
def test_enterprise_denial_ends_pending_profile_without_a_token_request(
    session: Session,
    project_id: int,
    settings: Settings,
    httpx_mock: HTTPXMock,
    provider_key: str,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key=provider_key,
        auth_method_key="oauth2_authorization_code",
        fields={"client_id": "client-id", "client_secret": "client-secret"},
    ).data
    started = repo.start(
        project_id=project_id,
        provider_key=provider_key,
        auth_method_key="oauth2_authorization_code",
        credential_ref=stored.credential_ref,
        settings=settings,
    ).data
    assert started.authorization_url is not None
    state = parse_qs(urlparse(started.authorization_url).query)["state"][0]

    denied = asyncio.run(
        repo.complete_oauth_callback(
            state=state,
            code=None,
            provider_error="access_denied",
            settings=settings,
        )
    )

    assert denied.status == "authorization-denied"
    connection = repo.status(project_id=project_id, provider_key=provider_key).connections[0]
    assert connection.status == "repair-required"
    assert httpx_mock.get_requests() == []


def test_oauth_start_uses_fixed_callback_pkce_and_digested_state(
    session: Session,
    project_id: int,
    settings: Settings,
) -> None:
    repo = AuthRepository(session)
    credential_ref = _interactive_google_profile(repo, project_id)

    started, raw_state, query = _start_google(
        repo,
        project_id=project_id,
        credential_ref=credential_ref,
        settings=settings,
    )

    assert started.status == "authorization-pending"
    assert started.credential_ref == credential_ref
    assert "state" not in started.model_dump()
    assert query["redirect_uri"] == ["http://127.0.0.1:5180/api/v1/auth/oauth/callback"]
    assert query["response_type"] == ["code"]
    assert query["access_type"] == ["offline"]
    assert query["code_challenge_method"] == ["S256"]

    state_row = session.exec(select(OAuthState)).one()
    assert state_row.state == hashlib.sha256(raw_state.encode()).hexdigest()
    assert raw_state not in state_row.state
    assert state_row.redirect_uri == query["redirect_uri"][0]
    assert state_row.expires_at is not None
    assert state_row.integration_credential_id is not None

    encrypted_row = session.get(IntegrationCredential, state_row.integration_credential_id)
    assert encrypted_row is not None and encrypted_row.id is not None
    payload = json.loads(
        IntegrationCredentialRepository(session).get_decrypted(encrypted_row.id).decode()
    )
    pending = payload["_oauth_pending"]
    verifier = pending["code_verifier"]
    expected_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    assert query["code_challenge"] == [expected_challenge]
    assert raw_state not in json.dumps(payload)


def test_oauth_callback_exchanges_once_and_persists_normalized_credential(
    session: Session,
    project_id: int,
    settings: Settings,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    credential_ref = _interactive_google_profile(repo, project_id)
    _started, state, _query = _start_google(
        repo,
        project_id=project_id,
        credential_ref=credential_ref,
        settings=settings,
    )
    httpx_mock.add_response(
        method="POST",
        url="https://oauth2.googleapis.com/token",
        json={
            "access_token": "new-access-value",
            "refresh_token": "new-refresh-value",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/webmasters.readonly",
            "token_type": "Bearer",
        },
    )

    completed = asyncio.run(
        repo.complete_oauth_callback(
            state=state,
            code="one-time-code",
            settings=settings,
        )
    )

    assert completed.status == "connected"
    assert completed.credential_ref == credential_ref
    connection = repo.status(
        project_id=project_id,
        provider_key="google-search-console",
    ).connections[0]
    assert connection.status == "connected"
    assert connection.scopes == ["https://www.googleapis.com/auth/webmasters.readonly"]
    stored_scope = session.exec(select(CredentialScope)).one()
    assert stored_scope.scope == "https://www.googleapis.com/auth/webmasters.readonly"

    state_row = session.exec(select(OAuthState)).one()
    assert state_row.consumed_at is not None
    assert state_row.integration_credential_id is not None
    encrypted_row = session.get(IntegrationCredential, state_row.integration_credential_id)
    assert encrypted_row is not None and encrypted_row.id is not None
    payload = json.loads(
        IntegrationCredentialRepository(session).get_decrypted(encrypted_row.id).decode()
    )
    assert payload["access_token"] == "new-access-value"
    assert payload["refresh_token"] == "new-refresh-value"
    assert "_oauth_pending" not in payload

    with pytest.raises(ConflictError, match="invalid or expired OAuth transaction"):
        asyncio.run(
            repo.complete_oauth_callback(
                state=state,
                code="replayed-code",
                settings=settings,
            )
        )
    assert len(httpx_mock.get_requests()) == 1


def test_failed_reconnect_preserves_the_active_credential(
    session: Session,
    project_id: int,
    settings: Settings,
) -> None:
    repo = AuthRepository(session)
    active = repo.store_credential(
        project_id=project_id,
        provider_key="google-search-console",
        auth_method_key="oauth2_refresh_token",
        profile_key="primary",
        fields={
            "client_id": "google-client-id",
            "client_secret": "google-client-secret",
            "refresh_token": "still-working-refresh-value",
            "default_site_url": "https://example.test/",
        },
    ).data
    pending = repo.store_credential(
        project_id=project_id,
        provider_key="google-search-console",
        auth_method_key="oauth2_authorization_code",
        profile_key="primary",
        fields={
            "client_id": "replacement-client-id",
            "client_secret": "replacement-client-secret",
            "default_site_url": "https://example.test/",
        },
    ).data
    assert pending.credential_ref == active.credential_ref
    assert pending.status == "connected"
    _started, state, _query = _start_google(
        repo,
        project_id=project_id,
        credential_ref=active.credential_ref,
        settings=settings,
    )

    denied = asyncio.run(
        repo.complete_oauth_callback(
            state=state,
            code=None,
            provider_error="access_denied",
            settings=settings,
        )
    )

    assert denied.status == "authorization-denied"
    connection = repo.status(
        project_id=project_id,
        provider_key="google-search-console",
    ).connections[0]
    assert connection.status == "connected"
    row = session.exec(
        select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.kind == "google-search-console",
            IntegrationCredential.profile_key == "primary",
        )
    ).one()
    assert row.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id))
    assert payload["refresh_token"] == "still-working-refresh-value"
    assert "_oauth_pending" not in payload


def test_expired_google_credential_refreshes_and_persists_rotation(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key="google-search-console",
        auth_method_key="oauth2_refresh_token",
        profile_key="primary",
        fields={
            "client_id": "refresh-client-id",
            "client_secret": "refresh-client-secret",
            "refresh_token": "old-refresh-value",
            "default_site_url": "https://example.test/",
        },
        expires_at=utcnow() - timedelta(minutes=5),
    ).data
    httpx_mock.add_response(
        method="POST",
        url="https://oauth2.googleapis.com/token",
        json={
            "access_token": "refreshed-access-value",
            "refresh_token": "rotated-refresh-value",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/webmasters.readonly",
            "token_type": "Bearer",
        },
    )

    first = asyncio.run(
        repo.resolve_for_execution(
            project_id=project_id,
            provider_key="google-search-console",
            credential_ref=stored.credential_ref,
            operation="test.refresh.first",
            required_scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
        )
    )
    second = asyncio.run(
        repo.resolve_for_execution(
            project_id=project_id,
            provider_key="google-search-console",
            credential_ref=stored.credential_ref,
            operation="test.refresh.second",
            required_scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
        )
    )

    first_payload = json.loads(first.secret_payload)
    second_payload = json.loads(second.secret_payload)
    assert first_payload["access_token"] == "refreshed-access-value"
    assert first_payload["refresh_token"] == "rotated-refresh-value"
    assert second_payload == first_payload
    assert len(httpx_mock.get_requests()) == 1
    row = session.get(IntegrationCredential, first.integration.id)
    assert row is not None and row.expires_at is not None
    assert row.expires_at > utcnow() + timedelta(minutes=50)

    updated = repo.update_credential(
        project_id=project_id,
        credential_ref=stored.credential_ref,
        fields={"default_site_url": "https://updated.example.test/"},
        label="Updated manual refresh",
    ).data
    assert updated.status == "connected"
    session.expire_all()
    row = session.get(IntegrationCredential, first.integration.id)
    assert row is not None and row.id is not None
    edited_payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id))
    assert edited_payload["access_token"] == "refreshed-access-value"
    assert edited_payload["refresh_token"] == "rotated-refresh-value"
    connection = repo.status(
        project_id=project_id,
        provider_key="google-search-console",
    ).connections[0]
    assert connection.scopes == ["https://www.googleapis.com/auth/webmasters.readonly"]


def test_manual_refresh_does_not_invent_scopes_when_provider_omits_them(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key="google-search-console",
        auth_method_key="oauth2_refresh_token",
        profile_key="manual-unknown-scope",
        fields={
            "client_id": "refresh-client-id",
            "client_secret": "refresh-client-secret",
            "refresh_token": "manual-refresh-value",
        },
        expires_at=utcnow() - timedelta(minutes=5),
    ).data
    httpx_mock.add_response(
        method="POST",
        url="https://oauth2.googleapis.com/token",
        json={"access_token": "fresh-access-value", "expires_in": 3600},
    )

    with pytest.raises(ConflictError, match="credential scopes are unknown"):
        asyncio.run(
            repo.resolve_for_execution(
                project_id=project_id,
                provider_key="google-search-console",
                credential_ref=stored.credential_ref,
                operation="test.manual-refresh.unknown-scope",
                required_scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
            )
        )

    connection = repo.status(
        project_id=project_id,
        provider_key="google-search-console",
    ).connections[0]
    assert connection.scopes == []
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == stored.credential_ref)
    ).one()
    assert (credential.config_json or {}).get("scope_status") == "unknown"


@pytest.mark.parametrize("failure_kind", ["timeout", "rate-limit", "server-error"])
def test_transient_refresh_failure_keeps_credential_retryable(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    failure_kind: str,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key="google-search-console",
        auth_method_key="oauth2_refresh_token",
        profile_key=f"transient-{failure_kind}",
        fields={
            "client_id": "refresh-client-id",
            "client_secret": "refresh-client-secret",
            "refresh_token": "still-authorized-refresh-value",
        },
        expires_at=utcnow() - timedelta(minutes=5),
    ).data
    if failure_kind == "timeout":
        httpx_mock.add_exception(httpx.ReadTimeout("provider timed out"))
    else:
        httpx_mock.add_response(
            method="POST",
            url="https://oauth2.googleapis.com/token",
            status_code=429 if failure_kind == "rate-limit" else 503,
            json={"error": "temporarily_unavailable"},
        )

    with pytest.raises(ConflictError, match="temporarily unavailable") as excinfo:
        asyncio.run(
            repo.resolve_for_execution(
                project_id=project_id,
                provider_key="google-search-console",
                credential_ref=stored.credential_ref,
                operation="test.refresh.transient",
                required_scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
            )
        )

    assert excinfo.value.retryable is True
    assert excinfo.value.data["status"] == "temporarily-unavailable"
    session.expire_all()
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == stored.credential_ref)
    ).one()
    assert credential.status == "connected"

    httpx_mock.add_response(
        method="POST",
        url="https://oauth2.googleapis.com/token",
        json={
            "access_token": "recovered-access-value",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/webmasters.readonly",
        },
    )
    resolved = asyncio.run(
        repo.resolve_for_execution(
            project_id=project_id,
            provider_key="google-search-console",
            credential_ref=stored.credential_ref,
            operation="test.refresh.recovered",
            required_scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
        )
    )

    assert json.loads(resolved.secret_payload)["access_token"] == "recovered-access-value"
    events = session.exec(
        select(CredentialRefreshEvent).where(CredentialRefreshEvent.credential_id == credential.id)
    ).all()
    assert [event.status for event in events] == ["retryable-failure", "refreshed"]


def test_required_scopes_are_checked_before_credential_use(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key="google-search-console",
        auth_method_key="oauth2_access_token",
        profile_key="primary",
        fields={
            "access_token": "scope-access-value",
            "default_site_url": "https://example.test/",
        },
    ).data
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == stored.credential_ref)
    ).one()
    assert credential.id is not None
    credential.config_json = {
        **(credential.config_json or {}),
        "scope_status": "known",
    }
    session.add(credential)
    session.add(
        CredentialScope(
            credential_id=credential.id,
            scope="https://www.googleapis.com/auth/webmasters.readonly",
        )
    )
    session.commit()

    resolved = asyncio.run(
        repo.resolve_for_execution(
            project_id=project_id,
            provider_key="google-search-console",
            credential_ref=stored.credential_ref,
            operation="test.scope.allowed",
            required_scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
        )
    )
    assert resolved.credential.credential_ref == stored.credential_ref

    with pytest.raises(ConflictError, match="credential is missing required scopes"):
        asyncio.run(
            repo.resolve_for_execution(
                project_id=project_id,
                provider_key="google-search-console",
                credential_ref=stored.credential_ref,
                operation="test.scope.denied",
                required_scopes=["https://www.googleapis.com/auth/webmasters"],
            )
        )


def test_manual_token_replacement_discards_prior_scope_evidence(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key="google-search-console",
        auth_method_key="oauth2_access_token",
        profile_key="manual-replacement",
        fields={"access_token": "first-access-value"},
    ).data
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == stored.credential_ref)
    ).one()
    assert credential.id is not None
    credential.config_json = {**(credential.config_json or {}), "scope_status": "known"}
    session.add(credential)
    session.add(
        CredentialScope(
            credential_id=credential.id,
            scope="https://www.googleapis.com/auth/webmasters.readonly",
        )
    )
    session.commit()

    replaced = repo.store_credential(
        project_id=project_id,
        provider_key="google-search-console",
        auth_method_key="oauth2_access_token",
        profile_key="manual-replacement",
        fields={"access_token": "replacement-access-value"},
    ).data

    assert replaced.credential_ref == stored.credential_ref
    session.expire_all()
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == stored.credential_ref)
    ).one()
    assert (credential.config_json or {}).get("scope_status") == "unknown"
    assert (
        session.exec(
            select(CredentialScope).where(CredentialScope.credential_id == credential.id)
        ).all()
        == []
    )
    with pytest.raises(ConflictError, match="credential scopes are unknown"):
        asyncio.run(
            repo.resolve_for_execution(
                project_id=project_id,
                provider_key="google-search-console",
                credential_ref=stored.credential_ref,
                operation="test.manual-replacement.scope-gate",
                required_scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
            )
        )


def test_legacy_unknown_scopes_remain_usable_only_without_scope_requirement(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key="google-search-console",
        auth_method_key="oauth2_access_token",
        profile_key="legacy",
        fields={"access_token": "legacy-access-value"},
    ).data

    resolved = asyncio.run(
        repo.resolve_for_execution(
            project_id=project_id,
            provider_key="google-search-console",
            credential_ref=stored.credential_ref,
            operation="test.legacy.no-scope-requirement",
            required_scopes=[],
        )
    )
    assert json.loads(resolved.secret_payload)["access_token"] == "legacy-access-value"

    with pytest.raises(ConflictError, match="credential scopes are unknown"):
        asyncio.run(
            repo.resolve_for_execution(
                project_id=project_id,
                provider_key="google-search-console",
                credential_ref=stored.credential_ref,
                operation="test.legacy.scope-required",
                required_scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
            )
        )
