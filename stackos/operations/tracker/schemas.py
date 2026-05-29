"""Input schemas for tracker operations."""

from __future__ import annotations

from typing import (
    Any,
    Literal,
)

from pydantic import (
    ConfigDict,
    Field,
)

from stackos.db.models import (
    TrackerItemStatus,
    TrackerSourceKind,
    TrackerTicketKind,
)
from stackos.mcp.contract import MCPInput

TrackerResponseMode = Literal["compact", "standard", "verbose"]


class TrackerProjectInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "response_mode": "compact"}},
    )

    project_id: int
    response_mode: TrackerResponseMode = Field(
        default="compact",
        description=(
            "Agent response shape. compact is default; standard/verbose returns "
            "the full tracker rows for diagnostics."
        ),
    )


class TrackerGetInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"project_id": 1, "include_graph": True},
                {
                    "project_id": 1,
                    "task_key": "telegram-comms",
                    "ticket_keys": ["telegram-ingress", "telegram-reply"],
                    "include_graph": False,
                },
            ]
        },
    )

    project_id: int
    statuses: list[TrackerItemStatus] | None = None
    task_key: str | None = None
    ticket_keys: list[str] | None = None
    ticket_ids: list[int] | None = None
    block_state: Literal["blocked", "open"] | None = None
    dependency_ticket_key: str | None = None
    workflow_key: str | None = None
    run_plan_id: int | None = None
    assignee: str | None = None
    include_graph: bool = True


class TrackerNextInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "limit": 5, "response_mode": "compact"}},
    )

    project_id: int
    limit: int = Field(default=5, ge=1, le=50)
    assignee: str | None = None
    include_blocked: bool = True
    response_mode: TrackerResponseMode = Field(
        default="compact",
        description=(
            "Agent response shape. compact is default; standard/verbose returns "
            "full tracker ticket rows for diagnostics."
        ),
    )


class TrackerTicketInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "ticket_key": "slack-ingress",
                "response_mode": "compact",
            }
        },
    )

    project_id: int
    ticket_key: str
    response_mode: TrackerResponseMode = Field(
        default="compact",
        description=(
            "Agent response shape. compact is default; standard/verbose returns "
            "the full tracker brief for diagnostics."
        ),
    )


class TrackerHistoryInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "limit": 50, "response_mode": "compact"}},
    )

    project_id: int
    limit: int | None = Field(default=None, ge=1, le=200)
    after_id: int | None = None
    response_mode: TrackerResponseMode = Field(
        default="compact",
        description=(
            "Agent response shape. compact is default; standard/verbose returns "
            "full tracker history rows with before/after/patch payloads."
        ),
    )


class TrackerChangedInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"project_id": 1, "since_rev": 42, "response_mode": "compact"}
        },
    )

    project_id: int
    since_rev: int | None = None
    limit: int = Field(default=50, ge=1, le=200)
    response_mode: TrackerResponseMode = Field(
        default="compact",
        description=(
            "Agent response shape. compact is default; standard/verbose returns "
            "full tracker history rows with before/after/patch payloads."
        ),
    )


class TrackerSearchInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"project_id": 1, "query": "telegram", "response_mode": "compact"}
        },
    )

    project_id: int
    query: str
    limit: int = Field(default=20, ge=1, le=100)
    response_mode: TrackerResponseMode = Field(
        default="compact",
        description=(
            "Agent response shape. compact is default; standard/verbose returns "
            "full matching task and ticket rows for diagnostics."
        ),
    )


class TrackerCreateTaskInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "key": "telegram-comms",
                "title": "Telegram communications flow",
                "goal": "Support message ingress and safe replies.",
                "created_by": "codex",
            }
        },
    )

    project_id: int
    key: str
    title: str
    goal: str = ""
    description: str = ""
    status: TrackerItemStatus = TrackerItemStatus.NOT_STARTED
    priority_key: str = "p2"
    lane_key: str = "implementation"
    owner: str | None = None
    task_type: str = "task"
    source_kind: TrackerSourceKind = TrackerSourceKind.MANUAL
    source_json: dict[str, Any] | None = None
    definition_of_done_json: list[str] | None = None
    constraints_json: list[str] | None = None
    expected_outcomes_json: list[str] | None = None
    completion_evidence_json: dict[str, Any] | None = None
    context_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    created_by: str | None = None
    create_default_ticket: bool = False


class TrackerCreateTicketInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "project_id": 1,
                    "task_key": "telegram-comms",
                    "key": "telegram-ingress",
                    "title": "Wire Telegram ingress",
                    "dependency_keys": [],
                },
                {
                    "project_id": 1,
                    "task_key": "telegram-comms",
                    "tickets_json": [
                        {"key": "telegram-ingress", "title": "Wire Telegram ingress"},
                        {
                            "key": "telegram-reply",
                            "title": "Send safe replies",
                            "dependency_keys": ["telegram-ingress"],
                        },
                    ],
                    "dry_run": True,
                },
            ]
        },
    )

    project_id: int
    task_key: str | None = None
    key: str | None = None
    title: str | None = None
    goal: str = ""
    status: TrackerItemStatus = TrackerItemStatus.NOT_STARTED
    kind: TrackerTicketKind = TrackerTicketKind.TICKET
    assignee: str | None = None
    priority_key: str = "p2"
    lane_key: str = "implementation"
    parent_ticket_key: str | None = None
    dependency_keys: list[str] | None = None
    blocker_reason: str | None = None
    outcome: str | None = None
    effort: str | None = None
    source_kind: TrackerSourceKind = TrackerSourceKind.MANUAL
    source_json: dict[str, Any] | None = None
    definition_of_done_json: list[str] | None = None
    constraints_json: list[str] | None = None
    expected_changes_json: list[str] | None = None
    allowed_paths_json: list[str] | None = None
    references_json: list[dict[str, Any]] | None = None
    completion_evidence_json: dict[str, Any] | None = None
    context_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    created_by: str | None = None
    tickets_json: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Optional ticket list import. When provided, task_key is shared by default "
            "and single-ticket key/title fields are ignored."
        ),
    )
    dependencies_json: list[dict[str, Any]] | None = None
    dry_run: bool = Field(
        default=False,
        description="When tickets_json is provided, validate and normalize without writing.",
    )


class TrackerUpdateTaskInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "task_key": "telegram-comms",
                "patch_json": {"status": "in-progress"},
            }
        },
    )

    project_id: int
    task_key: str
    patch_json: dict[str, Any]
    actor: str | None = None


class TrackerRejectTaskInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "task_key": "workflow-10",
                "reason": "Superseded by operator decision.",
                "actor": "codex",
            }
        },
    )

    project_id: int
    task_key: str | None = Field(
        default=None,
        description="Task key to reject. Use this for manual tracker tasks.",
    )
    run_plan_id: int | None = Field(
        default=None,
        description=(
            "Run-plan id to reject. The tracker task key resolves to workflow-{run_plan_id}; "
            "draft or started run plans are aborted before tracker cascade."
        ),
    )
    reason: str
    actor: str | None = None


class TrackerUpdateTicketInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "project_id": 1,
                    "ticket_key": "telegram-ingress",
                    "patch_json": {"status": "complete", "outcome": "verified"},
                },
                {
                    "project_id": 1,
                    "updates_json": [
                        {
                            "ticket_key": "telegram-ingress",
                            "patch_json": {
                                "status": "complete",
                                "completion_evidence_json": {"summary": "Webhook verified."},
                            },
                        },
                        {
                            "ticket_key": "telegram-reply",
                            "patch_json": {
                                "assignee": "codex",
                                "add_dependency_keys": ["telegram-policy"],
                                "remove_dependency_keys": ["telegram-ingress"],
                            },
                        },
                    ],
                },
                {
                    "project_id": 1,
                    "ticket_key": "telegram-reply",
                    "patch_json": {
                        "add_dependency_keys": ["telegram-policy"],
                        "remove_dependency_keys": ["telegram-ingress"],
                    },
                    "dry_run": True,
                },
            ]
        },
    )

    project_id: int
    ticket_key: str | None = None
    patch_json: dict[str, Any] | None = None
    updates_json: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Optional list of atomic ticket patch updates. Each item identifies a ticket "
            "by ticket_id or ticket_key and supplies patch_json. Use add_dependency_keys "
            "and remove_dependency_keys for small dependency edits; dependency_keys "
            "intentionally replaces the full dependency list."
        ),
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "Preview dependency-changing patch_json or updates_json without writing. "
            "Returns dependency diffs for dependency fields and leaves ordinary updates quiet."
        ),
    )
    actor: str | None = None


class TrackerPatchInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "patch_json": {
                    "tickets": {
                        "telegram-ingress": {
                            "status": "complete",
                            "outcome": "Webhook verified.",
                        }
                    }
                },
            }
        },
    )

    project_id: int
    patch_json: dict[str, Any]
    actor: str | None = None


class TrackerPickInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "assignee": "codex"}},
    )

    project_id: int
    assignee: str
    ticket_key: str | None = None


class TrackerReleaseInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"project_id": 1, "ticket_key": "telegram-ingress", "actor": "codex"}
        },
    )

    project_id: int
    ticket_key: str
    actor: str | None = None


class TrackerLinkRunPlanInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"project_id": 1, "task_key": "manual-review", "run_plan_id": 12}
        },
    )

    project_id: int
    task_key: str
    run_plan_id: int
    actor: str | None = None


__all__ = [
    "TrackerChangedInput",
    "TrackerCreateTaskInput",
    "TrackerCreateTicketInput",
    "TrackerGetInput",
    "TrackerHistoryInput",
    "TrackerLinkRunPlanInput",
    "TrackerNextInput",
    "TrackerPatchInput",
    "TrackerPickInput",
    "TrackerProjectInput",
    "TrackerRejectTaskInput",
    "TrackerReleaseInput",
    "TrackerResponseMode",
    "TrackerSearchInput",
    "TrackerTicketInput",
    "TrackerUpdateTaskInput",
    "TrackerUpdateTicketInput",
]
