"""Add artifact lifecycle fields.

Revision ID: 0022_artifact_lifecycle
Revises: 0021_browser_automation
Create Date: 2026-06-14

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022_artifact_lifecycle"
down_revision: str | None = "0021_browser_automation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "artifacts",
        sa.Column("status", sa.String(length=40), nullable=False, server_default="approved"),
    )
    op.add_column(
        "artifacts",
        sa.Column("superseded_by_artifact_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "artifacts",
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.execute("UPDATE artifacts SET updated_at = created_at WHERE updated_at IS NULL")
    with op.batch_alter_table("artifacts") as batch_op:
        batch_op.alter_column("updated_at", existing_type=sa.DateTime(), nullable=False)
    op.create_index("ix_artifacts_project_status", "artifacts", ["project_id", "status"])
    op.create_index(
        "ix_artifacts_superseded_by",
        "artifacts",
        ["superseded_by_artifact_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_artifacts_superseded_by", table_name="artifacts")
    op.drop_index("ix_artifacts_project_status", table_name="artifacts")
    with op.batch_alter_table("artifacts") as batch_op:
        batch_op.drop_column("updated_at")
        batch_op.drop_column("superseded_by_artifact_id")
        batch_op.drop_column("status")
