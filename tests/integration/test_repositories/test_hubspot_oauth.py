from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.auth_providers.repository.utils import utcnow
from stackos.config import Settings
from stackos.db.models import (
    Credential,
    CredentialAccount,
    CredentialScope,
    IntegrationCredential,
)
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository

CRM_CORE_SCOPES = {
    "crm.objects.contacts.read",
    "crm.objects.contacts.write",
    "crm.objects.companies.read",
    "crm.objects.companies.write",
    "crm.objects.deals.read",
    "crm.objects.deals.write",
    "crm.objects.owners.read",
    "crm.schemas.contacts.read",
    "crm.schemas.companies.read",
    "crm.schemas.deals.read",
}


def _store_hubspot(
    repo: AuthRepository,
    project_id: int,
    *,
    scope_bundles: list[str] | str | None = None,
    transactional_email_entitlement_confirmed: bool | None = None,
) -> str:
    fields: dict[str, object] = {
        "client_id": "hubspot-client-id",
        "client_secret": "hubspot-client-secret",
    }
    if scope_bundles is not None:
        fields["scope_bundles"] = scope_bundles
    if transactional_email_entitlement_confirmed is not None:
        fields["transactional_email_entitlement_confirmed"] = (
            transactional_email_entitlement_confirmed
        )
    return repo.store_credential(
        project_id=project_id,
        provider_key="hubspot",
        auth_method_key="oauth2_authorization_code",
        profile_key="primary",
        fields=fields,
    ).data.credential_ref


def test_hubspot_start_uses_required_and_selected_optional_scope_bundles(
    session: Session,
    project_id: int,
    settings: Settings,
) -> None:
    repo = AuthRepository(session)
    credential_ref = _store_hubspot(
        repo,
        project_id,
        scope_bundles=["sales", "transactional-communications"],
    )

    started = repo.start(
        project_id=project_id,
        provider_key="hubspot",
        auth_method_key="oauth2_authorization_code",
        credential_ref=credential_ref,
        settings=settings,
    ).data

    assert started.authorization_url is not None
    parsed = urlparse(started.authorization_url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://app.hubspot.com/oauth/authorize"
    )
    query = parse_qs(parsed.query)
    assert set(query["scope"][0].split()) == CRM_CORE_SCOPES
    assert set(query["optional_scope"][0].split()) == {
        "crm.objects.leads.read",
        "crm.objects.leads.write",
        "crm.objects.products.read",
        "crm.objects.products.write",
        "crm.objects.line_items.read",
        "crm.objects.line_items.write",
        "crm.objects.quotes.read",
        "crm.objects.goals.read",
        "transactional-email",
    }
    assert "code_challenge" not in query
    assert query["redirect_uri"] == [settings.oauth_callback_uri]


def test_hubspot_webhook_bundle_does_not_request_automation_consent(
    session: Session,
    project_id: int,
    settings: Settings,
) -> None:
    repo = AuthRepository(session)
    credential_ref = _store_hubspot(
        repo,
        project_id,
        scope_bundles=["webhooks"],
    )

    started = repo.start(
        project_id=project_id,
        provider_key="hubspot",
        auth_method_key="oauth2_authorization_code",
        credential_ref=credential_ref,
        settings=settings,
    ).data

    assert started.authorization_url is not None
    query = parse_qs(urlparse(started.authorization_url).query)
    assert set(query["scope"][0].split()) == CRM_CORE_SCOPES
    assert "optional_scope" not in query
    assert "automation" not in query["scope"][0].split()


