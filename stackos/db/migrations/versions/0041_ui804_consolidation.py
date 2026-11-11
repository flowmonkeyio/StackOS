"""Join project management and UI804 migrations without rewriting either history.

Revision ID: 0041_ui804_consolidation
Revises: 0040_artifact_managed_files, 0039_tracker_board_arrivals
"""

revision = "0041_ui804_consolidation"
down_revision = ("0040_artifact_managed_files", "0039_tracker_board_arrivals")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
