"""Project guidance uses existing context carriers, not runtime editorial policy.

These are transport/identity checks. Agents still resolve current sources and
decide whether a changed guide invalidates a review; the rehearsal covers that.
"""

from __future__ import annotations

import pytest

from tests.integration.test_mcp.conftest import MCPClient


def _call(client: MCPClient, operation: str, **arguments: object) -> dict:
    result = client.call_tool_structured(operation, {"response_mode": "raw", **arguments})
    return result.get("data", result)


@pytest.mark.parametrize(
    ("workflow", "inputs"),
    [
        ("seo.keyword-research", {"goal": "Qualify useful existing-page opportunities"}),
        ("branding.content-production", {"operator_intent": "Prepare one reviewed article"}),
        (
            "marketing.campaign-production",
            {
                "product_description": "A project operations tool",
                "product_photo_refs": ["artifact:fixture-product-photo"],
                "media_request": {"formats": ["image"], "count": 1},
            },
        ),
    ],
)
def test_guidance_selection_reaches_each_workflow_without_becoming_live_truth(
    mcp_client: MCPClient, seeded_project: dict, workflow: str, inputs: dict
) -> None:
    pid = seeded_project["data"]["id"]
    # Opaque fixture pointers only: creating a plan neither dereferences these
    # nor claims they are approved/read. The main agent must do that separately.
    selection = {
        "project_id": pid,
        "owner": "branding",
        "profile_ref": "resource:fixture-profile",
        "guide_ref": "artifact:fixture-guide-a",
        "approval_ref": "decision:fixture-approval-a",
    }
    _call(
        mcp_client,
        "workflowExtension.upsert",
        project_id=pid,
        workflow_key=workflow,
        selected_context_json={"project_guidance": selection},
    )
    created = _call(
        mcp_client,
        "runPlan.create",
        project_id=pid,
        workflow_key=workflow,
        inputs_json=inputs,
        selected_context_json={"assignment": {"attempt": "draft-1"}},
    )
    assert "id" in created, created
    frozen = _call(mcp_client, "runPlan.get", project_id=pid, run_plan_id=created["id"])
    assert frozen["selected_context_json"]["project_guidance"] == selection
    assert frozen["selected_context_json"]["assignment"] == {"attempt": "draft-1"}

    current_selection = {**selection, "guide_ref": "artifact:fixture-guide-b"}
    _call(
        mcp_client,
        "workflowExtension.upsert",
        project_id=pid,
        workflow_key=workflow,
        selected_context_json={"project_guidance": current_selection},
    )
    current = _call(mcp_client, "workflowExtension.get", project_id=pid, workflow_key=workflow)
    historical = _call(mcp_client, "runPlan.get", project_id=pid, run_plan_id=created["id"])
    assert current["extension"]["selected_context_json"]["project_guidance"] == current_selection
    assert historical["selected_context_json"]["project_guidance"] == selection
    # A current template digest is deliberately not evidence of guide freshness.
    assert historical["workflow_contract"]["status"] == "current"
    assert historical["status"] == "draft"

    other = mcp_client.call_tool_structured(
        "project.create",
        {"slug": "other-project", "name": "Other Project", "domain": "other.example"},
    )["data"]
    other_context = _call(
        mcp_client, "workflowExtension.get", project_id=other["id"], workflow_key=workflow
    )
    assert other_context["extension"] is None
    denied = _call(mcp_client, "runPlan.get", project_id=other["id"], run_plan_id=created["id"])
    assert "selected_context_json" not in denied
    assert "detail" in denied
