"""add StackOS plugin catalog tables

Revision ID: 0006_stackos_plugin_catalog
Revises: 0004_workspace_bindings
Create Date: 2026-05-20

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_stackos_plugin_catalog"
down_revision: str | None = "0004_workspace_bindings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plugins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False, server_default="0.1.0"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("slug", name="uq_plugins_slug"),
    )
    op.create_index("ix_plugins_slug", "plugins", ["slug"])

    op.create_table(
        "project_plugins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("plugin_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("enabled_at", sa.DateTime(), nullable=True),
        sa.Column("disabled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plugin_id"], ["plugins.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "project_id",
            "plugin_id",
            name="uq_project_plugins_project_plugin",
        ),
    )
    op.create_index("ix_project_plugins_project", "project_plugins", ["project_id"])

    op.create_table(
        "capabilities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plugin_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(length=80), nullable=False, server_default="domain"),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["plugin_id"], ["plugins.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("plugin_id", "key", name="uq_capabilities_plugin_key"),
    )
    op.create_index("ix_capabilities_plugin", "capabilities", ["plugin_id"])

    op.create_table(
        "providers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plugin_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("auth_type", sa.String(length=80), nullable=False, server_default="none"),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["plugin_id"], ["plugins.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("plugin_id", "key", name="uq_providers_plugin_key"),
    )
    op.create_index("ix_providers_plugin", "providers", ["plugin_id"])

    op.create_table(
        "actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plugin_id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("capability_key", sa.String(length=160), nullable=True),
        sa.Column("risk_level", sa.String(length=40), nullable=False, server_default="read"),
        sa.Column("input_schema_json", sa.JSON(), nullable=True),
        sa.Column("output_schema_json", sa.JSON(), nullable=True),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["plugin_id"], ["plugins.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("plugin_id", "key", name="uq_actions_plugin_key"),
    )
    op.create_index("ix_actions_plugin", "actions", ["plugin_id"])
    op.create_index("ix_actions_provider", "actions", ["provider_id"])

    op.create_table(
        "action_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("action_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["action_id"], ["actions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("action_id", "version", name="uq_action_versions_action_version"),
    )
    op.create_index("ix_action_versions_action", "action_versions", ["action_id"])


def downgrade() -> None:
    op.drop_index("ix_action_versions_action", table_name="action_versions")
    op.drop_table("action_versions")
    op.drop_index("ix_actions_provider", table_name="actions")
    op.drop_index("ix_actions_plugin", table_name="actions")
    op.drop_table("actions")
    op.drop_index("ix_providers_plugin", table_name="providers")
    op.drop_table("providers")
    op.drop_index("ix_capabilities_plugin", table_name="capabilities")
    op.drop_table("capabilities")
    op.drop_index("ix_project_plugins_project", table_name="project_plugins")
    op.drop_table("project_plugins")
    op.drop_index("ix_plugins_slug", table_name="plugins")
    op.drop_table("plugins")