def test_hubspot_callback_persists_plural_scopes_and_hub_account(
    session: Session,
    project_id: int,
    settings: Settings,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    credential_ref = _store_hubspot(
        repo,
        project_id,
        scope_bundles="marketing",
        transactional_email_entitlement_confirmed=True,
    )
    started = repo.start(
        project_id=project_id,
        provider_key="hubspot",
        auth_method_key="oauth2_authorization_code",
        credential_ref=credential_ref,
        settings=settings,
    ).data
    assert started.authorization_url is not None
    state = parse_qs(urlparse(started.authorization_url).query)["state"][0]
    granted_scopes = sorted(
        CRM_CORE_SCOPES
        | {
            "forms",
            "crm.lists.read",
            "marketing.campaigns.read",
            "subscriptions-status-read",
        }
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubspot.com/oauth/2026-03/token",
        json={
            "access_token": "hubspot-access",
            "refresh_token": "hubspot-refresh",
            "expires_in": 1800,
            "scopes": granted_scopes,
            "hub_id": 1234567,
            "user_id": 7654321,
            "app_id": 4567,
            "hub_domain": "example.test",
            "is_private_distribution": False,
        },
    )

    completed = asyncio.run(
        repo.complete_oauth_callback(state=state, code="hubspot-code", settings=settings)
    )

    assert completed.status == "connected"
    request = httpx_mock.get_requests()[0]
    form = parse_qs(request.content.decode())
    assert form["client_id"] == ["hubspot-client-id"]
    assert form["client_secret"] == ["hubspot-client-secret"]
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    assert credential.id is not None
    assert credential.config_json["transactional_email_entitlement_confirmed"] is True
    integration = session.exec(
        select(IntegrationCredential).where(
            IntegrationCredential.id == credential.integration_credential_id
        )
    ).one()
    assert integration.config_json["transactional_email_entitlement_confirmed"] is True
    stored_scopes = {
        item.scope
        for item in session.exec(
            select(CredentialScope).where(CredentialScope.credential_id == credential.id)
        ).all()
    }
    assert stored_scopes == set(granted_scopes)
    account = session.exec(
        select(CredentialAccount).where(CredentialAccount.credential_id == credential.id)
    ).one()
    assert account.provider_account_id == "1234567"
    assert account.metadata_json == {
        "hub_id": 1234567,
        "user_id": 7654321,
        "app_id": 4567,
        "hub_domain": "example.test",
        "is_private_distribution": False,
    }
    safe_status = repo.status(project_id=project_id, provider_key="hubspot").model_dump(mode="json")
    rendered_status = json.dumps(safe_status)
    assert "hubspot-client-secret" not in rendered_status
    assert "hubspot-access" not in rendered_status
    assert "hubspot-refresh" not in rendered_status
    assert safe_status["connections"][0]["account"]["provider_account_id"] == "1234567"


def test_hubspot_callback_rejects_token_without_hub_account(
    session: Session,
    project_id: int,
    settings: Settings,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    credential_ref = _store_hubspot(repo, project_id)
    started = repo.start(
        project_id=project_id,
        provider_key="hubspot",
        auth_method_key="oauth2_authorization_code",
        credential_ref=credential_ref,
        settings=settings,
    ).data
    assert started.authorization_url is not None
    state = parse_qs(urlparse(started.authorization_url).query)["state"][0]
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubspot.com/oauth/2026-03/token",
        json={
            "access_token": "unbound-access",
            "refresh_token": "unbound-refresh",
            "expires_in": 1800,
            "scopes": sorted(CRM_CORE_SCOPES),
        },
    )

    completed = asyncio.run(
        repo.complete_oauth_callback(state=state, code="hubspot-code", settings=settings)
    )

    assert completed.status == "repair-required"
    connection = repo.status(project_id=project_id, provider_key="hubspot").connections[0]
    assert connection.status == "repair-required"
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    assert credential.id is not None
    assert (
        session.exec(
            select(CredentialAccount).where(CredentialAccount.credential_id == credential.id)
        ).first()
        is None
    )
    integration = session.get(IntegrationCredential, credential.integration_credential_id)
    assert integration is not None and integration.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(integration.id))
    assert "access_token" not in payload
    assert "refresh_token" not in payload


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("refresh_token", None),
        ("refresh_token", ""),
        ("expires_in", None),
        ("expires_in", 0),
        ("scopes", None),
        ("scopes", []),
    ],
)
def test_hubspot_callback_rejects_incomplete_token_lifecycle(
    session: Session,
    project_id: int,
    settings: Settings,
    httpx_mock: HTTPXMock,
    field: str,
    invalid_value: object,
) -> None:
    repo = AuthRepository(session)
    credential_ref = _store_hubspot(repo, project_id)
    started = repo.start(
        project_id=project_id,
        provider_key="hubspot",
        auth_method_key="oauth2_authorization_code",
        credential_ref=credential_ref,
        settings=settings,
    ).data
    assert started.authorization_url is not None
    state = parse_qs(urlparse(started.authorization_url).query)["state"][0]
    response: dict[str, object] = {
        "access_token": "incomplete-access",
        "refresh_token": "incomplete-refresh",
        "expires_in": 1800,
        "scopes": sorted(CRM_CORE_SCOPES),
        "hub_id": 1234567,
    }
    if invalid_value is None:
        response.pop(field)
    else:
        response[field] = invalid_value
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubspot.com/oauth/2026-03/token",
        json=response,
    )

    completed = asyncio.run(
        repo.complete_oauth_callback(state=state, code="hubspot-code", settings=settings)
    )

    assert completed.status == "repair-required"
    connection = repo.status(project_id=project_id, provider_key="hubspot").connections[0]
    assert connection.status == "repair-required"
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    integration = session.get(IntegrationCredential, credential.integration_credential_id)
    assert integration is not None and integration.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(integration.id))
    assert "access_token" not in payload
    assert "refresh_token" not in payload


