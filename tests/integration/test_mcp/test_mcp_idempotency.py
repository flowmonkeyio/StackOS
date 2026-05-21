"""Idempotency dedup window for generic mutating MCP tools."""

from __future__ import annotations

from .conftest import MCPClient


def _run_token(mcp: MCPClient, project_id: int) -> str:
    plan_json = {
        "schema_version": "stackos.run-plan.v1",
        "key": "idempotency-resource.run",
        "title": "Idempotency resource run",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "write",
                    "tool": "resource.upsert",
                    "plugin_slug": "core",
                    "resource_key": "learning",
                }
            ]
        },
        "steps": [{"id": "write", "title": "Write resource"}],
    }
    created = mcp.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": plan_json},
    )
    started = mcp.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    token = started["data"]["run_token"]
    mcp.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "write",
            "run_token": token,
        },
    )
    return token


def _resource_args(project_id: int, token: str, title: str, key: str) -> dict:
    return {
        "project_id": project_id,
        "plugin_slug": "core",
        "resource_key": "learning",
        "title": title,
        "data_json": {"body": title},
        "idempotency_key": key,
        "run_token": token,
    }


def test_idempotency_replay_returns_cached_envelope(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    pid = seeded_project["data"]["id"]
    token = _run_token(mcp_client, pid)
    args = _resource_args(pid, token, "Replay", "abc-123")

    first = mcp_client.call_tool_structured("resource.upsert", args)
    second = mcp_client.call_tool_structured("resource.upsert", args)

    assert first["data"]["id"] == second["data"]["id"]


def test_idempotency_different_keys_create_distinct_rows(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    pid = seeded_project["data"]["id"]
    token = _run_token(mcp_client, pid)

    first = mcp_client.call_tool_structured(
        "resource.upsert",
        _resource_args(pid, token, "First", "first-key"),
    )
    second = mcp_client.call_tool_structured(
        "resource.upsert",
        _resource_args(pid, token, "Second", "second-key"),
    )

    assert first["data"]["id"] != second["data"]["id"]


def test_failed_call_does_not_poison_idempotency_key(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    pid = seeded_project["data"]["id"]
    token = _run_token(mcp_client, pid)
    key = "retry-after-failure"

    err = mcp_client.call_tool_error(
        "resource.upsert",
        {
            "project_id": pid,
            "plugin_slug": "core",
            "resource_key": "learning",
            "record_id": 9999,
            "data_json": {"body": "bad"},
            "idempotency_key": key,
            "run_token": token,
        },
    )
    assert err["code"] == -32004

    fixed = mcp_client.call_tool_structured(
        "resource.upsert",
        _resource_args(pid, token, "Fixed", key),
    )
    assert fixed["data"]["title"] == "Fixed"
