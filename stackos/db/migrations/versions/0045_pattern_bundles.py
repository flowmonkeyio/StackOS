"""Minimal trusted bundle baseline and deletion intent."""

import sqlalchemy as sa
from alembic import op

revision = "0045_pattern_bundles"
down_revision = "0044_patterns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add in place: rebuilding the table would lose sqlite_sequence's deleted
    # high-water ID and could make an old identity/revision etag valid again.
    op.add_column("patterns", sa.Column("bundled_id", sa.String(120), nullable=True))
    op.add_column("patterns", sa.Column("bundled_version", sa.String(120), nullable=True))
    op.add_column("patterns", sa.Column("bundled_base_hash", sa.String(64), nullable=True))
    op.add_column("patterns", sa.Column("content_hash", sa.String(64), nullable=True))
    op.create_index("uq_patterns_bundled_id", "patterns", ["bundled_id"], unique=True)
    op.create_table("pattern_deletions", sa.Column("bundled_id", sa.String(120), primary_key=True))


def downgrade() -> None:
    op.drop_table("pattern_deletions")
    op.drop_index("uq_patterns_bundled_id", table_name="patterns")
    for column in ("bundled_id", "bundled_version", "bundled_base_hash", "content_hash"):
        op.drop_column("patterns", column)
