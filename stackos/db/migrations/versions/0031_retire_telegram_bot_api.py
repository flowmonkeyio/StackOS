"""Retire Bot API setup and routing without translating it into TDLib Accounts.

Revision ID: 0031_retire_telegram_bot_api
Revises: 0030_durable_action_runtime
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0031_retire_telegram_bot_api"
down_revision = "0030_durable_action_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def upgrade() -> None:
    bind = op.get_bind()
    # Retain safe Account identities and ActionCall history. Remove their active
    # attachment/auth/operation surfaces; native setup must be explicit.
    bind.execute(
        sa.text("""
        DELETE FROM project_credentials WHERE credential_id IN
        (SELECT id FROM credentials WHERE provider_key = 'telegram-bot')
    """)
    )
    bind.execute(
        sa.text("""
        UPDATE credentials SET status = 'revoked',
            revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP
        WHERE provider_key = 'telegram-bot'
    """)
    )
    bind.execute(
        sa.text("""
        DELETE FROM actions WHERE plugin_id IN (SELECT id FROM plugins WHERE slug = 'communications')
        AND key LIKE 'telegram-bot.%'
    """)
    )
    bind.execute(sa.text("DELETE FROM auth_providers WHERE key = 'telegram-bot'"))
    bind.execute(
        sa.text("""
        DELETE FROM providers WHERE key = 'telegram-bot'
        AND plugin_id IN (SELECT id FROM plugins WHERE slug = 'communications')
    """)
    )
    rows = (
        bind.execute(
            sa.text("""
        SELECT rr.id, rr.data_json, r.key FROM resource_records rr
        JOIN resources r ON rr.resource_id = r.id JOIN plugins p ON r.plugin_id = p.id
        WHERE p.slug = 'communications'
        AND r.key IN ('communication-profile', 'communication-target', 'communication-channel')
    """)
        )
        .mappings()
        .all()
    )
    for row in rows:
        data = _object(row["data_json"])
        facets = _object(data.get("provider_facets"))
        changed = False
        if row["key"] == "communication-profile" and "telegram-bot" in facets:
            del facets["telegram-bot"]
            data["provider_facets"] = facets
            if not facets:
                data["enabled"] = False
            changed = True
        elif data.get("provider_key") == "telegram-bot":
            if row["key"] == "communication-target":
                data["enabled"] = False
                data["send_policy"] = {**_object(data.get("send_policy")), "mode": "deny"}
            else:
                data["ingest_enabled"] = False
                data["send_enabled"] = False
                data["surface_binding_state"] = "repair-required"
                data["surface_binding_issue"] = "telegram_bot_api_retired"
            changed = True
        if changed:
            data["metadata_json"] = {
                **_object(data.get("metadata_json")),
                "telegram_setup_required": True,
                "telegram_repair_hint": "Connect a TDLib bot or user Account and explicitly bind a Telegram profile and targets.",
            }
            bind.execute(
                sa.text("UPDATE resource_records SET data_json=:data WHERE id=:id"),
                {"id": row["id"], "data": json.dumps(data)},
            )


def downgrade() -> None:
    # Revoked legacy credentials/routes are never silently re-enabled.
    pass