def test_hubspot_failed_reconnect_without_hub_account_preserves_active_connection(
    session: Session,
    project_id: int,
    settings: Settings,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    credential_ref = _store_hubspot(repo, project_id)
    started = repo.start(
        project_id=project_id,
        provider_key="hubspot",
        auth_method_key="oauth2_authorization_code",
        credential_ref=credential_ref,
        settings=settings,
    ).data
    assert started.authorization_url is not None
    state = parse_qs(urlparse(started.authorization_url).query)["state"][0]
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubspot.com/oauth/2026-03/token",
        json={
            "access_token": "working-access",
            "refresh_token": "working-refresh",
            "expires_in": 1800,
            "scopes": sorted(CRM_CORE_SCOPES),
            "hub_id": 1234567,
        },
    )
    connected = asyncio.run(
        repo.complete_oauth_callback(state=state, code="first-code", settings=settings)
    )
    assert connected.status == "connected"

    pending_ref = _store_hubspot(repo, project_id)
    assert pending_ref == credential_ref
    restarted = repo.start(
        project_id=project_id,
        provider_key="hubspot",
        auth_method_key="oauth2_authorization_code",
        credential_ref=credential_ref,
        settings=settings,
    ).data
    assert restarted.authorization_url is not None
    reconnect_state = parse_qs(urlparse(restarted.authorization_url).query)["state"][0]
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubspot.com/oauth/2026-03/token",
        json={
            "access_token": "unbound-replacement-access",
            "refresh_token": "unbound-replacement-refresh",
            "expires_in": 1800,
            "scopes": sorted(CRM_CORE_SCOPES),
        },
    )

    completed = asyncio.run(
        repo.complete_oauth_callback(
            state=reconnect_state,
            code="replacement-code",
            settings=settings,
        )
    )

    assert completed.status == "repair-required"
    connection = repo.status(project_id=project_id, provider_key="hubspot").connections[0]
    assert connection.status == "connected"
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    assert credential.id is not None
    account = session.exec(
        select(CredentialAccount).where(CredentialAccount.credential_id == credential.id)
    ).one()
    assert account.provider_account_id == "1234567"
    integration = session.get(IntegrationCredential, credential.integration_credential_id)
    assert integration is not None and integration.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(integration.id))
    assert payload["access_token"] == "working-access"
    assert payload["refresh_token"] == "working-refresh"


