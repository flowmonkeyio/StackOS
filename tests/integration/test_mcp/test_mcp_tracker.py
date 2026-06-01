"""MCP parity tests for project task tracker operations."""

from __future__ import annotations

from .conftest import MCPClient


def _workflow_step_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "tracker.workflow-step.run",
        "title": "Tracker workflow step",
        "steps": [{"id": "deliver", "title": "Deliver"}],
    }


def _workflow_two_step_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "tracker.workflow-spine.run",
        "title": "Tracker workflow spine",
        "steps": [
            {"id": "deliver", "title": "Deliver"},
            {"id": "verify", "title": "Verify", "depends_on": ["deliver"]},
        ],
    }


def test_tracker_operations_are_registered(mcp_client: MCPClient) -> None:
    listed_tools = mcp_client.list_tools()
    tools = {tool["name"] for tool in listed_tools}

    assert {
        "tracker.status",
        "tracker.get",
        "tracker.next",
        "tracker.blockers",
        "tracker.brief",
        "tracker.why",
        "tracker.execute",
        "tracker.verify",
        "tracker.history",
        "tracker.changed",
        "tracker.search",
        "tracker.createTask",
        "tracker.createTicket",
        "tracker.updateTask",
        "tracker.updateTicket",
        "tracker.patch",
        "tracker.rejectTask",
        "tracker.pick",
        "tracker.release",
        "tracker.linkRunPlan",
    } <= tools
    by_name = {tool["name"]: tool for tool in listed_tools}
    create_props = by_name["tracker.createTicket"]["inputSchema"]["properties"]
    update_props = by_name["tracker.updateTicket"]["inputSchema"]["properties"]
    reject_props = by_name["tracker.rejectTask"]["inputSchema"]["properties"]
    assert {"tickets_json", "dependencies_json", "dry_run", "run_plan_id", "step_id"} <= set(
        create_props
    )
    assert "does not add dependency edges" in create_props["run_plan_id"]["description"]
    assert "Attachment is containment/linkage only" in create_props["step_id"]["description"]
    assert {"updates_json", "dry_run"} <= set(update_props)
    assert {"task_key", "run_plan_id", "reason"} <= set(reject_props)


def test_tracker_mcp_vertical_slice(mcp_client: MCPClient, seeded_project: dict) -> None:
    project_id = int(seeded_project["data"]["id"])

    created_task = mcp_client.call_tool_structured(
        "tracker.createTask",
        {
            "project_id": project_id,
            "key": "mcp-tracker",
            "title": "MCP tracker",
            "created_by": "mcp-test",
        },
    )
    assert created_task["data"]["task"]["key"] == "mcp-tracker"

    created_ticket = mcp_client.call_tool_structured(
        "tracker.createTicket",
        {
            "project_id": project_id,
            "task_key": "mcp-tracker",
            "key": "mcp-ticket",
            "title": "MCP ticket",
            "created_by": "mcp-test",
        },
    )
    assert created_ticket["data"]["ticket"]["key"] == "mcp-ticket"

    next_work = mcp_client.call_tool_structured("tracker.next", {"project_id": project_id})
    assert [ticket["key"] for ticket in next_work["tickets"]] == ["mcp-ticket"]
    assert "project_id" not in next_work["tickets"][0]

    status = mcp_client.call_tool_structured("tracker.status", {"project_id": project_id})
    assert status["tracker_id"]
    assert "tracker" not in status

    picked = mcp_client.call_tool_structured(
        "tracker.pick",
        {
            "project_id": project_id,
            "ticket_key": "mcp-ticket",
            "assignee": "codex",
        },
    )
    assert picked["data"]["ticket"]["status"] == "in-progress"
    assert picked["data"]["ticket"]["assignee"] == "codex"

    brief = mcp_client.call_tool_structured(
        "tracker.brief",
        {"project_id": project_id, "ticket_key": "mcp-ticket"},
    )
    assert brief["ticket"]["key"] == "mcp-ticket"
    assert brief["task"]["key"] == "mcp-tracker"
    assert "project_id" not in brief["ticket"]
    assert "created_at" not in brief["ticket"]

    full_brief = mcp_client.call_tool_structured(
        "tracker.brief",
        {"project_id": project_id, "ticket_key": "mcp-ticket", "response_mode": "standard"},
    )
    assert full_brief["ticket"]["project_id"] == project_id
    assert full_brief["ticket"]["created_at"]

    history = mcp_client.call_tool_structured(
        "tracker.history",
        {"project_id": project_id, "limit": 5},
    )
    assert history["items"]
    assert "summary" in history["items"][0]
    assert "patch_json" not in history["items"][0]

    full_history = mcp_client.call_tool_structured(
        "tracker.history",
        {"project_id": project_id, "limit": 5, "response_mode": "standard"},
    )
    assert "patch_json" in full_history["items"][0]

    changed = mcp_client.call_tool_structured(
        "tracker.changed",
        {"project_id": project_id, "since_rev": 0, "limit": 5},
    )
    assert changed["changes"]
    assert "before_json" not in changed["changes"][0]

    searched = mcp_client.call_tool_structured(
        "tracker.search",
        {"project_id": project_id, "query": "mcp-ticket"},
    )
    assert [ticket["key"] for ticket in searched["tickets"]] == ["mcp-ticket"]
    assert "project_id" not in searched["tickets"][0]


