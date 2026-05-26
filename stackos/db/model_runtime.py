"""SQLModel table declarations for agent requests, workspace sessions, runs, and jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from stackos.db.model_base import _enum_column, _utcnow
from stackos.db.model_enums import (
    AgentRequestAttentionStatus,
    AgentRequestStatus,
    RunKind,
    RunStatus,
    RunStepStatus,
)


class AgentRequest(SQLModel, table=True):
    """Generic project inbox item that agents can claim and turn into runs.

    Provider plugins may feed this queue through trusted ingestion or granted
    run-plan steps. The table remains core and provider-agnostic so Telegram,
    IMAP, webhooks, schedules, CI events, and future triggers share one claim
    contract instead of inventing provider-specific queues.
    """

    __tablename__ = "agent_requests"
    __table_args__ = (
        UniqueConstraint("project_id", "request_key", name="uq_agent_requests_project_key"),
        Index("ix_agent_requests_project_status", "project_id", "status"),
        Index("ix_agent_requests_project_attention", "project_id", "attention_status"),
        Index("ix_agent_requests_project_created", "project_id", "created_at"),
        Index("ix_agent_requests_claim", "status", "claim_expires_at"),
        Index("ix_agent_requests_source_record", "source_resource_record_id"),
        Index("ix_agent_requests_run_plan", "run_plan_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    request_key: str = Field(max_length=200)
    title: str = Field(max_length=300)
    body_preview: str = Field(default="", sa_column=Column(Text, nullable=False))
    source_provider: str | None = Field(default=None, max_length=160)
    source_kind: str | None = Field(default=None, max_length=120)
    source_resource_key: str | None = Field(default=None, max_length=160)
    source_resource_record_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("resource_records.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    source_message_ref: str | None = Field(default=None, max_length=300)
    priority: int = Field(default=0, nullable=False)
    status: AgentRequestStatus = Field(sa_column=_enum_column(AgentRequestStatus))
    attention_status: AgentRequestAttentionStatus = Field(
        sa_column=_enum_column(AgentRequestAttentionStatus)
    )
    claimed_by: str | None = Field(default=None, max_length=120)
    claim_token_hash: str | None = Field(default=None, max_length=128)
    claimed_at: datetime | None = Field(default=None)
    claim_expires_at: datetime | None = Field(default=None)
    run_plan_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("run_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    completed_at: datetime | None = Field(default=None)
    ignored_at: datetime | None = Field(default=None)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class WorkspaceBinding(SQLModel, table=True):
    """Daemon-owned mapping from an external repo/workspace to a project.

    Plugin-provided MCP bridges run from arbitrary site repositories. They send
    repo fingerprints and framework hints to the singleton daemon; this table
    is the durable, non-invasive binding back to ``projects``. No required
    ``.env`` / ``.mcp.json`` / repo-local StackOS file is needed.
    """

    __tablename__ = "workspace_bindings"
    __table_args__ = (
        Index("ix_workspace_bindings_project", "project_id"),
        Index("ix_workspace_bindings_git_remote", "git_remote_url"),
        UniqueConstraint("repo_fingerprint", name="uq_workspace_bindings_fingerprint"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    repo_fingerprint: str = Field(max_length=128)
    git_remote_url: str | None = Field(default=None, max_length=500)
    normalized_repo_name: str | None = Field(default=None, max_length=200)
    last_known_root: str | None = Field(default=None, max_length=1000)
    framework: str | None = Field(default=None, max_length=120)
    content_model_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)
    last_seen_at: datetime | None = Field(default=None)


class AgentSession(SQLModel, table=True):
    """Ephemeral-ish record for a plugin MCP bridge connected to the daemon."""

    __tablename__ = "agent_sessions"
    __table_args__ = (
        Index("ix_agent_sessions_project", "project_id"),
        Index("ix_agent_sessions_fingerprint", "repo_fingerprint"),
        Index("ix_agent_sessions_last_seen", "last_seen_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    workspace_binding_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("workspace_bindings.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    runtime: str = Field(default="unknown", max_length=40)
    cwd: str | None = Field(default=None, max_length=1000)
    repo_fingerprint: str | None = Field(default=None, max_length=128)
    git_remote_url: str | None = Field(default=None, max_length=500)
    thread_id: str | None = Field(default=None, max_length=160)
    client_session_id: str | None = Field(default=None, max_length=160)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    last_seen_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Run(SQLModel, table=True):
    """Top-level pipeline audit (PLAN.md L374).

    ``parent_run_id`` enables ``run.children`` / cascade abort; ``heartbeat_at``
    is the daemon-restart-orphan signal.
    """

    __tablename__ = "runs"
    __table_args__ = (
        # Primary look-ups per PLAN.md L472-L474.
        Index("idx_runs_project_started", "project_id", "started_at"),
        Index("idx_runs_parent", "parent_run_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    kind: RunKind = Field(sa_column=_enum_column(RunKind))
    parent_run_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    client_session_id: str | None = Field(default=None, max_length=120)
    started_at: datetime = Field(default_factory=_utcnow, nullable=False)
    ended_at: datetime | None = Field(default=None)
    status: RunStatus = Field(sa_column=_enum_column(RunStatus))
    error: str | None = Field(default=None)
    heartbeat_at: datetime | None = Field(default=None)
    last_step: str | None = Field(default=None, max_length=120)
    last_step_at: datetime | None = Field(default=None)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))


class RunStep(SQLModel, table=True):
    """Per-skill audit grain (PLAN.md L376).

    ``cost_cents`` is the cost-of-truth (PLAN.md L376); ``runs.metadata_json.cost``
    is denormalised for fast UI display.
    """

    __tablename__ = "run_steps"
    __table_args__ = (Index("idx_run_steps_run", "run_id", "step_index"),)

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(
        sa_column=Column(
            ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    step_index: int = Field(nullable=False)
    skill_name: str = Field(max_length=120)
    started_at: datetime | None = Field(default=None)
    ended_at: datetime | None = Field(default=None)
    status: RunStepStatus = Field(sa_column=_enum_column(RunStepStatus))
    input_snapshot_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    output_snapshot_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = Field(default=None)
    cost_cents: int = Field(default=0)
    integration_calls_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))


class RunStepCall(SQLModel, table=True):
    """Per-MCP-call audit grain inside a skill step (PLAN.md L377)."""

    __tablename__ = "run_step_calls"
    __table_args__ = (Index("idx_run_step_calls_step", "run_step_id"),)

    id: int | None = Field(default=None, primary_key=True)
    run_step_id: int = Field(
        sa_column=Column(
            ForeignKey("run_steps.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    mcp_tool: str = Field(max_length=120)
    request_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    response_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    duration_ms: int | None = Field(default=None)
    error: str | None = Field(default=None)
    cost_cents: int = Field(default=0)


class IdempotencyKey(SQLModel, table=True):
    """Mutating-tool dedup (PLAN.md L378).

    UNIQUE ``(project_id, tool_name, idempotency_key)``; replays within the
    24 h window short-circuit to ``response_json``.
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "tool_name",
            "idempotency_key",
            name="uq_idempotency",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    tool_name: str = Field(max_length=120)
    idempotency_key: str = Field(max_length=120)
    run_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    response_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ScheduledJob(SQLModel, table=True):
    """Per-project schedules (PLAN.md L379)."""

    __tablename__ = "scheduled_jobs"
    __table_args__ = (Index("ix_scheduled_jobs_project", "project_id"),)

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    kind: str = Field(max_length=120)
    cron_expr: str = Field(max_length=120)
    next_run_at: datetime | None = Field(default=None)
    last_run_at: datetime | None = Field(default=None)
    last_run_status: str | None = Field(default=None, max_length=32)
    enabled: bool = Field(default=True)


__all__ = [
    "AgentRequest",
    "AgentSession",
    "IdempotencyKey",
    "Run",
    "RunStep",
    "RunStepCall",
    "ScheduledJob",
    "WorkspaceBinding",
]
