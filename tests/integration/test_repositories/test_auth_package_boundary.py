"""Host lifecycle calls the installed protocol package only at explicit boundaries."""

import pytest
from sqlmodel import select
from stackos_connectors import auth as connector_auth

from stackos.auth_providers import AuthRepository
from stackos.db.models import Credential, OAuthState
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
