"""Real auth routes preserve owner, attachment and encrypted backing boundaries.

Fast checks replace Supabase HTTP only. The opt-in Postgres case uses a fresh
explicit local Supabase fixture and real Auth, with synthetic confirmed users.
Provider HTTP is replaced in both cases; no live provider credentials are used.
"""

import json
import os
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlmodel import Session, select

from stackos.api.deps import get_settings
from stackos.auth import UserPrincipal
from stackos.crypto.aes_gcm import CryptoError, decrypt_account
from stackos.db.models import Credential, CredentialUsageEvent, IntegrationCredential, OAuthState
from stackos.repositories.projects import IntegrationCredentialRepository
from stackos.server import create_app


def exercise_accounts(api, selected, monkeypatch, emails, password):
    original = httpx.AsyncClient
    provider_calls = []

    async def provider(request):
        provider_calls.append(request)
        assert request.headers["Authorization"] == "Bearer synthetic-private-key"
        return httpx.Response(200, json={"data": []})

    # Intercept only provider transport; real Supabase requests keep real HTTP.
    def client(**kwargs):
        return original(**kwargs, mounts={"https://openrouter.ai": httpx.MockTransport(provider)})

    monkeypatch.setattr("stackos.auth_providers.repository.testing.httpx.AsyncClient", client)

    def login(email):
        api.cookies.clear()
        api.headers.pop("X-CSRF-Token", None)
        response = api.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        api.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return response.json()["user_id"]

    def project(slug):
        response = api.post(
            "/api/v1/projects", json={"slug": slug, "name": slug, "domain": "example.test"}
        )
        assert response.status_code == 201, response.text
        return response.json()["data"]["id"]

    def create(name, **extra):
        response = api.post(
            "/api/v1/auth/accounts/openrouter",
            json={
                "display_name": name,
                "fields": {"api_key": "synthetic-private-key"},
                **extra,
            },
        )
        assert response.status_code == 201, response.text
        assert "synthetic-private-key" not in response.text
        return response.json()["data"]["credential_ref"]

    owner = login(emails[0])
    assert (
        api.post(
            "/api/v1/auth/accounts/telegram",
            json={
                "display_name": "Local only",
                "auth_method_key": "tdlib-user-session",
                "fields": {},
            },
        ).status_code
        == 403
    )
    own_project = project("a-" + uuid4().hex[:8])
    ref = create("Shared name")
    attach = f"/api/v1/projects/{own_project}/connections/accounts/{ref}"
    assert api.post(attach).status_code == 200
    edit = api.get(f"/api/v1/auth/accounts/{ref}")
    assert edit.status_code == 200, edit.text
    assert edit.json()["secret_present"]["api_key"] is True
    assert "synthetic-private-key" not in edit.text
    assert api.post(f"/api/v1/auth/accounts/{ref}/test").status_code == 200
    assert len(provider_calls) == 1
    # Native/other-process credentials never authorize Account browsing.
    issued = api.post(
        "/api/v1/operations/localRuntime.authorize/call",
        json={
            "arguments": {"project_id": own_project},
        },
    )
    assert issued.status_code == 200, issued.text
    computer_token = issued.json()["credential"]
    assert (
        api.get(
            "/api/v1/auth/accounts",
            headers={
                "Authorization": "Bearer " + computer_token,
            },
        ).status_code
        == 403
    )

    login(emails[1])
    other_project = project("b-" + uuid4().hex[:8])
    assert api.get("/api/v1/auth/accounts").json()["accounts"] == []
    assert api.get(f"/api/v1/auth/accounts/{ref}").status_code == 404
    assert (
        api.patch(f"/api/v1/auth/accounts/{ref}", json={"display_name": "stolen"}).status_code
        == 404
    )
    assert api.post(f"/api/v1/auth/accounts/{ref}/test").status_code == 404
    assert (
        api.post(
            "/api/v1/auth/accounts/openrouter/start",
            json={
                "credential_ref": ref,
            },
        ).status_code
        == 404
    )
    assert api.post(f"/api/v1/auth/accounts/{ref}/revoke").status_code == 404
    assert (
        api.post(f"/api/v1/projects/{other_project}/connections/accounts/{ref}").status_code == 404
    )
    assert api.delete(attach).status_code == 404
    assert api.get(f"/api/v1/projects/{own_project}/connections/accounts").status_code == 404
    assert len(provider_calls) == 1
    assert create("Shared name") != ref

    login(emails[0])
    # The server holds encrypted bytes and resolves them only inside its owner context.
    with Session(api.app.state.engine) as session:
        session.info.update(
            user_principal=UserPrincipal(owner, selected.environment_id),
            server_environment_id=selected.environment_id,
        )
        account = session.exec(select(Credential).where(Credential.credential_ref == ref)).one()
        backing = session.get(IntegrationCredential, account.integration_credential_id)
        assert b"synthetic-private-key" not in backing.encrypted_payload
        assert (
            IntegrationCredentialRepository(session).get_decrypted(backing.id)
            == b"synthetic-private-key"
        )
        with pytest.raises(CryptoError):
            decrypt_account(
                backing.encrypted_payload,
                nonce=backing.nonce,
                credential_ref=ref,
                provider_key="openrouter",
                seed=os.urandom(32),
            )
        audits = session.exec(select(CredentialUsageEvent)).all()
        assert "synthetic-private-key" not in json.dumps([a.metadata_json for a in audits])

    # OAuth is a one-use transaction owned by the initiating user/environment.
    created = api.post(
        "/api/v1/auth/accounts/google-search-console",
        json={
            "display_name": "OAuth",
            "auth_method_key": "oauth2_authorization_code",
            "attach_project_id": own_project,
            "fields": {"client_id": "synthetic-client", "client_secret": "synthetic-app-secret"},
        },
    )
    assert created.status_code == 201, created.text
    oauth_ref = created.json()["data"]["credential_ref"]
    started = api.post(
        "/api/v1/auth/accounts/google-search-console/start",
        json={
            "credential_ref": oauth_ref,
            "auth_method_key": "oauth2_authorization_code",
            "attach_project_id": own_project,
        },
    )
    assert started.status_code == 200, started.text
    state = parse_qs(urlparse(started.json()["data"]["authorization_url"]).query)["state"][0]
    api.cookies.clear()
    landing = api.get(
        "/api/v1/auth/oauth/callback",
        params={
            "state": state,
            "error": "access_denied",
        },
    )
    assert landing.status_code == 200 and "Continue Account connection" in landing.text
    assert landing.headers["cache-control"] == "no-store"
    login(emails[1])
    assert (
        api.post(
            "/api/v1/auth/accounts/google-search-console/start",
            json={
                "credential_ref": oauth_ref,
                "auth_method_key": "oauth2_authorization_code",
            },
        ).status_code
        == 404
    )
    completion = "/api/v1/auth/oauth/complete"
    params = {"state": state, "error": "access_denied"}
    denied = api.get(completion, params=params, follow_redirects=False)
    assert denied.status_code == 303 and "oauth_status=error" in denied.headers["location"]
    with Session(api.app.state.engine) as session:
        transaction = session.exec(
            select(OAuthState).where(
                OAuthState.credential_id == created.json()["data"]["credential_id"]
            )
        ).one()
        assert transaction.consumed_at is None
    login(emails[0])
    finished = api.get(completion, params=params, follow_redirects=False)
    assert finished.status_code == 303 and "oauth_status=denied" in finished.headers["location"]
    assert finished.headers["location"].startswith(selected.server_url + "/accounts?")
    repeated = api.get(completion, params=params, follow_redirects=False)
    assert "oauth_status=error" in repeated.headers["location"]
    assert api.post(f"/api/v1/auth/accounts/{ref}/revoke").status_code == 409
    assert api.delete(attach).status_code == 200
    assert api.post(f"/api/v1/auth/accounts/{ref}/revoke").status_code == 200
    with Session(api.app.state.engine) as session:
        row = session.exec(select(Credential).where(Credential.credential_ref == ref)).one()
        assert row.integration_credential_id is None


