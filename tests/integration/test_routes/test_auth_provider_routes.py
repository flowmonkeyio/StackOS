"""REST tests for generic StackOS auth-provider flows."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from pytest_httpx import HTTPXMock


class _AuthSMTP:
    fail_with_secret = False

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def login(self, username: str, password: str) -> None:
        assert username == "mailer@example.test"
        assert password == "smtp-secret"
        if self.fail_with_secret:
            raise RuntimeError(f"provider rejected password {password}")

    def quit(self) -> None:
        return None

    def close(self) -> None:
        return None


class _AuthIMAP:
    fail_with_secret = False

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def login(self, username: str, password: str) -> None:
        assert username == "support@example.test"
        assert password == "imap-secret"
        if self.fail_with_secret:
            raise RuntimeError(f"provider rejected password {password}")

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        assert mailbox == "INBOX"
        assert readonly is True
        return ("OK", [b"1"])

    def close(self) -> None:
        return None

    def logout(self) -> None:
        return None


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


def test_disabled_plugin_rejects_provider_setup(
    api: TestClient,
    project_id: int,
) -> None:
    enabled = api.post(f"/api/v1/projects/{project_id}/plugins/utils/enable", json={})
    assert enabled.status_code == 200, enabled.text
    disabled = api.post(f"/api/v1/projects/{project_id}/plugins/utils/disable", json={})
    assert disabled.status_code == 200, disabled.text

    started = api.post(f"/api/v1/projects/{project_id}/auth/firecrawl/start", json={})
    assert started.status_code == 409, started.text
    assert started.json()["data"]["next_action"] == (
        "Enable the plugin before connecting this provider."
    )

    stored = api.post(
        f"/api/v1/projects/{project_id}/auth/firecrawl/credentials",
        json={
            "auth_method_key": "api_key",
            "profile_key": "primary",
            "fields": {"api_key": "fc-secret"},
        },
    )
    assert stored.status_code == 409, stored.text


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
            "result": {
                "id": 123456,
                "is_bot": True,
                "username": "support_bot",
                "first_name": "Support Bot",
            },
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
    assert body["metadata"]["first_name"] == "Support Bot"
    status_response = api.get(
        f"/api/v1/projects/{project_id}/auth/status?provider_key=telegram-bot"
    )
    assert status_response.status_code == 200, status_response.text
    [connection] = status_response.json()["connections"]
    assert connection["account"]["provider_account_id"] == "123456"
    assert connection["account"]["display_name"] == "@support_bot"
    assert connection["account"]["metadata_json"]["username"] == "support_bot"
    rendered = json.dumps(response.json())
    assert "123456:ABC" not in rendered
    assert "telegram-secret" not in rendered


def test_auth_test_supports_smtp_without_secret_leak(
    api: TestClient,
    project_id: int,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    import stackos.integrations.smtp as smtp_integration

    _AuthSMTP.fail_with_secret = False
    monkeypatch.setattr(smtp_integration.smtplib, "SMTP", _AuthSMTP)
    stored = api.post(
        f"/api/v1/projects/{project_id}/auth/smtp/credentials",
        json={
            "auth_method_key": "smtp-password",
            "profile_key": "primary",
            "fields": {
                "password": "smtp-secret",
                "host": "smtp.example.test",
                "port": 587,
                "tls_mode": "none",
                "username": "mailer@example.test",
                "from_email": "mailer@example.test",
            },
        },
    )
    assert stored.status_code == 201, stored.text

    response = api.post(
        f"/api/v1/projects/{project_id}/auth/test",
        json={"credential_ref": stored.json()["data"]["credential_ref"]},
    )

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["ok"] is True
    assert body["provider_key"] == "smtp"
    assert body["metadata"]["host"] == "smtp.example.test"
    assert "smtp-secret" not in json.dumps(response.json())


def test_auth_test_smtp_failure_redacts_provider_exception(
    api: TestClient,
    project_id: int,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    import stackos.integrations.smtp as smtp_integration

    _AuthSMTP.fail_with_secret = True
    monkeypatch.setattr(smtp_integration.smtplib, "SMTP", _AuthSMTP)
    stored = api.post(
        f"/api/v1/projects/{project_id}/auth/smtp/credentials",
        json={
            "auth_method_key": "smtp-password",
            "profile_key": "primary",
            "fields": {
                "password": "smtp-secret",
                "host": "smtp.example.test",
                "port": 587,
                "tls_mode": "none",
                "username": "mailer@example.test",
                "from_email": "mailer@example.test",
            },
        },
    )
    assert stored.status_code == 201, stored.text

    response = api.post(
        f"/api/v1/projects/{project_id}/auth/test",
        json={"credential_ref": stored.json()["data"]["credential_ref"]},
    )

    rendered = json.dumps(response.json())
    assert response.status_code == 502, response.text
    assert "SMTP credential test failed" in rendered
    assert "RuntimeError" in rendered
    assert "smtp-secret" not in rendered
    assert "provider rejected password" not in rendered
    _AuthSMTP.fail_with_secret = False


def test_auth_test_supports_imap_without_secret_leak(
    api: TestClient,
    project_id: int,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    import stackos.integrations.imap as imap_integration

    _AuthIMAP.fail_with_secret = False
    monkeypatch.setattr(imap_integration.imaplib, "IMAP4", _AuthIMAP)
    stored = api.post(
        f"/api/v1/projects/{project_id}/auth/imap/credentials",
        json={
            "auth_method_key": "imap-password",
            "profile_key": "primary",
            "fields": {
                "password": "imap-secret",
                "host": "imap.example.test",
                "port": 143,
                "tls_mode": "none",
                "username": "support@example.test",
                "default_mailbox": "INBOX",
            },
        },
    )
    assert stored.status_code == 201, stored.text

    response = api.post(
        f"/api/v1/projects/{project_id}/auth/test",
        json={"credential_ref": stored.json()["data"]["credential_ref"]},
    )

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["ok"] is True
    assert body["provider_key"] == "imap"
    assert body["metadata"]["default_mailbox"] == "INBOX"
    assert "imap-secret" not in json.dumps(response.json())


def test_auth_test_imap_failure_redacts_provider_exception(
    api: TestClient,
    project_id: int,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    import stackos.integrations.imap as imap_integration

    _AuthIMAP.fail_with_secret = True
    monkeypatch.setattr(imap_integration.imaplib, "IMAP4", _AuthIMAP)
    stored = api.post(
        f"/api/v1/projects/{project_id}/auth/imap/credentials",
        json={
            "auth_method_key": "imap-password",
            "profile_key": "primary",
            "fields": {
                "password": "imap-secret",
                "host": "imap.example.test",
                "port": 143,
                "tls_mode": "none",
                "username": "support@example.test",
                "default_mailbox": "INBOX",
            },
        },
    )
    assert stored.status_code == 201, stored.text

    response = api.post(
        f"/api/v1/projects/{project_id}/auth/test",
        json={"credential_ref": stored.json()["data"]["credential_ref"]},
    )

    rendered = json.dumps(response.json())
    assert response.status_code == 502, response.text
    assert "IMAP credential test failed" in rendered
    assert "RuntimeError" in rendered
    assert "imap-secret" not in rendered
    assert "provider rejected password" not in rendered
    _AuthIMAP.fail_with_secret = False


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
