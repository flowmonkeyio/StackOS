"""MCP parity tests for project task tracker operations."""

from __future__ import annotations

from .conftest import MCPClient


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
        "tracker.pick",
        "tracker.release",
        "tracker.linkRunPlan",
    } <= tools
    by_name = {tool["name"]: tool for tool in listed_tools}
    create_props = by_name["tracker.createTicket"]["inputSchema"]["properties"]
    update_props = by_name["tracker.updateTicket"]["inputSchema"]["properties"]
    assert {"tickets_json", "dependencies_json", "dry_run"} <= set(create_props)
    assert {"updates_json", "dry_run"} <= set(update_props)


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
        {"project_id": project_id, "task_key": "mcp-ticket-list", "include_graph": False},
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
        {"project_id": project_id, "task_key": "mcp-ticket-list", "include_graph": False},
    )
    by_key = {ticket["key"]: ticket for ticket in snapshot["tickets"]}
    assert by_key["mcp-ticket-list-schema"]["completion_evidence_json"]["summary"] == (
        "Bulk list path verified."
    )
    assert by_key["mcp-ticket-list-ui"]["assignee"] == "codex"
    assert by_key["mcp-ticket-list-ui"]["dependency_keys"] == ["mcp-ticket-list-docs"]