def test_private_accounts_routes(client, settings, monkeypatch):
    origin = "http://127.0.0.1:19610"
    selected = settings.model_copy(
        update={
            "process_role": "server",
            "environment_id": "account-route-test",
            "server_url": origin,
            "supabase_url": "http://127.0.0.1:19621",
            "supabase_anon_key": SecretStr("synthetic-anon"),
        }
    )
    client.app.state.settings = selected
    client.app.dependency_overrides[get_settings] = lambda: selected
    client.base_url = httpx.URL(origin)
    client.headers["Origin"] = origin
    original = httpx.AsyncClient
    users = {email: str(uuid4()) for email in ("a@example.test", "b@example.test")}

    def respond(request):
        if request.url.path.endswith("/user"):
            user = request.headers["authorization"][7:]
            return httpx.Response(200, json={"id": user, "email_confirmed_at": "2026-11-10"})
        user = users[json.loads(request.content)["email"]]
        return httpx.Response(
            200, json={"access_token": user, "refresh_token": "synthetic-refresh"}
        )

    monkeypatch.setattr(
        "stackos.auth.httpx.AsyncClient",
        lambda **kw: original(**kw, transport=httpx.MockTransport(respond)),
    )
    # Both modules share httpx; preserve the Auth fake beneath the provider mount.
    exercise_accounts(client, selected, monkeypatch, list(users), "password123")


def test_private_accounts_real_supabase(monkeypatch):
    env_file = os.environ.get("STACKOS_TEST_POSTGRES_ENV_FILE")
    if not env_file:
        pytest.skip("requires explicit disposable STACKOS_TEST_POSTGRES_ENV_FILE")
    from stackos.config import get_settings as read_settings

    monkeypatch.setenv("STACKOS_ENV_FILE", env_file)
    selected = read_settings()
    assert selected.process_role == "server"
    assert selected.supabase_url.startswith("http://127.0.0.1:")
    users = [f"{uuid4().hex}@example.test" for _ in range(2)]
    password = uuid4().hex
    ids = []
    headers = {
        "apikey": selected.supabase_service_role_key.get_secret_value(),
        "Authorization": "Bearer " + selected.supabase_service_role_key.get_secret_value(),
    }
    try:
        with httpx.Client(base_url=selected.supabase_url + "/auth/v1", headers=headers) as auth:
            for email in users:
                response = auth.post(
                    "/admin/users",
                    json={
                        "email": email,
                        "password": password,
                        "email_confirm": True,
                    },
                )
                assert response.status_code == 200
                ids.append(response.json()["id"])
        with TestClient(create_app(selected), base_url=selected.server_url) as api:
            api.headers["Origin"] = selected.server_url
            exercise_accounts(api, selected, monkeypatch, users, password)
    finally:
        with httpx.Client(base_url=selected.supabase_url + "/auth/v1", headers=headers) as auth:
            for user_id in ids:
                assert auth.delete("/admin/users/" + user_id).status_code == 200
