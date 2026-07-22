"""Repository tests for the StackOS auth-provider boundary."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.db.models import (
    Credential,
    CredentialAccount,
    CredentialRefreshEvent,
    CredentialUsageEvent,
    IntegrationCredential,
)
from stackos.mcp.errors import IntegrationDownError
from stackos.repositories.base import ConflictError, NotFoundError, ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository


def test_status_wraps_existing_credentials_with_opaque_refs(
    session: Session,
    project_id: int,
) -> None:
    integration = (
        IntegrationCredentialRepository(session)
        .set(
            project_id=project_id,
            kind="firecrawl",
            secret_payload=b"fc-secret",
            config_json={"label": "Primary Firecrawl"},
        )
        .data
    )

    status = AuthRepository(session).status(project_id=project_id, provider_key="firecrawl")

    assert [provider.key for provider in status.providers] == ["firecrawl"]
    assert len(status.connections) == 1
    connection = status.connections[0]
    assert connection.credential_ref.startswith("cred_")
    assert connection.provider_key == "firecrawl"
    assert connection.status == "connected"
    assert connection.last_tested_at is None
    assert connection.setup_required is False

    credential = session.exec(
        select(Credential).where(Credential.integration_credential_id == integration.id)
    ).one()
    assert credential.credential_ref == connection.credential_ref
    assert credential.config_json == {"label": "Primary Firecrawl"}


def test_secret_fields_preserve_significant_whitespace(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    password = "  leading and trailing whitespace\t"

    repo.store_credential(
        project_id=project_id,
        provider_key="ftp",
        auth_method_key="ftp-password",
        profile_key="primary",
        fields={
            "password": password,
            "host": "ftp.example.com",
            "port": 21,
            "tls_mode": "none",
            "username": "deploy",
            "passive_mode": True,
            "timeout_s": 30,
            "encoding": "utf-8",
        },
    )

    row = session.exec(
        select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.kind == "ftp",
        )
    ).one()
    assert row.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id))
    assert payload["password"] == password


def test_status_normalizes_stale_failed_credential_from_backing_row(
    session: Session,
    project_id: int,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="firecrawl",
        secret_payload=b"fc-secret",
    )
    repo = AuthRepository(session)
    status = repo.status(project_id=project_id, provider_key="firecrawl")
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == status.connections[0].credential_ref)
    ).one()
    credential.status = "failed"
    session.add(credential)
    session.commit()

    status = repo.status(project_id=project_id, provider_key="firecrawl")

    assert status.connections[0].status == "connected"
    assert status.connections[0].setup_required is False


def test_credential_edit_preserves_omitted_secret_and_validates_host(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key="ftp",
        auth_method_key="ftp-password",
        profile_key="primary",
        label="Production FTP",
        fields={
            "password": "  exact password  ",
            "host": "old.example.test",
            "username": "deploy",
            "tls_mode": "none",
        },
    ).data

    edit = repo.get_credential_edit_state(
        project_id=project_id,
        credential_ref=stored.credential_ref,
    )
    assert edit.values["host"] == "old.example.test"
    assert "password" not in edit.values
    assert edit.secret_present == {"password": True}

    updated = repo.update_credential(
        project_id=project_id,
        credential_ref=stored.credential_ref,
        label="Production FTP",
        fields={"host": "192.0.2.10"},
    ).data
    assert updated.credential_ref == stored.credential_ref

    row = session.exec(
        select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.kind == "ftp",
            IntegrationCredential.profile_key == "primary",
        )
    ).one()
    assert row.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id))
    assert payload["password"] == "  exact password  "
    assert row.config_json is not None
    assert row.config_json["host"] == "192.0.2.10"

    with pytest.raises(ValidationError):
        repo.update_credential(
            project_id=project_id,
            credential_ref=stored.credential_ref,
            label="Production FTP",
            fields={"host": "ftp://192.0.2.10/public_html"},
        )
    with pytest.raises(ValidationError):
        repo.update_credential(
            project_id=project_id,
            credential_ref=stored.credential_ref,
            label="Production FTP",
            fields={"password": ""},
        )

    session.refresh(row)
    assert row.config_json is not None
    assert row.config_json["host"] == "192.0.2.10"
    assert (
        json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id))["password"]
        == "  exact password  "
    )


def test_thrown_auth_test_failure_is_sanitized_and_persisted(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingIntegration:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def test_credentials(self) -> dict[str, object]:
            raise IntegrationDownError(
                "provider secret=do-not-store",
                data={
                    "stage": "connect",
                    "reason_code": "connection_refused",
                    "retryable": True,
                    "password": "do-not-store",
                },
            )

    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key="firecrawl",
        auth_method_key="api_key",
        profile_key="primary",
        fields={"api_key": "do-not-store"},
    ).data
    monkeypatch.setattr(
        "stackos.auth_providers.repository.testing._integration_class_for",
        lambda kind: _FailingIntegration if kind == "firecrawl" else None,
    )

    tested = asyncio.run(
        repo.test(project_id=project_id, credential_ref=stored.credential_ref)
    ).data
    resolved = asyncio.run(
        repo.resolve_for_execution(
            project_id=project_id,
            provider_key="firecrawl",
            credential_ref=stored.credential_ref,
            operation="test.after-failed-auth-test",
        )
    )
    refreshed = repo.status(project_id=project_id, provider_key="firecrawl").connections[0]
    event = session.exec(
        select(CredentialUsageEvent).where(CredentialUsageEvent.operation == "auth.test")
    ).one()

    assert tested.ok is False
    assert tested.metadata["stage"] == "connect"
    assert tested.metadata["reason_code"] == "connection_refused"
    assert resolved.credential.status == "connected"
    assert refreshed.status == "connected"
    assert refreshed.setup_required is False
    assert refreshed.last_tested_at is not None
    rendered = json.dumps(
        {
            "result": tested.model_dump(mode="json"),
            "connection": refreshed.model_dump(mode="json"),
            "event": event.metadata_json,
        }
    )
    assert "do-not-store" not in rendered


def test_reddit_auth_test_acquires_core_token_before_connector(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key="reddit",
        auth_method_key="client_credentials",
        profile_key="research",
        fields={
            "client_id": "reddit-client-id",
            "client_secret": "reddit-client-secret",
            "user_agent": "stackos:test-suite:v1",
        },
    ).data
    httpx_mock.add_response(
        method="POST",
        url="https://www.reddit.com/api/v1/access_token",
        json={
            "access_token": "reddit-access-value",
            "token_type": "bearer",
            "expires_in": 3600,
        },
    )

    tested = asyncio.run(
        repo.test(project_id=project_id, credential_ref=stored.credential_ref)
    ).data

    request = httpx_mock.get_requests()[0]
    form = parse_qs(request.content.decode())
    assert tested.ok is True
    assert tested.status == "ok"
    assert request.headers["Authorization"].startswith("Basic ")
    assert request.headers["User-Agent"] == "stackos:test-suite:v1"
    assert form == {"grant_type": ["client_credentials"]}
    row = session.exec(
        select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.kind == "reddit",
            IntegrationCredential.profile_key == "research",
        )
    ).one()
    assert row.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id))
    assert payload["access_token"] == "reddit-access-value"
    assert payload["client_secret"] == "reddit-client-secret"


def test_acquired_client_credential_profile_can_be_edited_without_losing_token(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key="reddit",
        auth_method_key="client_credentials",
        profile_key="editable-research",
        fields={
            "client_id": "reddit-client-id",
            "client_secret": "reddit-client-secret",
            "user_agent": "stackos:editable-test:v1",
        },
    ).data
    httpx_mock.add_response(
        method="POST",
        url="https://www.reddit.com/api/v1/access_token",
        json={"access_token": "reddit-access-value", "expires_in": 3600},
    )
    asyncio.run(repo.test(project_id=project_id, credential_ref=stored.credential_ref))

    edit = repo.get_credential_edit_state(
        project_id=project_id,
        credential_ref=stored.credential_ref,
    )
    assert edit.secret_present == {
        "client_id": True,
        "client_secret": True,
        "user_agent": True,
    }
    updated = repo.update_credential(
        project_id=project_id,
        credential_ref=stored.credential_ref,
        fields={},
        label="Editable Reddit",
    ).data

    assert updated.status == "connected"
    assert updated.label == "Editable Reddit"
    assert len(httpx_mock.get_requests()) == 1
    row = session.exec(
        select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.kind == "reddit",
            IntegrationCredential.profile_key == "editable-research",
        )
    ).one()
    assert row.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id))
    assert payload["access_token"] == "reddit-access-value"
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == stored.credential_ref)
    ).one()
    assert (credential.config_json or {}).get("scope_status") == "known"


def test_pending_interactive_profile_can_be_edited_without_reentering_secrets(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key="google-search-console",
        auth_method_key="oauth2_authorization_code",
        profile_key="editable-pending",
        fields={
            "client_id": "google-client-id",
            "client_secret": "google-client-secret",
            "default_site_url": "https://example.test/",
        },
    ).data

    edit = repo.get_credential_edit_state(
        project_id=project_id,
        credential_ref=stored.credential_ref,
    )
    assert edit.secret_present == {"client_id": True, "client_secret": True}
    updated = repo.update_credential(
        project_id=project_id,
        credential_ref=stored.credential_ref,
        fields={"default_site_url": "https://updated.example.test/"},
        label="Pending Google",
    ).data

    assert updated.status == "pending"
    row = session.exec(
        select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.kind == "google-search-console",
            IntegrationCredential.profile_key == "editable-pending",
        )
    ).one()
    assert row.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id))
    assert payload["_oauth_application_pending"] == {
        "client_id": "google-client-id",
        "client_secret": "google-client-secret",
    }
    assert (row.config_json or {})["default_site_url"] == "https://updated.example.test/"


def test_telegram_bot_store_generates_webhook_secret(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)

    stored = repo.store_credential(
        project_id=project_id,
        provider_key="telegram-bot",
        auth_method_key="bot-token",
        profile_key="support",
        fields={"bot_token": "123456:ABC"},
    ).data

    row = session.exec(
        select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.kind == "telegram-bot",
        )
    ).one()
    assert row.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id).decode())
    assert stored.credential_ref.startswith("cred_")
    assert payload["bot_token"] == "123456:ABC"
    assert isinstance(payload["webhook_secret_token"], str)
    assert len(payload["webhook_secret_token"]) >= 32
    assert row.config_json is not None
    assert row.config_json["provider_account_id"] == "123456"
    assert "webhook_secret_token" not in row.config_json


def test_telegram_bot_token_can_only_claim_one_active_connection(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)

    first = repo.store_credential(
        project_id=project_id,
        provider_key="telegram-bot",
        auth_method_key="bot-token",
        profile_key="support",
        fields={"bot_token": "123456:ABC"},
    ).data
    replacement = repo.store_credential(
        project_id=project_id,
        provider_key="telegram-bot",
        auth_method_key="bot-token",
        profile_key="support",
        fields={"bot_token": "123456:ROTATED"},
    ).data

    assert replacement.credential_ref == first.credential_ref
    with pytest.raises(ConflictError) as exc:
        repo.store_credential(
            project_id=project_id,
            provider_key="telegram-bot",
            auth_method_key="bot-token",
            profile_key="analytics",
            fields={"bot_token": "123456:ROTATED"},
        )
    assert exc.value.data["provider_account_id"] == "123456"
    assert exc.value.data["existing_profile_key"] == "support"


def test_slack_bot_auth_test_syncs_safe_workspace_account(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)

    stored = repo.store_credential(
        project_id=project_id,
        provider_key="slack-bot",
        auth_method_key="bot-token",
        profile_key="support",
        fields={
            "bot_token": "xoxb-1234567890-safe-test-token",
            "signing_secret": "slack-signing-secret",
        },
    ).data
    row = session.exec(
        select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.kind == "slack-bot",
        )
    ).one()
    assert row.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id).decode())
    assert payload["bot_token"] == "xoxb-1234567890-safe-test-token"
    assert payload["signing_secret"] == "slack-signing-secret"
    assert "bot_token" not in (row.config_json or {})
    assert "signing_secret" not in (row.config_json or {})

    httpx_mock.add_response(
        method="POST",
        url="https://slack.com/api/auth.test",
        json={
            "ok": True,
            "team_id": "T123",
            "team": "Acme",
            "user_id": "U_BOT",
            "user": "stackos",
            "bot_id": "B123",
            "url": "https://acme.slack.com/",
        },
    )
    tested = asyncio.run(repo.test(project_id=project_id, credential_ref=stored.credential_ref))
    account = session.exec(select(CredentialAccount)).one()
    rendered = json.dumps(tested.data.model_dump(mode="json"))

    assert tested.data.ok is True
    assert tested.data.metadata["team_id"] == "T123"
    assert account.provider_account_id == "T123"
    assert account.display_name == "Acme"
    assert account.metadata_json["bot_id"] == "B123"
    assert "xoxb-1234567890-safe-test-token" not in rendered
    assert "slack-signing-secret" not in rendered


def test_failed_credential_profile_can_be_revoked(
    session: Session,
    project_id: int,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="firecrawl",
        secret_payload=b"fc-secret",
    )
    repo = AuthRepository(session)
    status = repo.status(project_id=project_id, provider_key="firecrawl")
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == status.connections[0].credential_ref)
    ).one()
    credential.status = "failed"
    session.add(credential)
    session.commit()

    revoked = repo.revoke(
        project_id=project_id,
        credential_ref=credential.credential_ref,
    ).data

    assert revoked.status == "revoked"
    assert revoked.credential_ref == credential.credential_ref


def test_openrouter_auth_test_passes_safe_attribution_config(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key="openrouter",
        auth_method_key="api_key",
        profile_key="default",
        fields={
            "api_key": "or-secret",
            "http_referer": "https://stackos.local",
            "app_title": "StackOS",
        },
    ).data
    row = session.exec(
        select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.kind == "openrouter",
        )
    ).one()
    assert row.id is not None
    assert IntegrationCredentialRepository(session).get_decrypted(row.id) == b"or-secret"
    assert row.config_json["http_referer"] == "https://stackos.local"
    assert row.config_json["app_title"] == "StackOS"
    httpx_mock.add_response(
        method="GET",
        url="https://openrouter.ai/api/v1/models",
        json={"data": [{"id": "openai/gpt-4.1"}]},
    )

    tested = asyncio.run(repo.test(project_id=project_id, credential_ref=stored.credential_ref))
    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(tested.data.model_dump(mode="json"))

    assert tested.data.ok is True
    assert tested.data.metadata["models_count"] == 1
    assert request.headers["Authorization"] == "Bearer or-secret"
    assert request.headers["HTTP-Referer"] == "https://stackos.local"
    assert request.headers["X-OpenRouter-Title"] == "StackOS"
    assert "or-secret" not in rendered


def test_trackbooth_auth_test_passes_safe_custom_api_url_config(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key="trackbooth",
        auth_method_key="api-key",
        profile_key="local",
        fields={
            "api_key": "tb-secret",
            "api_base_url": "http://localhost:3030",
        },
    ).data
    row = session.exec(
        select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.kind == "trackbooth",
        )
    ).one()
    assert row.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id).decode())
    assert payload == {"api_key": "tb-secret"}
    assert row.config_json["api_base_url"] == "http://localhost:3030"

    httpx_mock.add_response(
        method="GET",
        url="http://localhost:3030/api/agent-api/catalog",
        json={"data": [{"operation_id": "LinksController.findAll"}]},
    )

    tested = asyncio.run(repo.test(project_id=project_id, credential_ref=stored.credential_ref))
    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(tested.data.model_dump(mode="json"))

    assert tested.data.ok is True
    assert tested.data.metadata["endpoint_count"] == 1
    assert tested.data.metadata["api_base_url"] == "http://localhost:3030"
    assert request.headers["X-API-Key"] == "tb-secret"
    assert "tb-secret" not in rendered


def test_shopify_auth_test_passes_static_token_and_safe_store_config(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key="shopify",
        auth_method_key="admin-api-token",
        profile_key="primary",
        fields={
            "admin_api_access_token": "shpat-secret",
            "store_domain": "demo.myshopify.com",
            "api_version": "2026-07",
        },
    ).data
    row = session.exec(
        select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.kind == "shopify",
        )
    ).one()
    assert row.id is not None
    assert IntegrationCredentialRepository(session).get_decrypted(row.id) == b"shpat-secret"
    assert row.config_json["store_domain"] == "demo.myshopify.com"
    assert row.config_json["api_version"] == "2026-07"

    httpx_mock.add_response(
        method="POST",
        url="https://demo.myshopify.com/admin/api/2026-07/graphql.json",
        json={
            "data": {
                "shop": {
                    "id": "gid://shopify/Shop/1",
                    "name": "Demo Shop",
                    "myshopifyDomain": "demo.myshopify.com",
                }
            }
        },
    )

    tested = asyncio.run(repo.test(project_id=project_id, credential_ref=stored.credential_ref))
    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(tested.data.model_dump(mode="json"))

    assert tested.data.ok is True
    assert tested.data.metadata["shop_name"] == "Demo Shop"
    assert tested.data.metadata["store_domain"] == "demo.myshopify.com"
    assert request.headers["X-Shopify-Access-Token"] == "shpat-secret"
    assert "shpat-secret" not in rendered


def test_cloudflare_auth_test_verifies_token_without_zone_permission(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key="cloudflare",
        auth_method_key="api_token",
        profile_key="primary",
        fields={"api_token": "cloudflare-secret"},
    ).data
    row = session.exec(
        select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.kind == "cloudflare",
        )
    ).one()
    assert row.id is not None
    assert IntegrationCredentialRepository(session).get_decrypted(row.id) == b"cloudflare-secret"
    assert row.config_json == {
        "auth_method_key": "api_token",
        "profile_key": "primary",
    }

    httpx_mock.add_response(
        method="GET",
        url="https://api.cloudflare.com/client/v4/user/tokens/verify",
        json={
            "success": True,
            "errors": [],
            "messages": [],
            "result": {"id": "token-id", "status": "active"},
        },
    )

    tested = asyncio.run(repo.test(project_id=project_id, credential_ref=stored.credential_ref))
    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(tested.data.model_dump(mode="json"))

    assert request.headers["Authorization"] == "Bearer cloudflare-secret"
    assert tested.data.ok is True
    assert tested.data.metadata["zone_read_verified"] is False
    assert tested.data.metadata["dns_read_verified"] is False
    assert tested.data.metadata["dns_write_verified"] is False
    assert "token-id" not in rendered
    assert "cloudflare-secret" not in rendered


def test_telegram_status_excludes_global_credentials(
    session: Session,
    project_id: int,
) -> None:
    global_telegram = (
        IntegrationCredentialRepository(session)
        .set(
            project_id=None,
            kind="telegram-bot",
            secret_payload=b'{"bot_token":"global-secret"}',
            profile_key="global",
        )
        .data
    )
    IntegrationCredentialRepository(session).set(
        project_id=None,
        kind="firecrawl",
        secret_payload=b"fc-global",
        profile_key="global",
    )
    project_telegram = (
        IntegrationCredentialRepository(session)
        .set(
            project_id=project_id,
            kind="telegram-bot",
            secret_payload=b'{"bot_token":"project-secret"}',
            profile_key="support",
        )
        .data
    )
    IntegrationCredentialRepository(session).set(
        project_id=None,
        kind="slack-bot",
        secret_payload=b'{"bot_token":"global-slack-secret","signing_secret":"global"}',
        profile_key="global",
    )
    project_slack = (
        IntegrationCredentialRepository(session)
        .set(
            project_id=project_id,
            kind="slack-bot",
            secret_payload=b'{"bot_token":"project-slack-secret","signing_secret":"project"}',
            profile_key="support-slack",
        )
        .data
    )

    repo = AuthRepository(session)
    telegram = repo.status(project_id=project_id, provider_key="telegram-bot")
    slack = repo.status(project_id=project_id, provider_key="slack-bot")
    all_status = repo.status(project_id=project_id)

    assert [connection.profile_key for connection in telegram.connections] == ["support"]
    assert [connection.profile_key for connection in slack.connections] == ["support-slack"]
    assert {
        (connection.provider_key, connection.profile_key) for connection in all_status.connections
    } == {
        ("firecrawl", "global"),
        ("slack-bot", "support-slack"),
        ("telegram-bot", "support"),
    }

    global_credential = repo.sync_credential_for_integration(global_telegram.id)
    with pytest.raises(NotFoundError):
        asyncio.run(
            repo.resolve_for_execution(
                project_id=project_id,
                provider_key="telegram-bot",
                credential_ref=global_credential.credential_ref,
                operation="communications.telegram-bot.message.send",
            )
        )

    project_credential = repo.sync_credential_for_integration(project_telegram.id)
    resolved = asyncio.run(
        repo.resolve_for_execution(
            project_id=project_id,
            provider_key="telegram-bot",
            credential_ref=project_credential.credential_ref,
            operation="communications.telegram-bot.message.send",
        )
    )
    assert resolved.integration.profile_key == "support"

    slack_credential = repo.sync_credential_for_integration(project_slack.id)
    resolved_slack = asyncio.run(
        repo.resolve_for_execution(
            project_id=project_id,
            provider_key="slack-bot",
            credential_ref=slack_credential.credential_ref,
            operation="communications.slack-bot.message.send",
        )
    )
    assert resolved_slack.integration.profile_key == "support-slack"


def test_usage_and_refresh_events_redact_secret_metadata(
    session: Session,
    project_id: int,
) -> None:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="firecrawl",
        secret_payload=b"fc-secret",
    )
    repo = AuthRepository(session)
    status = repo.status(project_id=project_id, provider_key="firecrawl")
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == status.connections[0].credential_ref)
    ).one()

    repo.record_usage_event(
        credential=credential,
        provider_key="firecrawl",
        operation="auth.test",
        status="ok",
        metadata_json={"access_token": "tok", "nested": {"api_key": "secret"}},
    )
    repo.record_refresh_event(
        credential=credential,
        provider_key="firecrawl",
        status="refreshed",
        metadata_json={"refresh_token": "rt", "safe": "value"},
    )
    session.commit()

    usage = session.exec(select(CredentialUsageEvent)).one()
    refresh = session.exec(select(CredentialRefreshEvent)).one()

    assert usage.metadata_json == {
        "access_token": "[redacted]",
        "nested": {"api_key": "[redacted]"},
    }
    assert refresh.metadata_json == {"refresh_token": "[redacted]", "safe": "value"}


def test_auth_test_redacts_vendor_controlled_text_fields(
    session: Session,
    project_id: int,
    monkeypatch,
) -> None:
    class _TextLeakIntegration:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def test_credentials(self) -> dict:
            return {
                "ok": False,
                "vendor": "firecrawl",
                "status": "failed api_key=fc-secret",
                "summary": "Authorization: Bearer fc-secret",
                "next_action": "rotate refresh_token=rt-secret",
                "metadata": {"access_token": "tok-secret"},
            }

    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="firecrawl",
        secret_payload=b"fc-secret",
    )
    monkeypatch.setattr(
        "stackos.auth_providers.repository.integration_class_for",
        lambda kind: _TextLeakIntegration if kind == "firecrawl" else None,
    )
    status = AuthRepository(session).status(project_id=project_id, provider_key="firecrawl")

    out = asyncio.run(
        AuthRepository(session).test(
            project_id=project_id,
            credential_ref=status.connections[0].credential_ref,
        )
    ).data

    assert out.status == "failed api_key=[redacted]"
    assert out.summary == "Authorization: Bearer [redacted]"
    assert out.next_action == "rotate refresh_token=[redacted]"
    assert out.metadata["access_token"] == "[redacted]"
