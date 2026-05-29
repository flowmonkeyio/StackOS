"""add project workflow template extensions

Revision ID: 0017_workflow_template_extensions
Revises: 0016_tracker_completion_evidence
Create Date: 2026-05-28

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017_workflow_template_extensions"
down_revision: str | None = "0016_tracker_completion_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("workflow_template_extensions"):
        op.create_table(
            "workflow_template_extensions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("workflow_key", sa.String(length=160), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("input_defaults_json", sa.JSON(), nullable=True),
            sa.Column("selected_context_json", sa.JSON(), nullable=True),
            sa.Column("required_input_keys_json", sa.JSON(), nullable=True),
            sa.Column("guardrails_json", sa.JSON(), nullable=True),
            sa.Column("step_overrides_json", sa.JSON(), nullable=True),
            sa.Column("template_overrides_json", sa.JSON(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_by", sa.String(length=200), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "project_id",
                "workflow_key",
                name="uq_workflow_template_extensions_project_key",
            ),
        )
    else:
        columns = {column["name"] for column in inspector.get_columns("workflow_template_extensions")}
        if "template_overrides_json" not in columns:
            op.add_column(
                "workflow_template_extensions",
                sa.Column("template_overrides_json", sa.JSON(), nullable=True),
            )

    indexes = {
        item["name"]
        for item in sa.inspect(bind).get_indexes("workflow_template_extensions")
    }
    if "ix_workflow_template_extensions_enabled" not in indexes:
        op.create_index(
            "ix_workflow_template_extensions_enabled",
            "workflow_template_extensions",
            ["enabled"],
            unique=False,
        )
    if "ix_workflow_template_extensions_project" not in indexes:
        op.create_index(
            "ix_workflow_template_extensions_project",
            "workflow_template_extensions",
            ["project_id"],
            unique=False,
        )
    if "ix_workflow_template_extensions_workflow_key" not in indexes:
        op.create_index(
            "ix_workflow_template_extensions_workflow_key",
            "workflow_template_extensions",
            ["workflow_key"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_template_extensions_workflow_key",
        table_name="workflow_template_extensions",
    )
    op.drop_index(
        "ix_workflow_template_extensions_project",
        table_name="workflow_template_extensions",
    )
    op.drop_index(
        "ix_workflow_template_extensions_enabled",
        table_name="workflow_template_extensions",
    )
    op.drop_table("workflow_template_extensions")
