from __future__ import annotations


def test_mcp_board_intents_use_same_repository_and_enforce_project(mcp_client, seeded_project):
    project_id = seeded_project["data"]["id"]
    tasks = []
    for key in ("first", "second"):
        tasks.append(
            mcp_client.call_tool_structured(
                "tracker.createTask",
                {
                    "project_id": project_id,
                    "key": key,
                    "title": key,
                    "response_mode": "raw",
                },
            )["data"]["task"]
        )
    first, second = tasks
    intent = {
        "project_id": project_id,
        "task_key": first["key"],
        "expected_status": first["status"],
        "expected_updated_at": first["updated_at"],
        "idempotency_key": "mcp-priority",
        "priority_key": "p0",
    }
    saved = mcp_client.call_tool_structured("tracker.updateTaskPriority", intent)
    assert saved["data"]["task"]["priority_key"] == "p0", saved
    replay = mcp_client.call_tool_structured("tracker.updateTaskPriority", intent)
    assert replay["data"]["replayed"] is True
    moved = mcp_client.call_tool_structured(
        "tracker.reorderTask",
        {
            "project_id": project_id,
            "task_key": second["key"],
            "expected_status": second["status"],
            "expected_updated_at": second["updated_at"],
            "idempotency_key": "mcp-move",
            "anchor_task_key": first["key"],
            "placement": "after",
        },
    )
    assert moved["data"]["changed"] is True, moved
    assert moved["data"]["task"]["order_index"] == 1
    missing = mcp_client.call_tool_error(
        "tracker.updateTaskPriority",
        {
            **intent,
            "project_id": project_id + 100,
        },
    )
    assert missing["code"] == -32004, missing
