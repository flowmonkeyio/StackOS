"""Repository tests for the StackOS auth-provider boundary."""

from __future__ import annotations

import asyncio
import json

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
from stackos.repositories.base import ConflictError, NotFoundError
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
    assert connection.setup_required is False

    credential = session.exec(
        select(Credential).where(Credential.integration_credential_id == integration.id)
    ).one()
    assert credential.credential_ref == connection.credential_ref
    assert credential.config_json == {"label": "Primary Firecrawl"}


def test_failed_credential_requires_operator_setup(
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

    assert status.connections[0].status == "failed"
    assert status.connections[0].setup_required is True


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
        repo.resolve_for_execution(
            project_id=project_id,
            provider_key="telegram-bot",
            credential_ref=global_credential.credential_ref,
            operation="communications.telegram-bot.message.send",
        )

    project_credential = repo.sync_credential_for_integration(project_telegram.id)
    resolved = repo.resolve_for_execution(
        project_id=project_id,
        provider_key="telegram-bot",
        credential_ref=project_credential.credential_ref,
        operation="communications.telegram-bot.message.send",
    )
    assert resolved.integration.profile_key == "support"

    slack_credential = repo.sync_credential_for_integration(project_slack.id)
    resolved_slack = repo.resolve_for_execution(
        project_id=project_id,
        provider_key="slack-bot",
        credential_ref=slack_credential.credential_ref,
        operation="communications.slack-bot.message.send",
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
    assert out.metadata == {"access_token": "[redacted]"}
