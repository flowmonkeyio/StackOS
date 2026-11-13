"""Repair catalogs already stamped after an interrupted 0044 table creation."""

import sqlalchemy as sa
from alembic import op

revision = "0046_pattern_key_index"
down_revision = "0045_pattern_bundles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # An older retry could stamp 0045 with the table but no key index. Add only
    # the missing invariant; retain rows, bundled metadata and sqlite_sequence.
    inspector = sa.inspect(op.get_bind())
    unique_keys = inspector.get_unique_constraints("patterns") + [
        index
        for index in inspector.get_indexes("patterns")
        if index["unique"] and index.get("dialect_options", {}).get("sqlite_where") is None
    ]
    if not any(key["column_names"] == ["key"] for key in unique_keys):
        op.create_index("ix_patterns_key", "patterns", ["key"], unique=True)


def downgrade() -> None:
    # Correct 0044/0045 catalogs already require this index. Keep that invariant
    # when moving back to either revision; 0044's downgrade drops its own table.
    pass