def test_tracker_create_ticket_can_target_workflow_step(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    plan = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _workflow_step_plan_json()},
    )
    run_plan_id = int(plan["data"]["id"])
    workflow_task_key = f"workflow-{run_plan_id}"
    workflow_step_ticket_key = f"{workflow_task_key}-deliver"

    created = mcp_client.call_tool_structured(
        "tracker.createTicket",
        {
            "project_id": project_id,
            "run_plan_id": run_plan_id,
            "step_id": "deliver",
            "key": "workflow-child-single",
            "title": "Single child ticket",
            "source_json": {"chat_ref": "slack:thread:123"},
            "created_by": "mcp-test",
            "response_mode": "raw",
        },
    )

    assert created["data"]["task"]["key"] == workflow_task_key
    assert created["data"]["ticket"]["parent_ticket_key"] == workflow_step_ticket_key
    assert created["data"]["ticket"]["run_plan_id"] == run_plan_id
    assert created["data"]["ticket"]["run_plan_step_id"] is not None
    assert created["data"]["ticket"]["source_json"]["chat_ref"] == "slack:thread:123"
    assert created["data"]["ticket"]["source_json"]["step_id"] == "deliver"
    detached_verify = mcp_client.call_tool_structured(
        "tracker.verify",
        {"project_id": project_id, "ticket_key": "workflow-child-single"},
    )
    detached_checks = {check["key"]: check for check in detached_verify["checks"]}
    assert detached_checks["workflow-step-child-bridge"]["passed"] is False
    assert detached_checks["workflow-child-reachable-from-step"]["passed"] is False
    detached_step_verify = mcp_client.call_tool_structured(
        "tracker.verify",
        {"project_id": project_id, "ticket_key": workflow_step_ticket_key},
    )
    detached_step_checks = {check["key"]: check for check in detached_step_verify["checks"]}
    assert detached_step_checks["workflow-step-child-bridge"]["passed"] is False
    assert detached_step_checks["workflow-step-children-reachable"]["passed"] is False
    assert detached_step_checks["workflow-step-open-children"]["passed"] is False
    detached_snapshot = mcp_client.call_tool_structured(
        "tracker.get",
        {
            "project_id": project_id,
            "task_key": workflow_task_key,
            "response_mode": "raw",
        },
    )
    assert any(
        "Attachment is containment only" in warning
        for warning in detached_snapshot["graph"]["warnings"]
    )

    list_payload = {
        "project_id": project_id,
        "run_plan_id": run_plan_id,
        "step_id": "deliver",
        "tickets_json": [
            {
                "key": "workflow-child-a",
                "title": "Child A",
                "dependency_keys": [workflow_step_ticket_key],
            },
            {
                "key": "workflow-child-b",
                "title": "Child B",
                "dependency_keys": ["workflow-child-a"],
            },
        ],
        "created_by": "mcp-test",
        "response_mode": "raw",
    }
    dry_run = mcp_client.call_tool_structured(
        "tracker.createTicket",
        {**list_payload, "dry_run": True},
    )
    imported = mcp_client.call_tool_structured("tracker.createTicket", list_payload)

    assert dry_run["data"]["valid"] is True
    assert imported["data"]["task"]["key"] == workflow_task_key
    assert [ticket["parent_ticket_key"] for ticket in imported["data"]["tickets"]] == [
        workflow_step_ticket_key,
        workflow_step_ticket_key,
    ]
    assert imported["data"]["tickets"][0]["run_plan_id"] == run_plan_id
    assert imported["data"]["tickets"][0]["source_json"]["step_id"] == "deliver"
    dependency_pairs = {
        (item["ticket_key"], item["depends_on_ticket_key"])
        for item in imported["data"]["dependencies"]
    }
    assert ("workflow-child-a", workflow_step_ticket_key) in dependency_pairs
    assert ("workflow-child-b", "workflow-child-a") in dependency_pairs


