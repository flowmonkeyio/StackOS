"""MCP tests for StackOS project memory tools."""

from __future__ import annotations

import json
from typing import Any

from .conftest import MCPClient


def test_context_tool_schemas_expose_the_hard_result_limit(mcp_client: MCPClient) -> None:
    tools = {tool["name"]: tool for tool in mcp_client.list_tools()}

    for tool_name in ("context.query", "context.timeline"):
        limit_schema = tools[tool_name]["inputSchema"]["properties"]["limit"]
        integer_schema = next(
            branch for branch in limit_schema["anyOf"] if branch.get("type") == "integer"
        )
        assert integer_schema["maximum"] == 50


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


def test_context_timeline_stream_filters_tracker_status_events_by_task(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    for task_key, ticket_key in [
        ("live-one", "live-one-ticket"),
        ("live-two", "live-two-ticket"),
    ]:
        mcp_client.call_tool_structured(
            "tracker.createTask",
            {
                "project_id": project_id,
                "key": task_key,
                "title": task_key,
                "created_by": "mcp-test",
            },
        )
        mcp_client.call_tool_structured(
            "tracker.createTicket",
            {
                "project_id": project_id,
                "task_key": task_key,
                "key": ticket_key,
                "title": ticket_key,
                "created_by": "mcp-test",
            },
        )

    mcp_client.call_tool_structured(
        "tracker.updateTicket",
        {
            "project_id": project_id,
            "ticket_key": "live-two-ticket",
            "patch_json": {"status": "in-progress"},
            "actor": "mcp-test",
            "response_mode": "raw",
        },
    )
    mcp_client.call_tool_structured(
        "tracker.updateTicket",
        {
            "project_id": project_id,
            "ticket_key": "live-one-ticket",
            "patch_json": {"status": "in-progress"},
            "actor": "mcp-test",
            "response_mode": "raw",
        },
    )

    response = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/context/timeline/stream"
        "?task_key=live-one&max_events=1&poll_ms=250&replay=true",
        headers=mcp_client._headers(),
    )
    response.raise_for_status()
    events = _parse_sse(response.text)

    assert len(events) == 1
    assert events[0]["event"] == "tracker-status"
    assert events[0]["data"]["event_type"] == "tracker.ticket.status_changed"
    assert events[0]["data"]["metadata_json"]["task_key"] == "live-one"
    assert events[0]["data"]["metadata_json"]["ticket_key"] == "live-one-ticket"
    assert "live-two-ticket" not in response.text


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


def _parse_sse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in text.strip().split("\n\n"):
        if not block:
            continue
        event: dict[str, Any] = {"data": ""}
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("id: "):
                event["id"] = line[4:]
            elif line.startswith("event: "):
                event["event"] = line[7:]
            elif line.startswith("data: "):
                data_lines.append(line[6:])
        if data_lines:
            event["data"] = json.loads("\n".join(data_lines))
        events.append(event)
    return events
