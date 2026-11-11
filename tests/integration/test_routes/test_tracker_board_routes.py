from __future__ import annotations

import json

import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

import stackos.cli.operation_commands as operation_cli
from stackos.auth import derive_ui_token
from stackos.cli import app
from stackos.context.repository import ContextRepository
from stackos.db.models import IdempotencyKey, TrackerRevision


def _create(api, project_id, key):
    response = api.post(
        "/api/v1/operations/tracker.createTask/call",
        json={
            "arguments": {
                "project_id": project_id,
                "key": key,
                "title": key,
                "response_mode": "raw",
            }
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["task"]


def _arguments(task, intent="board-edit"):
    return {
        "project_id": task["project_id"],
        "task_key": task["key"],
        "expected_updated_at": task["updated_at"],
        "expected_status": task["status"],
        "idempotency_key": intent,
    }


def test_browser_priority_and_reorder_share_bounded_owner_and_stream(api, project_id):
    first = _create(api, project_id, "first")
    second = _create(api, project_id, "second")
    token = derive_ui_token(api.headers["authorization"].removeprefix("Bearer "))
    headers = {"authorization": f"Bearer {token}"}
    priority = api.post(
        "/api/v1/operations/tracker.updateTaskPriority/call",
        headers=headers,
        json={"arguments": {**_arguments(first), "priority_key": "p0"}},
    )
    assert priority.status_code == 200, priority.text
    saved = priority.json()["data"]["task"]
    assert saved["priority_key"] == "p0"
    assert saved["status"] == first["status"]
    moved = api.post(
        "/api/v1/operations/tracker.reorderTask/call",
        headers=headers,
        json={
            "arguments": {**_arguments(second), "anchor_task_key": "first", "placement": "after"}
        },
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["data"]["changed"]
    assert moved.json()["data"]["task"]["order_index"] == 1
    replay = api.post(
        "/api/v1/operations/tracker.reorderTask/call",
        headers=headers,
        json={
            "arguments": {**_arguments(second), "anchor_task_key": "first", "placement": "after"}
        },
    )
    assert replay.json()["data"]["replayed"] is True
    # Priority and the whole reorder emit two durable notifications.
    with api.stream(
        "GET",
        f"/api/v1/projects/{project_id}/context/timeline/stream?replay=true&max_events=2",
        headers=headers,
    ) as response:
        assert response.status_code == 200
        frames = response.read().decode()
    assert "event: tracker-status" in frames
    assert '"event_type":"tracker.task.changed"' in frames.replace(" ", "")
    assert '"event_type":"tracker.tasks.changed"' in frames.replace(" ", "")
    assert "tracker.task.status_changed" not in frames
    stale = api.post(
        "/api/v1/operations/tracker.updateTaskPriority/call",
        headers=headers,
        json={"arguments": {**_arguments(first, "stale"), "priority_key": "p1"}},
    )
    assert stale.status_code == 409
    assert "current_updated_at" in stale.text and "tracker.get" in stale.text
    forbidden = api.post(
        "/api/v1/operations/tracker.updateTask/call",
        headers=headers,
        json={
            "arguments": {
                "project_id": project_id,
                "task_key": "first",
                "patch_json": {"status": "complete"},
            }
        },
    )
    assert forbidden.status_code == 403


def test_large_reorder_rest_receipt_and_scoped_sse_share_one_batch(api, project_id):
    tasks = [_create(api, project_id, f"large-{index}") for index in range(62)]
    with Session(api.app.state.engine) as session:
        before = session.exec(
            select(TrackerRevision).where(TrackerRevision.project_id == project_id)
        ).all()
        prior_rev = before[-1].rev
    arguments = {
        **_arguments(tasks[0], "large-reorder"),
        "anchor_task_key": tasks[-1]["key"],
        "placement": "before",
    }
    response = api.post(
        "/api/v1/operations/tracker.reorderTask/call", json={"arguments": arguments}
    )
    assert response.status_code == 200, response.text
    receipt = response.json()["data"]
    assert receipt["rev"] == prior_rev + 1
    assert len(receipt["affected_task_keys"]) == 62
    with Session(api.app.state.engine) as session:
        context = ContextRepository(session)
        page = context.tracker_status_timeline(project_id=project_id, limit=50)
        assert len(page.items) == page.total_estimate == 1
        assert page.next_cursor is None
        batch = page.items[0]
        assert batch.metadata_json["rev"] == receipt["rev"]
        assert batch.metadata_json["affected_task_keys"] == receipt["affected_task_keys"]
        assert context.tracker_status_timeline(project_id=project_id, after_id=batch.id).items == []
        assert (
            len(
                session.exec(
                    select(TrackerRevision).where(
                        TrackerRevision.project_id == project_id, TrackerRevision.rev > prior_rev
                    )
                ).all()
            )
            == 1
        )
        assert (
            len(
                session.exec(
                    select(IdempotencyKey).where(IdempotencyKey.tool_name == "tracker.reorderTask")
                ).all()
            )
            == 1
        )
    for key in (tasks[0]["key"], tasks[-1]["key"]):
        streamed = api.get(
            f"/api/v1/projects/{project_id}/context/timeline/stream",
            params={
                "task_key": key,
                "replay": "true",
                "max_events": 1,
                "limit": 50,
                "poll_ms": 250,
            },
        )
        assert streamed.status_code == 200
        frames = []
        for frame in streamed.text.split("\n\n"):
            if "event: tracker-status\n" in frame:
                frames.append(
                    json.loads(
                        next(line[6:] for line in frame.splitlines() if line.startswith("data: "))
                    )
                )
        assert len(frames) == 1
        assert frames[0]["id"] == batch.id
        assert frames[0]["event_type"] == "tracker.tasks.changed"
        assert frames[0]["metadata_json"]["rev"] == receipt["rev"]
    replay = api.post("/api/v1/operations/tracker.reorderTask/call", json={"arguments": arguments})
    assert replay.json()["data"]["replayed"] is True
    assert replay.json()["data"]["rev"] == receipt["rev"]


@pytest.mark.parametrize(
    "operation,change",
    [
        ("tracker.updateTaskPriority", {"priority_key": "p1"}),
        ("tracker.reorderTask", {"anchor_task_key": "other", "placement": "after"}),
    ],
)
def test_board_schema_denies_lifecycle_and_unbounded_fields(api, project_id, operation, change):
    task = _create(api, project_id, "strict-board")
    arguments = {**_arguments(task), **change}
    invalid = [
        {"status": "complete"},
        {"patch_json": {"order_index": 1}},
        {"order_index": 1},
        {"actor": "operator"},
        {"run_token": "fake"},
        {"project_id": True},
        {"expected_etag": "unused"},
        {"response_mode": "ack"},
        {"idempotency_key": ""},
    ]
    invalid += (
        [{"priority_key": "urgent"}, {"placement": "after"}]
        if "Priority" in operation
        else [{"priority_key": "p0"}, {"placement": "across"}]
    )
    for patch in invalid:
        response = api.post(
            f"/api/v1/operations/{operation}/call", json={"arguments": {**arguments, **patch}}
        )
        assert response.status_code == 422, (patch, response.text)
    for key in ("expected_status", "expected_updated_at", "idempotency_key"):
        response = api.post(
            f"/api/v1/operations/{operation}/call",
            json={"arguments": {k: v for k, v in arguments.items() if k != key}},
        )
        assert response.status_code == 422, response.text


def test_cli_board_call_reaches_real_rest_owner(api, project_id, monkeypatch, tmp_path):
    task = _create(api, project_id, "cli-board")
    path = tmp_path / "intent.json"
    path.write_text(json.dumps({**_arguments(task), "priority_key": "p3"}))

    def request(method, path, *, body=None, **kwargs):
        response = api.request(method, path, json=body)
        assert response.status_code == 200, response.text
        return response.json()

    monkeypatch.setattr(operation_cli, "_api_request", request)
    result = CliRunner().invoke(
        app, ["ops", "call", "tracker.updateTaskPriority", "--input", str(path)]
    )
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["data"]["task"]["priority_key"] == "p3"


def test_board_intents_cannot_reach_another_projects_task_or_anchor(api, project_id):
    task = _create(api, project_id, "local-task")
    response = api.post(
        "/api/v1/projects",
        json={"slug": "other-board", "name": "Other", "domain": "example.com", "locale": "en-US"},
    )
    assert response.status_code == 201, response.text
    other_id = response.json()["data"]["id"]
    other = _create(api, other_id, "foreign-task")
    missing = api.post(
        "/api/v1/operations/tracker.updateTaskPriority/call",
        json={
            "arguments": {
                **_arguments(other),
                "project_id": project_id,
                "priority_key": "p0",
            }
        },
    )
    assert missing.status_code == 404, missing.text
    anchor = api.post(
        "/api/v1/operations/tracker.reorderTask/call",
        json={
            "arguments": {
                **_arguments(task),
                "anchor_task_key": other["key"],
                "placement": "before",
            }
        },
    )
    assert anchor.status_code == 409, anchor.text
    for candidate in (task, other):
        current = api.post(
            "/api/v1/operations/tracker.get/call",
            json={
                "arguments": {
                    "project_id": candidate["project_id"],
                    "task_key": candidate["key"],
                    "response_mode": "raw",
                }
            },
        ).json()["tasks"][0]
        assert current == candidate
