"""Join automations and home-page migrations without rewriting either history.

Revision ID: 0043_automations_homes_merge
Revises: 0042_schedule_main_thread, 0042_task_delivery_capacity
"""

revision = "0043_automations_homes_merge"
down_revision = ("0042_schedule_main_thread", "0042_task_delivery_capacity")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
