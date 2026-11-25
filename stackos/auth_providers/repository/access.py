"""Account ownership shared by inventory, lifecycle and encrypted backing access."""

from sqlmodel import Session, col

from stackos.db.model_auth import Credential
from stackos.repositories.base import NotFoundError
from stackos.repositories.project_access import user_principal

# Existing operations only; native Telegram authorization remains local-only.
ACCOUNT_OPERATIONS = frozenset(
    {
        "account.list",
        "account.create",
        "account.get",
        "account.update",
        "account.start",
        "account.test",
        "account.revoke",
        "connection.list",
        "connection.attach",
        "connection.detach",
    }
)
ACCOUNT_SETUP_OPERATIONS = frozenset(
    {
        "account.create",
        "account.get",
        "account.update",
        "account.start",
        "account.revoke",
        "connection.attach",
        "connection.detach",
    }
)


def account_owner_values(session: Session) -> dict[str, str]:
    principal = user_principal(session)
    if principal is None:
        return {}
    return {"owner_user_id": principal.user_id, "environment_id": principal.environment_id}


def account_owner_filter(session: Session):
    principal = user_principal(session)
    if principal is None:
        return True
    return (col(Credential.owner_user_id) == principal.user_id) & (
        col(Credential.environment_id) == principal.environment_id
    )


def require_account_owner(session: Session, credential: Credential | None) -> Credential:
    principal = user_principal(session)
    if credential is None or (
        principal is not None
        and (
            credential.owner_user_id != principal.user_id
            or credential.environment_id != principal.environment_id
        )
    ):
        raise NotFoundError("Account not found")
    return credential
