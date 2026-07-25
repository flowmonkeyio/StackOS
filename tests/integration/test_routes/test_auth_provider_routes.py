"""REST contracts for global Accounts and project Connections."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from pytest_httpx import HTTPXMock

from stackos.operations.dispatcher import OperationDispatcher


def _create_account(
    api: TestClient,
    *,
    provider_key: str,
    display_name: str,
    fields: dict[str, str],
    auth_method_key: str | None = None,
    attach_project_id: int | None = None,
) -> dict:
    body: dict[str, object] = {
        "display_name": display_name,
        "fields": fields,
        "attach_project_id": attach_project_id,
    }
    if auth_method_key is not None:
        body["auth_method_key"] = auth_method_key
    response = api.post(f"/api/v1/auth/accounts/{provider_key}", json=body)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _firecrawl_account(
    api: TestClient,
    *,
    project_id: int | None = None,
    display_name: str = "Firecrawl - Default",
) -> dict:
    return _create_account(
        api,
        provider_key="firecrawl",
        display_name=display_name,
        auth_method_key="api_key",
        fields={"api_key": "fc-secret"},
        attach_project_id=project_id,
    )


def test_global_inventory_and_project_connections_are_distinct(
    api: TestClient,
    project_id: int,
) -> None:
    first = _firecrawl_account(api, display_name="Shared Production")
    second = _firecrawl_account(
        api,
        project_id=project_id,
        display_name="Project Default",
    )

    inventory = api.get("/api/v1/auth/accounts?provider_key=firecrawl")
    connections = api.get(
        f"/api/v1/projects/{project_id}/connections/accounts?provider_key=firecrawl"
    )

    assert inventory.status_code == 200, inventory.text
    assert connections.status_code == 200, connections.text
    assert [item["credential_ref"] for item in inventory.json()["accounts"]] == [
        second["credential_ref"],
        first["credential_ref"],
    ]
    assert [item["credential_ref"] for item in connections.json()["accounts"]] == [
        second["credential_ref"]
    ]
    assert first["project_ids"] == []
    assert second["project_ids"] == [project_id]
    rendered = json.dumps(inventory.json())
    assert "fc-secret" not in rendered
    assert "encrypted_payload" not in rendered
    assert "profile_key" not in rendered


def test_attach_and_detach_do_not_mutate_the_global_account(
    api: TestClient,
    project_id: int,
) -> None:
    account = _firecrawl_account(api)
    ref = account["credential_ref"]

    attached = api.post(f"/api/v1/projects/{project_id}/connections/accounts/{ref}")
    detached = api.delete(f"/api/v1/projects/{project_id}/connections/accounts/{ref}")
    inventory = api.get("/api/v1/auth/accounts?provider_key=firecrawl").json()

    assert attached.status_code == 200, attached.text
    assert attached.json()["data"]["project_ids"] == [project_id]
    assert detached.status_code == 200, detached.text
    assert detached.json()["data"]["project_ids"] == []
    assert inventory["accounts"][0]["status"] == "connected"


def test_exact_account_routes_dispatch_registered_operation_contracts(
    api: TestClient,
    project_id: int,
    monkeypatch,
) -> None:
    dispatched: list[tuple[str, bool]] = []
    original_dispatch = OperationDispatcher.dispatch

    async def recorded_dispatch(self, name, arguments, **kwargs):
        dispatched.append((name, kwargs.get("trusted_local_admin") is True))
        return await original_dispatch(self, name, arguments, **kwargs)

    monkeypatch.setattr(OperationDispatcher, "dispatch", recorded_dispatch)

    account = _firecrawl_account(api)
    ref = account["credential_ref"]
    assert api.get("/api/v1/auth/accounts").status_code == 200
    assert api.get(f"/api/v1/auth/accounts/{ref}").status_code == 200
    assert (
        api.patch(
            f"/api/v1/auth/accounts/{ref}",
            json={"display_name": "Firecrawl - Renamed", "fields": {}},
        ).status_code
        == 200
    )
    assert api.post(f"/api/v1/projects/{project_id}/connections/accounts/{ref}").status_code == 200
    assert api.get(f"/api/v1/projects/{project_id}/connections/accounts").status_code == 200
    assert (
        api.delete(f"/api/v1/projects/{project_id}/connections/accounts/{ref}").status_code == 200
    )
    assert api.post(f"/api/v1/auth/accounts/{ref}/revoke").status_code == 200

    assert dispatched == [
        ("account.create", True),
        ("account.list", True),
        ("account.get", True),
        ("account.update", True),
        ("connection.attach", True),
        ("connection.list", True),
        ("connection.detach", True),
        ("account.revoke", True),
    ]


def test_account_edit_test_and_revoke_are_global_admin_actions(
    api: TestClient,
    project_id: int,
    monkeypatch,
) -> None:
    class _Probe:
        def __init__(self, *, payload: bytes, **_kwargs: object) -> None:
            assert payload == b"fc-secret"

        async def test_credentials(self) -> dict[str, object]:
            return {
                "ok": True,
                "status": "connected",
                "summary": "Account is ready",
                "metadata": {"account_id": "safe-id"},
            }

    monkeypatch.setattr(
        "stackos.auth_providers.repository.integration_class_for",
        lambda _provider_key: _Probe,
    )
    account = _firecrawl_account(api, project_id=project_id)
    ref = account["credential_ref"]

    edited = api.patch(
        f"/api/v1/auth/accounts/{ref}",
        json={
            "display_name": "Firecrawl Production",
            "fields": {"api_key": "fc-secret"},
        },
    )
    tested = api.post(f"/api/v1/auth/accounts/{ref}/test")
    blocked = api.post(f"/api/v1/auth/accounts/{ref}/revoke")
    detached = api.delete(f"/api/v1/projects/{project_id}/connections/accounts/{ref}")
    revoked = api.post(f"/api/v1/auth/accounts/{ref}/revoke")

    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["display_name"] == "Firecrawl Production"
    assert tested.status_code == 200, tested.text
    assert tested.json()["data"]["ok"] is True
    assert blocked.status_code == 409
    assert "Detach" in blocked.text
    assert detached.status_code == 200
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["data"]["status"] == "revoked"


def _google_account(api: TestClient, *, project_id: int) -> dict:
    return _create_account(
        api,
        provider_key="google-search-console",
        display_name="Search Console - Default",
        auth_method_key="oauth2_authorization_code",
        fields={
            "client_id": "route-client-id",
            "client_secret": "route-client-secret",
            "default_site_url": "https://example.test/",
        },
        attach_project_id=project_id,
    )


def test_oauth_returns_to_the_server_stored_project_connections_origin(
    api: TestClient,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    account = _google_account(api, project_id=project_id)
    started = api.post(
        "/api/v1/auth/accounts/google-search-console/start",
        json={
            "auth_method_key": "oauth2_authorization_code",
            "credential_ref": account["credential_ref"],
            "attach_project_id": project_id,
            "return_surface": "project-connections",
        },
    )
    assert started.status_code == 200, started.text
    start_data = started.json()["data"]
    assert start_data["return_surface"] == "project-connections"
    query = parse_qs(urlparse(start_data["authorization_url"]).query)
    assert query["redirect_uri"] == ["http://127.0.0.1:5180/api/v1/auth/oauth/callback"]
    state = query["state"][0]
    httpx_mock.add_response(
        method="POST",
        url="https://oauth2.googleapis.com/token",
        json={
            "access_token": "route-access-value",
            "refresh_token": "route-refresh-value",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/webmasters.readonly",
            "token_type": "Bearer",
        },
    )

    auth_header = api.headers.pop("authorization")
    try:
        callback = api.get(
            "/api/v1/auth/oauth/callback",
            params={"state": state, "code": "route-code-value"},
            headers={"host": "127.0.0.1:5180"},
            follow_redirects=False,
        )
    finally:
        api.headers["authorization"] = auth_header

    assert callback.status_code == 303, callback.text
    assert callback.headers["location"].startswith(
        f"http://127.0.0.1:5180/projects/{project_id}/connections?"
    )
    assert "oauth_status=connected" in callback.headers["location"]
    assert state not in callback.headers["location"]
    assert "route-code-value" not in callback.headers["location"]


def test_oauth_started_from_accounts_returns_to_accounts(
    api: TestClient,
    project_id: int,
) -> None:
    account = _google_account(api, project_id=project_id)
    started = api.post(
        "/api/v1/auth/accounts/google-search-console/start",
        json={
            "auth_method_key": "oauth2_authorization_code",
            "credential_ref": account["credential_ref"],
            "return_surface": "accounts",
        },
    )
    state = parse_qs(urlparse(started.json()["data"]["authorization_url"]).query)["state"][0]

    auth_header = api.headers.pop("authorization")
    try:
        callback = api.get(
            "/api/v1/auth/oauth/callback",
            params={"state": state, "error": "access_denied"},
            follow_redirects=False,
        )
    finally:
        api.headers["authorization"] = auth_header

    assert callback.status_code == 303
    assert callback.headers["location"].startswith("http://127.0.0.1:5180/accounts?")
    assert "oauth_status=denied" in callback.headers["location"]


def test_project_credential_routes_are_removed_without_compatibility_shims(
    api: TestClient,
    project_id: int,
) -> None:
    old_create = api.post(
        f"/api/v1/projects/{project_id}/auth/firecrawl/credentials",
        json={
            "profile_key": "default",
            "fields": {"api_key": "must-not-be-stored"},
        },
    )
    old_status = api.get(f"/api/v1/projects/{project_id}/auth/status")

    assert old_create.status_code in {404, 405}
    assert old_status.status_code == 404
