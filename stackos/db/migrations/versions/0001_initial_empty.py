"""initial empty migration

The 28-table schema lands in M1. This M0 placeholder exists so the migration
graph has a head and `alembic upgrade head` is a no-op success rather than
an error about no revisions.

Revision ID: 0001_initial_empty
Revises:
Create Date: 2026-05-06

"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001_initial_empty"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op: schema lands in M1."""


def downgrade() -> None:
    """No-op: nothing to undo."""