def test_tracker_verify_flags_workflow_gate_children_that_bypass_delivery(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    plan = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _workflow_two_step_plan_json()},
    )
    run_plan_id = int(plan["data"]["id"])
    workflow_task_key = f"workflow-{run_plan_id}"
    deliver_step_key = f"{workflow_task_key}-deliver"

    mcp_client.call_tool_structured(
        "tracker.createTicket",
        {
            "project_id": project_id,
            "run_plan_id": run_plan_id,
            "step_id": "deliver",
            "tickets_json": [
                {
                    "key": "workflow-spine-impl-a",
                    "title": "Implement customer fix A",
                    "dependency_keys": [deliver_step_key],
                },
                {
                    "key": "workflow-spine-impl-b",
                    "title": "Implement customer fix B",
                    "dependency_keys": [deliver_step_key],
                },
                {
                    "key": "workflow-spine-signoff",
                    "title": "Signoff evidence",
                    "lane_key": "review",
                    "dependency_keys": ["workflow-spine-impl-a"],
                },
            ],
            "created_by": "mcp-test",
            "response_mode": "raw",
        },
    )

    signoff_verify = mcp_client.call_tool_structured(
        "tracker.verify",
        {"project_id": project_id, "ticket_key": "workflow-spine-signoff"},
    )
    signoff_checks = {check["key"]: check for check in signoff_verify["checks"]}
    assert signoff_checks["workflow-child-reachable-from-step"]["passed"] is True
    assert signoff_checks["workflow-child-gate-contained"]["passed"] is False

    deliver_verify = mcp_client.call_tool_structured(
        "tracker.verify",
        {"project_id": project_id, "ticket_key": deliver_step_key},
    )
    deliver_checks = {check["key"]: check for check in deliver_verify["checks"]}
    assert deliver_checks["workflow-step-gate-children-contained"]["passed"] is False

    snapshot = mcp_client.call_tool_structured(
        "tracker.get",
        {
            "project_id": project_id,
            "task_key": workflow_task_key,
            "response_mode": "raw",
        },
    )
    assert any("can bypass delivery work" in warning for warning in snapshot["graph"]["warnings"])


def test_tracker_verify_flags_workflow_terminal_child_handoffs(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    plan = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _workflow_two_step_plan_json()},
    )
    run_plan_id = int(plan["data"]["id"])
    workflow_task_key = f"workflow-{run_plan_id}"
    deliver_step_key = f"{workflow_task_key}-deliver"
    verify_step_key = f"{workflow_task_key}-verify"

    mcp_client.call_tool_structured(
        "tracker.createTicket",
        {
            "project_id": project_id,
            "run_plan_id": run_plan_id,
            "step_id": "deliver",
            "tickets_json": [
                {
                    "key": "workflow-handoff-impl",
                    "title": "Implement customer fix",
                    "dependency_keys": [deliver_step_key],
                },
                {
                    "key": "workflow-handoff-signoff",
                    "title": "Signoff evidence",
                    "lane_key": "review",
                    "dependency_keys": ["workflow-handoff-impl"],
                },
            ],
            "created_by": "mcp-test",
            "response_mode": "raw",
        },
    )

    missing_handoff = mcp_client.call_tool_structured(
        "tracker.verify",
        {"project_id": project_id, "ticket_key": verify_step_key},
    )
    missing_checks = {check["key"]: check for check in missing_handoff["checks"]}
    assert missing_checks["workflow-next-step-terminal-children"]["passed"] is False
    assert (
        "workflow-handoff-signoff"
        in missing_checks["workflow-next-step-terminal-children"]["detail"]
    )

    snapshot = mcp_client.call_tool_structured(
        "tracker.get",
        {
            "project_id": project_id,
            "task_key": workflow_task_key,
            "response_mode": "raw",
        },
    )
    assert any(
        "terminal child tickets" in warning and verify_step_key in warning
        for warning in snapshot["graph"]["warnings"]
    )

    mcp_client.call_tool_structured(
        "tracker.updateTicket",
        {
            "project_id": project_id,
            "ticket_key": verify_step_key,
            "patch_json": {"add_dependency_keys": ["workflow-handoff-signoff"]},
            "actor": "mcp-test",
            "response_mode": "raw",
        },
    )
    repaired_handoff = mcp_client.call_tool_structured(
        "tracker.verify",
        {"project_id": project_id, "ticket_key": verify_step_key},
    )
    repaired_checks = {check["key"]: check for check in repaired_handoff["checks"]}
    assert repaired_checks["workflow-next-step-terminal-children"]["passed"] is True

    open_step = mcp_client.call_tool_structured(
        "tracker.verify",
        {"project_id": project_id, "ticket_key": deliver_step_key},
    )
    open_step_checks = {check["key"]: check for check in open_step["checks"]}
    assert open_step_checks["workflow-step-open-children"]["passed"] is False

    mcp_client.call_tool_structured(
        "tracker.updateTicket",
        {
            "project_id": project_id,
            "ticket_key": deliver_step_key,
            "patch_json": {"status": "complete"},
            "actor": "mcp-test",
            "response_mode": "raw",
        },
    )
    completed_step = mcp_client.call_tool_structured(
        "tracker.verify",
        {"project_id": project_id, "ticket_key": deliver_step_key},
    )
    completed_checks = {check["key"]: check for check in completed_step["checks"]}
    assert completed_checks["workflow-step-open-children"]["passed"] is False


