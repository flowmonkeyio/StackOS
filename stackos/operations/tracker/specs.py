"""Operation spec registration for tracker operations."""

from __future__ import annotations

from typing import Any

from stackos.operations.spec import (
    OperationExample,
    OperationSpec,
)
from stackos.operations.tracker.read_handlers import (
    tracker_blockers,
    tracker_brief,
    tracker_changed,
    tracker_execute,
    tracker_get,
    tracker_history,
    tracker_next,
    tracker_search,
    tracker_status,
    tracker_verify,
    tracker_why,
)
from stackos.operations.tracker.schemas import (
    TrackerChangedInput,
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
    TrackerSearchInput,
    TrackerTicketInput,
    TrackerUpdateTaskInput,
    TrackerUpdateTicketInput,
)
from stackos.operations.tracker.spec_helpers import (
    _read_spec,
    _write_spec,
)
from stackos.operations.tracker.write_handlers import (
    tracker_create_task,
    tracker_create_ticket,
    tracker_link_run_plan,
    tracker_patch,
    tracker_pick,
    tracker_reject_task,
    tracker_release,
    tracker_update_task,
    tracker_update_ticket,
)
from stackos.repositories.base import Page
from stackos.repositories.tracker import (
    TrackerBriefOut,
    TrackerChangedOut,
    TrackerHistoryOut,
    TrackerNextOut,
    TrackerSearchOut,
    TrackerSnapshotOut,
    TrackerStatusOut,
    TrackerVerifyOut,
)


def operation_specs() -> list[OperationSpec]:
    return [
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
                "Fetch filtered tasks, tickets, dependencies, links, and optional graph projection."
            ),
            input_model=TrackerGetInput,
            output_model=TrackerSnapshotOut,
            handler=tracker_get,
            purpose=(
                "Use this for UI rendering, list review by task/ticket ids, or when "
                "the agent needs a bounded project work map."
            ),
            examples=(
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
                "requiring generated tracker keys. Attachment does not add dependency "
                "edges; pass dependency_keys or dependencies_json to bridge workflow "
                "execution order."
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
        _write_spec(
            name="tracker.rejectTask",
            summary="Reject a tracker task or run-plan mirror and cascade child tickets.",
            input_model=TrackerRejectTaskInput,
            handler=tracker_reject_task,
            purpose=(
                "Use this when the operator rejects, parks, or supersedes a task/run. "
                "The task becomes deferred with explicit rejection evidence; every child "
                "ticket closes as deferred with rejection outcome and metadata so there is "
                "one clear terminal state."
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
