"""Keep removed automation IDs distinct from future schedules and their runs.

Revision ID: 0037_schedule_id_nonreuse
Revises: 0036_workflow_schedules
"""

import sqlalchemy as sa
from alembic import op

revision = "0037_schedule_id_nonreuse"
down_revision = "0036_workflow_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    ddl = bind.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type='table' AND name='scheduled_jobs'")
    ).scalar_one()
    # This table has only outbound FKs, so its rebuild needs no FK suspension.
    # Include DDL and sequence seeding in one SQLite savepoint for safe retry.
    with bind.begin_nested():
        if "AUTOINCREMENT" not in ddl.upper():
            with op.batch_alter_table(
                "scheduled_jobs", recreate="always", table_kwargs={"sqlite_autoincrement": True}
            ):
                pass
        # The prechange allocator forgot deleted IDs. Existing occurrence snapshots
        # retain the ones whose identity still owns historical runs.
        history_max = (
            bind.execute(
                sa.text(
                    "SELECT MAX(json_extract(metadata_json, '$.schedule_id')) FROM agent_requests "
                    "WHERE source_kind = 'schedule' "
                    "AND json_type(metadata_json, '$.schedule_id') = 'integer' "
                    "AND typeof(json_extract(metadata_json, '$.schedule_id')) = 'integer'"
                )
            ).scalar_one()
            or 0
        )
        live_max = bind.execute(sa.text("SELECT MAX(id) FROM scheduled_jobs")).scalar_one() or 0
        previous = bind.execute(
            sa.text("SELECT seq FROM sqlite_sequence WHERE name = 'scheduled_jobs'")
        ).scalar_one_or_none()
        upper_bound = max(previous or 0, live_max, history_max)
        if previous is None:
            bind.execute(
                sa.text("INSERT INTO sqlite_sequence (name, seq) VALUES ('scheduled_jobs', :seq)"),
                {"seq": upper_bound},
            )
        else:
            bind.execute(
                sa.text("UPDATE sqlite_sequence SET seq = :seq WHERE name = 'scheduled_jobs'"),
                {"seq": upper_bound},
            )


def downgrade() -> None:
    # Preserve the stronger allocation invariant and all retained identity references.
    pass
