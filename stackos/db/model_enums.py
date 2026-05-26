"""Enum and lifecycle transition declarations for StackOS DB models."""

from __future__ import annotations

import enum


class RunKind(enum.StrEnum):
    """Persists to ``runs.kind`` for StackOS execution audit rows."""

    RUN_PLAN = "run-plan"
    SKILL_RUN = "skill-run"
    ACTION = "action"
    SCHEDULED_JOB = "scheduled-job"
    MAINTENANCE = "maintenance"


class RunStatus(enum.StrEnum):
    """Persists to ``runs.status`` per PLAN.md L390."""

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    ABORTED = "aborted"


class RunStepStatus(enum.StrEnum):
    """Persists to ``run_steps.status`` per PLAN.md L400."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class RunPlanStatus(enum.StrEnum):
    """Persists to ``run_plans.status`` for StackOS run-plan lifecycle."""

    DRAFT = "draft"
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class RunPlanStepStatus(enum.StrEnum):
    """Persists to ``run_plan_steps.status`` for agent-owned run plans."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class ApprovalRequestStatus(enum.StrEnum):
    """Persists to ``approval_requests.status`` for explicit gates."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ActionCallStatus(enum.StrEnum):
    """Persists to ``action_calls.status`` for generic action execution audit."""

    DRY_RUN = "dry-run"
    SUCCESS = "success"
    FAILED = "failed"


class AgentRequestStatus(enum.StrEnum):
    """Persists to ``agent_requests.status`` for the generic agent inbox."""

    NEW = "new"
    CLAIMED = "claimed"
    RUN_CREATED = "run-created"
    RUN_STARTED = "run-started"
    RESPONDED = "responded"
    RESOLVED = "resolved"
    IGNORED = "ignored"
    FAILED = "failed"


class AgentRequestAttentionStatus(enum.StrEnum):
    """Persists to ``agent_requests.attention_status`` for local inbox state."""

    UNREAD = "unread"
    READ = "read"
    ARCHIVED = "archived"


class PluginSource(enum.StrEnum):
    """Persists to ``plugins.source`` for StackOS catalog ownership."""

    BUILTIN = "builtin"
    REPO = "repo"
    PROJECT = "project"
    USER = "user"


class TrackerItemStatus(enum.StrEnum):
    """Persists to tracker task/ticket status columns."""

    NOT_STARTED = "not-started"
    IN_PROGRESS = "in-progress"
    COMPLETE = "complete"
    DEFERRED = "deferred"


class TrackerTicketKind(enum.StrEnum):
    """Persists to ``tracker_tickets.kind`` for graph rendering."""

    TICKET = "ticket"
    GROUP = "group"


class TrackerSourceKind(enum.StrEnum):
    """Persists to tracker source-kind columns."""

    MANUAL = "manual"
    WORKFLOW = "workflow"
    AGENT_REQUEST = "agent-request"
    EXTERNAL = "external"
    SYSTEM = "system"


class TrackerLinkKind(enum.StrEnum):
    """Persists to ``tracker_ticket_links.link_kind``."""

    RUN_PLAN = "run-plan"
    RUN_PLAN_STEP = "run-plan-step"
    RUN = "run"
    AGENT_REQUEST = "agent-request"
    RESOURCE = "resource"
    ARTIFACT = "artifact"
    ACTION_CALL = "action-call"
    EXTERNAL = "external"


# Run lifecycle. PLAN.md L390. ``running`` is the only entry; the three
# terminal states are mutually exclusive and not re-enterable (a new run row
# is created instead).
RUN_STATUS_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.RUNNING: frozenset({RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.ABORTED}),
    RunStatus.SUCCESS: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.ABORTED: frozenset(),
}


# Run plans are the first-class workflow execution state. They link to a normal
# ``runs`` audit row only once started.
RUN_PLAN_STATUS_TRANSITIONS: dict[RunPlanStatus, frozenset[RunPlanStatus]] = {
    RunPlanStatus.DRAFT: frozenset({RunPlanStatus.STARTED, RunPlanStatus.ABORTED}),
    RunPlanStatus.STARTED: frozenset(
        {RunPlanStatus.COMPLETED, RunPlanStatus.FAILED, RunPlanStatus.ABORTED}
    ),
    RunPlanStatus.COMPLETED: frozenset(),
    RunPlanStatus.FAILED: frozenset(),
    RunPlanStatus.ABORTED: frozenset(),
}


RUN_PLAN_STEP_STATUS_TRANSITIONS: dict[RunPlanStepStatus, frozenset[RunPlanStepStatus]] = {
    RunPlanStepStatus.PENDING: frozenset(
        {
            RunPlanStepStatus.RUNNING,
            RunPlanStepStatus.SKIPPED,
            RunPlanStepStatus.BLOCKED,
        }
    ),
    RunPlanStepStatus.BLOCKED: frozenset({RunPlanStepStatus.PENDING, RunPlanStepStatus.SKIPPED}),
    RunPlanStepStatus.RUNNING: frozenset(
        {
            RunPlanStepStatus.SUCCESS,
            RunPlanStepStatus.FAILED,
            RunPlanStepStatus.SKIPPED,
        }
    ),
    RunPlanStepStatus.SUCCESS: frozenset(),
    RunPlanStepStatus.FAILED: frozenset(),
    RunPlanStepStatus.SKIPPED: frozenset(),
}


APPROVAL_REQUEST_STATUS_TRANSITIONS: dict[
    ApprovalRequestStatus, frozenset[ApprovalRequestStatus]
] = {
    ApprovalRequestStatus.PENDING: frozenset(
        {
            ApprovalRequestStatus.APPROVED,
            ApprovalRequestStatus.REJECTED,
            ApprovalRequestStatus.CANCELLED,
        }
    ),
    ApprovalRequestStatus.APPROVED: frozenset(),
    ApprovalRequestStatus.REJECTED: frozenset(),
    ApprovalRequestStatus.CANCELLED: frozenset(),
}


ACTION_CALL_STATUS_TRANSITIONS: dict[ActionCallStatus, frozenset[ActionCallStatus]] = {
    ActionCallStatus.DRY_RUN: frozenset(),
    ActionCallStatus.SUCCESS: frozenset(),
    ActionCallStatus.FAILED: frozenset(),
}


AGENT_REQUEST_STATUS_TRANSITIONS: dict[AgentRequestStatus, frozenset[AgentRequestStatus]] = {
    AgentRequestStatus.NEW: frozenset(
        {
            AgentRequestStatus.CLAIMED,
            AgentRequestStatus.IGNORED,
            AgentRequestStatus.FAILED,
        }
    ),
    AgentRequestStatus.CLAIMED: frozenset(
        {
            AgentRequestStatus.NEW,
            AgentRequestStatus.RUN_CREATED,
            AgentRequestStatus.RUN_STARTED,
            AgentRequestStatus.RESPONDED,
            AgentRequestStatus.RESOLVED,
            AgentRequestStatus.IGNORED,
            AgentRequestStatus.FAILED,
        }
    ),
    AgentRequestStatus.RUN_CREATED: frozenset(
        {
            AgentRequestStatus.RUN_STARTED,
            AgentRequestStatus.RESPONDED,
            AgentRequestStatus.RESOLVED,
            AgentRequestStatus.IGNORED,
            AgentRequestStatus.FAILED,
        }
    ),
    AgentRequestStatus.RUN_STARTED: frozenset(
        {
            AgentRequestStatus.RESPONDED,
            AgentRequestStatus.RESOLVED,
            AgentRequestStatus.FAILED,
        }
    ),
    AgentRequestStatus.RESPONDED: frozenset(
        {
            AgentRequestStatus.RESOLVED,
            AgentRequestStatus.FAILED,
        }
    ),
    AgentRequestStatus.RESOLVED: frozenset(),
    AgentRequestStatus.IGNORED: frozenset(),
    AgentRequestStatus.FAILED: frozenset(),
}


TRACKER_ITEM_STATUS_TRANSITIONS: dict[TrackerItemStatus, frozenset[TrackerItemStatus]] = {
    TrackerItemStatus.NOT_STARTED: frozenset(
        {
            TrackerItemStatus.IN_PROGRESS,
            TrackerItemStatus.COMPLETE,
            TrackerItemStatus.DEFERRED,
        }
    ),
    TrackerItemStatus.IN_PROGRESS: frozenset(
        {
            TrackerItemStatus.COMPLETE,
            TrackerItemStatus.DEFERRED,
            TrackerItemStatus.NOT_STARTED,
        }
    ),
    TrackerItemStatus.DEFERRED: frozenset(
        {
            TrackerItemStatus.NOT_STARTED,
            TrackerItemStatus.IN_PROGRESS,
            TrackerItemStatus.COMPLETE,
        }
    ),
    TrackerItemStatus.COMPLETE: frozenset({TrackerItemStatus.IN_PROGRESS}),
}


__all__ = [
    "ACTION_CALL_STATUS_TRANSITIONS",
    "AGENT_REQUEST_STATUS_TRANSITIONS",
    "APPROVAL_REQUEST_STATUS_TRANSITIONS",
    "RUN_PLAN_STATUS_TRANSITIONS",
    "RUN_PLAN_STEP_STATUS_TRANSITIONS",
    "RUN_STATUS_TRANSITIONS",
    "TRACKER_ITEM_STATUS_TRANSITIONS",
    "ActionCallStatus",
    "AgentRequestAttentionStatus",
    "AgentRequestStatus",
    "ApprovalRequestStatus",
    "PluginSource",
    "RunKind",
    "RunPlanStatus",
    "RunPlanStepStatus",
    "RunStatus",
    "RunStepStatus",
    "TrackerItemStatus",
    "TrackerLinkKind",
    "TrackerSourceKind",
    "TrackerTicketKind",
]
