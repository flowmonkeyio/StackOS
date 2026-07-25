"""Global Account identity and project Connection attachment contracts."""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.db.models import Credential, IntegrationCredential, ProjectCredential
from stackos.repositories.base import ConflictError, NotFoundError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.resources import ResourceRepository


def _project(session: Session, *, slug: str) -> int:
    created = ProjectRepository(session).create(
        slug=slug,
        name=slug.replace("-", " ").title(),
        domain=f"{slug}.example.test",
        locale="en-US",
    )
    assert created.data.id is not None
    return created.data.id


def _openrouter_account(repo: AuthRepository, *, name: str) -> str:
    stored = repo.store_credential(
        provider_key="openrouter",
        display_name=name,
        fields={"api_key": f"{name}-secret"},
    ).data
    assert stored.project_ids == []
    return stored.credential_ref


def test_one_account_can_be_attached_to_multiple_projects(
    session: Session,
    project_id: int,
) -> None:
    second_project_id = _project(session, slug="second-project")
    repo = AuthRepository(session)
    account_ref = _openrouter_account(repo, name="OpenRouter - Default")

    repo.attach_account(project_id=project_id, credential_ref=account_ref)
    repo.attach_account(project_id=second_project_id, credential_ref=account_ref)

    account = repo.get_account(credential_ref=account_ref)
    assert account.display_name == "OpenRouter - Default"
    assert account.project_ids == [project_id, second_project_id]
    assert (
        len(
            session.exec(
                select(ProjectCredential).where(
                    ProjectCredential.credential_id == account.credential_id
                )
            ).all()
        )
        == 2
    )


