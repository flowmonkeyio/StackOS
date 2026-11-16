"""Move the saved legacy preference to each existing permanent project ID."""

import sqlalchemy as sa
from alembic import op

revision = "0049_project_task_capacity"
down_revision = "0048_product_receipts_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = {column["name"] for column in inspector.get_columns("task_delivery_capacity")}
    if columns == {"project_id", "concurrent_tasks"}:
        return  # Current metadata adoption or completed DDL with a lost stamp.
    if columns != {"id", "concurrent_tasks"}:
        raise RuntimeError("Existing task_delivery_capacity schema is incompatible")
    # Explicit savepoint keeps SQLite DDL and the legacy copy atomic even when
    # its driver has not begun a transaction for SELECT/CREATE statements.
    with connection.begin_nested():
        op.create_table(
            "project_task_capacity_migration",
            sa.Column(
                "project_id",
                sa.Integer(),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("concurrent_tasks", sa.Integer(), nullable=False),
            sa.CheckConstraint(
                "concurrent_tasks BETWEEN 1 AND 32", name="ck_task_delivery_capacity_limit"
            ),
        )
        connection.execute(
            sa.text(
                "INSERT INTO project_task_capacity_migration (project_id, concurrent_tasks) "
                "SELECT id, COALESCE((SELECT concurrent_tasks "
                "FROM task_delivery_capacity WHERE id=1), 3) "
                "FROM projects"
            )
        )
        op.drop_table("task_delivery_capacity")
        op.rename_table("project_task_capacity_migration", "task_delivery_capacity")


def downgrade() -> None:
    connection = op.get_bind()
    limits = (
        connection.execute(
            sa.text(
                "SELECT DISTINCT COALESCE(c.concurrent_tasks, 3) FROM projects p "
                "LEFT JOIN task_delivery_capacity c ON c.project_id=p.id"
            )
        )
        .scalars()
        .all()
    )
    if len(limits) > 1:
        raise RuntimeError(
            "Independent project limits cannot be collapsed without losing preferences"
        )
    with connection.begin_nested():
        op.create_table(
            "legacy_task_capacity_migration",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("concurrent_tasks", sa.Integer(), nullable=False),
            sa.CheckConstraint("id = 1", name="ck_task_delivery_capacity_singleton"),
            sa.CheckConstraint(
                "concurrent_tasks BETWEEN 1 AND 32", name="ck_task_delivery_capacity_limit"
            ),
        )
        if limits:
            connection.execute(
                sa.text("INSERT INTO legacy_task_capacity_migration VALUES (1, :limit)"),
                {"limit": limits[0]},
            )
        op.drop_table("task_delivery_capacity")
        op.rename_table("legacy_task_capacity_migration", "task_delivery_capacity")
