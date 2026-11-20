"""Move active schedule setup to goal plus optional workflow, preserving history.

Revision ID: 0042_schedule_main_thread
Revises: 0041_ui804_consolidation
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0042_schedule_main_thread"
down_revision = "0041_ui804_consolidation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("scheduled_jobs")}
    # Schema and data change atomically; replay never transforms new configuration.
    with bind.begin_nested():
        if "goal" in columns:
            return
        op.add_column("scheduled_jobs", sa.Column("goal", sa.String(), nullable=True))
        schedules = sa.table(
            "scheduled_jobs",
            sa.column("id"),
            sa.column("project_id"),
            sa.column("inputs_json", sa.JSON()),
            sa.column("execution_json", sa.JSON()),
            sa.column("goal"),
            sa.column("enabled"),
            sa.column("next_run_at"),
        )
        for row in bind.execute(sa.select(schedules)).mappings().all():
            inputs = row["inputs_json"] or {}
            goal = next(
                (
                    inputs.get(key).strip()
                    for key in ("goal", "instructions")
                    if isinstance(inputs.get(key), str) and inputs[key].strip()
                ),
                None,
            )
            old = row["execution_json"] or {}
            execution = {
                key: old[key] for key in ("project_dir", "host_key", "mcp_config_ref") if key in old
            }
            # Keep context and all admitted/historical request snapshots untouched.
            root = bind.execute(
                sa.text(
                    "SELECT w.last_known_root FROM projects p JOIN workspace_bindings w "
                    "ON p.primary_workspace_binding_id=w.id AND w.project_id=p.id WHERE p.id=:id"
                ),
                {"id": row["project_id"]},
            ).scalar()
            saved_root = execution.get("project_dir")
            try:
                ready = bool(
                    goal
                    and root
                    and Path(root).is_dir()
                    and (
                        not saved_root
                        or Path(saved_root).expanduser().resolve() == Path(root).resolve()
                    )
                )
            except (OSError, ValueError, RuntimeError):
                ready = False
            values = {"goal": goal, "execution_json": execution or None}
            if not ready:
                values.update(enabled=False, next_run_at=None)
            bind.execute(sa.update(schedules).where(schedules.c.id == row["id"]).values(**values))


def downgrade() -> None:
    # Replaced orchestration is not recreated by a rollback.
    op.execute(sa.text("UPDATE scheduled_jobs SET enabled=0, next_run_at=NULL"))
    op.drop_column("scheduled_jobs", "goal")
