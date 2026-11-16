"""Index only canonical receipt statistics inputs for bounded reads.

Revision ID: 0044_receipt_statistics_index
Revises: 0043_automations_homes_merge
"""

from alembic import op

revision = "0044_receipt_statistics_index"
down_revision = "0043_automations_homes_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from stackos.repositories.tracker.receipt_projection import initialize_receipt_projection

    # One upgrade pass, never an agent read-time full-history backfill.
    initialize_receipt_projection(op.get_bind())


def downgrade() -> None:
    op.drop_table("tracker_receipt_projections")
