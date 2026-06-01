"""add StackOS project task tracker

Revision ID: 0015_stackos_task_tracker
Revises: 0014_stackos_agent_requests
Create Date: 2026-05-24

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015_stackos_task_tracker"
down_revision: str | None = "0014_stackos_agent_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


tracker_status = sa.Enum(
    "not-started",
    "in-progress",
    "complete",
    "deferred",
    "aborted",
    "failed",
    "skipped",
    name="ck_trackeritemstatus",
    native_enum=False,
    length=64,
)
tracker_source = sa.Enum(
    "manual",
    "workflow",
    "agent-request",
    "external",
    "system",
    name="ck_trackersourcekind",
    native_enum=False,
    length=64,
)
tracker_ticket_kind = sa.Enum(
    "ticket",
    "group",
    name="ck_trackerticketkind",
    native_enum=False,
    length=64,
)
tracker_link_kind = sa.Enum(
    "run-plan",
    "run-plan-step",
    "run",
    "agent-request",
    "resource",
    "artifact",
    "action-call",
    "external",
    name="ck_trackerlinkkind",
    native_enum=False,
    length=64,
)


def upgrade() -> None:
    op.create_table(
        "task_trackers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "key", name="uq_task_trackers_project_key"),
    )
    op.create_index("ix_task_trackers_project", "task_trackers", ["project_id"])

    op.create_table(
        "task_tracker_lanes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tracker_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tracker_id"], ["task_trackers.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tracker_id", "key", name="uq_task_tracker_lanes_tracker_key"),
    )
    op.create_index(
        "ix_task_tracker_lanes_tracker",
        "task_tracker_lanes",
        ["tracker_id", "position"],
    )

    op.create_table(
        "task_tracker_priorities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tracker_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tracker_id"], ["task_trackers.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "tracker_id",
            "key",
            name="uq_task_tracker_priorities_tracker_key",
        ),
    )
    op.create_index(
        "ix_task_tracker_priorities_tracker",
        "task_tracker_priorities",
        ["tracker_id", "position"],
    )

    op.create_table(
        "tracker_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tracker_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", tracker_status, nullable=False),
        sa.Column("priority_key", sa.String(length=40), nullable=False, server_default="p2"),
        sa.Column(
            "lane_key",
            sa.String(length=80),
            nullable=False,
            server_default="implementation",
        ),
        sa.Column("owner", sa.String(length=120), nullable=True),
        sa.Column("task_type", sa.String(length=80), nullable=False, server_default="task"),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_kind", tracker_source, nullable=False),
        sa.Column("source_json", sa.JSON(), nullable=True),
        sa.Column("definition_of_done_json", sa.JSON(), nullable=False),
        sa.Column("constraints_json", sa.JSON(), nullable=False),
        sa.Column("expected_outcomes_json", sa.JSON(), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["tracker_id"], ["task_trackers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tracker_id", "key", name="uq_tracker_tasks_tracker_key"),
    )
    op.create_index("ix_tracker_tasks_project_status", "tracker_tasks", ["project_id", "status"])
    op.create_index("ix_tracker_tasks_project_lane", "tracker_tasks", ["project_id", "lane_key"])
    op.create_index(
        "ix_tracker_tasks_project_priority",
        "tracker_tasks",
        ["project_id", "priority_key"],
    )
    op.create_index(
        "ix_tracker_tasks_project_source",
        "tracker_tasks",
        ["project_id", "source_kind"],
    )
    op.create_index(
        "ix_tracker_tasks_tracker_position",
        "tracker_tasks",
        ["tracker_id", "order_index"],
    )

    op.create_table(
        "tracker_tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tracker_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("parent_ticket_id", sa.Integer(), nullable=True),
        sa.Column("run_plan_id", sa.Integer(), nullable=True),
        sa.Column("run_plan_step_id", sa.Integer(), nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("agent_request_id", sa.Integer(), nullable=True),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", tracker_status, nullable=False),
        sa.Column("kind", tracker_ticket_kind, nullable=False),
        sa.Column("assignee", sa.String(length=120), nullable=True),
        sa.Column("priority_key", sa.String(length=40), nullable=False, server_default="p2"),
        sa.Column(
            "lane_key",
            sa.String(length=80),
            nullable=False,
            server_default="implementation",
        ),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocker_reason", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("effort", sa.String(length=40), nullable=True),
        sa.Column("source_kind", tracker_source, nullable=False),
        sa.Column("source_json", sa.JSON(), nullable=True),
        sa.Column("definition_of_done_json", sa.JSON(), nullable=False),
        sa.Column("constraints_json", sa.JSON(), nullable=False),
        sa.Column("expected_changes_json", sa.JSON(), nullable=False),
        sa.Column("allowed_paths_json", sa.JSON(), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["tracker_id"], ["task_trackers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tracker_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_ticket_id"],
            ["tracker_tickets.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["run_plan_id"], ["run_plans.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["run_plan_step_id"],
            ["run_plan_steps.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["agent_request_id"], ["agent_requests.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("tracker_id", "key", name="uq_tracker_tickets_tracker_key"),
    )
    op.create_index(
        "ix_tracker_tickets_project_status",
        "tracker_tickets",
        ["project_id", "status"],
    )
    op.create_index("ix_tracker_tickets_project_task", "tracker_tickets", ["project_id", "task_id"])
    op.create_index("ix_tracker_tickets_project_lane", "tracker_tickets", ["project_id", "lane_key"])
    op.create_index(
        "ix_tracker_tickets_project_priority",
        "tracker_tickets",
        ["project_id", "priority_key"],
    )
    op.create_index(
        "ix_tracker_tickets_project_assignee",
        "tracker_tickets",
        ["project_id", "assignee"],
    )
    op.create_index("ix_tracker_tickets_parent", "tracker_tickets", ["parent_ticket_id"])
    op.create_index("ix_tracker_tickets_run_plan", "tracker_tickets", ["run_plan_id"])
    op.create_index("ix_tracker_tickets_step", "tracker_tickets", ["run_plan_step_id"])

    op.create_table(
        "tracker_ticket_dependencies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tracker_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("depends_on_ticket_id", sa.Integer(), nullable=False),
        sa.Column("dependency_type", sa.String(length=80), nullable=False, server_default="blocks"),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tracker_id"], ["task_trackers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tracker_tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["depends_on_ticket_id"],
            ["tracker_tickets.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "ticket_id",
            "depends_on_ticket_id",
            name="uq_tracker_ticket_dependencies_pair",
        ),
    )
    op.create_index(
        "ix_tracker_ticket_dependencies_project",
        "tracker_ticket_dependencies",
        ["project_id"],
    )
    op.create_index(
        "ix_tracker_ticket_dependencies_ticket",
        "tracker_ticket_dependencies",
        ["ticket_id"],
    )
    op.create_index(
        "ix_tracker_ticket_dependencies_depends_on",
        "tracker_ticket_dependencies",
        ["depends_on_ticket_id"],
    )

    op.create_table(
        "tracker_ticket_references",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tracker_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("ref_type", sa.String(length=80), nullable=False),
        sa.Column("ref", sa.String(length=1000), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tracker_id"], ["task_trackers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tracker_tickets.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_tracker_ticket_references_project",
        "tracker_ticket_references",
        ["project_id"],
    )
    op.create_index(
        "ix_tracker_ticket_references_ticket",
        "tracker_ticket_references",
        ["ticket_id"],
    )
    op.create_index(
        "ix_tracker_ticket_references_ref",
        "tracker_ticket_references",
        ["ref_type", "ref"],
    )

    op.create_table(
        "tracker_ticket_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tracker_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("ticket_id", sa.Integer(), nullable=True),
        sa.Column("link_kind", tracker_link_kind, nullable=False),
        sa.Column("ref", sa.String(length=1000), nullable=True),
        sa.Column("run_plan_id", sa.Integer(), nullable=True),
        sa.Column("run_plan_step_id", sa.Integer(), nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("agent_request_id", sa.Integer(), nullable=True),
        sa.Column("resource_record_id", sa.Integer(), nullable=True),
        sa.Column("artifact_id", sa.Integer(), nullable=True),
        sa.Column("action_call_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tracker_id"], ["task_trackers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tracker_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tracker_tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_plan_id"], ["run_plans.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["run_plan_step_id"],
            ["run_plan_steps.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["agent_request_id"], ["agent_requests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["resource_record_id"],
            ["resource_records.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["action_call_id"], ["action_calls.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_tracker_ticket_links_project", "tracker_ticket_links", ["project_id"])
    op.create_index("ix_tracker_ticket_links_task", "tracker_ticket_links", ["task_id"])
    op.create_index("ix_tracker_ticket_links_ticket", "tracker_ticket_links", ["ticket_id"])
    op.create_index("ix_tracker_ticket_links_kind", "tracker_ticket_links", ["link_kind"])
    op.create_index(
        "ix_tracker_ticket_links_run_plan",
        "tracker_ticket_links",
        ["run_plan_id"],
    )
    op.create_index(
        "ix_tracker_ticket_links_run_plan_step",
        "tracker_ticket_links",
        ["run_plan_step_id"],
    )
    op.create_index(
        "ix_tracker_ticket_links_agent_request",
        "tracker_ticket_links",
        ["agent_request_id"],
    )

    op.create_table(
        "tracker_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tracker_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=True),
        sa.Column("change_kind", sa.String(length=80), nullable=False),
        sa.Column("entity_kind", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("entity_key", sa.String(length=200), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.Column("patch_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tracker_id"], ["task_trackers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tracker_id", "rev", name="uq_tracker_revisions_tracker_rev"),
    )
    op.create_index("ix_tracker_revisions_project", "tracker_revisions", ["project_id", "created_at"])
    op.create_index("ix_tracker_revisions_entity", "tracker_revisions", ["entity_kind", "entity_id"])

    op.create_table(
        "tracker_tombstones",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tracker_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("entity_kind", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("entity_key", sa.String(length=200), nullable=False),
        sa.Column("deleted_by", sa.String(length=120), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("snapshot_json", sa.JSON(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tracker_id"], ["task_trackers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "tracker_id",
            "entity_kind",
            "entity_key",
            name="uq_tracker_tombstones_tracker_entity",
        ),
    )
    op.create_index(
        "ix_tracker_tombstones_project",
        "tracker_tombstones",
        ["project_id", "deleted_at"],
    )


def downgrade() -> None:
    # Downgrade removes only the tables this revision owns.
    op.drop_index("ix_tracker_tombstones_project", table_name="tracker_tombstones")
    op.drop_table("tracker_tombstones")
    op.drop_index("ix_tracker_revisions_entity", table_name="tracker_revisions")
    op.drop_index("ix_tracker_revisions_project", table_name="tracker_revisions")
    op.drop_table("tracker_revisions")
    op.drop_index("ix_tracker_ticket_links_agent_request", table_name="tracker_ticket_links")
    op.drop_index("ix_tracker_ticket_links_run_plan_step", table_name="tracker_ticket_links")
    op.drop_index("ix_tracker_ticket_links_run_plan", table_name="tracker_ticket_links")
    op.drop_index("ix_tracker_ticket_links_kind", table_name="tracker_ticket_links")
    op.drop_index("ix_tracker_ticket_links_ticket", table_name="tracker_ticket_links")
    op.drop_index("ix_tracker_ticket_links_task", table_name="tracker_ticket_links")
    op.drop_index("ix_tracker_ticket_links_project", table_name="tracker_ticket_links")
    op.drop_table("tracker_ticket_links")
    op.drop_index("ix_tracker_ticket_references_ref", table_name="tracker_ticket_references")
    op.drop_index("ix_tracker_ticket_references_ticket", table_name="tracker_ticket_references")
    op.drop_index("ix_tracker_ticket_references_project", table_name="tracker_ticket_references")
    op.drop_table("tracker_ticket_references")
    op.drop_index(
        "ix_tracker_ticket_dependencies_depends_on",
        table_name="tracker_ticket_dependencies",
    )
    op.drop_index("ix_tracker_ticket_dependencies_ticket", table_name="tracker_ticket_dependencies")
    op.drop_index("ix_tracker_ticket_dependencies_project", table_name="tracker_ticket_dependencies")
    op.drop_table("tracker_ticket_dependencies")
    op.drop_index("ix_tracker_tickets_step", table_name="tracker_tickets")
    op.drop_index("ix_tracker_tickets_run_plan", table_name="tracker_tickets")
    op.drop_index("ix_tracker_tickets_parent", table_name="tracker_tickets")
    op.drop_index("ix_tracker_tickets_project_assignee", table_name="tracker_tickets")
    op.drop_index("ix_tracker_tickets_project_priority", table_name="tracker_tickets")
    op.drop_index("ix_tracker_tickets_project_lane", table_name="tracker_tickets")
    op.drop_index("ix_tracker_tickets_project_task", table_name="tracker_tickets")
    op.drop_index("ix_tracker_tickets_project_status", table_name="tracker_tickets")
    op.drop_table("tracker_tickets")
    op.drop_index("ix_tracker_tasks_tracker_position", table_name="tracker_tasks")
    op.drop_index("ix_tracker_tasks_project_source", table_name="tracker_tasks")
    op.drop_index("ix_tracker_tasks_project_priority", table_name="tracker_tasks")
    op.drop_index("ix_tracker_tasks_project_lane", table_name="tracker_tasks")
    op.drop_index("ix_tracker_tasks_project_status", table_name="tracker_tasks")
    op.drop_table("tracker_tasks")
    op.drop_index("ix_task_tracker_priorities_tracker", table_name="task_tracker_priorities")
    op.drop_table("task_tracker_priorities")
    op.drop_index("ix_task_tracker_lanes_tracker", table_name="task_tracker_lanes")
    op.drop_table("task_tracker_lanes")
    op.drop_index("ix_task_trackers_project", table_name="task_trackers")
    op.drop_table("task_trackers")
