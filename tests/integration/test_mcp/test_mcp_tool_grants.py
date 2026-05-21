"""Tool-grant matrix enforcement for StackOS run-plan tools."""

from __future__ import annotations

from .conftest import MCPClient


def _start_resource_run_plan(mcp: MCPClient, project_id: int) -> str:
    plan_json = {
        "schema_version": "stackos.run-plan.v1",
        "key": "grant-check.run",
        "title": "Grant check",
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


def _start_run_for_skill(mcp: MCPClient, project_id: int, skill_name: str) -> str:
    env = mcp.call_tool_structured(
        "run.start",
        {
            "project_id": project_id,
            "kind": "run-plan",
            "skill_name": skill_name,
        },
    )
    return env["data"]["run_token"]


def test_run_plan_controller_can_call_step_grant_tool(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    pid = seeded_project["data"]["id"]
    token = _start_resource_run_plan(mcp_client, pid)

    out = mcp_client.call_tool_structured(
        "resource.upsert",
        {
            "project_id": pid,
            "plugin_slug": "core",
            "resource_key": "learning",
            "title": "Grant check",
            "data_json": {"body": "ok"},
            "run_token": token,
        },
    )

    assert out["data"]["resource_key"] == "learning"


def test_unknown_skill_with_provisioned_token_is_forbidden(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    pid = seeded_project["data"]["id"]
    token = _start_run_for_skill(mcp_client, pid, "unknown-skill")

    err = mcp_client.call_tool_error(
        "resource.upsert",
        {
            "project_id": pid,
            "plugin_slug": "core",
            "resource_key": "learning",
            "data_json": {"body": "blocked"},
            "run_token": token,
        },
    )

    assert err["code"] == -32007


def test_unmatched_token_is_forbidden(mcp_client: MCPClient, seeded_project: dict) -> None:
    pid = seeded_project["data"]["id"]

    err = mcp_client.call_tool_error(
        "resource.upsert",
        {
            "project_id": pid,
            "plugin_slug": "core",
            "resource_key": "learning",
            "data_json": {"body": "blocked"},
            "run_token": "totally-bogus-token-not-in-runs",
        },
    )

    assert err["code"] == -32007
    assert err["data"]["skill"] == "__invalid__"


def test_no_run_token_cannot_write_project_state_or_call_vendor(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    pid = seeded_project["data"]["id"]

    resource_err = mcp_client.call_tool_error(
        "resource.upsert",
        {
            "project_id": pid,
            "plugin_slug": "core",
            "resource_key": "learning",
            "title": "System grant",
            "data_json": {"body": "ok"},
        },
    )
    assert resource_err["code"] == -32007
    assert resource_err["data"]["skill"] == "__system__"

    err = mcp_client.call_tool_error(
        "dataforseo.serp",
        {"project_id": pid, "keyword": "best crm software"},
    )
    assert err["code"] == -32007
    assert err["data"]["skill"] == "__system__"
