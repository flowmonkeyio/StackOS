from __future__ import annotations

from fastapi.testclient import TestClient


def _operation_data(response):
    body = response.json()
    return body.get("data", body)


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
    assert _operation_data(next_work)["tickets"][0]["key"] == "rest-ticket"
    assert "stackos-operation;dur=" in next_work.headers["server-timing"]
    assert "stackos-http;dur=" in next_work.headers["server-timing"]
    assert int(next_work.headers["x-stackos-operation-duration-ms"]) >= 0

    snapshot = api.post(
        "/api/v1/operations/tracker.get/call",
        json={
            "arguments": {
                "project_id": project_id,
                "include_graph": True,
                "response_mode": "raw",
            }
        },
    )
    assert snapshot.status_code == 200, snapshot.text
    assert {node["id"] for node in _operation_data(snapshot)["graph"]["nodes"]} >= {
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
        json={"arguments": {**list_arguments, "response_mode": "raw"}},
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
                "response_mode": "raw",
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
                "response_mode": "raw",
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
                "response_mode": "raw",
            }
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    reviewed_ticket = _operation_data(reviewed)["tickets"][0]
    assert reviewed_ticket["key"] == "rest-ticket-list-ui"
    assert reviewed_ticket["dependency_keys"] == ["rest-ticket-list-docs"]


def test_tracker_operations_rest_rejects_workflow_list_item_parent_conflict(
    api: TestClient,
    project_id: int,
) -> None:
    created = api.post(
        "/api/v1/operations/runPlan.create/call",
        json={
            "arguments": {
                "project_id": project_id,
                "run_plan_json": {
                    "schema_version": "stackos.run-plan.v1",
                    "key": "rest-parent-conflict-workflow.run",
                    "title": "REST parent conflict workflow",
                    "steps": [
                        {"id": "scope", "title": "Scope"},
                        {"id": "deliver", "title": "Deliver", "depends_on": ["scope"]},
                    ],
                },
            }
        },
    )
    assert created.status_code == 200, created.text
    run_plan_id = created.json()["data"]["id"]
    conflicting_arguments = {
        "project_id": project_id,
        "run_plan_id": run_plan_id,
        "step_id": "deliver",
        "tickets_json": [
            {
                "key": "rest-parent-conflict-child",
                "title": "Parent conflict child",
                "parent_ticket_key": f"workflow-{run_plan_id}-scope",
            }
        ],
        "created_by": "route-test",
    }

    dry_run = api.post(
        "/api/v1/operations/tracker.createTicket/call",
        json={"arguments": {**conflicting_arguments, "dry_run": True}},
    )
    written = api.post(
        "/api/v1/operations/tracker.createTicket/call",
        json={"arguments": conflicting_arguments},
    )

    for response in (dry_run, written):
        assert response.status_code == 422, response.text
        assert "parent_ticket_key is resolved from run_plan_id and step_id" in response.text
        assert f"workflow-{run_plan_id}-deliver" in response.text


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
                "response_mode": "raw",
            }
        },
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["data"]["task"]["status"] == "aborted"
    assert body["data"]["task"]["completion_evidence_json"]["decision"] == "rejected"
    assert [result["action"] for result in body["data"]["results"]] == [
        "rejected",
        "rejected",
    ]


def test_tracker_operations_rest_reopen_workflow_by_run_id(
    api: TestClient,
    project_id: int,
) -> None:
    created = api.post(
        "/api/v1/operations/runPlan.create/call",
        json={
            "arguments": {
                "project_id": project_id,
                "run_plan_json": {
                    "schema_version": "stackos.run-plan.v1",
                    "key": "rest-reopen-workflow.run",
                    "title": "REST reopen workflow",
                    "steps": [
                        {"id": "scope-work", "title": "Scope work"},
                        {"id": "deliver-tickets", "title": "Deliver tickets"},
                        {"id": "verify-delivery", "title": "Verify delivery"},
                    ],
                },
            }
        },
    )
    assert created.status_code == 200, created.text
    run_plan_id = created.json()["data"]["id"]

    started = api.post(
        "/api/v1/operations/runPlan.start/call",
        json={"arguments": {"project_id": project_id, "run_plan_id": run_plan_id}},
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["data"]["run_id"]
    run_token = started.json()["data"]["run_token"]

    for step_id in ("scope-work", "deliver-tickets", "verify-delivery"):
        claimed = api.post(
            "/api/v1/operations/runPlan.claimStep/call",
            json={
                "arguments": {
                    "project_id": project_id,
                    "run_plan_id": run_plan_id,
                    "step_id": step_id,
                    "run_token": run_token,
                }
            },
        )
        assert claimed.status_code == 200, claimed.text
        recorded = api.post(
            "/api/v1/operations/runPlan.recordStep/call",
            json={
                "arguments": {
                    "project_id": project_id,
                    "run_plan_id": run_plan_id,
                    "step_id": step_id,
                    "status": "success",
                    "result_json": {"summary": f"{step_id} complete"},
                    "run_token": run_token,
                }
            },
        )
        assert recorded.status_code == 200, recorded.text

    reopened = api.post(
        "/api/v1/operations/tracker.reopen/call",
        json={
            "arguments": {
                "project_id": project_id,
                "run_id": run_id,
                "reason": "More follow-up work was found after closeout.",
                "actor": "route-test",
                "response_mode": "raw",
            }
        },
    )
    assert reopened.status_code == 200, reopened.text
    body = reopened.json()["data"]
    assert body["run_plan_id"] == run_plan_id
    assert body["run_id"] == run_id
    assert body["run_token"] == run_token
    assert body["reopened_step_id"] == "deliver-tickets"
    assert body["reset_step_ids"] == ["deliver-tickets", "verify-delivery"]
    assert body["task"]["status"] == "in-progress"
