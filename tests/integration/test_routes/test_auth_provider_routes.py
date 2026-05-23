"""REST tests for generic StackOS auth-provider flows."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from pytest_httpx import HTTPXMock


def _create_firecrawl_credential(api: TestClient, project_id: int) -> dict:
    response = api.post(
        f"/api/v1/projects/{project_id}/auth/firecrawl/credentials",
        json={
            "auth_method_key": "api_key",
            "profile_key": "primary",
            "label": "Primary Firecrawl",
            "fields": {"api_key": "fc-secret"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_auth_status_returns_opaque_refs_and_no_secrets(
    api: TestClient,
    project_id: int,
) -> None:
    _create_firecrawl_credential(api, project_id)

    response = api.get(f"/api/v1/projects/{project_id}/auth/status?provider_key=firecrawl")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["providers"][0]["key"] == "firecrawl"
    assert body["providers"][0]["auth_methods"][0]["key"] == "api_key"
    assert body["connections"][0]["credential_ref"].startswith("cred_")
    assert body["connections"][0]["profile_key"] == "primary"
    assert body["connections"][0]["auth_method_key"] == "api_key"
    assert body["connections"][0]["label"] == "Primary Firecrawl"
    assert body["connections"][0]["status"] == "connected"
    rendered = json.dumps(body)
    assert "fc-secret" not in rendered
    assert "encrypted_payload" not in rendered
    assert "plaintext_payload" not in rendered


def test_auth_start_for_api_key_returns_local_setup_url_only(
    api: TestClient,
    project_id: int,
) -> None:
    response = api.post(f"/api/v1/projects/{project_id}/auth/firecrawl/start", json={})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["status"] == "requires-local-credential"
    assert body["data"]["auth_method_key"] == "api_key"
    assert body["data"]["setup_url"].endswith(
        f"/projects/{project_id}/connections?provider_key=firecrawl"
    )
    assert body["data"]["authorization_url"] is None
    assert body["data"]["credential_ref"] is None


def test_auth_test_uses_credential_ref_and_returns_sanitized_result(
    api: TestClient,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential = _create_firecrawl_credential(api, project_id)
    httpx_mock.add_response(
        method="POST",
        url="https://api.firecrawl.dev/v2/scrape",
        json={"data": {"markdown": "# ok"}},
    )

    response = api.post(
        f"/api/v1/projects/{project_id}/auth/test",
        json={"credential_ref": credential["credential_ref"]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["ok"] is True
    assert body["data"]["provider_key"] == "firecrawl"
    assert body["data"]["credential_ref"].startswith("cred_")
    rendered = json.dumps(body)
    assert "fc-secret" not in rendered
    assert "encrypted_payload" not in rendered


def test_auth_test_supports_telegram_bot_token_without_secret_leak(
    api: TestClient,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    stored = api.post(
        f"/api/v1/projects/{project_id}/auth/telegram-bot/credentials",
        json={
            "auth_method_key": "bot-token",
            "profile_key": "support",
            "label": "Support Bot",
            "fields": {
                "bot_token": "123456:ABC",
                "webhook_secret_token": "telegram-secret",
                "api_base_url": "http://127.0.0.1:8081",
            },
        },
    )
    assert stored.status_code == 201, stored.text
    credential_ref = stored.json()["data"]["credential_ref"]

    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8081/bot123456:ABC/getMe",
        json={
            "ok": True,
            "result": {"id": 123456, "is_bot": True, "username": "support_bot"},
        },
    )

    response = api.post(
        f"/api/v1/projects/{project_id}/auth/test",
        json={"credential_ref": credential_ref},
    )

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["ok"] is True
    assert body["provider_key"] == "telegram-bot"
    assert body["metadata"]["bot_id"] == 123456
    assert body["metadata"]["username"] == "support_bot"
    rendered = json.dumps(response.json())
    assert "123456:ABC" not in rendered
    assert "telegram-secret" not in rendered


def test_auth_revoke_removes_backing_secret_and_preserves_redacted_history(
    api: TestClient,
    project_id: int,
) -> None:
    _create_firecrawl_credential(api, project_id)
    status = api.get(f"/api/v1/projects/{project_id}/auth/status?provider_key=firecrawl")
    credential_ref = status.json()["connections"][0]["credential_ref"]

    response = api.post(
        f"/api/v1/projects/{project_id}/auth/revoke",
        json={"credential_ref": credential_ref},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["status"] == "revoked"
    assert body["data"]["credential_ref"] == credential_ref

    after = api.get(f"/api/v1/projects/{project_id}/auth/status?provider_key=firecrawl")
    connection = after.json()["connections"][0]
    assert connection["credential_ref"] == credential_ref
    assert connection["status"] == "revoked"
    assert connection["setup_required"] is True


def test_auth_credential_setup_rejects_legacy_top_level_secret_fields(
    api: TestClient,
    project_id: int,
) -> None:
    response = api.post(
        f"/api/v1/projects/{project_id}/auth/firecrawl/credentials",
        json={
            "auth_method_key": "api_key",
            "fields": {"api_key": "fc-secret"},
            "plaintext_payload": "legacy-secret",
        },
    )

    assert response.status_code == 422, response.text


def test_auth_credential_setup_requires_method_fields(
    api: TestClient,
    project_id: int,
) -> None:
    missing = api.post(
        f"/api/v1/projects/{project_id}/auth/wordpress/credentials",
        json={
            "auth_method_key": "application_password",
            "label": "Editorial",
            "fields": {"username": "editor", "application_password": "app pass"},
        },
    )

    assert missing.status_code == 422, missing.text
    assert "wp_url" in missing.json()["detail"]

    stored = api.post(
        f"/api/v1/projects/{project_id}/auth/wordpress/credentials",
        json={
            "auth_method_key": "application_password",
            "label": "Editorial",
            "fields": {
                "username": "editor",
                "application_password": "app pass",
                "wp_url": "https://wp.example",
            },
        },
    )

    assert stored.status_code == 201, stored.text
    body = stored.json()
    assert body["data"]["provider_key"] == "wordpress"
    assert "app pass" not in json.dumps(body)


def test_auth_test_rejects_provider_key_selector(
    api: TestClient,
    project_id: int,
) -> None:
    for profile_key in ("primary", "secondary"):
        response = api.post(
            f"/api/v1/projects/{project_id}/auth/firecrawl/credentials",
            json={
                "auth_method_key": "api_key",
                "profile_key": profile_key,
                "fields": {"api_key": f"fc-{profile_key}"},
            },
        )
        assert response.status_code == 201, response.text

    response = api.post(
        f"/api/v1/projects/{project_id}/auth/test",
        json={"provider_key": "firecrawl"},
    )

    assert response.status_code == 422, response.text
    assert "credential_ref" in response.text