def test_hubspot_incomplete_reconnect_preserves_active_connection(
    session: Session,
    project_id: int,
    settings: Settings,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    credential_ref = _store_hubspot(repo, project_id)
    started = repo.start(
        project_id=project_id,
        provider_key="hubspot",
        auth_method_key="oauth2_authorization_code",
        credential_ref=credential_ref,
        settings=settings,
    ).data
    assert started.authorization_url is not None
    state = parse_qs(urlparse(started.authorization_url).query)["state"][0]
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubspot.com/oauth/2026-03/token",
        json={
            "access_token": "working-access",
            "refresh_token": "working-refresh",
            "expires_in": 1800,
            "scopes": sorted(CRM_CORE_SCOPES),
            "hub_id": 1234567,
        },
    )
    connected = asyncio.run(
        repo.complete_oauth_callback(state=state, code="first-code", settings=settings)
    )
    assert connected.status == "connected"

    assert _store_hubspot(repo, project_id) == credential_ref
    restarted = repo.start(
        project_id=project_id,
        provider_key="hubspot",
        auth_method_key="oauth2_authorization_code",
        credential_ref=credential_ref,
        settings=settings,
    ).data
    assert restarted.authorization_url is not None
    reconnect_state = parse_qs(urlparse(restarted.authorization_url).query)["state"][0]
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubspot.com/oauth/2026-03/token",
        json={
            "access_token": "replacement-access",
            "expires_in": 1800,
            "scopes": sorted(CRM_CORE_SCOPES),
            "hub_id": 1234567,
        },
    )

    completed = asyncio.run(
        repo.complete_oauth_callback(
            state=reconnect_state,
            code="replacement-code",
            settings=settings,
        )
    )

    assert completed.status == "repair-required"
    connection = repo.status(project_id=project_id, provider_key="hubspot").connections[0]
    assert connection.status == "connected"
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    integration = session.get(IntegrationCredential, credential.integration_credential_id)
    assert integration is not None and integration.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(integration.id))
    assert payload["access_token"] == "working-access"
    assert payload["refresh_token"] == "working-refresh"


def test_hubspot_rejects_unknown_scope_bundle_before_authorization(
    session: Session,
    project_id: int,
    settings: Settings,
) -> None:
    repo = AuthRepository(session)

    with pytest.raises(ValidationError, match="scope bundle"):
        credential_ref = _store_hubspot(repo, project_id, scope_bundles=["unknown-bundle"])
        repo.start(
            project_id=project_id,
            provider_key="hubspot",
            auth_method_key="oauth2_authorization_code",
            credential_ref=credential_ref,
            settings=settings,
        )