def test_tracker_mcp_create_get_update_accept_ticket_lists(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])

    mcp_client.call_tool_structured(
        "tracker.createTask",
        {
            "project_id": project_id,
            "key": "mcp-ticket-list",
            "title": "MCP ticket list",
            "created_by": "mcp-test",
        },
    )
    list_payload = {
        "project_id": project_id,
        "task_key": "mcp-ticket-list",
        "tickets_json": [
            {
                "key": "mcp-ticket-list-schema",
                "title": "Schema contract",
                "completion_evidence_json": {"changed_files": ["stackos/operations/tracker.py"]},
            },
            {
                "key": "mcp-ticket-list-ui",
                "title": "UI review",
                "dependency_keys": ["mcp-ticket-list-schema"],
            },
            {
                "key": "mcp-ticket-list-docs",
                "title": "Docs review",
            },
        ],
        "created_by": "mcp-test",
        "response_mode": "raw",
    }

    dry_run = mcp_client.call_tool_structured(
        "tracker.createTicket",
        {**list_payload, "dry_run": True},
    )
    assert dry_run["data"]["dry_run"] is True
    assert dry_run["data"]["valid"] is True
    assert [result["action"] for result in dry_run["data"]["results"]] == [
        "validated",
        "validated",
        "validated",
    ]

    empty_review = mcp_client.call_tool_structured(
        "tracker.get",
        {
            "project_id": project_id,
            "task_key": "mcp-ticket-list",
            "include_graph": False,
            "response_mode": "raw",
        },
    )
    assert empty_review["tickets"] == []

    imported = mcp_client.call_tool_structured("tracker.createTicket", list_payload)
    assert imported["data"]["valid"] is True
    assert [ticket["key"] for ticket in imported["data"]["tickets"]] == [
        "mcp-ticket-list-schema",
        "mcp-ticket-list-ui",
        "mcp-ticket-list-docs",
    ]
    assert imported["data"]["dependencies"][0]["depends_on_ticket_key"] == "mcp-ticket-list-schema"

    preview = mcp_client.call_tool_structured(
        "tracker.updateTicket",
        {
            "project_id": project_id,
            "ticket_key": "mcp-ticket-list-ui",
            "patch_json": {
                "add_dependency_keys": ["mcp-ticket-list-docs"],
                "remove_dependency_keys": ["mcp-ticket-list-schema"],
            },
            "dry_run": True,
            "response_mode": "raw",
        },
    )
    assert preview["data"]["dry_run"] is True
    assert preview["data"]["valid"] is True
    assert preview["data"]["dependency_preview"]["added_dependency_keys"] == [
        "mcp-ticket-list-docs"
    ]
    assert preview["data"]["dependency_preview"]["removed_dependency_keys"] == [
        "mcp-ticket-list-schema"
    ]

    review = mcp_client.call_tool_structured(
        "tracker.get",
        {
            "project_id": project_id,
            "task_key": "mcp-ticket-list",
            "ticket_keys": ["mcp-ticket-list-ui"],
            "include_graph": False,
            "response_mode": "raw",
        },
    )
    assert [ticket["key"] for ticket in review["tickets"]] == ["mcp-ticket-list-ui"]

    updated = mcp_client.call_tool_structured(
        "tracker.updateTicket",
        {
            "project_id": project_id,
            "updates_json": [
                {
                    "ticket_key": "mcp-ticket-list-schema",
                    "patch_json": {
                        "status": "complete",
                        "completion_evidence_json": {
                            "changed_files": ["stackos/repositories/tracker.py"],
                            "summary": "Bulk list path verified.",
                        },
                    },
                },
                {
                    "ticket_key": "mcp-ticket-list-ui",
                    "patch_json": {
                        "assignee": "codex",
                        "add_dependency_keys": ["mcp-ticket-list-docs"],
                        "remove_dependency_keys": ["mcp-ticket-list-schema"],
                    },
                },
            ],
            "actor": "mcp-test",
        },
    )
    assert updated["data"]["valid"] is True
    assert [result["action"] for result in updated["data"]["results"]] == ["updated", "updated"]

    snapshot = mcp_client.call_tool_structured(
        "tracker.get",
        {
            "project_id": project_id,
            "task_key": "mcp-ticket-list",
            "include_graph": False,
            "response_mode": "raw",
        },
    )
    by_key = {ticket["key"]: ticket for ticket in snapshot["tickets"]}
    assert by_key["mcp-ticket-list-schema"]["completion_evidence_json"]["summary"] == (
        "Bulk list path verified."
    )
    assert by_key["mcp-ticket-list-ui"]["assignee"] == "codex"
    assert by_key["mcp-ticket-list-ui"]["dependency_keys"] == ["mcp-ticket-list-docs"]


