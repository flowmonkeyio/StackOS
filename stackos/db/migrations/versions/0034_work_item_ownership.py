"""Persist canonical native work-item ownership without launching execution.

Revision ID: 0034_work_item_ownership
Revises: 0033_project_id_nonreuse
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_work_item_ownership"
down_revision: str | None = "0033_project_id_nonreuse"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_item_ownership",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("canonical_item_id", sa.String(64), nullable=False),
        sa.Column("owner_ref", sa.String(160), nullable=True),
        sa.Column("previous_owner_ref", sa.String(160), nullable=True),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("scope_digest", sa.String(64), nullable=False),
        sa.Column("checkpoint_revision", sa.Integer(), nullable=False),
        sa.Column("checkpoint_ref", sa.String(1000), nullable=True),
        sa.Column("continuity_json", sa.JSON(), nullable=False),
        sa.Column("quiescence_ref", sa.String(1000), nullable=True),
        sa.Column("quiescence_revision", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "canonical_item_id", name="uq_work_item_ownership_item"),
        sa.CheckConstraint("epoch >= 1 AND revision >= 1 AND checkpoint_revision >= 0"),
    )


def downgrade() -> None:
    # Populated ownership contains stops and cumulative budgets; dropping it can
    # make a future installation admit the same work as a fresh attempt.
    if op.get_bind().execute(sa.text("SELECT 1 FROM work_item_ownership LIMIT 1")).first():
        raise RuntimeError("Refusing to discard persisted ownership/stop history")
    op.drop_table("work_item_ownership")
