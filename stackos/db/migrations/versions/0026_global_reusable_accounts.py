"""Promote credentials to global reusable Accounts with project attachments.

Revision ID: 0026_global_reusable_accounts
Revises: 0025_visible_chromium_profiles
Create Date: 2026-07-24

"""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0026_global_reusable_accounts"
down_revision: str | None = "0025_visible_chromium_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _mapping(row: Any) -> Mapping[str, Any]:
    return row._mapping if hasattr(row, "_mapping") else row


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _account_ref() -> str:
    return f"cred_{secrets.token_urlsafe(18)}"


def _provider_name(bind: Any, provider_key: str) -> str:
    row = bind.execute(
        sa.text("SELECT name FROM auth_providers WHERE key = :provider_key ORDER BY id LIMIT 1"),
        {"provider_key": provider_key},
    ).first()
    if row is not None and str(row[0]).strip():
        return str(row[0]).strip()
    return provider_key.replace("-", " ").title()


def _unique_account_name(
    *,
    provider_key: str,
    preferred: str,
    used: set[tuple[str, str]],
) -> tuple[str, str]:
    base = " ".join(preferred.split())[:200] or f"{provider_key.title()} - Default"
    candidate = base
    suffix = 2
    while (provider_key, candidate.casefold()) in used:
        suffix_text = f" ({suffix})"
        candidate = f"{base[: 200 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    key = candidate.casefold()
    used.add((provider_key, key))
    return candidate, key


def _legacy_rows(bind: Any) -> list[dict[str, Any]]:
    rows = bind.execute(
        sa.text(
            """
            SELECT
                i.id AS integration_id,
                i.project_id AS integration_project_id,
                i.kind,
                i.profile_key AS integration_profile_key,
                i.encrypted_payload,
                i.nonce,
                i.expires_at AS integration_expires_at,
                i.config_json AS integration_config_json,
                i.created_at AS integration_created_at,
                i.updated_at AS integration_updated_at,
                c.id AS credential_id,
                c.project_id AS credential_project_id,
                c.auth_provider_id,
                c.credential_ref,
                c.provider_key AS credential_provider_key,
                c.auth_type,
                c.auth_method_key,
                c.profile_key AS credential_profile_key,
                c.status,
                c.expires_at AS credential_expires_at,
                c.last_tested_at,
                c.revoked_at,
                c.config_json AS credential_config_json,
                c.created_at AS credential_created_at,
                c.updated_at AS credential_updated_at
            FROM integration_credentials AS i
            LEFT JOIN credentials AS c
              ON c.integration_credential_id = i.id
            ORDER BY i.id
            """
        )
    ).all()
    return [dict(_mapping(row)) for row in rows]


def _prepare_accounts(bind: Any) -> list[dict[str, Any]]:
    """Preflight every legacy decryption before any schema/data mutation."""
    rows = _legacy_rows(bind)

    from stackos.config import get_settings
    from stackos.crypto.aes_gcm import configure_seed_path, decrypt

    settings = get_settings()
    configure_seed_path(settings.seed_path)

    orphan_rows = bind.execute(
        sa.text(
            """
            SELECT credential_ref, status
            FROM credentials AS c
            LEFT JOIN integration_credentials AS i
              ON i.id = c.integration_credential_id
            WHERE i.id IS NULL
            """
        )
    ).all()
    invalid_orphans = [
        _mapping(row) for row in orphan_rows if str(_mapping(row)["status"] or "") != "revoked"
    ]
    if invalid_orphans:
        raise RuntimeError(
            "global Account migration cannot preserve credential rows without encrypted backing"
        )
    if not rows:
        return []

    used: set[tuple[str, str]] = set()

    prepared: list[dict[str, Any]] = []
    for row in rows:
        provider_key = str(row["credential_provider_key"] or row["kind"])
        credential_ref = str(row["credential_ref"] or _account_ref())
        plaintext = decrypt(
            bytes(row["encrypted_payload"]),
            nonce=bytes(row["nonce"]),
            project_id=row["integration_project_id"],
            kind=str(row["kind"]),
        )
        config = _json_object(row["credential_config_json"] or row["integration_config_json"])
        label = str(config.pop("label", "") or "").strip()
        config.pop("profile_key", None)
        preferred = label or f"{_provider_name(bind, provider_key)} - Default"
        display_name, display_name_key = _unique_account_name(
            provider_key=provider_key,
            preferred=preferred,
            used=used,
        )
        prepared.append(
            {
                **row,
                "credential_ref": credential_ref,
                "provider_key": provider_key,
                "display_name": display_name,
                "display_name_key": display_name_key,
                "safe_config": config,
                "plaintext": plaintext,
            }
        )
    return prepared


