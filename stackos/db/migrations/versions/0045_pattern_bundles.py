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
    # SQLite may retain each DDL statement before the revision stamp commits.
    # Resume only missing objects, including a complete-DDL/lost-stamp replay.
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("patterns")}
    for name, length in (
        ("bundled_id", 120),
        ("bundled_version", 120),
        ("bundled_base_hash", 64),
        ("content_hash", 64),
    ):
        if name not in columns:
            op.add_column("patterns", sa.Column(name, sa.String(length), nullable=True))
    unique_keys = inspector.get_unique_constraints("patterns") + [
        index for index in inspector.get_indexes("patterns") if index["unique"]
    ]
    if not any(key["column_names"] == ["bundled_id"] for key in unique_keys):
        op.create_index("uq_patterns_bundled_id", "patterns", ["bundled_id"], unique=True)
    if not inspector.has_table("pattern_deletions"):
        op.create_table(
            "pattern_deletions", sa.Column("bundled_id", sa.String(120), primary_key=True)
        )


def downgrade() -> None:
    op.drop_table("pattern_deletions")
    op.drop_index("uq_patterns_bundled_id", table_name="patterns")
    for column in ("bundled_id", "bundled_version", "bundled_base_hash", "content_hash"):
        op.drop_column("patterns", column)
