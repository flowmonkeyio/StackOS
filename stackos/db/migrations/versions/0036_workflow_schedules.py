"""Add explicit workflow schedule setup; legacy metadata stays inert.

Revision ID: 0036_workflow_schedules
Revises: 0035_generic_task_delivery
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_workflow_schedules"
down_revision: str | None = "0035_generic_task_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    # A completed rebuild may precede the version stamp. Never disarm explicitly
    # configured schedules on a replay after that interruption.
    columns = {column["name"] for column in sa.inspect(bind).get_columns("scheduled_jobs")}
    if "last_request_id" in columns:
        return
    # SQLite otherwise creates the batch's temporary table outside its first DML
    # transaction. A failed rebuild then leaves that table behind and blocks retry.
    with bind.begin_nested():
        with op.batch_alter_table("scheduled_jobs") as batch:
            batch.add_column(sa.Column("name", sa.String(200), nullable=True))
            batch.add_column(sa.Column("timezone", sa.String(100), nullable=True))
            batch.add_column(sa.Column("template_key", sa.String(200), nullable=True))
            batch.add_column(sa.Column("inputs_json", sa.JSON(), nullable=True))
            batch.add_column(sa.Column("execution_json", sa.JSON(), nullable=True))
            batch.add_column(sa.Column("last_request_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_scheduled_jobs_last_request",
                "agent_requests",
                ["last_request_id"],
                ["id"],
                ondelete="SET NULL",
            )
        op.execute(sa.text("UPDATE scheduled_jobs SET enabled = 0, next_run_at = NULL"))


def downgrade() -> None:
    with op.batch_alter_table("scheduled_jobs") as batch:
        batch.drop_constraint("fk_scheduled_jobs_last_request", type_="foreignkey")
        for name in (
            "last_request_id",
            "execution_json",
            "inputs_json",
            "template_key",
            "timezone",
            "name",
        ):
            batch.drop_column(name)