_CASCADING_CREDENTIAL_CHILD_COLUMNS: dict[str, tuple[str, ...]] = {
    "credential_scopes": ("id", "credential_id", "scope", "created_at"),
    "credential_accounts": (
        "id",
        "credential_id",
        "provider_account_id",
        "display_name",
        "metadata_json",
        "created_at",
        "updated_at",
    ),
    "provider_object_references": (
        "id",
        "project_id",
        "credential_id",
        "provider_key",
        "provider_account_id",
        "object_type",
        "provider_object_id",
        "safe_ref",
        "display_name",
        "metadata_json",
        "stale_at",
        "created_at",
        "updated_at",
    ),
}


def _snapshot_credential_children(bind: Any) -> dict[str, list[dict[str, Any]]]:
    """Keep rows SQLite cascades while replacing the Accounts table."""
    snapshots: dict[str, list[dict[str, Any]]] = {}
    for table_name, columns in _CASCADING_CREDENTIAL_CHILD_COLUMNS.items():
        select_columns = ", ".join(columns)
        rows = bind.execute(sa.text(f"SELECT {select_columns} FROM {table_name}")).all()
        snapshots[table_name] = [dict(_mapping(row)) for row in rows]
    return snapshots


def _snapshot_nullable_credential_references(bind: Any) -> dict[str, list[dict[str, Any]]]:
    """Keep audit and OAuth Account links SQLite would otherwise null out."""
    snapshots: dict[str, list[dict[str, Any]]] = {}
    for table_name in (
        "action_calls",
        "credential_usage_events",
        "credential_refresh_events",
    ):
        rows = bind.execute(
            sa.text(f"SELECT id, credential_id FROM {table_name} WHERE credential_id IS NOT NULL")
        ).all()
        snapshots[table_name] = [dict(_mapping(row)) for row in rows]
    rows = bind.execute(
        sa.text(
            """
            SELECT id, credential_id, integration_credential_id
            FROM oauth_states
            WHERE credential_id IS NOT NULL OR integration_credential_id IS NOT NULL
            """
        )
    ).all()
    snapshots["oauth_states"] = [dict(_mapping(row)) for row in rows]
    return snapshots


def _restore_credential_children(
    bind: Any,
    snapshots: Mapping[str, list[dict[str, Any]]],
    *,
    surviving_credential_ids: set[int],
) -> None:
    """Restore CASCADE children only for Accounts that survived the cutover."""
    for table_name, columns in _CASCADING_CREDENTIAL_CHILD_COLUMNS.items():
        placeholders = ", ".join(f":{column}" for column in columns)
        insert = sa.text(f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})")
        for row in snapshots[table_name]:
            if int(row["credential_id"]) not in surviving_credential_ids:
                continue
            bind.execute(insert, row)


def _restore_nullable_credential_references(
    bind: Any,
    snapshots: Mapping[str, list[dict[str, Any]]],
    *,
    surviving_credential_ids: set[int],
    surviving_integration_ids: set[int],
) -> None:
    """Restore SET NULL links without reviving intentionally removed Accounts."""
    for table_name in (
        "action_calls",
        "credential_usage_events",
        "credential_refresh_events",
    ):
        update = sa.text(f"UPDATE {table_name} SET credential_id = :credential_id WHERE id = :id")
        for row in snapshots[table_name]:
            if int(row["credential_id"]) not in surviving_credential_ids:
                continue
            bind.execute(update, row)

    for row in snapshots["oauth_states"]:
        values: dict[str, Any] = {"id": row["id"]}
        assignments: list[str] = []
        credential_id = row["credential_id"]
        integration_id = row["integration_credential_id"]
        if credential_id is not None and int(credential_id) in surviving_credential_ids:
            values["credential_id"] = credential_id
            assignments.append("credential_id = :credential_id")
        if integration_id is not None and int(integration_id) in surviving_integration_ids:
            values["integration_credential_id"] = integration_id
            assignments.append("integration_credential_id = :integration_credential_id")
        if assignments:
            bind.execute(
                sa.text(f"UPDATE oauth_states SET {', '.join(assignments)} WHERE id = :id"),
                values,
            )


