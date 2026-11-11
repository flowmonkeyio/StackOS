"""Persist producer-owned file custody separately from editable provenance."""

import sqlalchemy as sa
from alembic import op

revision = "0040_artifact_managed_files"
down_revision = "0039_project_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "managed_file_json" not in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("artifacts")
    }:
        op.add_column("artifacts", sa.Column("managed_file_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("artifacts", "managed_file_json")
