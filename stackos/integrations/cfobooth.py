"""Host scope binding for native CFOBooth catalog actions.

Wire contract: CFOBooth docs/engineering/stackos-wire-contract-837.md.
The companion package owns URL, schema, probe and HTTP behavior.
"""

from sqlmodel import select

from stackos.actions.connectors import ActionConnectorRequest
from stackos.db.models import CredentialAccount, ProjectCredential
from stackos.repositories.base import ValidationError


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
    account = request.session.exec(
        select(CredentialAccount).where(CredentialAccount.credential_id == credential.credential_id)
    ).first()
    metadata = account.metadata_json or {} if account is not None else {}
    if (
        account is None
        or not account.provider_account_id
        or not metadata.get("key_id")
        or metadata.get("organization_id") != config.get("organization_id")
    ):
        raise ValidationError("CFOBooth requires verified key identity; test the Account again")
    return {
        "project_id": request.project_id,
        "credential_ref": credential.credential_ref,
        "api_base_url": normalize_api_base_url(config.get("api_base_url")),
        "subject": account.provider_account_id,
        "organization_id": metadata["organization_id"],
        "key_id": metadata["key_id"],
    }
