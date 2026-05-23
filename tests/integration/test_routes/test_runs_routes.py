"""Runs router tests — list / get / children / abort / heartbeat.

REST does NOT expose start/finish/resume/fork (MCP-only per parity table).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _seed_run(
    api: TestClient,
    project_id: int,
    *,
    parent_id: int | None = None,
    kind: str = "skill-run",
) -> int:
    """Create a Run row directly via the engine on app.state."""
    from sqlmodel import Session

    from stackos.db.models import Run, RunKind, RunStatus

    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as s:
        run = Run(
            project_id=project_id,
            kind=RunKind(kind),
            status=RunStatus.RUNNING,
            parent_run_id=parent_id,
        )
        s.add(run)
        s.commit()
        s.refresh(run)
        assert run.id is not None
        return run.id


def test_list_runs_for_project(api: TestClient, project_id: int) -> None:
    """List + cursor pagination for runs."""
    rid = _seed_run(api, project_id)
    resp = api.get(f"/api/v1/projects/{project_id}/runs")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any(r["id"] == rid for r in items)


def test_get_run_404_on_missing(api: TestClient) -> None:
    """Unknown id → 404."""
    resp = api.get("/api/v1/runs/999")
    assert resp.status_code == 404


def test_run_children(api: TestClient, project_id: int) -> None:
    """``GET /runs/{id}/children`` returns direct children only."""
    parent = _seed_run(api, project_id)
    child = _seed_run(api, project_id, parent_id=parent)
    resp = api.get(f"/api/v1/runs/{parent}/children")
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()]
    assert child in ids


def test_abort_run(api: TestClient, project_id: int) -> None:
    """``abort`` moves running → aborted; ``cascade=true`` walks children."""
    parent = _seed_run(api, project_id)
    child = _seed_run(api, project_id, parent_id=parent)
    resp = api.post(f"/api/v1/runs/{parent}/abort?cascade=true")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "aborted"
    child_status = api.get(f"/api/v1/runs/{child}").json()["status"]
    assert child_status == "aborted"


def test_heartbeat_is_idempotent(api: TestClient, project_id: int) -> None:
    """Heartbeat updates ``heartbeat_at``; missing run → ``data=None``."""
    rid = _seed_run(api, project_id)
    resp1 = api.post(f"/api/v1/runs/{rid}/heartbeat")
    resp2 = api.post(f"/api/v1/runs/{rid}/heartbeat")
    assert resp1.status_code == resp2.status_code == 200
    assert resp2.json()["data"]["status"] == "running"

    miss = api.post("/api/v1/runs/9999/heartbeat")
    assert miss.status_code == 200
    assert miss.json()["data"] is None


def test_no_start_endpoint(api: TestClient) -> None:
    """``POST /api/v1/runs`` is intentionally absent (MCP-only)."""
    resp = api.post("/api/v1/runs", json={})
    assert resp.status_code in (404, 405)
