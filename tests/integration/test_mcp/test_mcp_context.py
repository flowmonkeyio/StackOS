"""MCP tests for StackOS project memory tools."""

from __future__ import annotations

import json

from .conftest import MCPClient


def _create_learning(mcp: MCPClient, project_id: int) -> dict:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/learnings",
        json={
            "statement": "Founder creative lowered CPA with api_key=secret",
            "domain": "media-buying",
            "confidence": "medium",
            "review_state": "accepted",
            "tags": ["creative"],
            "evidence_json": {"refresh_token": "rt"},
        },
        headers=mcp._headers(),
    )
    response.raise_for_status()
    return response.json()["data"]


def test_agent_visible_context_reads_are_bounded_and_sanitized(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    learning = _create_learning(mcp_client, project_id)

    context = mcp_client.call_tool_structured(
        "context.query",
        {
            "project_id": project_id,
            "sources": ["learnings"],
            "fields": ["statement", "confidence"],
            "tags": ["creative"],
            "limit": 1,
        },
    )
    advanced_err = mcp_client.call_tool_error(
        "context.query",
        {
            "project_id": project_id,
            "sources": ["learnings"],
            "fields": ["statement", "evidence_json"],
            "tags": ["creative"],
            "limit": 1,
        },
    )
    learning_advanced_err = mcp_client.call_tool_error(
        "learning.query",
        {
            "project_id": project_id,
            "review_state": "accepted",
            "fields": ["statement", "evidence_json"],
            "limit": 5,
        },
    )
    learning_page = mcp_client.call_tool_structured(
        "learning.query",
        {
            "project_id": project_id,
            "review_state": "accepted",
            "fields": ["statement", "confidence"],
            "limit": 5,
        },
    )

    assert context["items"][0]["id"] == learning["id"]
    assert context["items"][0]["fields"]["statement"].endswith("api_key=[redacted]")
    assert advanced_err["code"] == -32007
    assert advanced_err["data"]["denied_fields"] == ["evidence_json"]
    assert learning_advanced_err["code"] == -32007
    assert learning_page["items"][0]["id"] == learning["id"]
    assert learning_page["items"][0]["fields"] == {
        "statement": "Founder creative lowered CPA with api_key=[redacted]",
        "confidence": "medium",
    }
    assert "secret" not in json.dumps(context)


def test_context_timeline_returns_projected_event_page(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    learning = _create_learning(mcp_client, project_id)

    timeline = mcp_client.call_tool_structured(
        "context.timeline",
        {
            "project_id": project_id,
            "event_type": "learning.create",
            "fields": ["event_type", "summary"],
            "limit": 1,
            "response_mode": "raw",
        },
    )

    assert list(timeline) == ["items", "next_cursor", "total_estimate"]
    assert timeline["items"][0]["source"] == "events"
    assert timeline["items"][0]["provenance"]["table"] == "project_events"
    assert timeline["items"][0]["fields"] == {
        "event_type": "learning.create",
        "summary": learning["statement"],
    }
    assert "sources" not in timeline


def test_context_writes_are_not_system_granted(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    for tool_name, arguments in [
        ("context.snapshot", {"project_id": project_id, "name": "x"}),
        ("learning.create", {"project_id": project_id, "statement": "x"}),
        ("learning.update", {"project_id": project_id, "learning_id": 1}),
        ("experiment.create", {"project_id": project_id, "hypothesis": "x"}),
        (
            "experiment.recordObservation",
            {"project_id": project_id, "experiment_id": 1, "metrics_json": {}},
        ),
        (
            "experiment.recordDecision",
            {"project_id": project_id, "experiment_id": 1, "decision": "x"},
        ),
        ("decision.record", {"project_id": project_id, "decision": "x"}),
    ]:
        err = mcp_client.call_tool_error(tool_name, arguments)
        assert err["code"] == -32007
        assert err["message"] == "ToolNotGrantedError"


def test_experiment_and_decision_queries_are_visible(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    exp_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/experiments",
        json={
            "key": "offer-test",
            "name": "Offer Test",
            "domain": "media-buying",
            "hypothesis": "Offer A improves conversion rate.",
            "status": "running",
        },
        headers=mcp_client._headers(),
    )
    exp_resp.raise_for_status()
    experiment_id = exp_resp.json()["data"]["id"]
    decision_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/decisions",
        json={"experiment_id": experiment_id, "decision": "Keep running."},
        headers=mcp_client._headers(),
    )
    decision_resp.raise_for_status()

    experiments = mcp_client.call_tool_structured(
        "experiment.query",
        {"project_id": project_id, "status": "running", "fields": ["hypothesis", "status"]},
    )
    decisions = mcp_client.call_tool_structured(
        "decision.query",
        {"project_id": project_id, "experiment_id": experiment_id, "fields": ["decision"]},
    )

    assert [item["id"] for item in experiments["items"]] == [experiment_id]
    assert experiments["items"][0]["fields"] == {
        "hypothesis": "Offer A improves conversion rate.",
        "status": "running",
    }
    assert decisions["items"][0]["fields"] == {"decision": "Keep running."}
