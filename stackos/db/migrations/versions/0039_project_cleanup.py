"""Retain the project identity while managed-file cleanup is incomplete."""

import sqlalchemy as sa
from alembic import op

revision = "0039_project_cleanup"
down_revision = "0038_project_order"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "cleanup_json" not in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("projects")
    }:
        op.add_column("projects", sa.Column("cleanup_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "cleanup_json")
