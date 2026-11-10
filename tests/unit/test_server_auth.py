"""Server auth regression checks. Supabase HTTP is substituted in this file.

Real Auth, Postgres and browser proof is recorded separately in the delivery receipt.
"""

from __future__ import annotations

import json
import time
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlmodel import Session, select

from stackos.api.deps import get_settings
from stackos.auth import session_cookie_name
from stackos.config import Settings
from stackos.db.models import Project, UserSession
from stackos.repositories.projects import ProjectRepository

ORIGIN = "http://127.0.0.1:18510"


@pytest.fixture
def online(client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch):
    selected = settings.model_copy(
        update={
            "process_role": "server",
            "environment_id": "auth-test",
            "server_url": ORIGIN,
            "supabase_url": "http://127.0.0.1:18521",
            "supabase_anon_key": SecretStr("test-anon"),
        }
    )
    client.app.state.settings = selected
    client.app.dependency_overrides[get_settings] = lambda: selected
    client.base_url = httpx.URL(ORIGIN)
    client.headers.clear()
    client.headers["Origin"] = ORIGIN
    state = {"user_id": str(uuid4()), "verified": True, "error": None, "calls": []}

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1" and request.url.port == 18521
        assert request.headers["apikey"] == "test-anon"
        state["calls"].append((request.method, request.url.path, request.url.query))
        if state["error"]:
            return httpx.Response(state["error"], json={"message": "provider-secret-error"})
        if request.url.path.endswith("/user"):
            return httpx.Response(
                200,
                json={
                    "id": state["user_id"],
                    "email_confirmed_at": "2026-11-10T00:00:00Z" if state["verified"] else None,
                },
            )
        return httpx.Response(
            200, json={"access_token": "private-access", "refresh_token": "private-refresh"}
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "stackos.auth.httpx.AsyncClient",
        lambda **kw: original(
            **kw,
            transport=httpx.MockTransport(respond),
        ),
    )
    return client, selected, state


def sign_in(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/auth/login", json={"email": "user@example.test", "password": "password-123"}
    )
    assert response.status_code == 200, response.text
    assert "private-access" not in response.text and "private-refresh" not in response.text
    return response.json()


def test_verified_session_and_no_data_adoption(online):
    client, selected, state = online
    with Session(client.app.state.engine) as session:
        existing = (
            ProjectRepository(session)
            .create(slug="old-data", name="Old data", domain="example.test")
            .data
        )
        original = session.get(Project, existing.id).model_dump(mode="json")
    response = client.post(
        "/api/v1/auth/register", json={"email": "user@example.test", "password": "password-123"}
    )
    assert response.json() == {"status": "verification_pending"}
    assert not client.cookies
    with Session(client.app.state.engine) as session:
        assert session.exec(select(UserSession)).all() == []
        assert session.exec(select(Project)).one().model_dump(mode="json") == original
    body = sign_in(client)
    response = client.get("/api/v1/auth/session")
    assert response.json()["user_id"] == state["user_id"]
    assert response.json()["csrf_token"] == body["csrf_token"]
    assert response.headers["cache-control"] == "no-store"
    with Session(client.app.state.engine) as session:
        assert session.exec(select(Project)).one().model_dump(mode="json") == original
        row = session.exec(select(UserSession)).one()
        assert b"private-access" not in row.encrypted_payload
        assert client.cookies.get(session_cookie_name(selected)) != row.token_digest
    assert client.get("/api/v1/projects").json()["items"] == []
    # Accounts are now admitted through the accepted owner-aware repository;
    # signing up still cannot see legacy standalone Accounts.
    assert client.get("/api/v1/auth/accounts").json()["accounts"] == []
    for path in (
        "/mcp",
        "/generated-assets/private.txt",
    ):
        assert client.get(path).status_code == 403


