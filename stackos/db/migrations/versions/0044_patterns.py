"""Add the empty, installation-wide pattern catalog."""

import sqlalchemy as sa
from alembic import op

revision = "0044_patterns"
down_revision = "0043_automations_homes_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("patterns"):
        op.create_table(
            "patterns",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("key", sa.String(120), nullable=False),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("summary", sa.String(1000), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("tags_json", sa.JSON(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sqlite_autoincrement=True,
        )
    # SQLite can retain CREATE TABLE when the following DDL/stamp is
    # interrupted. Resume the key invariant independently of table creation.
    inspector = sa.inspect(op.get_bind())
    unique_keys = inspector.get_unique_constraints("patterns") + [
        index
        for index in inspector.get_indexes("patterns")
        if index["unique"] and index.get("dialect_options", {}).get("sqlite_where") is None
    ]
    if not any(key["column_names"] == ["key"] for key in unique_keys):
        op.create_index("ix_patterns_key", "patterns", ["key"], unique=True)


def downgrade() -> None:
    op.drop_table("patterns")
