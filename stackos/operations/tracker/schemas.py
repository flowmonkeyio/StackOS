"""Input schemas for tracker operations."""

from __future__ import annotations

from datetime import date, datetime
from typing import (
    Annotated,
    Any,
    Literal,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from stackos.db.models import (
    TrackerItemStatus,
    TrackerSourceKind,
    TrackerTicketKind,
)
from stackos.host_mcp.project_agents import AgentSourceRef
from stackos.mcp.contract import MCPInput
from stackos.repositories.tracker.agent_stats import stats_timezone
from stackos.repositories.tracker.inspection_schema import ResultSourceKind
from stackos.repositories.tracker.schema import TrackerCorrectionIntent

TrackerResponseMode = Literal["compact", "raw", "standard", "verbose"]
AgentRole = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_][a-zA-Z0-9_-]{0,159}$", strict=True)]
# Patch payloads remain extensible. Only the explicit correction intent reserves
# typed fields; legacy correction prose and unrelated context remain opaque.
TICKET_CORRECTION_PATCH_SCHEMA: dict[str, Any] = {
    "properties": {
        "context_json": {
            "properties": {
                "correction": {
                    "if": {"type": "object", "required": ["work_intent"]},
                    "then": TrackerCorrectionIntent.model_json_schema(),
                }
            }
        }
    }
}


class TrackerTaskActivityInput(MCPInput):
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(ge=1)
    task_key: str = Field(min_length=1)
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)
    response_mode: TrackerResponseMode = "compact"


class TrackerTaskResultsInput(TrackerTaskActivityInput):
    source_kind: ResultSourceKind | None = None


class TaskDeliveryAgentOverride(BaseModel):
    """Task choices may omit fields supplied by project defaults."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=1)
    instructions: str | None = Field(default=None, min_length=1)
    host_key: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)
    provider: str | None = Field(default=None, min_length=1)
    profile: str | None = Field(default=None, min_length=1)
    source_ref: AgentSourceRef | None = None
    model_reasoning_effort: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def one_instruction_owner(self):
        if self.source_ref is not None and self.instructions is not None:
            raise ValueError("source_ref owns instructions; remove the editable copy")
        return self


class TaskDeliveryAgent(TaskDeliveryAgentOverride):
    """A resolved project agent contract, independent of host file layout."""

    name: str = Field(min_length=1)
    instructions: str = Field(min_length=1)


class TaskDeliveryDefaults(BaseModel):
    """Project defaults; task handoff validates the complete resolved roles."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    host_key: str | None = Field(default=None, min_length=1)
    delivery_agent: TaskDeliveryAgentOverride | None = None
    review_agent: TaskDeliveryAgentOverride | None = None
    agents: dict[AgentRole, TaskDeliveryAgentOverride] | None = None


class AgentSourceAdoption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_ref: AgentSourceRef
    expected_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class TrackerConfigureDeliveryInput(MCPInput):
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(ge=1)
    delivery: TaskDeliveryDefaults | None = None
    project_dir: str | None = None
    adopt_sources: dict[AgentRole, AgentSourceAdoption] | None = None
    preview: bool = False

    @model_validator(mode="after")
    def explicit_setup_mode(self):
        if self.adopt_sources is not None:
            if not self.adopt_sources or not self.project_dir or self.delivery is not None:
                raise ValueError("adopt_sources requires project_dir and preserves saved delivery")
        elif self.delivery is None or self.preview:
            raise ValueError("Provide delivery, or an explicit source adoption preview/write")
        return self

    response_mode: TrackerResponseMode = "raw"


