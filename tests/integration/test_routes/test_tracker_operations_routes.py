from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from tests.integration.test_repositories.test_agent_statistics import (
    _receipt,
    _repaired,
    _store,
    seed_dated_statistics,
)


def _operation_data(response):
    body = response.json()
    return body.get("data", body)


@pytest.mark.parametrize("name", ["tracker.taskActivity", "tracker.taskResults"])
def test_task_inspection_browser_preserves_scope_cursors_and_read_only_contract(
    api, project_id, name
):
    from stackos.db.models import Artifact
    from tests.integration.test_repositories.test_tracker_task_inspection import seed_inspection

    with Session(api.app.state.engine) as session:
        seed_inspection(session, project_id)
        for index in range(3):
            session.add(
                Artifact(
                    project_id=project_id,
                    kind="file",
                    uri=f"file:///tmp/{index}",
                    provenance_json={"task_key": "inspect"},
                )
            )
        session.commit()
    headers = {"Authorization": f"Bearer {api.get('/api/v1/auth/ui-token').json()['token']}"}
    args = {"project_id": project_id, "task_key": "inspect", "limit": 1}
    values = []
    for mode in ("compact", "raw"):
        response = api.post(
            f"/api/v1/operations/{name}/call",
            headers=headers,
            json={"arguments": {**args, "response_mode": mode}},
        )
        assert response.status_code == 200, response.text
        data = _operation_data(response)
        assert data["task_key"] == "inspect"
        assert data["project_id"] == project_id
        assert data["next_cursor"]
        assert data["items"][0]["source_id"] > 0
        assert data["coverage"]
        assert "warnings" in data
        values.append(data)
    assert values[0] == values[1]
    for invalid in (
        {"response_mode": "ack"},
        {"limit": 201},
        {"limit": 0},
        {"cursor": "not-a-cursor"},
        {"task_key": "missing"},
    ):
        response = api.post(
            f"/api/v1/operations/{name}/call",
            headers=headers,
            json={"arguments": {**args, **invalid}},
        )
        assert response.status_code in {400, 404, 422}, response.text
    response = api.post(
        f"/api/v1/operations/{name}/call",
        headers=headers,
        json={"arguments": {**args, "task_key": "other", "cursor": values[0]["next_cursor"]}},
    )
    assert response.status_code == 422, response.text


def test_tracker_metrics_survive_raw_and_compact_browser_projections(
    api: TestClient, project_id: int
) -> None:
    with Session(api.app.state.engine) as session:
        _store(session, project_id, "repaired", _repaired())
        unknown = _receipt()
        unknown.pop("usage")
        _store(session, project_id, "unknown", {"attempt": 1, "delivery": unknown})
    browser = api.get("/api/v1/auth/ui-token").json()["token"]
    headers = {"Authorization": f"Bearer {browser}"}
    expected = None
    for index_only in (True, False):
        for mode in ("raw", "compact"):
            result = api.post(
                "/api/v1/operations/tracker.get/call",
                headers=headers,
                json={
                    "arguments": {
                        "project_id": project_id,
                        "task_key": "overview",
                        "task_index_only": index_only,
                        "include_graph": False,
                        "response_mode": mode,
                    }
                },
            )
            assert result.status_code == 200, result.text
            data = _operation_data(result)
            metrics = data["tasks"][0]["agent_stats"]
            assert metrics["reported_usage"]["total"] == 100
            assert metrics["launch_attempts"] == 5
            assert metrics["coverage"]["usage_status"] == "partial"
            expected = metrics if expected is None else expected
            assert metrics == expected
            assert "PRIVATE" not in str(metrics)
            if not index_only:
                unknown_metrics = next(
                    t["agent_stats"] for t in data["tickets"] if t["key"] == "unknown"
                )
                assert unknown_metrics["reported_usage"] == {
                    "unit": "tokens",
                    "input": None,
                    "output": None,
                    "total": None,
                    "cache_read": None,
                    "cache_write": None,
                    "reasoning": None,
                    "remaining_input": None,
                    "input_with_unknown_cache": None,
                }
                assert unknown_metrics["coverage"]["usage_status"] == "unavailable"
            if mode == "compact":
                assert "PRIVATE" not in result.text


def test_agent_statistics_are_browser_readable_with_exact_usage_and_no_ack(
    api: TestClient, project_id: int
) -> None:
    with Session(api.app.state.engine) as session:
        _store(session, project_id, "browser-stats", _repaired())
    browser = api.get("/api/v1/auth/ui-token").json()["token"]
    headers = {"Authorization": f"Bearer {browser}"}
    for name, arguments in (
        ("tracker.agentStats", {"project_id": project_id}),
        ("tracker.agentStatsAll", {"is_active": None}),
    ):
        for mode in ("compact", "raw"):
            result = api.post(
                f"/api/v1/operations/{name}/call",
                headers=headers,
                json={"arguments": {**arguments, "response_mode": mode}},
            )
            assert result.status_code == 200, result.text
            stats = _operation_data(result)
            assert stats["totals"]["reported_usage"]["total"] == 100
            assert stats["totals"]["launch_attempts"] == 4
            assert stats["totals"]["recorded_reviewer_rejects"] == 1
            assert "PRIVATE" not in result.text
        denied = api.post(
            f"/api/v1/operations/{name}/call",
            headers=headers,
            json={"arguments": {**arguments, "response_mode": "ack"}},
        )
        assert denied.status_code == 422, denied.text


