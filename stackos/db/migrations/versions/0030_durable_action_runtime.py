"""Persist generic durable action jobs, attempts, and delivery admission leases.

Revision ID: 0030_durable_action_runtime
Revises: 0029_canonical_communication_surface_bindings
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_durable_action_runtime"
down_revision: str | None = "0029_canonical_communication_surface_bindings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "durable_action_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "action_call_id",
            sa.Integer(),
            sa.ForeignKey("action_calls.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id", ondelete="SET NULL")),
        sa.Column("run_plan_id", sa.Integer(), sa.ForeignKey("run_plans.id", ondelete="SET NULL")),
        sa.Column(
            "run_plan_step_id",
            sa.Integer(),
            sa.ForeignKey("run_plan_steps.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "credential_id",
            sa.Integer(),
            sa.ForeignKey("credentials.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("credential_ref", sa.String(length=120), nullable=False),
        sa.Column("action_ref", sa.String(length=300), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160)),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON()),
        sa.Column("pacing_json", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime()),
        sa.Column("paused_at", sa.DateTime()),
        sa.Column("cancelled_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("action_call_id", name="uq_durable_action_jobs_action_call"),
        sa.UniqueConstraint(
            "project_id", "idempotency_key", name="uq_durable_action_jobs_project_idempotency"
        ),
    )
    op.create_index(
        "ix_durable_action_jobs_project_state_due",
        "durable_action_jobs",
        ["project_id", "state", "due_at"],
    )
    op.create_index(
        "ix_durable_action_jobs_credential_state", "durable_action_jobs", ["credential_id", "state"]
    )
    op.create_index(
        "ix_durable_action_jobs_run_plan_step", "durable_action_jobs", ["run_plan_step_id"]
    )

    op.create_table(
        "durable_action_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("durable_action_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("destination_ref", sa.String(length=300), nullable=False),
        sa.Column("correlation_ref", sa.String(length=300), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("next_eligible_at", sa.DateTime(), nullable=False),
        sa.Column("lease_ref", sa.String(length=120)),
        sa.Column("lease_expires_at", sa.DateTime()),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("first_attempted_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("result_json", sa.JSON()),
        sa.Column("error", sa.String()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("job_id", "ordinal", name="uq_durable_action_items_job_ordinal"),
        sa.UniqueConstraint(
            "job_id", "correlation_ref", name="uq_durable_action_items_job_correlation"
        ),
    )
    op.create_index(
        "ix_durable_action_items_job_state_due",
        "durable_action_items",
        ["job_id", "state", "next_eligible_at"],
    )
    op.create_index("ix_durable_action_items_lease", "durable_action_items", ["lease_expires_at"])

    op.create_table(
        "durable_action_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "item_id",
            sa.Integer(),
            sa.ForeignKey("durable_action_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("attempt_ref", sa.String(length=120), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("provider_sending_id", sa.String(length=300)),
        sa.Column("temporary_message_ref", sa.String(length=300)),
        sa.Column("final_message_ref", sa.String(length=300)),
        sa.Column("progress_json", sa.JSON()),
        sa.Column("receipt_json", sa.JSON()),
        sa.Column("error", sa.String()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
        sa.UniqueConstraint("attempt_ref", name="uq_durable_action_attempts_ref"),
        sa.UniqueConstraint("item_id", "ordinal", name="uq_durable_action_attempts_item_ordinal"),
    )
    op.create_index("ix_durable_action_attempts_item", "durable_action_attempts", ["item_id"])
    op.create_index("ix_durable_action_attempts_state", "durable_action_attempts", ["state"])

    op.create_table(
        "action_delivery_admissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "credential_id",
            sa.Integer(),
            sa.ForeignKey("credentials.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("scope_key", sa.String(length=400), nullable=False),
        sa.Column("next_eligible_at", sa.DateTime(), nullable=False),
        sa.Column("lease_ref", sa.String(length=120)),
        sa.Column("lease_expires_at", sa.DateTime()),
        sa.Column("quiesced_at", sa.DateTime()),
        sa.Column("quiesce_reason", sa.String(length=300)),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "credential_id", "scope_key", name="uq_action_delivery_admissions_scope"
        ),
    )
    op.create_index(
        "ix_action_delivery_admissions_lease", "action_delivery_admissions", ["lease_expires_at"]
    )
    op.create_table(
        "durable_action_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("durable_action_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "artifact_id",
            sa.Integer(),
            sa.ForeignKey("artifacts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("artifact_uri", sa.String(length=2048), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("job_id", "artifact_id", name="uq_durable_action_artifacts_job_artifact"),
    )
    op.create_index(
        "ix_durable_action_artifacts_artifact", "durable_action_artifacts", ["artifact_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_durable_action_artifacts_artifact", table_name="durable_action_artifacts")
    op.drop_table("durable_action_artifacts")
    op.drop_index("ix_action_delivery_admissions_lease", table_name="action_delivery_admissions")
    op.drop_table("action_delivery_admissions")
    op.drop_index("ix_durable_action_attempts_state", table_name="durable_action_attempts")
    op.drop_index("ix_durable_action_attempts_item", table_name="durable_action_attempts")
    op.drop_table("durable_action_attempts")
    op.drop_index("ix_durable_action_items_lease", table_name="durable_action_items")
    op.drop_index("ix_durable_action_items_job_state_due", table_name="durable_action_items")
    op.drop_table("durable_action_items")
    op.drop_index("ix_durable_action_jobs_run_plan_step", table_name="durable_action_jobs")
    op.drop_index("ix_durable_action_jobs_credential_state", table_name="durable_action_jobs")
    op.drop_index("ix_durable_action_jobs_project_state_due", table_name="durable_action_jobs")
    op.drop_table("durable_action_jobs")
