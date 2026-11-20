from __future__ import annotations

from fastapi.testclient import TestClient

from stackos.auth import derive_ui_token


def test_console_can_edit_task_content_without_changing_lifecycle(api: TestClient, project_id: int):
    created = api.post(
        "/api/v1/operations/tracker.createTask/call",
        json={
            "arguments": {
                "project_id": project_id,
                "key": "content",
                "title": "Content",
                "response_mode": "raw",
            }
        },
    )
    assert created.status_code == 200, created.text
    task = created.json()["data"]["task"]
    token = api.headers["authorization"].removeprefix("Bearer ")
    response = api.post(
        "/api/v1/operations/tracker.updateTaskContent/call",
        headers={"authorization": f"Bearer {derive_ui_token(token)}"},
        json={
            "arguments": {
                "project_id": project_id,
                "task_key": task["key"],
                "expected_updated_at": task["updated_at"],
                "idempotency_key": "content-intent",
                "description": "Updated description",
                "additional_input": "Check narrow screens.",
            }
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["task"]["description"] == "Updated description"
    assert data["task"]["status"] == task["status"]
    assert data["input"]["text"] == "Check narrow screens."
    assert data["replayed"] is False
    assert response.headers["cache-control"] == "no-store"

    replay = api.post(
        "/api/v1/operations/tracker.updateTaskContent/call",
        headers={"authorization": f"Bearer {derive_ui_token(token)}"},
        json={
            "arguments": {
                "project_id": project_id,
                "task_key": task["key"],
                "expected_updated_at": task["updated_at"],
                "idempotency_key": "content-intent",
                "description": "Updated description",
                "additional_input": "Check narrow screens.",
            }
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["replayed"] is True
    assert replay.json()["data"]["rev"] == data["rev"]


def test_console_content_schema_rejects_execution_fields_and_invalid_intent(
    api: TestClient, project_id: int
):
    task = api.post(
        "/api/v1/operations/tracker.createTask/call",
        json={
            "arguments": {
                "project_id": project_id,
                "key": "strict-content",
                "title": "Strict",
                "response_mode": "raw",
            }
        },
    ).json()["data"]["task"]
    token = api.headers["authorization"].removeprefix("Bearer ")
    headers = {"authorization": f"Bearer {derive_ui_token(token)}"}
    arguments = {
        "project_id": project_id,
        "task_key": task["key"],
        "expected_updated_at": task["updated_at"],
        "idempotency_key": "strict-intent",
        "description": "Allowed content",
    }
    invalid = [
        {"status": "complete"},
        {"title": "Other"},
        {"goal": "Changed"},
        {"owner": "attacker"},
        {"actor": "agent"},
        {"priority_key": "p0"},
        {"lane_key": "done"},
        {"dependency_keys": []},
        {"run_id": 1},
        {"run_token": "caller-run"},
        {"source_json": {}},
        {"context_json": {}},
        {"completion_evidence_json": {}},
        {"description": None},
        {"description": 1},
        {"additional_input": "  "},
        {"additional_input": None},
        {"response_mode": "ack"},
        {"expected_etag": "unused"},
        {"idempotency_key": ""},
        {"project_id": True},
    ]
    for extra in invalid:
        response = api.post(
            "/api/v1/operations/tracker.updateTaskContent/call",
            headers=headers,
            json={"arguments": {**arguments, **extra}},
        )
        assert response.status_code == 422, (extra, response.text)
    for missing in ("expected_updated_at", "idempotency_key", "description"):
        response = api.post(
            "/api/v1/operations/tracker.updateTaskContent/call",
            headers=headers,
            json={"arguments": {key: value for key, value in arguments.items() if key != missing}},
        )
        assert response.status_code == 422, (missing, response.text)
    saved = api.post(
        "/api/v1/operations/tracker.get/call",
        json={
            "arguments": {
                "project_id": project_id,
                "task_key": task["key"],
                "response_mode": "raw",
            }
        },
    ).json()["tasks"][0]
    assert saved == task
    forbidden = api.post(
        "/api/v1/operations/tracker.updateTask/call",
        headers=headers,
        json={
            "arguments": {
                "project_id": project_id,
                "task_key": task["key"],
                "patch_json": {"status": "complete"},
            }
        },
    )
    assert forbidden.status_code == 403


def test_content_conflicts_and_project_scope_are_explicit(api: TestClient, project_id: int):
    task = api.post(
        "/api/v1/operations/tracker.createTask/call",
        json={
            "arguments": {
                "project_id": project_id,
                "key": "conflict-content",
                "title": "Conflict",
                "response_mode": "raw",
            }
        },
    ).json()["data"]["task"]
    arguments = {
        "project_id": project_id,
        "task_key": task["key"],
        "expected_updated_at": task["updated_at"],
        "idempotency_key": "once",
        "description": "First edit",
    }
    path = "/api/v1/operations/tracker.updateTaskContent/call"
    assert api.post(path, json={"arguments": arguments}).status_code == 200
    changed = api.post(path, json={"arguments": {**arguments, "description": "Different edit"}})
    assert changed.status_code == 409, changed.text
    stale = api.post(path, json={"arguments": {**arguments, "idempotency_key": "fresh-intent"}})
    assert stale.status_code == 409, stale.text
    assert "current_updated_at" in stale.text
    other_response = api.post(
        "/api/v1/projects",
        json={
            "slug": "other-content",
            "name": "Other content",
            "domain": "example.com",
            "locale": "en-US",
        },
    )
    assert other_response.status_code == 201, other_response.text
    other = other_response.json()["data"]["id"]
    missing = api.post(path, json={"arguments": {**arguments, "project_id": other}})
    assert missing.status_code == 404, missing.text