def test_concurrent_duplicate_attach_is_idempotent(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = AuthRepository(session)
    account_ref = _openrouter_account(repo, name="Concurrent Attach")
    attached = repo.attach_account(project_id=project_id, credential_ref=account_ref).data
    real_exec = session.exec
    hid_first_attachment_read = False

    class _MissingAttachment:
        @staticmethod
        def first() -> None:
            return None

    def stale_once(statement, *args, **kwargs):
        nonlocal hid_first_attachment_read
        sql = str(statement)
        if not hid_first_attachment_read and "FROM project_credentials" in sql:
            hid_first_attachment_read = True
            return _MissingAttachment()
        return real_exec(statement, *args, **kwargs)

    monkeypatch.setattr(session, "exec", stale_once)

    repeated = repo.attach_account(project_id=project_id, credential_ref=account_ref).data

    assert hid_first_attachment_read is True
    assert repeated.credential_ref == attached.credential_ref
    assert repeated.project_ids == [project_id]


@pytest.mark.asyncio
async def test_execution_requires_an_explicit_project_attachment(
    session: Session,
    project_id: int,
) -> None:
    unattached_project_id = _project(session, slug="unattached-project")
    repo = AuthRepository(session)
    account_ref = _openrouter_account(repo, name="Execution Account")
    repo.attach_account(project_id=project_id, credential_ref=account_ref)

    resolved = await repo.resolve_for_execution(
        project_id=project_id,
        provider_key="openrouter",
        credential_ref=account_ref,
        operation="test.resolve",
    )
    assert resolved.secret_payload

    with pytest.raises(NotFoundError, match="not attached"):
        await repo.resolve_for_execution(
            project_id=unattached_project_id,
            provider_key="openrouter",
            credential_ref=account_ref,
            operation="test.resolve",
        )


def test_account_names_are_case_insensitively_unique_per_provider(session: Session) -> None:
    repo = AuthRepository(session)
    _openrouter_account(repo, name="Shared Production")

    with pytest.raises(ConflictError, match="already exists"):
        _openrouter_account(repo, name="shared production")


def test_multiple_accounts_for_one_provider_are_selected_per_project(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    primary_ref = _openrouter_account(repo, name="OpenRouter - Primary")
    reporting_ref = _openrouter_account(repo, name="OpenRouter - Reporting")

    repo.attach_account(project_id=project_id, credential_ref=primary_ref)
    repo.attach_account(project_id=project_id, credential_ref=reporting_ref)

    assert {
        account.credential_ref
        for account in repo.status(project_id=None, provider_key="openrouter").accounts
    } == {primary_ref, reporting_ref}
    assert {
        account.credential_ref
        for account in repo.status(project_id=project_id, provider_key="openrouter").accounts
    } == {primary_ref, reporting_ref}


def test_slack_and_telegram_accounts_are_global_but_connections_are_project_bound(
    session: Session,
    project_id: int,
) -> None:
    second_project_id = _project(session, slug="communications-project")
    repo = AuthRepository(session)

    telegram = repo.store_credential(
        provider_key="telegram-bot",
        display_name="Telegram - Default",
        auth_method_key="bot-token",
        fields={"bot_token": "123456:test-token"},
    ).data
    slack = repo.store_credential(
        provider_key="slack-bot",
        display_name="Slack - Default",
        auth_method_key="bot-token",
        fields={
            "bot_token": "xoxb-test-token",
            "signing_secret": "signing-secret",
        },
    ).data

    for account_ref in (telegram.credential_ref, slack.credential_ref):
        repo.attach_account(project_id=project_id, credential_ref=account_ref)
        repo.attach_account(project_id=second_project_id, credential_ref=account_ref)
        assert repo.get_account(credential_ref=account_ref).project_ids == [
            project_id,
            second_project_id,
        ]


def test_detaching_one_project_does_not_revoke_the_account(
    session: Session,
    project_id: int,
) -> None:
    second_project_id = _project(session, slug="retained-project")
    repo = AuthRepository(session)
    account_ref = _openrouter_account(repo, name="Reusable")
    repo.attach_account(project_id=project_id, credential_ref=account_ref)
    repo.attach_account(project_id=second_project_id, credential_ref=account_ref)

    repo.detach_account(project_id=project_id, credential_ref=account_ref)

    account = repo.get_account(credential_ref=account_ref)
    assert account.status == "connected"
    assert account.project_ids == [second_project_id]


def test_revoked_accounts_keep_an_audit_tombstone_and_the_display_name_can_be_reused(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    account_ref = _openrouter_account(repo, name="Retired")
    repo.attach_account(project_id=project_id, credential_ref=account_ref)
    repo.detach_account(project_id=project_id, credential_ref=account_ref)

    account_id = repo.get_account(credential_ref=account_ref).credential_id
    repo.revoke(credential_ref=account_ref)

    tombstone = repo.get_account(credential_ref=account_ref)
    assert tombstone.credential_id == account_id
    assert tombstone.status == "revoked"
    assert tombstone.project_ids == []
    row = session.get(Credential, account_id)
    assert row is not None
    assert row.display_name_key == f"revoked:{account_ref}"
    assert row.integration_credential_id is None
    assert session.exec(select(IntegrationCredential)).all() == []
    assert all(
        account.credential_ref != account_ref
        for account in repo.status(project_id=None, provider_key="openrouter").accounts
    )
    assert _openrouter_account(repo, name="Retired") != account_ref


def test_detach_is_blocked_by_an_active_project_communication_profile(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    telegram = repo.store_credential(
        provider_key="telegram-bot",
        display_name="Support Bot",
        auth_method_key="bot-token",
        fields={"bot_token": "998877:test-token"},
        attach_project_id=project_id,
    ).data
    ResourceRepository(session).upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-profile",
        external_id="communication-profile:support",
        title="Support",
        data_json={
            "key": "support",
            "enabled": True,
            "provider_facets": {"telegram-bot": {"credential_ref": telegram.credential_ref}},
        },
        provenance_json={"source": "test"},
    )
    profiles = (
        ResourceRepository(session)
        .query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-profile",
            limit=100,
        )
        .items
    )
    assert (
        profiles
        and profiles[0].data_json["provider_facets"]["telegram-bot"]["credential_ref"]
        == telegram.credential_ref
    )

    with pytest.raises(ConflictError, match="communication profile"):
        repo.detach_account(
            project_id=project_id,
            credential_ref=telegram.credential_ref,
        )
