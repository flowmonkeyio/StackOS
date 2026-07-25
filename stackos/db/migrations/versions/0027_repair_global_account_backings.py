"""Repair Account backing links lost by the initial global Account migration.

Revision ID: 0027_repair_global_account_backings
Revises: 0026_global_reusable_accounts
Create Date: 2026-07-24

"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0027_repair_global_account_backings"
down_revision: str | None = "0026_global_reusable_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _mapping(row: Any) -> Mapping[str, Any]:
    return row._mapping if hasattr(row, "_mapping") else row


def upgrade() -> None:
    bind = op.get_bind()
    accounts = [
        dict(_mapping(row))
        for row in bind.execute(
            sa.text(
                """
                SELECT id, credential_ref, provider_key
                FROM credentials
                WHERE status <> 'revoked'
                  AND revoked_at IS NULL
                  AND integration_credential_id IS NULL
                ORDER BY id
                """
            )
        ).all()
    ]
    if not accounts:
        return

    backing_rows = [
        dict(_mapping(row))
        for row in bind.execute(
            sa.text(
                """
                SELECT id, encrypted_payload, nonce
                FROM integration_credentials
                ORDER BY id
                """
            )
        ).all()
    ]

    from stackos.config import get_settings
    from stackos.crypto.aes_gcm import CryptoError, configure_seed_path, decrypt_account

    configure_seed_path(get_settings().seed_path)
    matches_by_account: dict[int, list[int]] = {}
    for account in accounts:
        account_id = int(account["id"])
        matches: list[int] = []
        for backing in backing_rows:
            try:
                decrypt_account(
                    bytes(backing["encrypted_payload"]),
                    nonce=bytes(backing["nonce"]),
                    credential_ref=str(account["credential_ref"]),
                    provider_key=str(account["provider_key"]),
                )
            except CryptoError:
                continue
            matches.append(int(backing["id"]))
        matches_by_account[account_id] = matches

    account_ids_by_backing: dict[int, list[int]] = defaultdict(list)
    for account_id, matches in matches_by_account.items():
        for backing_id in matches:
            account_ids_by_backing[backing_id].append(account_id)
    ambiguous_account_ids = {
        account_id for account_id, matches in matches_by_account.items() if len(matches) != 1
    }
    ambiguous_account_ids.update(
        account_id
        for account_ids in account_ids_by_backing.values()
        if len(account_ids) > 1
        for account_id in account_ids
    )
    if ambiguous_account_ids:
        safe_accounts = ", ".join(
            f"{account['id']}/{account['provider_key']}"
            for account in accounts
            if int(account["id"]) in ambiguous_account_ids
        )
        raise RuntimeError(
            "global Account backing repair could not authenticate one unique backing for "
            f"Account/provider {safe_accounts}; restore the pre-0026 backup or contact support. "
            "No changes were applied."
        )

    for account in accounts:
        account_id = int(account["id"])
        bind.execute(
            sa.text(
                """
                UPDATE credentials
                SET integration_credential_id = :integration_credential_id
                WHERE id = :credential_id
                """
            ),
            {
                "credential_id": account_id,
                "integration_credential_id": matches_by_account[account_id][0],
            },
        )


def downgrade() -> None:
    """The repair is data-only and deliberately has no reverse mutation."""
