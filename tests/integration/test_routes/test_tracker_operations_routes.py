from __future__ import annotations

from fastapi.testclient import TestClient


def test_tracker_operations_rest_vertical_slice(api: TestClient, project_id: int) -> None:
    created_task = api.post(
        "/api/v1/operations/tracker.createTask/call",
        json={
            "arguments": {
                "project_id": project_id,
                "key": "rest-tracker",
                "title": "REST tracker",
                "created_by": "route-test",
            }
        },
    )
    assert created_task.status_code == 200, created_task.text
    assert created_task.json()["data"]["task"]["key"] == "rest-tracker"

    created_ticket = api.post(
        "/api/v1/operations/tracker.createTicket/call",
        json={
            "arguments": {
                "project_id": project_id,
                "task_key": "rest-tracker",
                "key": "rest-ticket",
                "title": "REST ticket",
                "definition_of_done_json": ["operation call succeeds"],
                "created_by": "route-test",
            }
        },
    )
    assert created_ticket.status_code == 200, created_ticket.text
    assert created_ticket.json()["data"]["ticket"]["key"] == "rest-ticket"

    next_work = api.post(
        "/api/v1/operations/tracker.next/call",
        json={"arguments": {"project_id": project_id}},
    )
    assert next_work.status_code == 200, next_work.text
    assert next_work.json()["tickets"][0]["key"] == "rest-ticket"

    snapshot = api.post(
        "/api/v1/operations/tracker.get/call",
        json={"arguments": {"project_id": project_id, "include_graph": True}},
    )
    assert snapshot.status_code == 200, snapshot.text
    assert {node["id"] for node in snapshot.json()["graph"]["nodes"]} >= {
        "task:rest-tracker",
        "ticket:rest-ticket",
    }


def test_tracker_operations_rest_create_ticket_accepts_list(
    api: TestClient, project_id: int
) -> None:
    created_task = api.post(
        "/api/v1/operations/tracker.createTask/call",
        json={
            "arguments": {
                "project_id": project_id,
                "key": "rest-ticket-list",
                "title": "REST ticket list",
                "created_by": "route-test",
            }
        },
    )
    assert created_task.status_code == 200, created_task.text

    list_arguments = {
        "project_id": project_id,
        "task_key": "rest-ticket-list",
        "tickets_json": [
            {"key": "rest-ticket-list-schema", "title": "Schema"},
            {
                "key": "rest-ticket-list-ui",
                "title": "UI",
                "dependency_keys": ["rest-ticket-list-schema"],
            },
            {"key": "rest-ticket-list-docs", "title": "Docs"},
        ],
        "created_by": "route-test",
    }
    dry_run = api.post(
        "/api/v1/operations/tracker.createTicket/call",
        json={"arguments": {**list_arguments, "dry_run": True}},
    )
    assert dry_run.status_code == 200, dry_run.text
    assert dry_run.json()["data"]["dry_run"] is True
    assert dry_run.json()["data"]["valid"] is True

    imported = api.post(
        "/api/v1/operations/tracker.createTicket/call",
        json={"arguments": list_arguments},
    )
    assert imported.status_code == 200, imported.text
    assert [ticket["key"] for ticket in imported.json()["data"]["tickets"]] == [
        "rest-ticket-list-schema",
        "rest-ticket-list-ui",
        "rest-ticket-list-docs",
    ]

    update_preview = api.post(
        "/api/v1/operations/tracker.updateTicket/call",
        json={
            "arguments": {
                "project_id": project_id,
                "ticket_key": "rest-ticket-list-ui",
                "patch_json": {
                    "add_dependency_keys": ["rest-ticket-list-docs"],
                    "remove_dependency_keys": ["rest-ticket-list-schema"],
                },
                "dry_run": True,
            }
        },
    )
    assert update_preview.status_code == 200, update_preview.text
    assert update_preview.json()["data"]["dry_run"] is True
    assert update_preview.json()["data"]["dependency_preview"]["final_dependency_keys"] == [
        "rest-ticket-list-docs"
    ]

    updated = api.post(
        "/api/v1/operations/tracker.updateTicket/call",
        json={
            "arguments": {
                "project_id": project_id,
                "updates_json": [
                    {
                        "ticket_key": "rest-ticket-list-schema",
                        "patch_json": {"status": "complete"},
                    },
                    {
                        "ticket_key": "rest-ticket-list-ui",
                        "patch_json": {
                            "assignee": "codex",
                            "add_dependency_keys": ["rest-ticket-list-docs"],
                            "remove_dependency_keys": ["rest-ticket-list-schema"],
                        },
                    },
                ],
            }
        },
    )
    assert updated.status_code == 200, updated.text
    assert [result["action"] for result in updated.json()["data"]["results"]] == [
        "updated",
        "updated",
    ]

    reviewed = api.post(
        "/api/v1/operations/tracker.get/call",
        json={
            "arguments": {
                "project_id": project_id,
                "task_key": "rest-ticket-list",
                "ticket_keys": ["rest-ticket-list-ui"],
                "include_graph": False,
            }
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    reviewed_ticket = reviewed.json()["tickets"][0]
    assert reviewed_ticket["key"] == "rest-ticket-list-ui"
    assert reviewed_ticket["dependency_keys"] == ["rest-ticket-list-docs"]


def test_tracker_operations_rest_reject_task_cascades_tickets(
    api: TestClient,
    project_id: int,
) -> None:
    created_task = api.post(
        "/api/v1/operations/tracker.createTask/call",
        json={
            "arguments": {
                "project_id": project_id,
                "key": "rest-reject-task",
                "title": "REST reject task",
                "created_by": "route-test",
            }
        },
    )
    assert created_task.status_code == 200, created_task.text

    created_tickets = api.post(
        "/api/v1/operations/tracker.createTicket/call",
        json={
            "arguments": {
                "project_id": project_id,
                "task_key": "rest-reject-task",
                "tickets_json": [
                    {"key": "rest-reject-open", "title": "Open"},
                    {"key": "rest-reject-next", "title": "Next"},
                ],
                "created_by": "route-test",
            }
        },
    )
    assert created_tickets.status_code == 200, created_tickets.text

    rejected = api.post(
        "/api/v1/operations/tracker.rejectTask/call",
        json={
            "arguments": {
                "project_id": project_id,
                "task_key": "rest-reject-task",
                "reason": "Operator rejected this task.",
                "actor": "route-test",
            }
        },
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["data"]["task"]["status"] == "deferred"
    assert body["data"]["task"]["completion_evidence_json"]["decision"] == "rejected"
    assert [result["action"] for result in body["data"]["results"]] == [
        "rejected",
        "rejected",
    ]
