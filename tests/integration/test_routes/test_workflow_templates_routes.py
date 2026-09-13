"""Workflow-template REST read-route tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _custom_template() -> dict:
    return {
        "schema_version": "stackos.workflow-template.v1",
        "key": "customer.monthly-close",
        "name": "Customer monthly close",
        "version": "0.1.0",
        "steps": [{"id": "review", "title": "Review"}],
    }


@pytest.mark.parametrize("source", ["project", "user"])
def test_template_authoring_through_cli_rest_is_inert_project_setup(
    api: TestClient,
    project_id: int,
    source: str,
) -> None:
    def call(name: str, arguments: dict):
        return api.post(
            f"/api/v1/operations/{name}/call",
            headers={"X-StackOS-Client-Surface": "cli"},
            json={"arguments": {"project_id": project_id, **arguments}},
        )

    before = call("runPlan.list", {}).json()
    saved = call("workflowTemplate.save", {"template_json": _custom_template(), "source": source})
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["summary"]["source"] == source
    forked = call(
        "workflowTemplate.fork",
        {
            "key": "customer.monthly-close",
            "new_key": "customer.review-close",
        },
    )
    assert forked.status_code == 200, forked.text
    assert forked.json()["data"]["summary"]["key"] == "customer.review-close"
    assert call("runPlan.list", {}).json() == before

    conflicting = _custom_template() | {"name": "Changed without a version bump"}
    rejected = call("workflowTemplate.save", {"template_json": conflicting, "source": source})
    assert rejected.status_code == 409, rejected.text
    missing_project = api.post(
        "/api/v1/operations/workflowTemplate.save/call",
        json={"arguments": {"template_json": _custom_template()}},
    )
    assert missing_project.status_code == 422


@pytest.mark.parametrize(
    "draft",
    [
        {"template_json": {"steps": "not-a-list"}},
        {"template_yaml": "steps: [invalid YAML"},
    ],
)
def test_template_save_returns_model_readable_validation_error(
    api: TestClient,
    project_id: int,
    draft: dict,
) -> None:
    rejected = api.post(
        "/api/v1/operations/workflowTemplate.save/call",
        json={"arguments": {"project_id": project_id, **draft}},
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["data"]["errors"]


def test_template_fork_rejects_invalid_key_without_writes(
    api: TestClient,
    project_id: int,
) -> None:
    listing_url = f"/api/v1/projects/{project_id}/workflow-templates"
    before = api.get(listing_url).json()
    rejected = api.post(
        "/api/v1/operations/workflowTemplate.fork/call",
        json={
            "arguments": {
                "project_id": project_id,
                "key": "core.project-memory-review",
                "new_key": "invalid workflow key!",
            }
        },
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["data"]["errors"][0]["path"] == "key"
    assert api.get(listing_url).json() == before


@pytest.mark.parametrize(
    "operation,arguments",
    [
        ("workflowTemplate.save", {"template_json": _custom_template()}),
        (
            "workflowTemplate.fork",
            {
                "key": "core.project-memory-review",
                "new_key": "customer.memory-review",
            },
        ),
    ],
)
def test_template_authoring_does_not_elevate_browser_console_token(
    api: TestClient,
    project_id: int,
    operation: str,
    arguments: dict,
) -> None:
    token = api.app.state.ui_token
    rejected = api.post(
        f"/api/v1/operations/{operation}/call",
        headers={"Authorization": f"Bearer {token}", "X-StackOS-Client-Surface": "cli"},
        json={"arguments": {"project_id": project_id, **arguments}},
    )
    assert rejected.status_code == 403, rejected.text


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
