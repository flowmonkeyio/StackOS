"""Persist the daemon-wide task admission preference in the backed-up database."""

import sqlalchemy as sa
from alembic import op

revision = "0042_task_delivery_capacity"
down_revision = "0041_ui804_consolidation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("task_delivery_capacity"):
        # Metadata-based fixtures and a lost version stamp may already contain
        # this table. Preserve the saved preference; never silently adopt an
        # incompatible pre-existing owner.
        columns = inspector.get_columns("task_delivery_capacity")
        checks = inspector.get_check_constraints("task_delivery_capacity")
        if {column["name"] for column in columns} != {"id", "concurrent_tasks"} or {
            check["name"] for check in checks
        } != {"ck_task_delivery_capacity_singleton", "ck_task_delivery_capacity_limit"}:
            raise RuntimeError("Existing task_delivery_capacity schema is incompatible")
        return
    op.create_table(
        "task_delivery_capacity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("concurrent_tasks", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_task_delivery_capacity_singleton"),
        sa.CheckConstraint(
            "concurrent_tasks BETWEEN 1 AND 32", name="ck_task_delivery_capacity_limit"
        ),
    )


def downgrade() -> None:
    op.drop_table("task_delivery_capacity")
