"""Move TDLib application credentials out of individual Telegram Accounts.

Revision ID: 0032_shared_telegram_application
Revises: 0031_retire_telegram_bot_api
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0032_shared_telegram_application"
down_revision: str | None = "0031_retire_telegram_bot_api"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _repair_config(raw: dict[str, Any]) -> dict[str, Any]:
    config = dict(raw)
    config["telegram_application_conflict"] = True
    config["telegram_desired_connected"] = False
    state = config.get("telegram_auth")
    if isinstance(state, dict):
        config["telegram_auth"] = {
            **state,
            "state": "repair-required",
            "challenge_kind": None,
            "repair_hint": (
                "This Account used a different Telegram application. "
                "Recreate it under the shared application in local Accounts."
            ),
        }
    return config


def upgrade() -> None:
    op.create_table(
        "telegram_application",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("encrypted_payload", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    bind = op.get_bind()
    accounts = (
        bind.execute(
            sa.text(
                """
            SELECT c.id, c.credential_ref, c.config_json,
                   i.id AS backing_id, i.encrypted_payload, i.nonce
            FROM credentials AS c
            JOIN integration_credentials AS i ON i.id = c.integration_credential_id
            WHERE c.provider_key = 'telegram' AND c.status <> 'revoked'
                  AND c.revoked_at IS NULL
            ORDER BY c.id
            """
            )
        )
        .mappings()
        .all()
    )
    if not accounts:
        return

    from stackos.config import get_settings
    from stackos.crypto.aes_gcm import configure_seed_path, decrypt_account, encrypt
    from stackos.crypto.aes_gcm import encrypt_account as encrypt_credential

    configure_seed_path(get_settings().seed_path)
    resolved: list[tuple[Any, dict[str, Any], dict[str, Any], tuple[int, str] | None]] = []
    for account in accounts:
        config = account["config_json"]
        if isinstance(config, str):
            config = json.loads(config)
        if not isinstance(config, dict):
            config = {}
        raw = decrypt_account(
            bytes(account["encrypted_payload"]),
            nonce=bytes(account["nonce"]),
            credential_ref=str(account["credential_ref"]),
            provider_key="telegram",
        )
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Telegram Account encrypted payload must be an object")
        api_id = config.get("api_id")
        api_hash = payload.get("api_hash")
        pair = (
            (api_id, api_hash)
            if isinstance(api_id, int)
            and not isinstance(api_id, bool)
            and 0 < api_id < 2**31
            and isinstance(api_hash, str)
            and bool(api_hash)
            else None
        )
        resolved.append((account, config, payload, pair))

    application = next((pair for _row, _config, _payload, pair in resolved if pair), None)
    if application is not None:
        plaintext = json.dumps(
            {"api_id": application[0], "api_hash": application[1]}, separators=(",", ":")
        ).encode()
        ciphertext, nonce = encrypt(plaintext, project_id=None, kind="telegram-application")
        bind.execute(
            sa.text(
                """
                INSERT INTO telegram_application
                (id, encrypted_payload, nonce, created_at, updated_at)
                VALUES (1, :encrypted_payload, :nonce, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            ),
            {"encrypted_payload": ciphertext, "nonce": nonce},
        )

    for account, config, payload, pair in resolved:
        if application is None or pair != application:
            bind.execute(
                sa.text(
                    """
                    UPDATE credentials SET config_json = :config_json,
                        status = 'repair-required', updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id
                    """
                ),
                {"id": account["id"], "config_json": json.dumps(_repair_config(config))},
            )
            continue
        config.pop("api_id", None)
        payload.pop("api_hash", None)
        ciphertext, nonce = encrypt_credential(
            json.dumps(payload, separators=(",", ":")).encode(),
            credential_ref=str(account["credential_ref"]),
            provider_key="telegram",
        )
        bind.execute(
            sa.text("UPDATE credentials SET config_json = :config_json WHERE id = :id"),
            {"id": account["id"], "config_json": json.dumps(config)},
        )
        bind.execute(
            sa.text(
                """
                UPDATE integration_credentials
                SET encrypted_payload = :encrypted_payload, nonce = :nonce,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
                """
            ),
            {"id": account["backing_id"], "encrypted_payload": ciphertext, "nonce": nonce},
        )


def downgrade() -> None:
    bind = op.get_bind()
    application_count = bind.scalar(sa.text("SELECT COUNT(*) FROM telegram_application"))
    account_count = bind.scalar(
        sa.text("SELECT COUNT(*) FROM credentials WHERE provider_key = 'telegram'")
    )
    if application_count or account_count:
        raise RuntimeError(
            "Telegram application extraction cannot be downgraded with Account data; "
            "restore a database backup"
        )
    op.drop_table("telegram_application")
