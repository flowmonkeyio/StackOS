"""Workflow-template REST read-route tests."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_workflow_template_read_routes(api: TestClient, project_id: int) -> None:
    listing = api.get(f"/api/v1/projects/{project_id}/workflow-templates")
    assert listing.status_code == 200, listing.text
    keys = {item["key"] for item in listing.json()["templates"]}
    assert "core.project-memory-review" in keys

    detail = api.get(
        f"/api/v1/projects/{project_id}/workflow-templates/core.project-memory-review",
        params={"plugin_slug": "core"},
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["summary"]["key"] == "core.project-memory-review"
    assert body["spec"]["steps"][0]["id"] == "clarify-goal"
