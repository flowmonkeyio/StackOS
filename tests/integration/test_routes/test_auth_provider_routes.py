"""REST tests for generic StackOS auth-provider flows."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from pytest_httpx import HTTPXMock


def _create_firecrawl_credential(api: TestClient, project_id: int) -> dict:
    response = api.post(
        f"/api/v1/projects/{project_id}/auth/firecrawl/credentials",
        json={
            "plaintext_payload": "fc-secret",
            "config_json": {"label": "Primary Firecrawl"},
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
    assert body["connections"][0]["credential_ref"].startswith("cred_")
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
    assert body["data"]["status"] == "requires-local-secret"
    assert body["data"]["setup_url"].endswith(
        f"/projects/{project_id}/connections?provider_key=firecrawl"
    )
    assert body["data"]["authorization_url"] is None
    assert body["data"]["credential_ref"] is None


def test_auth_test_uses_provider_key_and_returns_sanitized_result(
    api: TestClient,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    _create_firecrawl_credential(api, project_id)
    httpx_mock.add_response(
        method="POST",
        url="https://api.firecrawl.dev/v2/scrape",
        json={"data": {"markdown": "# ok"}},
    )

    response = api.post(
        f"/api/v1/projects/{project_id}/auth/test",
        json={"provider_key": "firecrawl"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["ok"] is True
    assert body["data"]["provider_key"] == "firecrawl"
    assert body["data"]["credential_ref"].startswith("cred_")
    rendered = json.dumps(body)
    assert "fc-secret" not in rendered
    assert "encrypted_payload" not in rendered


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


def test_auth_secret_setup_rejects_secret_like_config(
    api: TestClient,
    project_id: int,
) -> None:
    response = api.post(
        f"/api/v1/projects/{project_id}/auth/firecrawl/credentials",
        json={
            "plaintext_payload": "fc-secret",
            "config_json": {"api_key": "fc-secret"},
        },
    )

    assert response.status_code == 422, response.text