def test_origin_csrf_and_environment_replay(online):
    client, selected, state = online
    assert (
        client.post(
            "/api/v1/auth/login", headers={"Origin": "http://127.0.0.1:19510"}, json={}
        ).status_code
        == 403
    )
    assert state["calls"] == []
    body = sign_in(client)
    assert client.post("/api/v1/auth/refresh").status_code == 403
    assert (
        client.post(
            "/api/v1/auth/refresh", headers={"X-CSRF-Token": body["csrf_token"]}
        ).status_code
        == 200
    )
    value = client.cookies.get(session_cookie_name(selected))
    other = selected.model_copy(update={"environment_id": "other"})
    assert session_cookie_name(other) != session_cookie_name(selected)
    client.app.state.settings = other
    client.cookies.set(session_cookie_name(other), value, domain="127.0.0.1", path="/")
    assert client.get("/api/v1/auth/session").status_code == 401


def test_no_admin_bootstrap_or_unverified_session(online, auth_token):
    client, _selected, state = online
    assert client.get("/api/v1/auth/ui-token").status_code == 401
    assert (
        client.get(
            "/api/v1/projects", headers={"Authorization": f"Bearer {auth_token}"}
        ).status_code
        == 401
    )
    state["verified"] = False
    response = client.post(
        "/api/v1/auth/login", json={"email": "user@example.test", "password": "password-123"}
    )
    assert response.status_code == 401
    assert not client.cookies


def test_expiry_logout_replay_and_provider_outage(online):
    client, selected, state = online
    body = sign_in(client)
    copied = client.cookies.get(session_cookie_name(selected))
    state["error"] = 503
    response = client.get("/api/v1/auth/session")
    assert response.status_code == 503 and "provider-secret-error" not in response.text
    response = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": body["csrf_token"]})
    assert response.json() == {"status": "signed_out", "provider_revoked": False}
    client.cookies.set(session_cookie_name(selected), copied, domain="127.0.0.1", path="/")
    assert client.get("/api/v1/auth/session").status_code == 401
    state["error"] = None
    client.cookies.clear()
    sign_in(client)
    with Session(client.app.state.engine) as session:
        row = session.exec(select(UserSession)).one()
        row.expires_at = int(time.time()) - 1
        session.add(row)
        session.commit()
    assert client.get("/api/v1/auth/session").status_code == 401


def test_recovery_callback_pending_invalid_retry_and_reset(online):
    client, _selected, state = online
    assert (
        client.post("/api/v1/auth/recover", json={"email": "user@example.test"}).status_code == 200
    )
    callback = "/api/v1/auth/callback?token_hash=test-hash&type=recovery"
    before = len(state["calls"])
    response = client.get(callback)
    assert response.status_code == 200 and len(state["calls"]) == before
    assert response.headers["referrer-policy"] == "strict-origin"
    assert (
        not client.cookies
        and "frame-ancestors 'none'" in response.headers["content-security-policy"]
    )
    state["error"] = 403
    response = client.post(
        "/api/v1/auth/verify", json={"token_hash": "expired", "type": "recovery"}
    )
    assert response.status_code == 401 and "verification_expired_or_invalid" in response.text
    state["error"] = 429
    assert (
        client.post("/api/v1/auth/resend", json={"email": "user@example.test"}).status_code == 429
    )
    state["error"] = None
    response = client.post(
        "/api/v1/auth/callback",
        content="token_hash=test-hash&type=recovery",
        follow_redirects=False,
    )
    assert response.status_code == 303
    body = client.get("/api/v1/auth/session").json()
    assert body["status"] == "recovery_required"
    assert client.get("/api/v1/projects").status_code == 403
    response = client.post(
        "/api/v1/auth/password",
        json={"password": "new-password-123"},
        headers={"X-CSRF-Token": body["csrf_token"]},
    )
    assert response.json() == {"status": "password_reset_sign_in_required"}
    assert not client.cookies
    with Session(client.app.state.engine) as session:
        assert session.exec(select(UserSession)).all() == []


def test_provider_values_never_in_response(online):
    client, _selected, _state = online
    sign_in(client)
    for path in ("session",):
        body = json.dumps(client.get(f"/api/v1/auth/{path}").json())
        assert "private-access" not in body and "private-refresh" not in body