def test_tracker_mcp_reject_task_cascades_tickets(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])

    mcp_client.call_tool_structured(
        "tracker.createTask",
        {
            "project_id": project_id,
            "key": "mcp-reject-task",
            "title": "MCP reject task",
            "created_by": "mcp-test",
        },
    )
    mcp_client.call_tool_structured(
        "tracker.createTicket",
        {
            "project_id": project_id,
            "task_key": "mcp-reject-task",
            "tickets_json": [
                {"key": "mcp-reject-open", "title": "Open"},
                {"key": "mcp-reject-done", "title": "Done"},
            ],
            "created_by": "mcp-test",
        },
    )
    mcp_client.call_tool_structured(
        "tracker.updateTicket",
        {
            "project_id": project_id,
            "ticket_key": "mcp-reject-done",
            "patch_json": {"status": "complete", "outcome": "Previously complete."},
            "actor": "mcp-test",
        },
    )

    rejected = mcp_client.call_tool_structured(
        "tracker.rejectTask",
        {
            "project_id": project_id,
            "task_key": "mcp-reject-task",
            "reason": "Operator api_key=sk-secret rejected this task.",
            "actor": "mcp-test",
            "response_mode": "raw",
        },
    )
    snapshot = mcp_client.call_tool_structured(
        "tracker.get",
        {
            "project_id": project_id,
            "task_key": "mcp-reject-task",
            "include_graph": False,
            "response_mode": "raw",
        },
    )

    assert rejected["data"]["task"]["status"] == "aborted"
    assert rejected["data"]["task"]["completion_evidence_json"]["decision"] == "rejected"
    assert "[redacted]" in rejected["data"]["task"]["completion_evidence_json"]["reason"]
    assert "sk-secret" not in str(rejected)
    assert [result["action"] for result in rejected["data"]["results"]] == [
        "rejected",
        "rejected",
    ]
    assert {ticket["status"] for ticket in snapshot["tickets"]} == {"aborted"}


def test_tracker_mcp_reject_completed_workflow_does_not_override_run_plan(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = int(seeded_project["data"]["id"])
    plan = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _workflow_step_plan_json()},
    )
    run_plan_id = int(plan["data"]["id"])
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": run_plan_id},
    )
    run_token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {"run_plan_id": run_plan_id, "step_id": "deliver", "run_token": run_token},
    )
    mcp_client.call_tool_structured(
        "runPlan.recordStep",
        {
            "run_plan_id": run_plan_id,
            "step_id": "deliver",
            "status": "success",
            "result_json": {"summary": "delivered"},
            "run_token": run_token,
        },
    )

    rejected = mcp_client.call_tool_error(
        "tracker.rejectTask",
        {
            "project_id": project_id,
            "run_plan_id": run_plan_id,
            "reason": "Operator rejected after completion.",
            "actor": "mcp-test",
        },
    )

    assert rejected["message"] == "ConflictError"
    assert rejected["data"]["run_plan_id"] == run_plan_id
    assert rejected["data"]["run_plan_status"] == "completed"
    snapshot = mcp_client.call_tool_structured(
        "tracker.get",
        {
            "project_id": project_id,
            "task_key": f"workflow-{run_plan_id}",
            "include_graph": False,
            "response_mode": "raw",
        },
    )
    assert snapshot["tasks"][0]["status"] == "complete"
    assert {ticket["status"] for ticket in snapshot["tickets"]} == {"complete"}
