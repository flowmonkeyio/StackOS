"""MCP parity tests for generic StackOS agent request operations."""

from __future__ import annotations

from .conftest import MCPClient


def _agent_request_ingest_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "mcp-agent-request-ingest.run",
        "title": "MCP agent request ingest",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "ingest",
                    "tool": "agentRequest.create",
                }
            ]
        },
        "steps": [{"id": "ingest", "title": "Ingest request"}],
    }


def _start_ingest_plan(mcp: MCPClient, project_id: int) -> str:
    created = mcp.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _agent_request_ingest_plan_json()},
    )
    started = mcp.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    mcp.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "ingest",
            "run_token": run_token,
        },
    )
    return run_token


def test_agent_request_operations_are_registered(mcp_client: MCPClient) -> None:
    tools = {tool["name"] for tool in mcp_client.list_tools()}

    assert {
        "agentRequest.list",
        "agentRequest.get",
        "agentRequest.create",
        "agentRequest.claim",
        "agentRequest.release",
        "agentRequest.linkRunPlan",
        "agentRequest.prepareRunPlan",
        "agentRequest.complete",
        "agentRequest.ignore",
    } <= tools


def test_agent_request_mcp_lifecycle_uses_operation_registry(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    denied = mcp_client.call_tool_error(
        "agentRequest.create",
        {
            "project_id": project_id,
            "request_key": "mcp:blocked",
            "title": "Blocked direct create",
        },
    )
    assert denied["code"] == -32007

    run_token = _start_ingest_plan(mcp_client, project_id)
    created = mcp_client.call_tool_structured(
        "agentRequest.create",
        {
            "project_id": project_id,
            "run_token": run_token,
            "request_key": "mcp:agent-request:1",
            "title": "Authorization: Bearer secret",
            "body_preview": "api_key=hidden",
            "source_provider": "telegram-bot",
            "source_kind": "telegram-message",
        },
    )
    request = created["data"]
    assert request["title"] == "Authorization: Bearer [redacted]"
    assert request["body_preview"] == "api_key=[redacted]"

    listed = mcp_client.call_tool_structured(
        "agentRequest.list",
        {"project_id": project_id, "claimable": True},
    )
    assert [item["id"] for item in listed["items"]] == [request["id"]]

    claimed = mcp_client.call_tool_structured(
        "agentRequest.claim",
        {
            "project_id": project_id,
            "request_id": request["id"],
            "claimed_by": "codex",
            "idempotency_key": "mcp-claim-agent-request-1",
        },
    )
    claim_token = claimed["data"]["claim_token"]
    replayed = mcp_client.call_tool_structured(
        "agentRequest.claim",
        {
            "project_id": project_id,
            "request_id": request["id"],
            "claimed_by": "codex",
            "idempotency_key": "mcp-claim-agent-request-1",
        },
    )
    assert replayed["data"]["claim_token"] == claim_token

    completed = mcp_client.call_tool_structured(
        "agentRequest.complete",
        {
            "project_id": project_id,
            "request_id": request["id"],
            "claim_token": claim_token,
            "status": "resolved",
        },
    )
    assert completed["data"]["status"] == "resolved"


def test_agent_request_prepare_run_plan_mcp_replay(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    run_token = _start_ingest_plan(mcp_client, project_id)
    created = mcp_client.call_tool_structured(
        "agentRequest.create",
        {
            "project_id": project_id,
            "run_token": run_token,
            "request_key": "mcp:agent-request:prepare",
            "title": "Prepare this request",
        },
    )["data"]

    arguments = {
        "project_id": project_id,
        "request_id": created["id"],
        "claimed_by": "codex",
        "idempotency_key": "mcp-prepare-agent-request-1",
        "run_plan_json": {
            "schema_version": "stackos.run-plan.v1",
            "key": "mcp.handle.request.run",
            "title": "MCP handle request",
            "steps": [{"id": "handle", "title": "Handle request"}],
        },
        "response_mode": "raw",
    }
    prepared = mcp_client.call_tool_structured("agentRequest.prepareRunPlan", arguments)
    replayed = mcp_client.call_tool_structured("agentRequest.prepareRunPlan", arguments)

    assert replayed["data"]["claim_token"] == prepared["data"]["claim_token"]
    assert prepared["data"]["request"]["status"] == "run-created"
    assert prepared["data"]["request"]["run_plan_id"] == prepared["data"]["run_plan"]["id"]
