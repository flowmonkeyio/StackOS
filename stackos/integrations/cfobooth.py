"""Host scope binding for native CFOBooth catalog actions.

Wire contract: CFOBooth docs/engineering/stackos-wire-contract-837.md.
The companion package owns URL, schema, probe and HTTP behavior.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlmodel import Session, col, select
from stackos_connectors.errors import ValidationError as NativeValidationError

from stackos.db.models import ActionCall, Credential, CredentialAccount, ProjectCredential
from stackos.generated_inventory import generated_inventory_scope_key
from stackos.repositories.base import ValidationError

if TYPE_CHECKING:
    from stackos.actions.connectors import ActionConnectorRequest

_INVALID_IDENTITY = "CFOBooth requires current verified key identity; test the Account again"


def verified_key_identity(subject: str | None, metadata: object) -> dict[str, str]:
    """Validate the Account evidence consumed by the host, including its lifetime."""
    from stackos_connectors.connectors.cfobooth.contract import validator

    if not isinstance(metadata, Mapping) or "expires_at" not in metadata:
        raise ValidationError(_INVALID_IDENTITY)
    wire = validator("current-key-v1")
    fields = wire.schema["properties"]["data"]["properties"]
    identity = {}
    try:
        for field, value, schema in (
            ("subject", subject, fields["user"]["properties"]["id"]),
            ("organization_id", metadata.get("organization_id"), fields["organizationId"]),
            ("key_id", metadata.get("key_id"), fields["keyId"]),
        ):
            if not isinstance(value, str) or not wire.evolve(schema=schema).is_valid(value):
                raise ValueError
            identity[field] = value
        expires_at = metadata["expires_at"]
        # The native probe also enforces the precise UTC timestamp spelling.
        if not wire.evolve(schema=fields["expiresAt"]).is_valid(expires_at) or (
            expires_at is not None
            and (
                not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z", expires_at)
                or datetime.fromisoformat(expires_at) <= datetime.now(UTC)
            )
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


def inventory_scope(session: Session, project_id: int, credential: Credential) -> dict:
    from stackos_connectors.connectors.cfobooth import normalize_api_base_url

    config = credential.config_json or {}
    identity = account_key_identity(session, credential.id)
    try:
        base = normalize_api_base_url(config.get("api_base_url"))
    except NativeValidationError:
        raise ValidationError(
            "CFOBooth requires a valid Account API URL; test the Account again"
        ) from None
    return {
        "project_id": project_id,
        "credential_ref": credential.credential_ref,
        "api_base_url": base,
        **identity,
    }


def catalog_sync_replay_matches(session: Session, call: ActionCall) -> bool:
    """Match a successful sync to the current verified scope and lifetimes."""
    scope_key = (call.metadata_json or {}).get("inventory_scope_key")
    if not scope_key or call.credential_id is None:
        return False
    credential = session.get(Credential, call.credential_id, populate_existing=True)
    if (
        credential is None
        or credential.status != "connected"
        or credential.revoked_at is not None
        or credential.integration_credential_id is None
    ):
        return False
    # Recreating the same identity or Connection retires its former inventory.
    lifetime = session.exec(
        select(ProjectCredential.id)
        .join(
            CredentialAccount,
            col(CredentialAccount.credential_id) == ProjectCredential.credential_id,
        )
        .where(
            col(ProjectCredential.project_id) == call.project_id,
            col(ProjectCredential.credential_id) == credential.id,
            col(ProjectCredential.attached_at) <= call.created_at,
            col(CredentialAccount.created_at) <= call.created_at,
        )
    ).first()
    if lifetime is None:
        return False
    try:
        return scope_key == generated_inventory_scope_key(
            inventory_scope(session, call.project_id, credential)
        )
    except ValidationError:
        return False