@pytest.mark.parametrize(
    "refresh_includes_scopes",
    [True, False],
    ids=["returned-scopes-replace-evidence", "omitted-scopes-preserve-evidence"],
)
def test_hubspot_refresh_rotates_tokens_and_preserves_verified_scopes_when_omitted(
    session: Session,
    project_id: int,
    settings: Settings,
    httpx_mock: HTTPXMock,
    refresh_includes_scopes: bool,
) -> None:
    repo = AuthRepository(session)
    credential_ref = _store_hubspot(repo, project_id)
    started = repo.start(
        project_id=project_id,
        provider_key="hubspot",
        auth_method_key="oauth2_authorization_code",
        credential_ref=credential_ref,
        settings=settings,
    ).data
    assert started.authorization_url is not None
    state = parse_qs(urlparse(started.authorization_url).query)["state"][0]
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubspot.com/oauth/2026-03/token",
        json={
            "access_token": "first-access",
            "refresh_token": "first-refresh",
            "expires_in": 1800,
            "scopes": sorted(CRM_CORE_SCOPES),
            "hub_id": 1234567,
        },
    )
    completed = asyncio.run(
        repo.complete_oauth_callback(state=state, code="hubspot-code", settings=settings)
    )
    assert completed.status == "connected"
    row = session.exec(
        select(IntegrationCredential).where(IntegrationCredential.kind == "hubspot")
    ).one()
    row.expires_at = utcnow() - timedelta(minutes=1)
    session.add(row)
    session.commit()
    refreshed_scopes = sorted(CRM_CORE_SCOPES | {"crm.objects.leads.read"})
    refresh_response: dict[str, object] = {
        "access_token": "renewed-access",
        "refresh_token": "rotated-refresh",
        "expires_in": 1800,
        "hub_id": 1234567,
    }
    if refresh_includes_scopes:
        refresh_response["scopes"] = refreshed_scopes
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubspot.com/oauth/2026-03/token",
        json=refresh_response,
    )

    resolved = asyncio.run(
        repo.resolve_for_execution(
            project_id=project_id,
            provider_key="hubspot",
            credential_ref=credential_ref,
            operation="test.hubspot.refresh",
            required_scopes=[
                (
                    "crm.objects.leads.read"
                    if refresh_includes_scopes
                    else "crm.objects.contacts.read"
                )
            ],
        )
    )

    refresh_request = httpx_mock.get_requests()[1]
    form = parse_qs(refresh_request.content.decode())
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["first-refresh"]
    payload = json.loads(resolved.secret_payload)
    assert payload["access_token"] == "renewed-access"
    assert payload["refresh_token"] == "rotated-refresh"
    expected_scopes = set(refreshed_scopes) if refresh_includes_scopes else CRM_CORE_SCOPES
    connection = repo.status(
        project_id=project_id,
        provider_key="hubspot",
    ).connections[0]
    assert set(connection.scopes) == expected_scopes
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    assert (credential.config_json or {}).get("scope_status") == "known"


@pytest.mark.parametrize("missing_field", ["refresh_token", "expires_in"])
def test_hubspot_refresh_rejects_incomplete_lifecycle_response(
    session: Session,
    project_id: int,
    settings: Settings,
    httpx_mock: HTTPXMock,
    missing_field: str,
) -> None:
    repo = AuthRepository(session)
    credential_ref = _store_hubspot(repo, project_id)
    started = repo.start(
        project_id=project_id,
        provider_key="hubspot",
        auth_method_key="oauth2_authorization_code",
        credential_ref=credential_ref,
        settings=settings,
    ).data
    assert started.authorization_url is not None
    state = parse_qs(urlparse(started.authorization_url).query)["state"][0]
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubspot.com/oauth/2026-03/token",
        json={
            "access_token": "first-access",
            "refresh_token": "first-refresh",
            "expires_in": 1800,
            "scopes": sorted(CRM_CORE_SCOPES),
            "hub_id": 1234567,
        },
    )
    completed = asyncio.run(
        repo.complete_oauth_callback(state=state, code="hubspot-code", settings=settings)
    )
    assert completed.status == "connected"
    row = session.exec(
        select(IntegrationCredential).where(IntegrationCredential.kind == "hubspot")
    ).one()
    row.expires_at = utcnow() - timedelta(minutes=1)
    session.add(row)
    session.commit()
    response: dict[str, object] = {
        "access_token": "incomplete-renewed-access",
        "refresh_token": "rotated-refresh",
        "expires_in": 1800,
        "scopes": sorted(CRM_CORE_SCOPES),
        "hub_id": 1234567,
    }
    response.pop(missing_field)
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubspot.com/oauth/2026-03/token",
        json=response,
    )

    with pytest.raises(ConflictError, match="credential renewal failed"):
        asyncio.run(
            repo.resolve_for_execution(
                project_id=project_id,
                provider_key="hubspot",
                credential_ref=credential_ref,
                operation="test.hubspot.incomplete-refresh",
                required_scopes=["crm.objects.contacts.read"],
            )
        )

    connection = repo.status(project_id=project_id, provider_key="hubspot").connections[0]
    assert connection.status == "repair-required"
    integration = session.exec(
        select(IntegrationCredential).where(IntegrationCredential.kind == "hubspot")
    ).one()
    assert integration.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(integration.id))
    assert payload["access_token"] == "first-access"
    assert payload["refresh_token"] == "first-refresh"