def upgrade() -> None:
    bind = op.get_bind()
    prepared = _prepare_accounts(bind)
    now = _now()

    # Legacy revoke already destroyed encrypted material and left only a
    # project-owned tombstone. It is not a reusable Account and cannot be
    # repaired, so remove it while retaining any separate redacted audit rows.
    bind.execute(
        sa.text(
            """
            DELETE FROM credentials
            WHERE integration_credential_id IS NULL
              AND status = 'revoked'
            """
        )
    )

    # SQLite batch table replacement deletes `credentials` twice in this
    # migration. Its foreign-key actions cascade the owned metadata and null
    # audit/OAuth links even though the Account IDs are recreated unchanged.
    # Snapshot after removing unrecoverable revoked tombstones, then restore
    # only rows that still point at a migrated reusable Account.
    cascading_child_snapshots = _snapshot_credential_children(bind)
    nullable_reference_snapshots = _snapshot_nullable_credential_references(bind)

    with op.batch_alter_table("credentials") as batch:
        batch.add_column(sa.Column("display_name", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("display_name_key", sa.String(length=200), nullable=True))

    from stackos.crypto.aes_gcm import encrypt_account

    account_ids_by_ref: dict[str, int] = {}
    attachments: dict[tuple[int, int], str] = {}
    for item in prepared:
        credential_id = item["credential_id"]
        if credential_id is None:
            result = bind.execute(
                sa.text(
                    """
                    INSERT INTO credentials (
                        auth_provider_id, integration_credential_id, credential_ref,
                        provider_key, display_name, display_name_key, auth_type,
                        auth_method_key, status, expires_at, last_tested_at, revoked_at,
                        config_json, created_at, updated_at
                    ) VALUES (
                        :auth_provider_id, :integration_id, :credential_ref,
                        :provider_key, :display_name, :display_name_key, :auth_type,
                        :auth_method_key, :status, :expires_at, NULL, NULL,
                        :config_json, :created_at, :updated_at
                    )
                    """
                ),
                {
                    "auth_provider_id": item["auth_provider_id"],
                    "integration_id": item["integration_id"],
                    "credential_ref": item["credential_ref"],
                    "provider_key": item["provider_key"],
                    "display_name": item["display_name"],
                    "display_name_key": item["display_name_key"],
                    "auth_type": item["auth_type"] or "unknown",
                    "auth_method_key": item["auth_method_key"] or "default",
                    "status": item["status"] or "connected",
                    "expires_at": item["integration_expires_at"],
                    "config_json": json.dumps(item["safe_config"]),
                    "created_at": item["integration_created_at"] or now,
                    "updated_at": item["integration_updated_at"] or now,
                },
            )
            credential_id = int(result.lastrowid)
        else:
            bind.execute(
                sa.text(
                    """
                    UPDATE credentials
                    SET integration_credential_id = :integration_id,
                        provider_key = :provider_key,
                        display_name = :display_name,
                        display_name_key = :display_name_key,
                        config_json = :config_json,
                        expires_at = COALESCE(expires_at, :integration_expires_at),
                        updated_at = :updated_at
                    WHERE id = :credential_id
                    """
                ),
                {
                    "integration_id": item["integration_id"],
                    "provider_key": item["provider_key"],
                    "display_name": item["display_name"],
                    "display_name_key": item["display_name_key"],
                    "config_json": json.dumps(item["safe_config"]),
                    "integration_expires_at": item["integration_expires_at"],
                    "updated_at": now,
                    "credential_id": credential_id,
                },
            )
        account_ids_by_ref[item["credential_ref"]] = int(credential_id)

        origin_project_id = item["credential_project_id"] or item["integration_project_id"]
        if origin_project_id is not None:
            attachments[(int(origin_project_id), int(credential_id))] = "migration"

        ciphertext, nonce = encrypt_account(
            item["plaintext"],
            credential_ref=item["credential_ref"],
            provider_key=item["provider_key"],
        )
        bind.execute(
            sa.text(
                """
                UPDATE integration_credentials
                SET encrypted_payload = :encrypted_payload,
                    nonce = :nonce,
                    updated_at = :updated_at
                WHERE id = :integration_id
                """
            ),
            {
                "encrypted_payload": ciphertext,
                "nonce": nonce,
                "updated_at": now,
                "integration_id": item["integration_id"],
            },
        )

    # Legacy-global Accounts attach only where an active durable execution
    # context explicitly names their stable credential_ref.
    active_consumers = bind.execute(
        sa.text(
            """
            SELECT DISTINCT project_id, credential_ref
            FROM execution_contexts
            WHERE status = 'active' AND credential_ref IS NOT NULL
            """
        )
    ).all()
    for row in active_consumers:
        consumer = _mapping(row)
        credential_id = account_ids_by_ref.get(str(consumer["credential_ref"]))
        if credential_id is None:
            continue
        attachments.setdefault(
            (int(consumer["project_id"]), credential_id),
            "migration-active-context",
        )

    # Any in-flight OAuth state was encrypted and authorized under the old
    # project-bound model. Consume it so the operator restarts cleanly.
    bind.execute(
        sa.text("UPDATE oauth_states SET consumed_at = :now WHERE consumed_at IS NULL"),
        {"now": now},
    )

    with op.batch_alter_table("oauth_states") as batch:
        batch.drop_index("ix_oauth_states_project_provider")
        batch.add_column(sa.Column("attach_project_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "return_surface",
                sa.String(length=40),
                nullable=False,
                server_default="accounts",
            )
        )
    bind.execute(
        sa.text(
            """
            UPDATE oauth_states
            SET attach_project_id = project_id,
                return_surface = 'project-connections'
            """
        )
    )
    with op.batch_alter_table("oauth_states") as batch:
        batch.create_foreign_key(
            "fk_oauth_states_attach_project",
            "projects",
            ["attach_project_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.drop_column("project_id")
        batch.create_index(
            "ix_oauth_states_attach_project_provider",
            ["attach_project_id", "provider_key"],
        )

    with op.batch_alter_table("credentials") as batch:
        batch.drop_index("ix_credentials_project_provider")
        batch.drop_column("profile_key")
        batch.drop_column("project_id")
        batch.alter_column("display_name", existing_type=sa.String(200), nullable=False)
        batch.alter_column(
            "display_name_key",
            existing_type=sa.String(200),
            nullable=False,
        )
        batch.create_unique_constraint(
            "uq_credentials_provider_display_name",
            ["provider_key", "display_name_key"],
        )
        batch.create_index("ix_credentials_provider", ["provider_key"])

    with op.batch_alter_table("integration_credentials") as batch:
        batch.drop_index("ix_integration_credentials_project_kind_profile")
        batch.drop_index("ix_integration_credentials_project")
        batch.drop_constraint(
            "uq_integration_credentials_project_kind_profile",
            type_="unique",
        )
        batch.drop_column("config_json")
        batch.drop_column("last_refreshed_at")
        batch.drop_column("expires_at")
        batch.drop_column("profile_key")
        batch.drop_column("kind")
        batch.drop_column("project_id")

    # SQLite batch table replacement deletes the legacy backing rows before
    # recreating them. The credentials FK uses ON DELETE SET NULL, so restore
    # the one-to-one Account backing link after the replacement completes.
    for item in prepared:
        bind.execute(
            sa.text(
                """
                UPDATE credentials
                SET integration_credential_id = :integration_id
                WHERE id = :credential_id
                """
            ),
            {
                "integration_id": item["integration_id"],
                "credential_id": account_ids_by_ref[item["credential_ref"]],
            },
        )

    surviving_credential_ids = set(account_ids_by_ref.values())
    surviving_integration_ids = {int(item["integration_id"]) for item in prepared}
    _restore_credential_children(
        bind,
        cascading_child_snapshots,
        surviving_credential_ids=surviving_credential_ids,
    )
    _restore_nullable_credential_references(
        bind,
        nullable_reference_snapshots,
        surviving_credential_ids=surviving_credential_ids,
        surviving_integration_ids=surviving_integration_ids,
    )

    op.create_table(
        "project_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.Integer(), nullable=False),
        sa.Column("attached_at", sa.DateTime(), nullable=False),
        sa.Column("attached_by", sa.String(length=120), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["credential_id"], ["credentials.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "credential_id",
            name="uq_project_credentials_project_credential",
        ),
    )
    op.create_index(
        "ix_project_credentials_project",
        "project_credentials",
        ["project_id"],
    )
    op.create_index(
        "ix_project_credentials_credential",
        "project_credentials",
        ["credential_id"],
    )
    for (project_id, credential_id), attached_by in sorted(attachments.items()):
        bind.execute(
            sa.text(
                """
                INSERT INTO project_credentials (
                    project_id, credential_id, attached_at, attached_by
                ) VALUES (:project_id, :credential_id, :attached_at, :attached_by)
                """
            ),
            {
                "project_id": project_id,
                "credential_id": credential_id,
                "attached_at": now,
                "attached_by": attached_by,
            },
        )


def downgrade() -> None:
    """Restore the old shape for empty/dev databases.

    Production rollback for this cutover is the documented pre-upgrade
    database backup. A downgrade with Account data would collapse multi-project
    attachments, so it fails closed instead of silently changing authorization.
    """
    bind = op.get_bind()
    count = int(bind.execute(sa.text("SELECT COUNT(*) FROM credentials")).scalar() or 0)
    if count:
        raise RuntimeError(
            "0026 downgrade with Account data is unsafe; restore the pre-upgrade database backup"
        )

    with op.batch_alter_table("integration_credentials") as batch:
        batch.add_column(sa.Column("project_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("kind", sa.String(length=120), nullable=False))
        batch.add_column(
            sa.Column(
                "profile_key",
                sa.String(length=160),
                nullable=False,
                server_default="default",
            )
        )
        batch.add_column(sa.Column("expires_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("last_refreshed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("config_json", sa.JSON(), nullable=True))
        batch.create_foreign_key(
            "fk_integration_credentials_project",
            "projects",
            ["project_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_unique_constraint(
            "uq_integration_credentials_project_kind_profile",
            ["project_id", "kind", "profile_key"],
        )
        batch.create_index("ix_integration_credentials_project", ["project_id"])
        batch.create_index(
            "ix_integration_credentials_project_kind_profile",
            ["project_id", "kind", "profile_key"],
        )

    with op.batch_alter_table("credentials") as batch:
        batch.drop_index("ix_credentials_provider")
        batch.drop_constraint("uq_credentials_provider_display_name", type_="unique")
        batch.add_column(sa.Column("project_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "profile_key",
                sa.String(length=160),
                nullable=False,
                server_default="default",
            )
        )
        batch.create_foreign_key(
            "fk_credentials_project",
            "projects",
            ["project_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index(
            "ix_credentials_project_provider",
            ["project_id", "provider_key"],
        )
        batch.drop_column("display_name_key")
        batch.drop_column("display_name")

    with op.batch_alter_table("oauth_states") as batch:
        batch.drop_index("ix_oauth_states_attach_project_provider")
        batch.add_column(sa.Column("project_id", sa.Integer(), nullable=False))
    with op.batch_alter_table("oauth_states") as batch:
        batch.create_foreign_key(
            "fk_oauth_states_project",
            "projects",
            ["project_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.drop_column("return_surface")
        batch.drop_column("attach_project_id")
        batch.create_index(
            "ix_oauth_states_project_provider",
            ["project_id", "provider_key"],
        )

    op.drop_index("ix_project_credentials_credential", table_name="project_credentials")
    op.drop_index("ix_project_credentials_project", table_name="project_credentials")
    op.drop_table("project_credentials")