def test_dated_statistics_browser_contract_and_invalid_filters(api: TestClient, project_id: int):
    with Session(api.app.state.engine) as session:
        filters, _ = seed_dated_statistics(session, project_id)
    filters = {
        **filters,
        "start_date": filters["start_date"].isoformat(),
        "end_date": filters["end_date"].isoformat(),
    }
    browser = api.get("/api/v1/auth/ui-token").json()["token"]
    headers = {"Authorization": f"Bearer {browser}"}
    for name, scope in (
        ("tracker.agentStats", {"project_id": project_id}),
        ("tracker.agentStatsAll", {"is_active": True}),
    ):
        baseline = None
        for mode in ("raw", "compact"):
            response = api.post(
                f"/api/v1/operations/{name}/call",
                headers=headers,
                json={"arguments": {**scope, **filters, "response_mode": mode}},
            )
            assert response.status_code == 200, response.text
            data = _operation_data(response)
            assert data["totals"]["reported_usage"]["cache_read"] == 160
            assert data["totals"]["reported_usage"]["total"] == 410
            assert data["timezone"] == "America/Los_Angeles"
            assert data["undated"]["launch_attempts"] == 1
            assert [day["reported_usage"]["total"] for day in data["daily"]] == [350, 60, 0]
            facts = {k: v for k, v in data.items() if k != "as_of"}
            assert baseline is None or facts == baseline
            baseline = facts
        for invalid in (
            {"start_date": None},
            {"end_date": "2026-10-31"},
            {"end_date": "2027-11-02"},
            {"timezone": "No/Such_Zone"},
            {"timezone": "../UTC"},
            {"group_by": "role"},
            {"start_date": "not-a-date"},
        ):
            response = api.post(
                f"/api/v1/operations/{name}/call",
                headers=headers,
                json={"arguments": {**scope, **filters, **invalid}},
            )
            assert response.status_code == 422, response.text


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

    task_index = api.post(
        "/api/v1/operations/tracker.get/call",
        json={
            "arguments": {
                "project_id": project_id,
                "task_index_only": True,
                "task_statuses": ["not-started", "in-progress"],
                "include_graph": False,
                "response_mode": "raw",
            }
        },
    )
    assert task_index.status_code == 200, task_index.text
    indexed_data = _operation_data(task_index)
    assert indexed_data["tickets"] == []
    assert indexed_data["graph"] is None
    indexed_task = next(task for task in indexed_data["tasks"] if task["key"] == "rest-tracker")
    assert indexed_task["ticket_summary"]["total_count"] == 1

    invalid_index = api.post(
        "/api/v1/operations/tracker.get/call",
        json={
            "arguments": {
                "project_id": project_id,
                "task_index_only": True,
                "include_graph": True,
            }
        },
    )
    assert invalid_index.status_code == 422, invalid_index.text


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


@pytest.mark.parametrize("response_mode", ["raw", "compact", "ack"])
def test_tracker_operations_rest_reopen_workflow_by_run_id(
    api: TestClient,
    project_id: int,
    response_mode: str,
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
                        {"id": "prepare", "title": "Prepare"},
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

    for step_id in ("scope-work", "prepare", "verify-delivery"):
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
                "response_mode": response_mode,
            }
        },
    )
    assert reopened.status_code == 200, reopened.text
    body = reopened.json()["refs"] if response_mode == "ack" else _operation_data(reopened)
    assert body["run_plan_id"] == run_plan_id
    assert body["run_id"] == run_id
    assert body.get("run_token") is None
    assert run_token not in reopened.text
    assert body["reopened_step_id"] == "verify-delivery"
    assert body["reset_step_ids"] == ["verify-delivery"]
    if response_mode != "ack":
        assert body["task"]["status"] == "in-progress"
    # The facade keeps controller credentials private; the canonical controller
    # owner still returns the same usable token on an explicit reopen.
    controller = api.post(
        "/api/v1/operations/runPlan.reopen/call",
        json={
            "arguments": {
                "project_id": project_id,
                "run_plan_id": run_plan_id,
                "step_id": "prepare",
                "reason": "Controller resume",
                "response_mode": "raw",
            }
        },
    )
    assert controller.status_code == 200, controller.text
    assert controller.json()["data"]["run_token"] == run_token
