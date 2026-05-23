"""add StackOS project memory and context tables

Revision ID: 0009_stackos_project_memory
Revises: 0008_stackos_auth_providers
Create Date: 2026-05-20

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_stackos_project_memory"
down_revision: str | None = "0008_stackos_auth_providers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_project_events_project_occurred",
        "project_events",
        ["project_id", "occurred_at"],
    )
    op.create_index("ix_project_events_source", "project_events", ["source_type", "source_id"])

    op.create_table(
        "context_index_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("domain", sa.String(length=120), nullable=True),
        sa.Column("provider_key", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=80), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_context_index_project_source",
        "context_index_entries",
        ["project_id", "source_type", "source_id"],
    )
    op.create_index(
        "ix_context_index_project_occurred",
        "context_index_entries",
        ["project_id", "occurred_at"],
    )
    op.create_index(
        "ix_context_index_domain_status",
        "context_index_entries",
        ["domain", "status"],
    )

    op.create_table(
        "context_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=300), nullable=True),
        sa.Column("query_json", sa.JSON(), nullable=False),
        sa.Column("selected_sources_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_context_snapshots_project", "context_snapshots", ["project_id"])
    op.create_index("ix_context_snapshots_run", "context_snapshots", ["run_id"])

    op.create_table(
        "learnings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("source_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("supersedes_learning_id", sa.Integer(), nullable=True),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=120), nullable=True),
        sa.Column("confidence", sa.String(length=40), nullable=False, server_default="unknown"),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="active"),
        sa.Column("review_state", sa.String(length=40), nullable=False, server_default="proposed"),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("applies_to_json", sa.JSON(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"],
            ["context_snapshots.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_learning_id"],
            ["learnings.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_learnings_project_status",
        "learnings",
        ["project_id", "status", "review_state"],
    )
    op.create_index("ix_learnings_domain", "learnings", ["domain"])

    op.create_table(
        "experiments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=160), nullable=True),
        sa.Column("name", sa.String(length=300), nullable=True),
        sa.Column("domain", sa.String(length=120), nullable=True),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=60), nullable=False, server_default="planned"),
        sa.Column("linked_template_ids_json", sa.JSON(), nullable=True),
        sa.Column("linked_run_ids_json", sa.JSON(), nullable=True),
        sa.Column("metric_targets_json", sa.JSON(), nullable=True),
        sa.Column("decision_policy_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "key", name="uq_experiments_project_key"),
    )
    op.create_index(
        "ix_experiments_project_status",
        "experiments",
        ["project_id", "status"],
    )
    op.create_index("ix_experiments_domain", "experiments", ["domain"])

    op.create_table(
        "experiment_variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("experiment_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=True),
        sa.Column("resources_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("experiment_id", "key", name="uq_experiment_variants_key"),
    )
    op.create_index(
        "ix_experiment_variants_experiment",
        "experiment_variants",
        ["experiment_id"],
    )

    op.create_table(
        "experiment_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("variant_key", sa.String(length=160), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_experiment_observations_experiment",
        "experiment_observations",
        ["experiment_id", "observed_at"],
    )
    op.create_index(
        "ix_experiment_observations_project",
        "experiment_observations",
        ["project_id", "observed_at"],
    )

    op.create_table(
        "decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=60), nullable=False, server_default="recorded"),
        sa.Column("decided_by", sa.String(length=120), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_decisions_project", "decisions", ["project_id", "created_at"])
    op.create_index("ix_decisions_experiment", "decisions", ["experiment_id"])

    op.create_table(
        "metric_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("metric_key", sa.String(length=160), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=True),
        sa.Column("dimensions_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_metric_snapshots_project_metric",
        "metric_snapshots",
        ["project_id", "metric_key", "captured_at"],
    )
    op.create_index("ix_metric_snapshots_source", "metric_snapshots", ["source_type", "source_id"])


def downgrade() -> None:
    op.drop_index("ix_metric_snapshots_source", table_name="metric_snapshots")
    op.drop_index("ix_metric_snapshots_project_metric", table_name="metric_snapshots")
    op.drop_table("metric_snapshots")
    op.drop_index("ix_decisions_experiment", table_name="decisions")
    op.drop_index("ix_decisions_project", table_name="decisions")
    op.drop_table("decisions")
    op.drop_index("ix_experiment_observations_project", table_name="experiment_observations")
    op.drop_index("ix_experiment_observations_experiment", table_name="experiment_observations")
    op.drop_table("experiment_observations")
    op.drop_index("ix_experiment_variants_experiment", table_name="experiment_variants")
    op.drop_table("experiment_variants")
    op.drop_index("ix_experiments_domain", table_name="experiments")
    op.drop_index("ix_experiments_project_status", table_name="experiments")
    op.drop_table("experiments")
    op.drop_index("ix_learnings_domain", table_name="learnings")
    op.drop_index("ix_learnings_project_status", table_name="learnings")
    op.drop_table("learnings")
    op.drop_index("ix_context_snapshots_run", table_name="context_snapshots")
    op.drop_index("ix_context_snapshots_project", table_name="context_snapshots")
    op.drop_table("context_snapshots")
    op.drop_index("ix_context_index_domain_status", table_name="context_index_entries")
    op.drop_index("ix_context_index_project_occurred", table_name="context_index_entries")
    op.drop_index("ix_context_index_project_source", table_name="context_index_entries")
    op.drop_table("context_index_entries")
    op.drop_index("ix_project_events_source", table_name="project_events")
    op.drop_index("ix_project_events_project_occurred", table_name="project_events")
    op.drop_table("project_events")
