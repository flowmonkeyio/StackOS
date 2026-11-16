"""Verified workflow setup receipts and independent permanent reminders.

Revision ID: 0044_project_setup_receipts
Revises: 0043_automations_homes_merge
"""

import sqlalchemy as sa
from alembic import op

revision = "0044_project_setup_receipts"
down_revision = "0043_automations_homes_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {item["name"]: item for item in sa.inspect(bind).get_columns("projects")}
    for name in ("setup_reminder_dismissed_at", "update_reminder_dismissed_at"):
        if name not in columns:
            op.add_column("projects", sa.Column(name, sa.DateTime()))
        elif not isinstance(columns[name]["type"], sa.DateTime) or not columns[name]["nullable"]:
            raise RuntimeError(
                f"Incompatible retained projects.{name}; restore the pre-upgrade backup"
            )
    inspector = sa.inspect(bind)
    if not inspector.has_table("project_setup_receipts"):
        op.create_table(
            "project_setup_receipts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "project_id",
                sa.Integer(),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("workflow_key", sa.String(200), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("successful_json", sa.JSON()),
            sa.Column("latest_result_json", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("project_id", "workflow_key"),
        )
    else:
        # Existing migration recovery can replay from a lost older version stamp.
        # Preserve receipts and dismissals; never synthesize successful setup.
        retained = {c["name"] for c in inspector.get_columns("project_setup_receipts")}
        expected = {
            "id",
            "project_id",
            "workflow_key",
            "revision",
            "successful_json",
            "latest_result_json",
            "updated_at",
        }
        if retained != expected:
            raise RuntimeError(
                "Incompatible retained setup receipts; restore the pre-upgrade backup"
            )
    if "ix_project_setup_receipts_project_id" not in {
        item["name"] for item in sa.inspect(bind).get_indexes("project_setup_receipts")
    }:
        op.create_index(
            "ix_project_setup_receipts_project_id", "project_setup_receipts", ["project_id"]
        )


def downgrade() -> None:
    op.drop_table("project_setup_receipts")
    op.drop_column("projects", "update_reminder_dismissed_at")
    op.drop_column("projects", "setup_reminder_dismissed_at")
