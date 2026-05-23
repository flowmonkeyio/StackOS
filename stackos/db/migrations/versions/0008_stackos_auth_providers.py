"""add StackOS auth provider and credential reference tables

Revision ID: 0008_stackos_auth_providers
Revises: 0007_stackos_resources_artifacts
Create Date: 2026-05-20

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_stackos_auth_providers"
down_revision: str | None = "0007_stackos_resources_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_providers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plugin_id", sa.Integer(), nullable=True),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("auth_type", sa.String(length=80), nullable=False, server_default="none"),
        sa.Column("scopes_json", sa.JSON(), nullable=True),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["plugin_id"], ["plugins.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("plugin_id", "key", name="uq_auth_providers_plugin_key"),
    )
    op.create_index("ix_auth_providers_plugin", "auth_providers", ["plugin_id"])
    op.create_index("ix_auth_providers_key", "auth_providers", ["key"])

    op.create_table(
        "credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("auth_provider_id", sa.Integer(), nullable=True),
        sa.Column("integration_credential_id", sa.Integer(), nullable=True),
        sa.Column("credential_ref", sa.String(length=120), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("auth_type", sa.String(length=80), nullable=False, server_default="none"),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="connected"),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["auth_provider_id"], ["auth_providers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["integration_credential_id"],
            ["integration_credentials.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("credential_ref", name="uq_credentials_ref"),
        sa.UniqueConstraint(
            "integration_credential_id",
            name="uq_credentials_integration_credential",
        ),
    )
    op.create_index(
        "ix_credentials_project_provider",
        "credentials",
        ["project_id", "provider_key"],
    )

    op.create_table(
        "credential_scopes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("credential_id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["credential_id"], ["credentials.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("credential_id", "scope", name="uq_credential_scopes_scope"),
    )
    op.create_index("ix_credential_scopes_credential", "credential_scopes", ["credential_id"])

    op.create_table(
        "credential_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("credential_id", sa.Integer(), nullable=False),
        sa.Column("provider_account_id", sa.String(length=300), nullable=True),
        sa.Column("display_name", sa.String(length=300), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["credential_id"], ["credentials.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_credential_accounts_credential", "credential_accounts", ["credential_id"])
    op.create_index(
        "ix_credential_accounts_provider_account",
        "credential_accounts",
        ["provider_account_id"],
    )

    op.create_table(
        "oauth_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("credential_id", sa.Integer(), nullable=True),
        sa.Column("integration_credential_id", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(length=200), nullable=False),
        sa.Column("redirect_uri", sa.String(length=2048), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["credential_id"], ["credentials.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["integration_credential_id"],
            ["integration_credentials.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("state", name="uq_oauth_states_state"),
    )
    op.create_index(
        "ix_oauth_states_project_provider",
        "oauth_states",
        ["project_id", "provider_key"],
    )

    op.create_table(
        "credential_usage_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("credential_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("operation", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["credential_id"], ["credentials.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_credential_usage_events_credential",
        "credential_usage_events",
        ["credential_id"],
    )
    op.create_index(
        "ix_credential_usage_events_project_provider",
        "credential_usage_events",
        ["project_id", "provider_key"],
    )

    op.create_table(
        "credential_refresh_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("credential_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["credential_id"], ["credentials.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_credential_refresh_events_credential",
        "credential_refresh_events",
        ["credential_id"],
    )
    op.create_index(
        "ix_credential_refresh_events_project_provider",
        "credential_refresh_events",
        ["project_id", "provider_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_credential_refresh_events_project_provider",
        table_name="credential_refresh_events",
    )
    op.drop_index("ix_credential_refresh_events_credential", table_name="credential_refresh_events")
    op.drop_table("credential_refresh_events")
    op.drop_index(
        "ix_credential_usage_events_project_provider",
        table_name="credential_usage_events",
    )
    op.drop_index("ix_credential_usage_events_credential", table_name="credential_usage_events")
    op.drop_table("credential_usage_events")
    op.drop_index("ix_oauth_states_project_provider", table_name="oauth_states")
    op.drop_table("oauth_states")
    op.drop_index("ix_credential_accounts_provider_account", table_name="credential_accounts")
    op.drop_index("ix_credential_accounts_credential", table_name="credential_accounts")
    op.drop_table("credential_accounts")
    op.drop_index("ix_credential_scopes_credential", table_name="credential_scopes")
    op.drop_table("credential_scopes")
    op.drop_index("ix_credentials_project_provider", table_name="credentials")
    op.drop_table("credentials")
    op.drop_index("ix_auth_providers_key", table_name="auth_providers")
    op.drop_index("ix_auth_providers_plugin", table_name="auth_providers")
    op.drop_table("auth_providers")
