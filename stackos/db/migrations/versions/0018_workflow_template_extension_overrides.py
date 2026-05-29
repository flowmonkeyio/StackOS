"""add atomic workflow template extension overrides

Revision ID: 0018_workflow_template_extension_overrides
Revises: 0017_workflow_template_extensions
Create Date: 2026-05-28

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018_workflow_template_extension_overrides"
down_revision: str | None = "0017_workflow_template_extensions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_column("workflow_template_extensions", "template_overrides_json"):
        op.add_column(
            "workflow_template_extensions",
            sa.Column("template_overrides_json", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    if _has_column("workflow_template_extensions", "template_overrides_json"):
        op.drop_column("workflow_template_extensions", "template_overrides_json")
