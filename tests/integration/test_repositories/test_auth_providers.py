"""Repository tests for the StackOS auth-provider boundary."""

from __future__ import annotations

import asyncio

import pytest
from sqlmodel import Session, select

from content_stack.auth_providers import AuthRepository
from content_stack.db.models import (
    Credential,
    CredentialRefreshEvent,
    CredentialUsageEvent,
)
from content_stack.repositories.base import NotFoundError
from content_stack.repositories.projects import IntegrationCredentialRepository


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

    repo = AuthRepository(session)
    telegram = repo.status(project_id=project_id, provider_key="telegram-bot")
    all_status = repo.status(project_id=project_id)

    assert [connection.profile_key for connection in telegram.connections] == ["support"]
    assert {
        (connection.provider_key, connection.profile_key) for connection in all_status.connections
    } == {("firecrawl", "global"), ("telegram-bot", "support")}

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
        "content_stack.auth_providers.repository.integration_class_for",
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