class TaskMcpConfigRef(BaseModel):
    """Task-only native route identity; never a project/default setting."""

    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TrackerStartDeliveryInput(MCPInput):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    project_id: int
    task_key: str = Field(min_length=1)
    project_dir: str = Field(min_length=1)
    mcp_config_ref: TaskMcpConfigRef | None = Field(
        default=None,
        description=(
            "Task-only absolute native TOML path and SHA256. Existing Codex stdio server "
            "args/cwd/non-secret env only; immutable across role sessions and rehandoff. "
            "Keep the fragment outside Git. Other hosts reject this reference."
        ),
    )
    host_key: str | None = Field(default=None, min_length=1)
    delivery_agent: TaskDeliveryAgentOverride | None = None
    review_agent: TaskDeliveryAgentOverride | None = None
    agents: dict[AgentRole, TaskDeliveryAgentOverride] | None = None
    agent_assignments: dict[str, AgentRole] | None = None
    run_plan_id: int | None = Field(default=None, ge=1)
    step_id: str | None = Field(default=None, min_length=1)
    response_mode: TrackerResponseMode = "compact"

    @model_validator(mode="after")
    def workflow_scope_pair(self):
        if (self.run_plan_id is None) != (self.step_id is None):
            raise ValueError("run_plan_id and step_id must be provided together")
        return self


TRACKER_STATUS_DESCRIPTION = (
    "Tracker lifecycle status. not-started = planned but not begun; "
    "in-progress = actively being worked; complete = finished successfully; "
    "deferred = valid work postponed for later; aborted = stopped, cancelled, "
    "or rejected; failed = attempted and unsuccessful; skipped = intentionally "
    "not executed."
)
TRACKER_PATCH_DESCRIPTION = (
    "Explicit tracker patch. For terminal closeout, set status plus a concise "
    "outcome when the target supports it and structured completion_evidence_json "
    "with the reason, proof, or residual risk. Terminal ticket updates can roll "
    "up parent task status, but ticket evidence is not copied to the task; add "
    "task evidence explicitly when task-level closeout evidence matters. Status meanings: "
    f"{TRACKER_STATUS_DESCRIPTION}"
)


class TrackerProjectInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "response_mode": "compact"}},
    )

    project_id: int
    response_mode: TrackerResponseMode = Field(
        default="compact",
        description=(
            "Agent response shape. compact is default; raw/standard/verbose return "
            "the full tracker rows for diagnostics. raw aliases standard."
        ),
    )


class AgentStatsFilters(BaseModel):
    group_by: Literal["agent", "model", "agent_model"] = "agent_model"
    start_date: date | None = Field(
        default=None,
        description="Inclusive local calendar date; supply with end_date, at most 366 days.",
    )
    end_date: date | None = Field(
        default=None, description="Inclusive local calendar date; supply with start_date."
    )
    timezone: str = Field(
        default="UTC",
        description=(
            "IANA timezone for launch.started_at calendar days. UI chooses its own default range."
        ),
    )

    @model_validator(mode="after")
    def validate_dates(self) -> AgentStatsFilters:
        stats_timezone(self.start_date, self.end_date, self.timezone)
        return self


class TrackerAgentStatsInput(TrackerProjectInput, AgentStatsFilters):
    project_id: int = Field(ge=1)


class TrackerAgentStatsAllInput(MCPInput, AgentStatsFilters):
    model_config = ConfigDict(extra="forbid")
    is_active: StrictBool | None = True


class TrackerTicketCountsInput(TrackerProjectInput):
    project_id: int = Field(ge=1)


class TrackerTicketCountsAllInput(MCPInput):
    model_config = ConfigDict(extra="forbid")
    is_active: StrictBool | None = True


class TrackerGetInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"project_id": 1, "include_graph": True},
                {
                    "project_id": 1,
                    "task_index_only": True,
                    "task_statuses": ["not-started", "in-progress"],
                    "include_graph": False,
                    "response_mode": "raw",
                },
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
    task_statuses: list[TrackerItemStatus] | None = Field(
        default=None,
        description=(
            "Filter top-level tasks by their own lifecycle status without filtering "
            "their ticket summaries. Use this with task_index_only for bounded operator "
            "views such as open-work portfolios."
        ),
    )
    task_key: str | None = None
    ticket_keys: list[str] | None = None
    ticket_ids: list[int] | None = None
    block_state: Literal["blocked", "open"] | None = None
    dependency_ticket_key: str | None = None
    workflow_key: str | None = None
    run_plan_id: int | None = None
    assignee: str | None = None
    include_graph: bool = True
    task_index_only: bool = Field(
        default=False,
        description=(
            "Return every matching task with whole-task agent_stats and an aggregate ticket "
            "summary while skipping "
            "ticket, dependency, link, and graph hydration. Pair with include_graph=false. "
            "This is intended for operator task indexes before one focused graph is loaded."
        ),
    )

    @model_validator(mode="after")
    def validate_task_index_scope(self) -> TrackerGetInput:
        if not self.task_index_only:
            return self
        if self.include_graph:
            raise ValueError("task_index_only requires include_graph=false")
        ticket_filters = {
            "statuses": self.statuses,
            "ticket_keys": self.ticket_keys,
            "ticket_ids": self.ticket_ids,
            "block_state": self.block_state,
            "dependency_ticket_key": self.dependency_ticket_key,
            "run_plan_id": self.run_plan_id,
            "assignee": self.assignee,
        }
        unsupported = [key for key, value in ticket_filters.items() if value]
        if unsupported:
            raise ValueError(
                "task_index_only does not support ticket-scoped filters: " + ", ".join(unsupported)
            )
        return self


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
            "Agent response shape. compact is default; raw/standard/verbose return "
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
            "Agent response shape. compact is default; raw/standard/verbose return "
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
            "Agent response shape. compact is default; raw/standard/verbose return "
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
            "Agent response shape. compact is default; raw/standard/verbose return "
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
            "Agent response shape. compact is default; raw/standard/verbose return "
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
    status: TrackerItemStatus = Field(
        default=TrackerItemStatus.NOT_STARTED,
        description=TRACKER_STATUS_DESCRIPTION,
    )
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
                    "run_plan_id": 42,
                    "step_id": "deliver",
                    "key": "fix-webhook-media-forwarding",
                    "title": "Forward Telegram media in canonical Slack handoff",
                    "dependency_keys": ["workflow-42-deliver"],
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
    run_plan_id: int | None = Field(
        default=None,
        description=(
            "Optional workflow run-plan id. When supplied with step_id, StackOS attaches "
            "the ticket under the mirrored workflow step but does not add dependency edges; "
            "pass dependency_keys or dependencies_json to bridge execution order."
        ),
    )
    step_id: str | None = Field(
        default=None,
        description=(
            "Optional workflow step id. Must be supplied with run_plan_id to attach "
            "tickets under that mirrored workflow step. Attachment is containment/linkage "
            "only and does not block or unblock execution without explicit dependencies."
        ),
    )
    key: str | None = None
    title: str | None = None
    goal: str = ""
    status: TrackerItemStatus = Field(
        default=TrackerItemStatus.NOT_STARTED,
        description=TRACKER_STATUS_DESCRIPTION,
    )
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
            "examples": [
                {
                    "project_id": 1,
                    "task_key": "telegram-comms",
                    "patch_json": {"status": "in-progress"},
                },
                {
                    "project_id": 1,
                    "task_key": "manual-probe",
                    "patch_json": {
                        "status": "skipped",
                        "completion_evidence_json": {
                            "reason": "Usability probe intentionally did not execute delivery."
                        },
                    },
                },
            ]
        },
    )

    project_id: int
    task_key: str
    patch_json: dict[str, Any] = Field(description=TRACKER_PATCH_DESCRIPTION)
    actor: str | None = None


class TrackerTaskBoardInput(MCPInput):
    project_id: StrictInt = Field(gt=0)
    task_key: StrictStr = Field(min_length=1, max_length=200)
    expected_status: TrackerItemStatus
    expected_updated_at: datetime
    idempotency_key: StrictStr = Field(
        min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    )
    run_token: None = None
    expected_etag: None = None
    response_mode: Literal["raw", "standard", "verbose"] = "raw"


class TrackerUpdateTaskPriorityInput(TrackerTaskBoardInput):
    priority_key: Literal["p0", "p1", "p2", "p3"]


class TrackerReorderTaskInput(TrackerTaskBoardInput):
    anchor_task_key: StrictStr = Field(min_length=1, max_length=200)
    placement: Literal["before", "after"]


