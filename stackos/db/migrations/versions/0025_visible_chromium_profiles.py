"""Restrict persisted browser launch options to visible Chromium preferences.

Revision ID: 0025_visible_chromium_profiles
Revises: 0024_provider_object_references
Create Date: 2026-07-24
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_visible_chromium_profiles"
down_revision: str | None = "0024_provider_object_references"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALLOWED_LAUNCH_OPTION_KEYS = frozenset({"locale", "timezone_id", "user_agent", "viewport"})


def _safe_options(value: object) -> dict[str, object] | None:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, dict):
        return None
    cleaned = {key: item for key, item in value.items() if key in _ALLOWED_LAUNCH_OPTION_KEYS}
    return cleaned or None


def _serialized_safe_options(value: object) -> str | None:
    cleaned = _safe_options(value)
    return json.dumps(cleaned, sort_keys=True, separators=(",", ":")) if cleaned else None


def upgrade() -> None:
    """Remove unsafe stored launch controls without touching profile directories."""
    bind = op.get_bind()
    profiles = sa.table(
        "browser_profiles",
        sa.column("id", sa.Integer()),
        sa.column("launch_options_json", sa.Text()),
    )
    rows = bind.execute(
        sa.select(
            profiles.c.id,
            sa.cast(profiles.c.launch_options_json, sa.Text()).label("options"),
        )
    ).mappings()
    for row in rows:
        cleaned = _serialized_safe_options(row["options"])
        if cleaned != row["options"]:
            bind.execute(
                profiles.update()
                .where(profiles.c.id == row["id"])
                .values(launch_options_json=cleaned)
            )


def downgrade() -> None:
    """Unsafe launch options cannot be reconstructed safely."""
