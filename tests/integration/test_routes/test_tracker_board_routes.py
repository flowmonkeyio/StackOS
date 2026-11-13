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


@pytest.mark.parametrize(
    ("operation", "field", "value"),
    [
        ("tracker.updateTaskPriority", "priority_key", "p3"),
        ("tracker.placeTask", "board_column", "backlog"),
    ],
)
def test_cli_board_call_reaches_real_rest_owner(
    api, project_id, monkeypatch, tmp_path, operation, field, value
):
    task = _create(api, project_id, "cli-board")
    path = tmp_path / "intent.json"
    path.write_text(json.dumps({**_arguments(task), field: value}))

    def request(method, path, *, body=None, **kwargs):
        response = api.request(method, path, json=body)
        assert response.status_code == 200, response.text
        return response.json()

    monkeypatch.setattr(operation_cli, "_api_request", request)
    result = CliRunner().invoke(app, ["ops", "call", operation, "--input", str(path)])
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["data"]["task"][field] == value


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


def test_place_task_uses_scoped_browser_operation_without_lifecycle_authority(api, project_id):
    task = _create(api, project_id, "place-local")
    foreign = api.post(
        "/api/v1/projects",
        json={
            "slug": "place-foreign",
            "name": "Foreign",
            "domain": "example.org",
            "locale": "en-US",
        },
    ).json()["data"]["id"]
    other = _create(api, foreign, "place-other")
    token = derive_ui_token(api.headers["authorization"].removeprefix("Bearer "))
    headers = {"authorization": f"Bearer {token}"}
    path = "/api/v1/operations/tracker.placeTask/call"
    denied = api.post(
        path,
        headers=headers,
        json={
            "arguments": {
                **_arguments(other),
                "project_id": project_id,
                "board_column": "backlog",
            }
        },
    )
    assert denied.status_code == 404, denied.text
    assert denied.json()["code"] == -32004
    assert denied.json()["data"]["task_key"] == other["key"]
    arguments = {**_arguments(task), "board_column": "backlog"}
    saved = api.post(path, headers=headers, json={"arguments": arguments})
    assert saved.status_code == 200, saved.text
    data = saved.json()["data"]
    assert data["task"]["board_column"] == data["task"]["lane_key"] == "backlog"
    assert data["task"]["status"] == task["status"]
    assert data["task"]["metadata_json"] is None
    replay = api.post(path, headers=headers, json={"arguments": arguments})
    assert replay.json()["data"]["replayed"] is True
    extra = api.post(
        path,
        headers=headers,
        json={
            "arguments": {
                **arguments,
                "status": "in-progress",
            }
        },
    )
    assert extra.status_code == 422
    for candidate in (data["task"], other):
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


def _board_operation(api, scope_id, operation, **arguments):
    response = api.post(
        f"/api/v1/operations/{operation}/call",
        json={"arguments": {"project_id": scope_id, "response_mode": "raw", **arguments}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _board_delivery(status):
    agent = {"name": "worker", "instructions": "Frozen fixture"}
    return {
        "delivery": {
            "status": status,
            "job_ref": "task-delivery:fixture",
            "delivery_agent": agent,
            "review_agent": agent,
        }
    }


def _board_evidence():
    identity = {
        "agent": "worker",
        "host_key": "codex",
        "model": "fixture",
        "started_at": "2026-11-12T00:00:00+00:00",
        "ticket_key": "child",
        "invocation_id": "recorded-call",
    }
    return {
        "automatic_delivery": {
            "attempt": 1,
            "delivery": {
                "launch": identity,
                "execution": identity,
            },
        }
    }


@pytest.mark.parametrize("writer", ["list-create", "list-update"])
def test_bulk_receipt_arrival_uses_shared_column_rank_and_preserves_manual_order(
    api, project_id, writer
):
    def call(operation, **arguments):
        return _board_operation(api, project_id, operation, **arguments)

    for key in ("old-progress", "top-progress"):
        top = call("tracker.createTask", key=key, title=key, status="in-progress")["data"]["task"]
    queued = call(
        "tracker.createTask",
        key="work",
        title="Work",
        status="in-progress",
        metadata_json=_board_delivery("queued"),
    )["data"]["task"]
    assert queued["board_column"] == "todo"
    if writer == "list-create":
        saved = call(
            "tracker.createTicket",
            task_key="work",
            tickets_json=[
                {
                    "key": "child",
                    "title": "Child",
                    "status": "in-progress",
                    "completion_evidence_json": _board_evidence(),
                }
            ],
        )
    else:
        call(
            "tracker.createTicket",
            task_key="work",
            key="child",
            title="Child",
            status="in-progress",
        )
        saved = call(
            "tracker.updateTicket",
            updates_json=[
                {
                    "ticket_key": "child",
                    "patch_json": {"completion_evidence_json": _board_evidence()},
                }
            ],
        )
    assert saved["data"]["valid"]
    entered = call("tracker.get", task_key="work")["tasks"][0]
    assert entered["board_column"] == "in-progress"
    assert entered["order_index"] < top["order_index"]
    manual = call(
        "tracker.reorderTask",
        **_arguments(entered, "manual-after-entry"),
        anchor_task_key=top["key"],
        placement="after",
    )["data"]["task"]
    assert manual["order_index"] > top["order_index"]
    same = call(
        "tracker.updateTicket",
        updates_json=[
            {
                "ticket_key": "child",
                "patch_json": {
                    "title": "Same column",
                    "completion_evidence_json": _board_evidence(),
                },
            }
        ],
    )
    assert same["data"]["valid"]
    assert call("tracker.get", task_key="work")["tasks"][0]["order_index"] == manual["order_index"]


def test_history_omits_derived_board_column_while_live_queue_stop_resume_stays_truthful(
    api, project_id
):
    def call(operation, **arguments):
        return _board_operation(api, project_id, operation, **arguments)

    call(
        "tracker.createTask",
        key="work",
        title="Work",
        status="in-progress",
        metadata_json=_board_delivery("queued"),
    )
    call(
        "tracker.createTicket",
        task_key="work",
        key="child",
        title="Child",
        status="in-progress",
        completion_evidence_json=_board_evidence(),
    )
    for state, column in (
        ("queued", "in-progress"),
        ("stopped", "stopped"),
        ("queued", "in-progress"),
    ):
        task = call(
            "tracker.updateTask",
            task_key="work",
            patch_json={
                "title": f"Still {state}",
                "metadata_json": _board_delivery(state),
            },
        )["data"]["task"]
        assert task["board_column"] == column
        assert call("tracker.get", task_key="work")["tasks"][0]["board_column"] == column
        history = call("tracker.history", limit=50)
        latest = max(
            (
                row
                for row in history["items"]
                if row["entity_kind"] == "task" and row["entity_key"] == "work"
            ),
            key=lambda row: row["id"],
        )
        for snapshot in (latest["before_json"], latest["after_json"]):
            assert "board_column" not in snapshot
            assert snapshot["status"] == "in-progress"
        assert latest["after_json"]["metadata_json"]["delivery"]["status"] == state
