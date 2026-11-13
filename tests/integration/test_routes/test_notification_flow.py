"""Real delivery/status producers through the registered notification read contract.

The desktop harness substitutes HTTP responses with the actual REST envelopes
and uses a fake OS notification. No installed app or production state is used.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from sqlmodel import Session

from stackos.repositories.tracker import TrackerRepository
from stackos.task_delivery import _update_delivery


def test_producer_operation_controller_and_safe_target(api, project_id):
    def call(name, **arguments):
        result = api.post(
            f"/api/v1/operations/{name}/call",
            json={
                "arguments": {
                    "project_id": project_id,
                    "response_mode": "raw",
                    **arguments,
                }
            },
        )
        assert result.status_code == 200, result.text
        return result.json()

    with Session(api.app.state.engine) as session:
        repo = TrackerRepository(session)
        repo.create_task(
            project_id=project_id,
            key="work",
            title="Work " + "long name " * 40,
            status="in-progress",
            metadata_json={
                "delivery": {
                    "status": "running",
                    "job_ref": "fixture-job",
                    "delivery_agent": {"name": "delivery", "instructions": "private prompt"},
                    "review_agent": {"name": "review", "instructions": "private prompt"},
                }
            },
        )
    fixture = {
        "project_id": project_id,
        "projects": api.get("/api/v1/projects?active_only=true&limit=100").json(),
        "baseline": call("tracker.changed", projection="notifications"),
    }
    cursor = fixture["baseline"]["current_rev"]
    with Session(api.app.state.engine) as session:
        repo = TrackerRepository(session)
        _update_delivery(repo, project_id, "work", status="stopped", reason="private error")
        _update_delivery(repo, project_id, "work", status="stopped", reason="same incident")
        _update_delivery(repo, project_id, "work", status="running")
        _update_delivery(repo, project_id, "work", status="stopped")
        repo.update_task(project_id=project_id, task_key="work", patch_json={"status": "failed"})
        repo.update_task(
            project_id=project_id, task_key="work", patch_json={"status": "in-progress"}
        )
        repo.update_task(project_id=project_id, task_key="work", patch_json={"status": "complete"})
        repo.create_task(project_id=project_id, key="failure", title="Failure")
        repo.create_ticket(
            project_id=project_id, task_key="failure", key="failed-child", title="Failed child"
        )
        repo.update_ticket(
            project_id=project_id, ticket_key="failed-child", patch_json={"status": "in-progress"}
        )
        repo.update_ticket(
            project_id=project_id, ticket_key="failed-child", patch_json={"status": "failed"}
        )
    pages = []
    while True:
        envelope = call("tracker.changed", since_rev=cursor, projection="notifications", limit=5)
        assert "private" not in json.dumps(envelope)
        pages.append({"since": cursor, "envelope": envelope})
        cursor = envelope["next_cursor"]
        if cursor is None:
            break
    fixture.update(
        pages=pages,
        final_rev=envelope["current_rev"],
        snapshot=call(
            "tracker.get",
            task_key="work",
            task_index_only=True,
            include_graph=False,
            projection="ui",
            metric_demand={"task_keys": []},
        ),
    )
    fixture["missing"] = {"ok": True, "data": {"tasks": []}}
    run = subprocess.run(
        ["node", "desktop/scripts/test-notifications.cjs", "--integration-fixture"],
        input=json.dumps(fixture),
        text=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[3],
        timeout=30,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "real producer/operation envelope/controller/deep-link fixture ok" in run.stdout


def test_notification_compact_preserves_cursor_contract_and_auth(api, project_id):
    for mode in ("raw", "compact"):
        response = api.post(
            "/api/v1/operations/tracker.changed/call",
            json={
                "arguments": {
                    "project_id": project_id,
                    "projection": "notifications",
                    "response_mode": mode,
                }
            },
        )
        assert response.status_code == 200, response.text
        data = response.json().get("data", response.json())
        assert data["projection"] == "notifications", response.json()
        assert data["project_id"] == project_id
        assert data["current_rev"] == 0
        assert data["next_cursor"] is None
        assert data["changes"] == []
    denied = api.post(
        "/api/v1/operations/tracker.changed/call",
        headers={"Authorization": ""},
        json={"arguments": {"project_id": project_id, "projection": "notifications"}},
    )
    assert denied.status_code == 401
