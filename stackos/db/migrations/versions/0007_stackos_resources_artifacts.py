"""add StackOS generic resources and artifacts

Revision ID: 0007_stackos_resources_artifacts
Revises: 0006_stackos_plugin_catalog
Create Date: 2026-05-20

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_stackos_resources_artifacts"
down_revision: str | None = "0006_stackos_plugin_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plugin_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("schema_json", sa.JSON(), nullable=False),
        sa.Column("ui_schema_json", sa.JSON(), nullable=True),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["plugin_id"], ["plugins.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("plugin_id", "key", name="uq_resources_plugin_key"),
    )
    op.create_index("ix_resources_plugin", "resources", ["plugin_id"])

    op.create_table(
        "resource_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=300), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("data_json", sa.JSON(), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resource_id"], ["resources.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "project_id",
            "resource_id",
            "external_id",
            name="uq_resource_records_project_resource_external",
        ),
    )
    op.create_index(
        "ix_resource_records_project_resource",
        "resource_records",
        ["project_id", "resource_id"],
    )
    op.create_index("ix_resource_records_resource", "resource_records", ["resource_id"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("plugin_id", sa.Integer(), nullable=True),
        sa.Column("resource_record_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("uri", sa.String(length=2048), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=True),
        sa.Column("mime_type", sa.String(length=160), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("provenance_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plugin_id"], ["plugins.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["resource_record_id"],
            ["resource_records.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_artifacts_project", "artifacts", ["project_id"])
    op.create_index("ix_artifacts_resource_record", "artifacts", ["resource_record_id"])
    op.create_index("ix_artifacts_plugin", "artifacts", ["plugin_id"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_plugin", table_name="artifacts")
    op.drop_index("ix_artifacts_resource_record", table_name="artifacts")
    op.drop_index("ix_artifacts_project", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_resource_records_resource", table_name="resource_records")
    op.drop_index("ix_resource_records_project_resource", table_name="resource_records")
    op.drop_table("resource_records")
    op.drop_index("ix_resources_plugin", table_name="resources")
    op.drop_table("resources")
