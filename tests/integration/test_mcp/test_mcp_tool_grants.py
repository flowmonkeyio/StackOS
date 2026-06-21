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


def _start_browser_status_run_plan(mcp: MCPClient, project_id: int) -> str:
    plan_json = {
        "schema_version": "stackos.run-plan.v1",
        "key": "browser-grant-check.run",
        "title": "Browser grant check",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "inspect-browser",
                    "tool": "browser.runtime.status",
                }
            ]
        },
        "steps": [{"id": "inspect-browser", "title": "Inspect browser"}],
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
            "step_id": "inspect-browser",
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


def test_run_plan_controller_can_call_browser_step_grant_tool(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    pid = seeded_project["data"]["id"]
    token = _start_browser_status_run_plan(mcp_client, pid)

    out = mcp_client.call_tool_structured(
        "browser.runtime.status",
        {
            "project_id": pid,
            "run_token": token,
            "response_mode": "raw",
        },
    )

    status = out
    while isinstance(status.get("data"), dict) and "provider" not in status:
        status = status["data"]
    assert status["provider"] == "playwright"
    assert status["executable_path"] is None


def test_run_plan_browser_grant_rejects_cross_project_arguments(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    pid = seeded_project["data"]["id"]
    token = _start_browser_status_run_plan(mcp_client, pid)
    other = mcp_client.call_tool_structured(
        "project.create",
        {
            "slug": "browser-grant-other-project",
            "name": "Browser Grant Other Project",
            "domain": "browser-grant-other.example",
            "locale": "en-US",
        },
    )

    err = mcp_client.call_tool_error(
        "browser.runtime.status",
        {
            "project_id": other["data"]["id"],
            "run_token": token,
        },
    )

    assert err["code"] == -32007
    assert "not scoped to this project" in err["data"]["detail"]


def test_terminal_run_plan_token_cannot_mutate_tracker_state(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    pid = seeded_project["data"]["id"]
    plan_json = {
        "schema_version": "stackos.run-plan.v1",
        "key": "terminal-tracker-guard.run",
        "title": "Terminal tracker guard",
        "steps": [{"id": "write", "title": "Write"}],
    }
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": pid, "run_plan_json": plan_json},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": pid, "run_plan_id": created["data"]["id"]},
    )
    token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "write",
            "run_token": token,
        },
    )
    mcp_client.call_tool_structured(
        "runPlan.abort",
        {
            "project_id": pid,
            "run_plan_id": created["data"]["id"],
            "reason": "test terminal guard",
        },
    )

    err = mcp_client.call_tool_error(
        "tracker.createTicket",
        {
            "project_id": pid,
            "task_key": f"workflow-{created['data']['id']}",
            "key": "terminal-token-mutation",
            "title": "Terminal token mutation",
            "run_token": token,
        },
    )

    assert err["code"] == -32007
    assert "running audit run" in err["data"]["detail"]


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
    assert err["code"] == -32601
