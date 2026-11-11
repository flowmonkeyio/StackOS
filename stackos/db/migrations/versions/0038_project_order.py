"""Persist catalog order while preserving the existing ID-ascending list.

Revision ID: 0038_project_order
Revises: 0037_schedule_id_nonreuse
"""

import sqlalchemy as sa
from alembic import op

revision = "0038_project_order"
down_revision = "0037_schedule_id_nonreuse"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    with bind.begin_nested():
        inspector = sa.inspect(bind)
        if "order_index" not in {column["name"] for column in inspector.get_columns("projects")}:
            op.add_column(
                "projects",
                sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
            )
            op.execute("UPDATE projects SET order_index = id")
        # A committed migration whose version stamp was lost must retain user moves.
        if "ix_projects_order_index" not in {
            index["name"] for index in inspector.get_indexes("projects")
        }:
            op.create_index("ix_projects_order_index", "projects", ["order_index"])


def downgrade() -> None:
    op.drop_index("ix_projects_order_index", table_name="projects")
    op.drop_column("projects", "order_index")
