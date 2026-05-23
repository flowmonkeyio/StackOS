"""add StackOS workflow template tables

Revision ID: 0010_stackos_workflow_templates
Revises: 0009_stackos_project_memory
Create Date: 2026-05-20

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_stackos_workflow_templates"
down_revision: str | None = "0009_stackos_project_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("plugin_id", sa.Integer(), nullable=True),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("origin_path", sa.String(length=1000), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="active"),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plugin_id"], ["plugins.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "project_id",
            "key",
            "source",
            name="uq_workflow_templates_project_key_source",
        ),
    )
    op.create_index("ix_workflow_templates_key", "workflow_templates", ["key"])
    op.create_index("ix_workflow_templates_project", "workflow_templates", ["project_id"])
    op.create_index("ix_workflow_templates_plugin", "workflow_templates", ["plugin_id"])
    op.create_index("ix_workflow_templates_source", "workflow_templates", ["source"])

    op.create_table(
        "workflow_template_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("spec_json", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["workflow_templates.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "template_id",
            "version",
            name="uq_workflow_template_versions_template_version",
        ),
    )
    op.create_index(
        "ix_workflow_template_versions_template",
        "workflow_template_versions",
        ["template_id"],
    )
    op.create_index(
        "ix_workflow_template_versions_checksum",
        "workflow_template_versions",
        ["checksum"],
    )

    op.create_table(
        "project_workflow_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("active_version_id", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["workflow_templates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["active_version_id"],
            ["workflow_template_versions.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "project_id",
            "template_id",
            name="uq_project_workflow_templates_project_template",
        ),
    )
    op.create_index(
        "ix_project_workflow_templates_project",
        "project_workflow_templates",
        ["project_id"],
    )
    op.create_index(
        "ix_project_workflow_templates_template",
        "project_workflow_templates",
        ["template_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_workflow_templates_template",
        table_name="project_workflow_templates",
    )
    op.drop_index(
        "ix_project_workflow_templates_project",
        table_name="project_workflow_templates",
    )
    op.drop_table("project_workflow_templates")
    op.drop_index(
        "ix_workflow_template_versions_checksum",
        table_name="workflow_template_versions",
    )
    op.drop_index(
        "ix_workflow_template_versions_template",
        table_name="workflow_template_versions",
    )
    op.drop_table("workflow_template_versions")
    op.drop_index("ix_workflow_templates_source", table_name="workflow_templates")
    op.drop_index("ix_workflow_templates_plugin", table_name="workflow_templates")
    op.drop_index("ix_workflow_templates_project", table_name="workflow_templates")
    op.drop_index("ix_workflow_templates_key", table_name="workflow_templates")
    op.drop_table("workflow_templates")