class TrackerUpdateTaskContentInput(MCPInput):
    """Human content edits cannot supply tracker or workflow execution state."""

    project_id: StrictInt = Field(gt=0)
    task_key: StrictStr = Field(min_length=1, max_length=200)
    expected_updated_at: datetime
    idempotency_key: StrictStr = Field(
        min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    )
    description: StrictStr | None = Field(default=None, max_length=20_000)
    additional_input: StrictStr | None = Field(default=None, max_length=20_000)
    run_token: None = None
    expected_etag: None = None
    response_mode: Literal["raw", "standard", "verbose"] = "raw"

    @model_validator(mode="after")
    def validate_content(self) -> TrackerUpdateTaskContentInput:
        fields = self.model_fields_set & {"description", "additional_input"}
        if not fields:
            raise ValueError("description or additional_input is required")
        if any(getattr(self, field) is None for field in fields):
            raise ValueError("content fields cannot be null; use an empty description to clear it")
        if self.additional_input is not None and not self.additional_input.strip():
            raise ValueError("additional_input must contain text")
        return self


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


class TrackerReopenInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "project_id": 2,
                    "task_key": "workflow-27",
                    "reason": "More follow-up work was found after closeout.",
                    "actor": "codex",
                },
                {
                    "project_id": 2,
                    "run_plan_id": 27,
                    "reason": "Operator wants the same workflow continued.",
                },
                {
                    "project_id": 2,
                    "run_id": 21,
                    "reason": "The linked workflow needs follow-up work.",
                },
                {
                    "project_id": 1,
                    "task_key": "manual-comms",
                    "reason": "New requirements were discovered.",
                },
            ]
        },
    )

    project_id: int
    task_key: str | None = Field(
        default=None,
        description="Task key to reopen. For workflow mirrors, workflow-{run_plan_id} is resolved.",
    )
    run_plan_id: int | None = Field(
        default=None,
        description=(
            "Optional run-plan id. When supplied, StackOS reopens the canonical "
            "run plan, linked audit run, and mirrored workflow task together."
        ),
    )
    run_id: int | None = Field(
        default=None,
        description=(
            "Optional linked audit run id. StackOS resolves it to the owning run plan "
            "when one exists."
        ),
    )
    step_id: str | None = Field(
        default=None,
        description=(
            "Optional workflow step id to resume from. Omit it and StackOS chooses "
            "the first failed/blocked step, or the last step."
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
                    "ticket_key": "manual-probe",
                    "patch_json": {
                        "status": "skipped",
                        "outcome": "Mock probe; no real delivery performed.",
                        "completion_evidence_json": {
                            "reason": "Usability probe intentionally did not execute delivery."
                        },
                    },
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
    patch_json: dict[str, Any] | None = Field(
        default=None,
        json_schema_extra=TICKET_CORRECTION_PATCH_SCHEMA,
        description=TRACKER_PATCH_DESCRIPTION
        + (
            " Ticket context_json.correction may explicitly admit work_intent "
            "(evidence_only or source_repair), with the full candidate_commit_sha "
            "and positive integer target_attempt. Merge existing context/evidence. "
            "Evidence-only intent applies only to that attempt and unchanged candidate; "
            "omitted/legacy intent retains source-repair validation."
        ),
    )
    updates_json: list[dict[str, Any]] | None = Field(
        default=None,
        json_schema_extra={"items": {"properties": {"patch_json": TICKET_CORRECTION_PATCH_SCHEMA}}},
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
            "Validate the exact patch_json or ordered updates_json without writing. "
            "Uses apply's transition and invariant rules and returns dependency diffs."
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
    "TaskDeliveryAgent",
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
    "TrackerReopenInput",
    "TrackerReorderTaskInput",
    "TrackerResponseMode",
    "TrackerSearchInput",
    "TrackerStartDeliveryInput",
    "TrackerTicketInput",
    "TrackerUpdateTaskContentInput",
    "TrackerUpdateTaskInput",
    "TrackerUpdateTaskPriorityInput",
    "TrackerUpdateTicketInput",
]
