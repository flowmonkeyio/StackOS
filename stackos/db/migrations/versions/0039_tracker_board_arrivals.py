"""Normalize untouched Board columns once, preserving saved manual order.

Revision ID: 0039_tracker_board_arrivals
Revises: 0038_project_primary_workspace
"""

from alembic import op
from sqlmodel import Session

from stackos.repositories.tracker.ordering import normalize_default_columns

revision = "0039_tracker_board_arrivals"
down_revision = "0038_project_primary_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Flush into Alembic's transaction; never commit an independently owned one.
    with Session(bind=op.get_bind()) as session:
        normalize_default_columns(session)
        session.flush()


def downgrade() -> None:
    # Saved order is operator state. A downgrade must not reverse it.
    pass
