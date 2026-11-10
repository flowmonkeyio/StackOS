"""Private Account custody uses the verified owner as well as project attachment."""

import pytest
from sqlmodel import Session, select

from stackos.auth import UserPrincipal
from stackos.auth_providers import AuthRepository
from stackos.db.models import Credential, ProjectCredential
from stackos.repositories.base import NotFoundError
from stackos.repositories.projects import IntegrationCredentialRepository, ProjectRepository


def identify(session: Session, user: str, environment: str = "account-test") -> None:
    session.info["server_environment_id"] = environment
    session.info["user_principal"] = UserPrincipal(user, environment)


def project(session: Session, slug: str) -> int:
    result = ProjectRepository(session).create(slug=slug, name=slug, domain="example.test").data
    assert result.id is not None
    return result.id


def account(session: Session, name: str = "Private") -> str:
    return (
        AuthRepository(session)
        .store_credential(
            provider_key="openrouter", display_name=name, fields={"api_key": "synthetic-secret"}
        )
        .data.credential_ref
    )


def test_foreign_and_legacy_accounts_are_invisible(session: Session) -> None:
    legacy = account(session, "Legacy")
    identify(session, "user-a")
    own_project = project(session, "a")
    ref = account(session)
    repo = AuthRepository(session)
    repo.attach_account(project_id=own_project, credential_ref=ref)
    identify(session, "user-b")
    foreign_project = project(session, "b")
    assert repo.status(project_id=None).accounts == []
    for hidden in (ref, legacy, "cred_missing"):
        for operation in (
            lambda hidden=hidden: repo.get_account(credential_ref=hidden),
            lambda hidden=hidden: repo.get_credential_edit_state(credential_ref=hidden),
            lambda hidden=hidden: repo.update_credential(
                credential_ref=hidden, display_name="stolen", fields={}
            ),
            lambda hidden=hidden: repo.attach_account(
                project_id=foreign_project, credential_ref=hidden
            ),
            lambda hidden=hidden: repo.detach_account(
                project_id=own_project, credential_ref=hidden
            ),
            lambda hidden=hidden: repo.revoke(credential_ref=hidden),
            lambda hidden=hidden: repo.require_attached_account(
                project_id=own_project, credential_ref=hidden
            ),
        ):
            with pytest.raises(NotFoundError):
                operation()
    # Per-owner names do not expose or collide with another user's Account.
    other = account(session)
    assert other != ref
    identify(session, "user-a", "another-environment")
    assert repo.status(project_id=None).accounts == []
    with pytest.raises(NotFoundError):
        repo.get_account(credential_ref=ref)


@pytest.mark.asyncio
async def test_attachment_cannot_override_account_owner(session: Session) -> None:
    identify(session, "user-a")
    ref = account(session)
    row = session.exec(select(Credential).where(Credential.credential_ref == ref)).one()
    assert row.integration_credential_id is not None
    identify(session, "user-b")
    own_project = project(session, "b")
    # A stale/invalid attachment still cannot grant access to a foreign Account.
    session.add(ProjectCredential(project_id=own_project, credential_id=row.id))
    session.commit()
    repo = AuthRepository(session)
    assert repo.status(project_id=own_project).accounts == []
    with pytest.raises(NotFoundError):
        await repo.resolve_for_execution(
            project_id=own_project,
            provider_key="openrouter",
            credential_ref=ref,
            operation="custody-test",
        )
    with pytest.raises(NotFoundError):
        IntegrationCredentialRepository(session).get_decrypted(row.integration_credential_id)


def test_oauth_state_cannot_be_consumed_by_another_owner(session: Session, settings) -> None:
    from urllib.parse import parse_qs, urlparse

    from stackos.db.models import OAuthState

    identify(session, "user-a")
    repo = AuthRepository(session)
    ref = repo.store_credential(
        provider_key="google-search-console",
        auth_method_key="oauth2_authorization_code",
        display_name="OAuth",
        fields={"client_id": "synthetic-id", "client_secret": "synthetic-app"},
    ).data.credential_ref
    started = repo.start(
        provider_key="google-search-console",
        auth_method_key="oauth2_authorization_code",
        credential_ref=ref,
        settings=settings,
    ).data
    state = parse_qs(urlparse(started.authorization_url).query)["state"][0]
    identify(session, "user-b")
    assert repo.consume_oauth_state(state=state) is None
    assert session.exec(select(OAuthState)).one().consumed_at is None
    identify(session, "user-a", "other-environment")
    assert repo.consume_oauth_state(state=state) is None
    identify(session, "user-a")
    assert repo.consume_oauth_state(state=state) is not None


@pytest.mark.parametrize(
    "surface,scope,run_token",
    [
        ("rest", "verified-user", None),
        ("mcp", "verified-user", None),
        ("rest", "local-runtime", None),
        ("rest", "verified-user", "synthetic-run-token"),
    ],
)
def test_account_setup_keeps_direct_human_authority(session, surface, scope, run_token):
    from stackos.mcp.context import MCPContext
    from stackos.mcp.errors import ToolNotGrantedError
    from stackos.mcp.permissions import check_call_grant

    ctx = MCPContext(
        session=session,
        request_id="custody-test",
        principal=UserPrincipal("user-a", "account-test"),
        run_token=run_token,
        extras={"surface": surface, "auth_scope": scope},
    )
    if surface == "rest" and scope == "verified-user" and run_token is None:
        check_call_grant("connection.attach", ctx)
    else:
        with pytest.raises(ToolNotGrantedError):
            check_call_grant("connection.attach", ctx)
