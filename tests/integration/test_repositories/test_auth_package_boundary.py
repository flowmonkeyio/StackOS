"""Host lifecycle calls the installed protocol package only at explicit boundaries."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select
from stackos_connectors import auth as connector_auth

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.db.models import (
    ActionCall,
    Credential,
    CredentialRefreshEvent,
    CredentialScope,
    CredentialUsageEvent,
    OAuthState,
    ProjectCredential,
)
from tests.integration.test_repositories.test_oauth_lifecycle import (
    _interactive_google_profile,
    _start_google,
)


async def test_host_keeps_consent_state_and_explicit_exchange_ownership(
    session,
    project_id,
    settings,
    httpx_mock,
    monkeypatch,
):
    repo = AuthRepository(session)
    calls = []
    original = connector_auth.request_token

    async def traced(*args, **kwargs):
        calls.append((args[0], kwargs["grant_type"]))
        assert "credential_ref" not in kwargs and "project_id" not in kwargs
        return await original(*args, **kwargs)

    monkeypatch.setattr(connector_auth, "request_token", traced)
    ref = _interactive_google_profile(repo, project_id)
    _, state, _ = _start_google(repo, project_id=project_id, credential_ref=ref, settings=settings)
    assert not calls
    stored_state = session.exec(select(OAuthState)).one()
    assert stored_state.state != state and stored_state.consumed_at is None
    httpx_mock.add_response(
        url="https://oauth2.googleapis.com/token",
        json={
            "access_token": "SYNTHETIC-TOKEN",
            "refresh_token": "SYNTHETIC-REFRESH",
            "expires_in": 3600,
        },
    )
    result = await repo.complete_oauth_callback(
        state=state, settings=settings, code="SYNTHETIC-CODE"
    )
    assert result.status == "connected"
    assert calls == [("google-search-console", "authorization_code")]
    assert "SYNTHETIC" not in repr(result)
    credential = session.exec(select(Credential).where(Credential.credential_ref == ref)).one()
    assert credential.status == "connected"
    from stackos.repositories.base import ConflictError

    with pytest.raises(ConflictError):
        await repo.complete_oauth_callback(state=state, settings=settings, code="SYNTHETIC-CODE")
    assert len(calls) == 1


def test_discovery_does_not_acquire_or_refresh_tokens(session, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("metadata discovery requested a token")

    monkeypatch.setattr(connector_auth, "request_token", forbidden)
    AuthRepository(session).sync_providers()


async def test_explicit_oauth_account_refreshes_then_executes_with_audit(
    session, project_id, httpx_mock
):
    """The saved canonical method owns refresh, action selection and audit."""
    repo = AuthRepository(session)
    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key="google-search-console",
        auth_method_key="oauth2_refresh_token",
        display_name="explicit-oauth-action",
        fields={
            "client_id": "explicit-client-canary",
            "client_secret": "explicit-secret-canary",
            "refresh_token": "explicit-refresh-canary",
        },
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    ).data
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == stored.credential_ref)
    ).one()
    original_id = credential.id
    backing_id = credential.integration_credential_id
    httpx_mock.add_response(
        method="POST",
        url="https://oauth2.googleapis.com/token",
        json={
            "access_token": "explicit-access-canary",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/webmasters.readonly",
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://www.googleapis.com/webmasters/v3/sites",
        match_headers={"Authorization": "Bearer explicit-access-canary"},
        json={"siteEntry": [{"siteUrl": "https://example.test/", "permissionLevel": "siteOwner"}]},
    )
    result = (
        await ActionRepository(session).execute(
            project_id=project_id,
            action_ref="seo.search-console.sites.list",
            credential_ref=stored.credential_ref,
            input_json={},
        )
    ).data
    assert result.action_call.status == "success"
    session.refresh(credential)
    assert credential.id == original_id and credential.integration_credential_id == backing_id
    assert credential.credential_ref == stored.credential_ref
    assert credential.auth_method_key == "oauth2_refresh_token"
    assert credential.status == "connected"
    assert session.exec(
        select(ProjectCredential).where(
            ProjectCredential.credential_id == original_id,
            ProjectCredential.project_id == project_id,
        )
    ).one()
    assert (
        session.exec(select(CredentialScope).where(CredentialScope.credential_id == original_id))
        .one()
        .scope
        == "https://www.googleapis.com/auth/webmasters.readonly"
    )
    refresh = session.exec(
        select(CredentialRefreshEvent).where(CredentialRefreshEvent.credential_id == original_id)
    ).one()
    assert refresh.status == "refreshed"
    audit = session.exec(select(ActionCall)).one()
    usages = session.exec(
        select(CredentialUsageEvent).where(CredentialUsageEvent.credential_id == original_id)
    ).all()
    assert usages and len(httpx_mock.get_requests()) == 2
    serialized = json.dumps(
        {
            "result": result.model_dump(mode="json"),
            "refresh": refresh.model_dump(mode="json"),
            "audit": audit.model_dump(mode="json"),
            "usage": [event.model_dump(mode="json") for event in usages],
        }
    )
    assert "canary" not in serialized
