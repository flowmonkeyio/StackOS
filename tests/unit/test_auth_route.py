"""Auth bootstrap endpoint tests — ``GET /api/v1/auth/ui-token``.

This endpoint is whitelisted from bearer-token auth so the same-origin
Vue UI can bootstrap its token at boot. The HostHeaderMiddleware (loopback
only) and CORSMiddleware (same-origin) are the upstream defences.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from content_stack.auth import derive_ui_token


def test_ui_token_returns_token_without_authorization(
    client: TestClient,
    auth_token: str,
) -> None:
    """The UI calls this endpoint *before* it has a token; whitelist required."""
    resp = client.get("/api/v1/auth/ui-token")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"token": derive_ui_token(auth_token)}
    assert body["token"] != auth_token


def test_ui_token_is_in_auth_whitelist() -> None:
    """Module-level invariant: the new path is in WHITELIST_PREFIXES."""
    from content_stack.auth import WHITELIST_PREFIXES, requires_auth

    assert "/api/v1/auth/ui-token" in WHITELIST_PREFIXES
    assert requires_auth("/api/v1/auth/ui-token") is False


def test_ui_token_rejects_non_loopback_host(client: TestClient) -> None:
    """HostHeaderMiddleware still runs for whitelisted paths — non-loopback → 421."""
    resp = client.get(
        "/api/v1/auth/ui-token",
        headers={"host": "evil.example.com"},
    )
    assert resp.status_code == 421


def test_ui_token_response_shape_only_carries_token(
    client: TestClient,
    auth_token: str,
) -> None:
    """Body must be ``{token: str}`` exactly — no leakage of other state.

    Guards against accidentally exposing settings or app-state internals
    via the bootstrap response.
    """
    resp = client.get("/api/v1/auth/ui-token")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"token"}
    assert body["token"] == derive_ui_token(auth_token)
    assert body["token"] != auth_token
    assert isinstance(body["token"], str)
    assert len(body["token"]) > 0


def test_ui_token_can_read_rest_data(client: TestClient, auth_token: str) -> None:
    """The browser token remains sufficient for observer-mode dashboard reads."""
    ui_token = derive_ui_token(auth_token)
    resp = client.get(
        "/api/v1/projects",
        headers={"authorization": f"Bearer {ui_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_ui_token_cannot_mutate_rest_data(client: TestClient, auth_token: str) -> None:
    """The browser token is not accepted for POST/PATCH/DELETE flows."""
    ui_token = derive_ui_token(auth_token)
    resp = client.post(
        "/api/v1/projects",
        headers={"authorization": f"Bearer {ui_token}"},
        json={
            "slug": "observer-mode",
            "name": "Observer Mode",
            "domain": "example.test",
            "locale": "en-US",
        },
    )
    assert resp.status_code == 403
    assert "read-only" in resp.json()["detail"]


def test_ui_token_cannot_access_mcp(client: TestClient, auth_token: str) -> None:
    """MCP remains agent-only even if the browser has a valid UI token."""
    ui_token = derive_ui_token(auth_token)
    resp = client.post(
        "/mcp",
        headers={"authorization": f"Bearer {ui_token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert resp.status_code == 403
    assert "read-only" in resp.json()["detail"]


def test_daemon_token_can_still_mutate_rest_data(client: TestClient, auth_token: str) -> None:
    """The daemon token remains the write-capable local-admin token."""
    resp = client.post(
        "/api/v1/projects",
        headers={"authorization": f"Bearer {auth_token}"},
        json={
            "slug": "agent-operated",
            "name": "Agent Operated",
            "domain": "example.test",
            "locale": "en-US",
        },
    )
    assert resp.status_code == 201
