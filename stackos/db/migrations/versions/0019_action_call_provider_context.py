"""Add provider context to action call audit.

Revision ID: 0019_action_call_provider_context
Revises: 0018_workflow_template_extension_overrides
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0019_action_call_provider_context"
down_revision: str | None = "0018_workflow_template_extension_overrides"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("action_calls", sa.Column("provider_context_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("action_calls", "provider_context_json")
