"""Seek project tracker streams by durable event cursor without sorting history.

Revision ID: 0045_tracker_stream_cursor
Revises: 0044_receipt_statistics_index
"""

from alembic import op

revision = "0045_tracker_stream_cursor"
down_revision = "0044_receipt_statistics_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve lost-version-stamp recovery when the index committed earlier.
    op.create_index(
        "ix_project_events_project_cursor",
        "project_events",
        ["project_id", "id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_project_events_project_cursor", table_name="project_events")
