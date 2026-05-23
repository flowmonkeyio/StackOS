from __future__ import annotations

from fastapi.testclient import TestClient
from pytest_httpx import HTTPXMock
from sqlmodel import Session

from content_stack.repositories.run_plans import RunPlanRepository


def _sitemap_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.sitemap-action.run",
        "title": "Sitemap action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "fetch-sitemap",
                    "tool": "action.execute",
                    "action_refs": ["utils.sitemap.fetch"],
                }
            ]
        },
        "steps": [
            {
                "id": "fetch-sitemap",
                "title": "Fetch sitemap",
                "action_refs": ["utils.sitemap.fetch"],
            }
        ],
    }


def test_operation_docs_are_agent_readable(api: TestClient) -> None:
    listed = api.get("/api/v1/operations", params={"surface": "rest"})
    described = api.get("/api/v1/operations/action.execute")

    assert listed.status_code == 200
    assert "action.execute" in {item["name"] for item in listed.json()["items"]}
    assert described.status_code == 200
    body = described.json()
    assert body["name"] == "action.execute"
    assert body["surfaces"]["mcp"]["enabled"] is True
    assert body["surfaces"]["rest"]["enabled"] is True
    assert body["surfaces"]["cli"]["enabled"] is True
    assert body["grant_policy"] == "run-plan-step-action-ref"
    assert "project_id" in body["input_schema"]["properties"]
    assert body["examples"][0]["arguments"]["action_ref"] == "utils.sitemap.fetch"
    assert any("credential_ref" in item for item in body["prerequisites"])


def test_operation_rest_call_uses_registered_action_handler(api: TestClient) -> None:
    resp = api.post(
        "/api/v1/operations/action.describe/call",
        json={"arguments": {"action_ref": "core.catalog.describe"}},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["manifest"]["action_ref"] == "core.catalog.describe"
    assert body["execution_available"] is False


def test_operation_rest_call_enforces_mcp_grants(api: TestClient, project_id: int) -> None:
    resp = api.post(
        "/api/v1/operations/action.execute/call",
        json={
            "arguments": {
                "project_id": project_id,
                "action_ref": "utils.sitemap.fetch",
                "input_json": {"urls": ["https://example.com/sitemap.xml"]},
            }
        },
    )

    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == -32007
    assert body["data"]["tool"] == "action.execute"


def test_operation_rest_action_execute_uses_run_plan_boundary(
    api: TestClient,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        repo = RunPlanRepository(session)
        created = repo.create(project_id=project_id, run_plan_json=_sitemap_action_plan_json())
        started = repo.start(created.data.id, project_id=project_id)
        claimed = repo.claim_step(
            run_plan_id=created.data.id,
            run_id=started.run_id,
            step_id="fetch-sitemap",
        )
        run_token = started.data.run_token

    httpx_mock.add_response(
        method="GET",
        url="https://example.com/sitemap.xml",
        text=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://example.com/a</loc></url>"
            "</urlset>"
        ),
    )

    resp = api.post(
        "/api/v1/operations/action.execute/call",
        json={
            "arguments": {
                "project_id": project_id,
                "run_token": run_token,
                "action_ref": "utils.sitemap.fetch",
                "input_json": {"urls": ["https://example.com/sitemap.xml"]},
            }
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["action_call"]["run_id"] == started.run_id
    assert body["data"]["action_call"]["run_plan_id"] == created.data.id
    assert body["data"]["action_call"]["run_plan_step_id"] == claimed.data.id
    assert body["data"]["output_json"]["entries"][0]["url"] == "https://example.com/a"

    audit_resp = api.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={
            "run_id": started.run_id,
            "run_plan_id": created.data.id,
            "run_plan_step_id": claimed.data.id,
            "status": "success",
        },
    )
    assert audit_resp.status_code == 200
    assert audit_resp.json()["total_estimate"] == 1


def test_operation_rest_run_token_cannot_spoof_project(
    api: TestClient,
    project_id: int,
) -> None:
    other_resp = api.post(
        "/api/v1/projects",
        json={
            "slug": "other-project",
            "name": "Other Project",
            "domain": "other.example",
            "locale": "en-US",
        },
    )
    assert other_resp.status_code == 201
    other_project_id = other_resp.json()["data"]["id"]

    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        repo = RunPlanRepository(session)
        created = repo.create(project_id=project_id, run_plan_json=_sitemap_action_plan_json())
        started = repo.start(created.data.id, project_id=project_id)
        repo.claim_step(
            run_plan_id=created.data.id,
            run_id=started.run_id,
            step_id="fetch-sitemap",
        )

    resp = api.post(
        "/api/v1/operations/action.execute/call",
        json={
            "arguments": {
                "project_id": other_project_id,
                "run_token": started.data.run_token,
                "action_ref": "utils.sitemap.fetch",
                "input_json": {"urls": ["https://example.com/sitemap.xml"]},
            }
        },
    )

    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == -32007
    assert body["detail"] == "run_token is not scoped to this project"
