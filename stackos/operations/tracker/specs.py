"""Operation spec registration for tracker operations."""

from __future__ import annotations

from typing import Any

from stackos.mcp.contract import WriteEnvelope
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import (
    OperationExample,
    OperationResponsePolicy,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.operations.tracker.read_handlers import (
    tracker_agent_stats,
    tracker_agent_stats_all,
    tracker_blockers,
    tracker_brief,
    tracker_changed,
    tracker_execute,
    tracker_get,
    tracker_history,
    tracker_next,
    tracker_search,
    tracker_status,
    tracker_task_activity,
    tracker_task_results,
    tracker_ticket_counts,
    tracker_ticket_counts_all,
    tracker_verify,
    tracker_why,
)
from stackos.operations.tracker.schemas import (
    TrackerAgentStatsAllInput,
    TrackerAgentStatsInput,
    TrackerChangedInput,
    TrackerConfigureDeliveryInput,
    TrackerCreateTaskInput,
    TrackerCreateTicketInput,
    TrackerGetInput,
    TrackerHistoryInput,
    TrackerLinkRunPlanInput,
    TrackerNextInput,
    TrackerPatchInput,
    TrackerPickInput,
    TrackerProjectInput,
    TrackerRejectTaskInput,
    TrackerReleaseInput,
    TrackerReopenInput,
    TrackerSearchInput,
    TrackerStartDeliveryInput,
    TrackerTaskActivityInput,
    TrackerTaskResultsInput,
    TrackerTicketCountsAllInput,
    TrackerTicketCountsInput,
    TrackerTicketInput,
    TrackerUpdateTaskContentInput,
    TrackerUpdateTaskInput,
    TrackerUpdateTicketInput,
)
from stackos.operations.tracker.spec_helpers import (
    _read_spec,
    _surfaces,
    _write_spec,
)
from stackos.operations.tracker.write_handlers import (
    tracker_configure_delivery,
    tracker_create_task,
    tracker_create_ticket,
    tracker_link_run_plan,
    tracker_patch,
    tracker_pick,
    tracker_reject_task,
    tracker_release,
    tracker_reopen,
    tracker_start_delivery,
    tracker_update_task,
    tracker_update_task_content,
    tracker_update_ticket,
)
from stackos.repositories.base import Page
from stackos.repositories.tracker import (
    TrackerBriefOut,
    TrackerChangedOut,
    TrackerHistoryOut,
    TrackerNextOut,
    TrackerReopenOut,
    TrackerSearchOut,
    TrackerSnapshotOut,
    TrackerStatusOut,
    TrackerTaskContentOut,
    TrackerTicketCountsOut,
    TrackerVerifyOut,
)
from stackos.repositories.tracker.inspection_schema import TaskActivityOut, TaskResultsOut
from stackos.repositories.tracker.schema import TrackerAgentStatsOut

_AGENT_STATS_RESPONSE_POLICY = OperationResponsePolicy(
    default_mode="compact",
    allowed_modes=("compact", "raw"),
    ack_safe=False,
    compact_notes=(
        "Keep scope, dates, timezone, group_by, as_of, totals, every row/day/series, "
        "undated facts and per-field/classification coverage.",
    ),
)

_TICKET_COUNTS_RESPONSE_POLICY = OperationResponsePolicy(
    default_mode="compact",
    allowed_modes=("compact", "raw"),
    ack_safe=False,
    compact_notes=("Keep scope, as_of, total_count, and all seven exact ticket status counts.",),
)


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="tracker.configureDelivery",
            summary="Save project task delivery defaults through local-admin setup.",
            input_model=TrackerConfigureDeliveryInput,
            output_model=WriteEnvelope[dict[str, Any]],
            handler=tracker_configure_delivery,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=False),
                rest=OperationSurface(
                    enabled=True,
                    browser_safe=True,
                    path="/api/v1/operations/tracker.configureDelivery/call",
                ),
                cli=OperationSurface(enabled=False),
            ),
            mutating=True,
            grant_policy="local-admin-console-write",
            response_policy=OperationResponsePolicy(
                default_mode="raw",
                allowed_modes=("raw",),
                ack_safe=False,
                raw_only_reason="Preserve saved defaults and tracker revision for setup readback.",
            ),
            purpose=(
                "Replace TaskTracker.config_json.delivery only, preserving other tracker keys. "
                "This changes future handoffs, never task snapshots or stored ticket evidence."
            ),
            prerequisites=(
                "Requires authenticated local-admin REST setup; no MCP or workflow grants.",
                "Read tracker.get response_mode=raw first; include all desired delivery defaults. "
                "Do not store secrets in instructions or selections.",
            ),
            returns=("Saved tracker configuration and revision; inspect with tracker.get raw.",),
            examples=(
                OperationExample(
                    title="Save project agents",
                    arguments={
                        "project_id": 1,
                        "delivery": {
                            "host_key": "codex",
                            "delivery_agent": {
                                "source_ref": {
                                    "host_key": "codex",
                                    "native_name": "builder",
                                    "source_path": ".codex/agents/builder.toml",
                                },
                            },
                            "review_agent": {
                                "source_ref": {
                                    "host_key": "codex",
                                    "native_name": "reviewer",
                                    "source_path": ".codex/agents/reviewer.toml",
                                },
                            },
                        },
                    },
                ),
            ),
        ),
        OperationSpec(
            name="tracker.startDelivery",
            summary="Start background task delivery and review using project agents.",
            input_model=TrackerStartDeliveryInput,
            output_model=WriteEnvelope[dict[str, Any]],
            handler=tracker_start_delivery,
            surfaces=_surfaces("tracker.startDelivery"),
            mutating=True,
            grant_policy="direct-tracker-write",
            response_policy=OperationResponsePolicy(
                default_mode="compact",
                allowed_modes=("compact", "raw"),
                compact_notes=(
                    "Keep task, workflow scope, job, ticket, status and inspection guidance.",
                ),
            ),
            purpose=(
                "Select taskwide dependency-driven execution for a prepared task. "
                "Resolve generic tracker delivery defaults with task overrides by role once. "
                "Ordinary work retains independent review and up to three "
                "same-session scoped repairs. "
                "Workflow mirrors execute their frozen contracts through the run-plan owner. "
                "Explicit unsuccessful results or native failures stop the same task."
            ),
            prerequisites=(
                "Prepare self-contained tickets and dependencies in an existing working directory. "
                "The canonical directory is retained through rehandoff; queued/running task "
                "writers cannot share it. Distinct directories may execute concurrently within "
                "the existing host capacity, including worktrees sharing Git metadata. "
                "Git is optional; supplied candidate evidence requires clean matching HEAD. "
                "Source-change and repair commit obligations come from project guidance.",
                "Optional task-only mcp_config_ref supplies an absolute outside-Git TOML path "
                "and SHA256. Selected Codex validates the same bytes and applies only existing "
                "stdio server args/cwd/non-secret env leaves through native -c overrides; "
                "Executables and auth stay unchanged. Other hosts reject before queueing. "
                "Only the canonical reference/digest is saved; omission on rehandoff retains it "
                "and changed identity rejects. Missing/changed fragments stop before launch. "
                "Prepare and verify actual per-tree tool bindings; --cd alone is insufficient.",
                "For a workflow task, supply run_plan_id and step_id for its running step; "
                "this is entry context, not a traversal limit. The daemon retains the bound "
                "controller run internally and claims/records mirrors through RunPlanRepository.",
                "agents maps safe declared role keys to exact source_ref selections. Fresh Codex "
                "admission requires sources in the bound workspace; unsupported native settings "
                "reject. Non-Codex hosts keep inline contracts. agent_assignments maps canonical "
                "ticket keys to selected roles, overriding frozen step metadata; otherwise use "
                "the unique applicable declared role. Ambiguity requires selection. Ordinary "
                "tickets require a separate reviewer. No workflow/step-name routing.",
                "Supply the project directory. Role fields may be omitted when saved in "
                "TaskTracker.config_json.delivery through local-admin tracker.configureDelivery. "
                "Task choices merge by role into one execution snapshot. An explicit handoff "
                "after complete/stopped execution resolves a new snapshot; queued/running work "
                "keeps its snapshot. Prior ticket receipts, sessions and repair allowance survive. "
                "No "
                "required .codex folder. Resolved names and instructions must be complete. "
                "Optional per-role host_key, "
                "model, provider and Hermes profile use the host_mcp capability catalog. "
                "Without defaults, omitted host/model keeps the Codex/inherited-model path. "
                "Codex, Claude Code and Grok retain native review controls; Gemini CLI and Hermes "
                "review with normal native permissions and review-only behavioral instructions. "
                "Claude Desktop cannot run headlessly. Unsupported choices fail without fallback.",
            ),
            returns=(
                "Returns task/job/status refs. Inspect tracker.get response_mode=raw for progress.",
                "The existing daemon scheduler owns execution; jobs do not survive daemon restart.",
                "Agents return status success/repair/blocked/failed, summary, outputs as a "
                "JSON-encoded object and nonactionable followups. Frozen outputs are validated by "
                "the run-plan owner. Mirror repair stops. No automatic replacement tasks.",
            ),
            examples=(
                OperationExample(
                    title="Use saved project defaults with a task reviewer override",
                    arguments={
                        "project_id": 1,
                        "task_key": "feature",
                        "project_dir": "/work/project",
                        "review_agent": {
                            "host_key": "gemini-cli",
                            "model": "gemini-3.1-pro-preview",
                        },
                    },
                ),
                OperationExample(
                    title="Execute a workflow task from its running entry step",
                    arguments={
                        "project_id": 1,
                        "task_key": "workflow-12",
                        "run_plan_id": 12,
                        "step_id": "verify-delivery",
                        "project_dir": "/work/project",
                        "delivery_agent": {
                            "source_ref": {
                                "host_key": "codex",
                                "native_name": "builder",
                                "source_path": ".codex/agents/builder.toml",
                            },
                        },
                        "review_agent": {
                            "source_ref": {
                                "host_key": "codex",
                                "native_name": "reviewer",
                                "source_path": ".codex/agents/reviewer.toml",
                            },
                        },
                    },
                ),
                OperationExample(
                    title="Start a prepared task with project agents",
                    arguments={
                        "project_id": 1,
                        "task_key": "feature",
                        "project_dir": "/work/project",
                        "delivery_agent": {
                            "name": "builder",
                            "instructions": "Implement and verify the ticket acceptance criteria.",
                            "host_key": "claude-code",
                            "model": "claude-sonnet-5-5",
                        },
                        "review_agent": {
                            "source_ref": {
                                "host_key": "codex",
                                "native_name": "reviewer",
                                "source_path": ".codex/agents/reviewer.toml",
                            },
                            "host_key": "codex",
                            "model": "gpt-6.1-sol",
                        },
                    },
                ),
            ),
        ),
        operation_spec(
            name="tracker.agentStats",
            summary=(
                "Read recorded agent launch attempts, reported usage and reviewer rejects "
                "for one project."
            ),
            input_model=TrackerAgentStatsInput,
            output_model=TrackerAgentStatsOut,
            handler=tracker_agent_stats,
            mutating=False,
            grant_policy="direct-read",
            response_policy=_AGENT_STATS_RESPONSE_POLICY,
            purpose=(
                "Read reset-aware deduplicated invocation facts. Group by agent, selected model "
                "or agent_model. Optional inclusive dates (at most 366 days) and IANA timezone "
                "scope totals/rows/daily identically; undated receipts are disclosed separately. "
                "Cache read is included in Input; reasoning in Output. "
                "Missing fields stay explicit."
            ),
            examples=(
                OperationExample(title="Read project agent activity", arguments={"project_id": 1}),
                OperationExample(
                    title="Read dated agent activity",
                    arguments={
                        "project_id": 1,
                        "group_by": "agent",
                        "start_date": "2026-11-01",
                        "end_date": "2026-11-07",
                        "timezone": "America/Los_Angeles",
                    },
                ),
            ),
        ),
        operation_spec(
            name="tracker.agentStatsAll",
            summary="Read recorded agent activity across active, archived or all projects.",
            input_model=TrackerAgentStatsAllInput,
            output_model=TrackerAgentStatsOut,
            handler=tracker_agent_stats_all,
            mutating=False,
            grant_policy="local-admin-read",
            response_policy=_AGENT_STATS_RESPONSE_POLICY,
            purpose=(
                "Local-console read over the same reset-aware dated projection. Agent identity "
                "stays project-scoped; explicit Model grouping combines visible projects. "
                "Model means recorded launch selection, not measured actual model. "
                "No role/host splitting."
            ),
            examples=(
                OperationExample(
                    title="Read active project activity", arguments={"is_active": True}
                ),
                OperationExample(
                    title="Read archived project activity", arguments={"is_active": False}
                ),
                OperationExample(title="Read all project activity", arguments={"is_active": None}),
                OperationExample(
                    title="Compare selected models across visible projects",
                    arguments={
                        "is_active": True,
                        "group_by": "model",
                        "start_date": "2026-11-01",
                        "end_date": "2026-11-07",
                        "timezone": "America/Los_Angeles",
                    },
                ),
            ),
        ),
        operation_spec(
            name="tracker.ticketCounts",
            summary="Read exact current ticket-status counts in the project's default tracker.",
            input_model=TrackerTicketCountsInput,
            output_model=TrackerTicketCountsOut,
            handler=tracker_ticket_counts,
            mutating=False,
            grant_policy="direct-read",
            response_policy=_TICKET_COUNTS_RESPONSE_POLICY,
            purpose=(
                "Read a payload-free current snapshot; includes group and workflow tickets once."
            ),
            examples=(
                OperationExample(title="Count project tickets", arguments={"project_id": 1}),
            ),
        ),
        operation_spec(
            name="tracker.ticketCountsAll",
            summary="Read current ticket-status totals across active, archived, or all projects.",
            input_model=TrackerTicketCountsAllInput,
            output_model=TrackerTicketCountsOut,
            handler=tracker_ticket_counts_all,
            mutating=False,
            grant_policy="local-admin-read",
            response_policy=_TICKET_COUNTS_RESPONSE_POLICY,
            purpose=(
                "Local-console snapshot across default trackers; not history or agent liveness."
            ),
            examples=(
                OperationExample(
                    title="Count active-project tickets", arguments={"is_active": True}
                ),
            ),
        ),
        _read_spec(
            name="tracker.status",
            summary="Summarize project tracker counts, ready work, blockers, and revision.",
            input_model=TrackerProjectInput,
            output_model=TrackerStatusOut | dict[str, Any],
            handler=tracker_status,
            purpose="Use this as the cheapest tracker health and progress check.",
            examples=(OperationExample(title="Get tracker status", arguments={"project_id": 1}),),
        ),
        _read_spec(
            name="tracker.get",
            summary=(
                "Fetch a task index or filtered tasks, tickets, relations, and graph projection."
            ),
            input_model=TrackerGetInput,
            output_model=TrackerSnapshotOut,
            handler=tracker_get,
            purpose=(
                "Use this for UI rendering, list review by task/ticket ids, or when "
                "the agent needs a bounded project work map. Use task_index_only for a "
                "complete operator task selector before loading one focused graph. "
                "Task agent_stats always cover all own ticket receipts; ticket agent_stats "
                "cover only that ticket. "
                "Nullable active_execution comes from actual host-call lifetime for "
                "task-index, ticket and detail reads, never persisted status. "
                "Work agent_stats.samples retain up to 120 cumulative measured points; "
                "agent_stats.last_execution is the latest recorded actual identity, "
                "independent of current activity. "
                "Compact responses preserve metrics and their usage/classification coverage."
            ),
            examples=(
                OperationExample(
                    title="Load the complete operator task index",
                    arguments={
                        "project_id": 1,
                        "task_index_only": True,
                        "include_graph": False,
                        "response_mode": "raw",
                    },
                ),
                OperationExample(
                    title="Load aggregate summaries for open tasks",
                    arguments={
                        "project_id": 1,
                        "task_index_only": True,
                        "task_statuses": ["not-started", "in-progress"],
                        "include_graph": False,
                        "response_mode": "raw",
                    },
                ),
                OperationExample(
                    title="Review selected tickets under one task",
                    arguments={
                        "project_id": 1,
                        "task_key": "core-tracker",
                        "ticket_keys": ["core-tracker-schema", "core-tracker-ui"],
                        "include_graph": False,
                    },
                ),
                OperationExample(
                    title="Fetch workflow-filtered tracker graph",
                    arguments={
                        "project_id": 1,
                        "workflow_key": "core.review",
                        "include_graph": True,
                    },
                ),
            ),
        ),
        _read_spec(
            name="tracker.next",
            summary="List ready tickets ranked by priority and dependency state.",
            input_model=TrackerNextInput,
            output_model=TrackerNextOut | dict[str, Any],
            handler=tracker_next,
            purpose="Use this before picking work so the agent does not invent the next ticket.",
            examples=(OperationExample(title="List next work", arguments={"project_id": 1}),),
        ),
        _read_spec(
            name="tracker.blockers",
            summary="List open tickets blocked by explicit blockers or incomplete dependencies.",
            input_model=TrackerProjectInput,
            output_model=TrackerNextOut | dict[str, Any],
            handler=tracker_blockers,
            purpose="Use this to triage stuck project work.",
            examples=(
                OperationExample(
                    title="List blocked tracker work",
                    arguments={"project_id": 1, "response_mode": "compact"},
                ),
            ),
        ),
        _read_spec(
            name="tracker.brief",
            summary="Get one ticket with task, dependency, dependent, reference, and link context.",
            input_model=TrackerTicketInput,
            output_model=TrackerBriefOut | dict[str, Any],
            handler=tracker_brief,
            purpose=(
                "Use this before doing ticket work. It is the agent-friendly "
                "bounded context packet."
            ),
            examples=(
                OperationExample(
                    title="Read one ticket before execution",
                    arguments={
                        "project_id": 1,
                        "ticket_key": "workflow-7-deliver",
                        "response_mode": "compact",
                    },
                ),
            ),
        ),
        _read_spec(
            name="tracker.why",
            summary="Explain why a ticket exists by returning its linked task and provenance.",
            input_model=TrackerTicketInput,
            output_model=TrackerBriefOut | dict[str, Any],
            handler=tracker_why,
            purpose="Use this when the agent or operator needs provenance and dependency context.",
            examples=(
                OperationExample(
                    title="Explain ticket provenance",
                    arguments={"project_id": 1, "ticket_key": "workflow-7-deliver"},
                ),
            ),
        ),
        _read_spec(
            name="tracker.execute",
            summary="Return the ticket execution brief without changing state.",
            input_model=TrackerTicketInput,
            output_model=TrackerBriefOut | dict[str, Any],
            handler=tracker_execute,
            purpose=(
                "Use this as the llm-tracker-style execution context before "
                "making code/tool changes."
            ),
            examples=(
                OperationExample(
                    title="Fetch execution context",
                    arguments={"project_id": 1, "ticket_key": "manual-comms-send"},
                ),
            ),
        ),
        _read_spec(
            name="tracker.verify",
            summary="Check whether a ticket is verification-ready and what remains.",
            input_model=TrackerTicketInput,
            output_model=TrackerVerifyOut | dict[str, Any],
            handler=tracker_verify,
            purpose="Use this before marking a ticket complete or asking a human for signoff.",
            examples=(
                OperationExample(
                    title="Check completion readiness",
                    arguments={"project_id": 1, "ticket_key": "manual-comms-send"},
                ),
            ),
        ),
        _read_spec(
            name="tracker.history",
            summary="Read append-only tracker revisions.",
            input_model=TrackerHistoryInput,
            output_model=Page[TrackerHistoryOut] | dict[str, Any],
            handler=tracker_history,
            purpose="Use this for audit and context recovery across prior runs.",
            examples=(
                OperationExample(
                    title="Read recent tracker history",
                    arguments={"project_id": 1, "limit": 20, "response_mode": "compact"},
                ),
            ),
        ),
        _read_spec(
            name="tracker.taskActivity",
            summary="Read persisted task-scoped history with stable cursor paging.",
            input_model=TrackerTaskActivityInput,
            output_model=TaskActivityOut,
            handler=tracker_task_activity,
            purpose=(
                "Inspect linked events and structured delivery lifecycle revisions. "
                "Coverage is not a complete internal audit; reads never write history."
            ),
            examples=(
                OperationExample(
                    title="Read older task history",
                    arguments={"project_id": 1, "task_key": "manual-work", "limit": 50},
                ),
            ),
        ),
        _read_spec(
            name="tracker.taskResults",
            summary="Read canonical results associated through explicit task provenance.",
            input_model=TrackerTaskResultsInput,
            output_model=TaskResultsOut,
            handler=tracker_task_results,
            purpose=(
                "Open shared Library details using canonical source identities and every explicit "
                "association reason. Historical execution contexts are included; "
                "association does not imply approval."
            ),
            examples=(
                OperationExample(
                    title="Read task files",
                    arguments={
                        "project_id": 1,
                        "task_key": "manual-work",
                        "source_kind": "artifact",
                    },
                ),
            ),
        ),
        _read_spec(
            name="tracker.changed",
            summary="Read tracker changes after a known revision.",
            input_model=TrackerChangedInput,
            output_model=TrackerChangedOut | dict[str, Any],
            handler=tracker_changed,
            purpose="Use this for efficient context refresh without reloading the whole tracker.",
            examples=(
                OperationExample(
                    title="Refresh from a known revision",
                    arguments={"project_id": 1, "since_rev": 120, "limit": 50},
                ),
            ),
        ),
        _read_spec(
            name="tracker.search",
            summary="Search project tracker tasks and tickets.",
            input_model=TrackerSearchInput,
            output_model=TrackerSearchOut | dict[str, Any],
            handler=tracker_search,
            purpose="Use this when the agent has a keyword but not a task/ticket key.",
            examples=(
                OperationExample(
                    title="Find a task or ticket by phrase",
                    arguments={
                        "project_id": 1,
                        "query": "telegram reply",
                        "response_mode": "compact",
                    },
                ),
            ),
        ),
        _write_spec(
            name="tracker.createTask",
            summary="Create one durable project task.",
            input_model=TrackerCreateTaskInput,
            handler=tracker_create_task,
            purpose="Use this when an agent/human has a work objective that can own tickets.",
            examples=(
                OperationExample(
                    title="Create an implementation task",
                    arguments={
                        "project_id": 1,
                        "key": "manual-comms",
                        "title": "Manual communications polish",
                        "goal": "Tighten agent-facing communication flows.",
                        "status": "in-progress",
                    },
                ),
            ),
        ),
        _write_spec(
            name="tracker.createTicket",
            summary="Create one executable ticket or validate/create a ticket list under a task.",
            input_model=TrackerCreateTicketInput,
            handler=tracker_create_ticket,
            purpose=(
                "Use this to split a task into clear executable units and dependencies. "
                "Pass tickets_json with dry_run=true to draft/review a list before "
                "creating tickets. For workflow execution, pass run_plan_id and step_id "
                "so StackOS attaches the work to the mirrored workflow step without "
                "requiring generated tracker keys. Pass both fields together or omit "
                "both; if only one is known, fetch tracker.brief, tracker.get, or "
                "runPlan.get before retrying. Attachment does not add dependency edges; "
                "pass dependency_keys or dependencies_json to bridge workflow execution "
                "order. Workflow-backed tasks use the run-plan step tickets as the "
                "single execution spine, so child tickets must attach to one step and "
                "the graph should have exactly one root workflow step ticket."
            ),
            examples=(
                OperationExample(
                    title="Dry-run a ticket list",
                    arguments={
                        "project_id": 1,
                        "task_key": "core-tracker",
                        "tickets_json": [
                            {"key": "core-tracker-schema", "title": "Add tracker schema"},
                            {
                                "key": "core-tracker-ui",
                                "title": "Expose tracker UI",
                                "dependency_keys": ["core-tracker-schema"],
                            },
                        ],
                        "dry_run": True,
                    },
                ),
                OperationExample(
                    title="Create a ticket under a workflow step",
                    arguments={
                        "project_id": 1,
                        "run_plan_id": 42,
                        "step_id": "deliver",
                        "key": "fix-media-handoff",
                        "title": "Forward source media in the canonical handoff",
                        "dependency_keys": ["workflow-42-deliver"],
                    },
                ),
            ),
        ),
        _write_spec(
            name="tracker.updateTask",
            summary="Update one task using an explicit patch.",
            input_model=TrackerUpdateTaskInput,
            handler=tracker_update_task,
            purpose="Use this for task status, ownership, lane, priority, and metadata updates.",
            examples=(
                OperationExample(
                    title="Mark a task ready for review",
                    arguments={
                        "project_id": 1,
                        "task_key": "manual-comms",
                        "patch_json": {
                            "status": "in-progress",
                            "lane_key": "review",
                        },
                    },
                ),
            ),
        ),
        OperationSpec(
            name="tracker.updateTaskContent",
            summary="Save a task description or append operator input without changing execution.",
            input_model=TrackerUpdateTaskContentInput,
            output_model=WriteEnvelope[TrackerTaskContentOut],
            handler=tracker_update_task_content,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=False),
                rest=OperationSurface(
                    enabled=True,
                    browser_safe=True,
                    path="/api/v1/operations/tracker.updateTaskContent/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call tracker.updateTaskContent"),
            ),
            mutating=True,
            grant_policy="local-admin-console-write",
            idempotency_policy="handler",
            response_policy=OperationResponsePolicy(
                default_mode="raw",
                allowed_modes=("raw",),
                ack_safe=False,
                raw_only_reason="Preserve saved content, input receipt and concurrency timestamp.",
            ),
            purpose=(
                "Human task-description correction and append-only additional input. "
                "This never changes lifecycle, workflow fields, tickets, dependencies or execution."
            ),
            prerequisites=(
                "Read the task with tracker.get response_mode=raw and use its updated_at.",
                "Use one stable idempotency_key per submission; refresh after a conflict.",
            ),
            returns=(
                "Saved task, tracker revision, appended input and intent-bound retry receipt.",
            ),
            examples=(
                OperationExample(
                    title="Add operator context to an existing task",
                    arguments={
                        "project_id": 1,
                        "task_key": "workflow-42",
                        "expected_updated_at": "2026-10-28T12:00:00",
                        "idempotency_key": "work-input-42-1",
                        "additional_input": "Also verify the narrow layout.",
                    },
                ),
            ),
        ),
        _write_spec(
            name="tracker.rejectTask",
            summary="Reject a tracker task or run-plan mirror and cascade child tickets.",
            input_model=TrackerRejectTaskInput,
            handler=tracker_reject_task,
            purpose=(
                "Use this when the operator rejects or aborts a task/run. "
                "The task becomes aborted with explicit rejection evidence; every child "
                "ticket closes as aborted with rejection outcome and metadata so there is "
                "one clear terminal state. Workflow-backed rejection applies only before "
                "the run plan completes or fails; completed/failed run plans remain "
                "canonical and should get follow-up tracker work instead."
            ),
            examples=(
                OperationExample(
                    title="Reject a workflow task by run plan",
                    arguments={
                        "project_id": 1,
                        "run_plan_id": 10,
                        "reason": "Operator parked this support investigation.",
                        "actor": "codex",
                    },
                ),
                OperationExample(
                    title="Reject a manual tracker task",
                    arguments={
                        "project_id": 1,
                        "task_key": "manual-comms",
                        "reason": "No longer needed after product decision.",
                        "actor": "codex",
                    },
                ),
            ),
        ),
        _write_spec(
            name="tracker.reopen",
            summary="Reopen a tracker task or workflow run plan with one command.",
            input_model=TrackerReopenInput,
            output_model=WriteEnvelope[TrackerReopenOut],
            handler=tracker_reopen,
            purpose=(
                "Use this when more work is discovered after a task or workflow was "
                "closed. StackOS resolves whether the target is a plain tracker task "
                "or a mirrored workflow run plan, then reopens the canonical lifecycle "
                "state in one operation. Returns safe run/reset-step references, "
                "without a controller token; controller callers use runPlan.start/reopen."
            ),
            examples=(
                OperationExample(
                    title="Reopen a workflow task",
                    arguments={
                        "project_id": 2,
                        "task_key": "workflow-27",
                        "reason": "More form follow-up work was found after closeout.",
                        "actor": "codex",
                    },
                ),
                OperationExample(
                    title="Reopen a manual task",
                    arguments={
                        "project_id": 1,
                        "task_key": "manual-comms",
                        "reason": "A new requirement was identified.",
                    },
                ),
            ),
        ),
        _write_spec(
            name="tracker.updateTicket",
            summary="Update one ticket or apply a list of atomic ticket patches.",
            input_model=TrackerUpdateTicketInput,
            handler=tracker_update_ticket,
            purpose=(
                "Use this for ticket status, assignee, blockers, outcome, evidence, and "
                "dependencies. Pass updates_json when several tickets need independent "
                "patch-only updates by ticket_key or ticket_id. For dependency changes, "
                "prefer add_dependency_keys/remove_dependency_keys unless replacing the "
                "whole dependency list. Add dry_run=true to preview dependency diffs "
                "without writing before a larger cleanup."
            ),
            examples=(
                OperationExample(
                    title="Patch a ticket list with safe dependency edits",
                    arguments={
                        "project_id": 1,
                        "updates_json": [
                            {
                                "ticket_key": "core-tracker-schema",
                                "patch_json": {"status": "complete"},
                            },
                            {
                                "ticket_key": "core-tracker-ui",
                                "patch_json": {
                                    "assignee": "codex",
                                    "add_dependency_keys": ["core-tracker-api"],
                                    "remove_dependency_keys": ["core-tracker-schema"],
                                },
                            },
                        ],
                    },
                ),
                OperationExample(
                    title="Preview dependency changes before applying",
                    arguments={
                        "project_id": 1,
                        "ticket_key": "core-tracker-ui",
                        "patch_json": {
                            "add_dependency_keys": ["core-tracker-api"],
                            "remove_dependency_keys": ["core-tracker-schema"],
                        },
                        "dry_run": True,
                    },
                ),
            ),
        ),
        _write_spec(
            name="tracker.patch",
            summary="Apply a small multi-entity task/ticket patch.",
            input_model=TrackerPatchInput,
            handler=tracker_patch,
            purpose="Use this when several tracker updates are part of one agent decision.",
            examples=(
                OperationExample(
                    title="Patch task and ticket state together",
                    arguments={
                        "project_id": 1,
                        "patch_json": {
                            "tasks": {"manual-comms": {"lane_key": "delivery"}},
                            "tickets": {"manual-comms-send": {"status": "in-progress"}},
                        },
                    },
                ),
            ),
        ),
        _write_spec(
            name="tracker.pick",
            summary="Claim a ready ticket or one explicit ticket for an assignee.",
            input_model=TrackerPickInput,
            handler=tracker_pick,
            purpose="Use this before work starts so ownership and in-progress state are recorded.",
            examples=(
                OperationExample(
                    title="Claim one ready ticket",
                    arguments={
                        "project_id": 1,
                        "ticket_key": "manual-comms-send",
                        "assignee": "codex",
                    },
                ),
            ),
        ),
        _write_spec(
            name="tracker.release",
            summary="Release one ticket's assignee without changing its status.",
            input_model=TrackerReleaseInput,
            handler=tracker_release,
            purpose="Use this when an agent stops owning a ticket.",
            examples=(
                OperationExample(
                    title="Release a claimed ticket",
                    arguments={
                        "project_id": 1,
                        "ticket_key": "manual-comms-send",
                    },
                ),
            ),
        ),
        _write_spec(
            name="tracker.linkRunPlan",
            summary="Link an existing task to a run plan.",
            input_model=TrackerLinkRunPlanInput,
            handler=tracker_link_run_plan,
            purpose=(
                "Use this when a manually created task should point at workflow execution state."
            ),
            examples=(
                OperationExample(
                    title="Link a manual task to workflow execution",
                    arguments={
                        "project_id": 1,
                        "task_key": "manual-comms",
                        "run_plan_id": 12,
                    },
                ),
            ),
        ),
    ]


__all__ = [
    "operation_specs",
]
