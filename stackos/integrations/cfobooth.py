"""Host scope binding for native CFOBooth catalog actions.

Wire contract: CFOBooth docs/engineering/stackos-wire-contract-837.md.
The companion package owns URL, schema, probe and HTTP behavior.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from sqlmodel import Session, select
from stackos_connectors.errors import ValidationError as NativeValidationError

from stackos.actions.connectors import ActionConnectorRequest
from stackos.db.models import CredentialAccount, ProjectCredential
from stackos.repositories.base import ValidationError

_INVALID_IDENTITY = "CFOBooth requires current verified key identity; test the Account again"


def verified_key_identity(subject: str | None, metadata: object) -> dict[str, str]:
    """Validate the Account evidence consumed by the host, including its lifetime."""
    if not isinstance(metadata, Mapping):
        raise ValidationError(_INVALID_IDENTITY)
    identity = {}
    try:
        for field, value in (
            ("subject", subject),
            ("organization_id", metadata.get("organization_id")),
            ("key_id", metadata.get("key_id")),
        ):
            if not isinstance(value, str) or str(UUID(value)) != value.lower():
                raise ValueError
            identity[field] = value
        expires_at = metadata.get("expires_at")
        if expires_at is not None and (
            not isinstance(expires_at, str)
            or not expires_at.endswith("Z")
            or datetime.fromisoformat(expires_at) <= datetime.now(UTC)
        ):
            raise ValueError
    except ValueError:
        raise ValidationError(_INVALID_IDENTITY) from None
    return identity


def account_key_identity(session: Session, credential_id: int | None) -> dict[str, str]:
    # Scalar projection also sees evidence replaced by a concurrent Account test.
    row = session.exec(
        select(CredentialAccount.provider_account_id, CredentialAccount.metadata_json).where(
            CredentialAccount.credential_id == credential_id
        )
    ).first()
    if row is None:
        raise ValidationError(_INVALID_IDENTITY)
    return verified_key_identity(row[0], row[1])


def project_attachment_revision(request: ActionConnectorRequest) -> tuple | None:
    """Read the persisted Connection lifetime, including a recreated attachment.

    Select scalar columns so an identity-map entry cannot conceal detach/reattach
    in another session. SQLite may reuse the row ID; attached_at distinguishes
    the new Connection without changing the global Account or inventory identity.
    """
    assert request.credential is not None and request.session is not None
    row = request.session.exec(
        select(ProjectCredential.id, ProjectCredential.attached_at).where(
            ProjectCredential.project_id == request.project_id,
            ProjectCredential.credential_id == request.credential.credential_id,
        )
    ).first()
    return tuple(row) if row is not None else None


def inventory_scope(request: ActionConnectorRequest) -> dict:
    from stackos_connectors.connectors.cfobooth import normalize_api_base_url

    if request.credential is None or request.session is None:
        raise ValidationError("CFOBooth requires a verified Account and inventory session")
    credential = request.credential
    config = credential.config_json or {}
    identity = account_key_identity(request.session, credential.credential_id)
    try:
        base = normalize_api_base_url(config.get("api_base_url"))
    except NativeValidationError:
        raise ValidationError(
            "CFOBooth requires a valid Account API URL; test the Account again"
        ) from None
    return {
        "project_id": request.project_id,
        "credential_ref": credential.credential_ref,
        "api_base_url": base,
        **identity,
    }
