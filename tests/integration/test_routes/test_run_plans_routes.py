"""Run-plan REST read-route tests."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from content_stack.db.models import RunPlanStatus
from content_stack.repositories.run_plans import RunPlanRepository


def _seed_run_plan(api: TestClient, project_id: int) -> int:
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        plan = (
            RunPlanRepository(session)
            .create(
                project_id=project_id,
                run_plan_json={
                    "schema_version": "stackos.run-plan.v1",
                    "key": "ops.review.run",
                    "title": "Review",
                    "steps": [{"id": "review", "title": "Review"}],
                },
            )
            .data
        )
        return plan.id


def test_list_run_plans_for_project(api: TestClient, project_id: int) -> None:
    run_plan_id = _seed_run_plan(api, project_id)

    resp = api.get(f"/api/v1/projects/{project_id}/run-plans")

    assert resp.status_code == 200
    assert any(item["id"] == run_plan_id for item in resp.json()["items"])


def test_list_run_plans_can_filter_by_bound_run(api: TestClient, project_id: int) -> None:
    run_plan_id = _seed_run_plan(api, project_id)
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        started = RunPlanRepository(session).start(run_plan_id, project_id=project_id).data

    resp = api.get(
        f"/api/v1/projects/{project_id}/run-plans",
        params={"run_id": started.run_id, "status": RunPlanStatus.STARTED.value},
    )

    assert resp.status_code == 200
    assert [item["id"] for item in resp.json()["items"]] == [run_plan_id]


def test_get_run_plan_with_steps(api: TestClient, project_id: int) -> None:
    run_plan_id = _seed_run_plan(api, project_id)

    resp = api.get(f"/api/v1/run-plans/{run_plan_id}")

    assert resp.status_code == 200
    assert resp.json()["id"] == run_plan_id
    assert resp.json()["steps"][0]["step_id"] == "review"


def test_get_run_plan_404_on_missing(api: TestClient) -> None:
    resp = api.get("/api/v1/run-plans/999999")

    assert resp.status_code == 404
