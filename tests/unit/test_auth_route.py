"""Auth bootstrap endpoint tests — ``GET /api/v1/auth/ui-token``.

This endpoint is whitelisted from bearer-token auth so the same-origin
Vue UI can bootstrap its token at boot. The HostHeaderMiddleware (loopback
only) and CORSMiddleware (same-origin) are the upstream defences.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from stackos.auth import derive_ui_token


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
    from stackos.auth import WHITELIST_PREFIXES, requires_auth

    assert "/api/v1/auth/ui-token" in WHITELIST_PREFIXES
    assert requires_auth("/api/v1/auth/ui-token") is False


def test_ui_token_rejects_non_loopback_host(client: TestClient) -> None:
    """HostHeaderMiddleware still runs for whitelisted paths — non-loopback → 421."""
    resp = client.get(
        "/api/v1/auth/ui-token",
        headers={"host": "evil.example.com"},
    )
    assert resp.status_code == 421


def test_public_ingress_paths_allow_tunnel_host_but_still_verify_provider(
    client: TestClient,
) -> None:
    """Tunnel/deployed Hosts reach only provider-verified ingress paths."""
    resp = client.post(
        "/api/v1/ingress/telegram/1/support-bot",
        headers={"host": "stackos-local.ngrok-free.app"},
        json={"update_id": 1},
    )
    assert resp.status_code != 421


def test_tunnel_host_cannot_reach_non_ingress_api(client: TestClient) -> None:
    """The public Host bypass is scoped to webhook ingress, not the whole API."""
    resp = client.get(
        "/api/v1/projects",
        headers={"host": "stackos-local.ngrok-free.app"},
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


def test_ui_token_can_call_read_only_operations(client: TestClient, auth_token: str) -> None:
    """POST is only a transport detail when the operation spec is read-only."""
    project_id = _create_project(client, auth_token)
    ui_token = derive_ui_token(auth_token)

    resp = client.post(
        "/api/v1/operations/agentRequest.list/call",
        headers={"authorization": f"Bearer {ui_token}"},
        json={"arguments": {"project_id": project_id, "claimable": True}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    assert body["total_estimate"] == 0


def test_ui_token_can_call_telegram_profile_setup_operation(
    client: TestClient,
    auth_token: str,
) -> None:
    """The browser can configure safe bot policy after storing daemon-side secrets."""
    project_id = _create_project(client, auth_token)
    ui_token = derive_ui_token(auth_token)

    stored = client.post(
        f"/api/v1/projects/{project_id}/auth/telegram-bot/credentials",
        headers={"authorization": f"Bearer {ui_token}"},
        json={
            "auth_method_key": "bot-token",
            "profile_key": "support",
            "fields": {
                "bot_token": "123456:ABC",
                "webhook_secret_token": "telegram-secret",
            },
        },
    )
    assert stored.status_code == 201, stored.text

    resp = client.post(
        "/api/v1/operations/communicationProfile.upsert/call",
        headers={"authorization": f"Bearer {ui_token}"},
        json={
            "arguments": {
                "project_id": project_id,
                "key": "support-bot",
                "identity": {
                    "display_name": "Support Bot",
                    "purpose": "Handle support requests from approved Telegram users.",
                    "voice": "Concise and calm.",
                },
                "provider_facets": {"telegram-bot": {"auth_profile_key": "support"}},
                "access_policy": {
                    "dm_mode": "allowlist",
                    "group_mode": "allowlist",
                    "user_mode": "allowlist",
                    "allowed_chat_refs": ["telegram-chat:999"],
                    "allowed_user_refs": ["telegram-user:555"],
                },
                "response_mode": "raw",
            }
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["key"] == "support-bot"
    assert resp.json()["data"]["provider_facets"]["telegram-bot"]["auth_profile_key"] == "support"
    assert "123456:ABC" not in resp.text
    assert "telegram-secret" not in resp.text


def test_ui_token_can_call_ingress_setup_operation(
    client: TestClient,
    auth_token: str,
) -> None:
    """The browser setup surface can configure the generic public ingress URL."""
    project_id = _create_project(client, auth_token)
    ui_token = derive_ui_token(auth_token)

    resp = client.post(
        "/api/v1/operations/ingressEndpoint.configure/call",
        headers={"authorization": f"Bearer {ui_token}"},
        json={
            "arguments": {
                "project_id": project_id,
                "driver": "public-url",
                "public_base_url": "https://stackos.example.com",
            }
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["public_base_url"] == "https://stackos.example.com"


def test_ui_token_can_update_tracker_task_status(client: TestClient, auth_token: str) -> None:
    """The local console can close or reopen tasks without exposing daemon auth."""
    project_id = _create_project(client, auth_token)
    ui_token = derive_ui_token(auth_token)

    created = client.post(
        "/api/v1/operations/tracker.createTask/call",
        headers={"authorization": f"Bearer {auth_token}"},
        json={
            "arguments": {
                "project_id": project_id,
                "key": "ui-status-task",
                "title": "UI status task",
                "created_by": "test",
            }
        },
    )
    assert created.status_code == 200, created.text

    resp = client.post(
        "/api/v1/operations/tracker.updateTask/call",
        headers={"authorization": f"Bearer {ui_token}"},
        json={
            "arguments": {
                "project_id": project_id,
                "task_key": "ui-status-task",
                "patch_json": {"status": "complete"},
                "actor": "ui",
            }
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["task"]["status"] == "complete"


def test_ui_token_cannot_call_mutating_operations(client: TestClient, auth_token: str) -> None:
    """Operation POST access stays limited by the operation spec, not URL shape."""
    project_id = _create_project(client, auth_token)
    ui_token = derive_ui_token(auth_token)

    resp = client.post(
        "/api/v1/operations/agentRequest.create/call",
        headers={"authorization": f"Bearer {ui_token}"},
        json={
            "arguments": {
                "project_id": project_id,
                "request_key": "blocked-ui-create",
                "title": "Blocked UI create",
            }
        },
    )
    assert resp.status_code == 403
    assert "browser-safe operations" in resp.json()["detail"]

    tracker_resp = client.post(
        "/api/v1/operations/tracker.createTicket/call",
        headers={"authorization": f"Bearer {ui_token}"},
        json={
            "arguments": {
                "project_id": project_id,
                "task_key": "ui-status-task",
                "key": "blocked-ui-ticket",
                "title": "Blocked UI ticket",
            }
        },
    )
    assert tracker_resp.status_code == 403
    assert "browser-safe operations" in tracker_resp.json()["detail"]

    lifecycle_resp = client.post(
        "/api/v1/operations/runPlan.reopen/call",
        headers={"authorization": f"Bearer {ui_token}"},
        json={"arguments": {"run_plan_id": 1, "reason": "UI token must not reopen workflows."}},
    )
    assert lifecycle_resp.status_code == 403
    assert "browser-safe operations" in lifecycle_resp.json()["detail"]


def test_ui_token_can_manage_provider_auth_setup(client: TestClient, auth_token: str) -> None:
    """The browser token can only perform narrow local-admin credential setup."""
    project_id = _create_project(client, auth_token)
    ui_token = derive_ui_token(auth_token)

    created = client.post(
        f"/api/v1/projects/{project_id}/auth/firecrawl/credentials",
        headers={"authorization": f"Bearer {ui_token}"},
        json={
            "auth_method_key": "api_key",
            "label": "Primary",
            "fields": {"api_key": "fc-secret"},
        },
    )
    assert created.status_code == 201, created.text
    credential_ref = created.json()["data"]["credential_ref"]
    assert credential_ref.startswith("cred_")
    assert "fc-secret" not in created.text

    revoked = client.post(
        f"/api/v1/projects/{project_id}/auth/revoke",
        headers={"authorization": f"Bearer {ui_token}"},
        json={"credential_ref": credential_ref},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["data"]["status"] == "revoked"
    assert "fc-secret" not in revoked.text


def test_ui_token_can_create_project_for_local_setup(
    client: TestClient,
    auth_token: str,
) -> None:
    """The browser token can create the first project without exposing daemon auth."""
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
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["slug"] == "observer-mode"


def test_ui_token_cannot_mutate_general_rest_data(client: TestClient, auth_token: str) -> None:
    """The browser token is not accepted for general PATCH/DELETE flows."""
    ui_token = derive_ui_token(auth_token)
    created = client.post(
        "/api/v1/projects",
        headers={"authorization": f"Bearer {auth_token}"},
        json={
            "slug": "observer-mode",
            "name": "Observer Mode",
            "domain": "example.test",
            "locale": "en-US",
        },
    )
    project_id = created.json()["data"]["id"]

    resp = client.patch(
        f"/api/v1/projects/{project_id}",
        headers={"authorization": f"Bearer {ui_token}"},
        json={"name": "Changed"},
    )
    assert resp.status_code == 403
    assert "create projects" in resp.json()["detail"]


def test_ui_token_cannot_access_mcp(client: TestClient, auth_token: str) -> None:
    """MCP remains agent-only even if the browser has a valid UI token."""
    ui_token = derive_ui_token(auth_token)
    resp = client.post(
        "/mcp",
        headers={"authorization": f"Bearer {ui_token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert resp.status_code == 403
    assert "create projects" in resp.json()["detail"]


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


def _create_project(client: TestClient, auth_token: str) -> int:
    resp = client.post(
        "/api/v1/projects",
        headers={"authorization": f"Bearer {auth_token}"},
        json={
            "slug": "auth-ui-project",
            "name": "Auth UI Project",
            "domain": "example.test",
            "locale": "en-US",
        },
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["data"]["id"])
