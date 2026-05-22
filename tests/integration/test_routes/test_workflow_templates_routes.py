"""Workflow-template REST read-route tests."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_workflow_template_read_routes(api: TestClient, project_id: int) -> None:
    listing = api.get(f"/api/v1/projects/{project_id}/workflow-templates")
    assert listing.status_code == 200, listing.text
    keys = {item["key"] for item in listing.json()["templates"]}
    assert "core.project-memory-review" in keys
    assert "gtm.account-research" in keys
    assert "media-buying.campaign-launch" in keys

    detail = api.get(
        f"/api/v1/projects/{project_id}/workflow-templates/core.project-memory-review",
        params={"plugin_slug": "core"},
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["summary"]["key"] == "core.project-memory-review"
    assert body["spec"]["steps"][0]["id"] == "clarify-goal"

    gtm_listing = api.get(
        f"/api/v1/projects/{project_id}/workflow-templates",
        params={"plugin_slug": "gtm"},
    )
    assert gtm_listing.status_code == 200, gtm_listing.text
    assert {item["key"] for item in gtm_listing.json()["templates"]} >= {
        "gtm.account-research",
        "gtm.pipeline-risk-review",
    }

    gtm_detail = api.get(
        f"/api/v1/projects/{project_id}/workflow-templates/gtm.account-research",
        params={"plugin_slug": "gtm"},
    )
    assert gtm_detail.status_code == 200, gtm_detail.text
    assert gtm_detail.json()["summary"]["plugin_slug"] == "gtm"
    assert gtm_detail.json()["spec"]["steps"][0]["id"] == "orient"

    media_listing = api.get(
        f"/api/v1/projects/{project_id}/workflow-templates",
        params={"plugin_slug": "media-buying"},
    )
    assert media_listing.status_code == 200, media_listing.text
    assert {item["key"] for item in media_listing.json()["templates"]} >= {
        "media-buying.campaign-launch",
        "media-buying.performance-diagnosis",
    }

    media_detail = api.get(
        f"/api/v1/projects/{project_id}/workflow-templates/media-buying.campaign-launch",
        params={"plugin_slug": "media-buying"},
    )
    assert media_detail.status_code == 200, media_detail.text
    assert media_detail.json()["summary"]["plugin_slug"] == "media-buying"
    assert media_detail.json()["spec"]["steps"][0]["id"] == "orient"
