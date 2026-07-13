"""Every mutating tool's response carries ``{data, run_id, project_id}``.

Raw read mode returns bare data; compact reads use the operation response envelope.
"""

from __future__ import annotations

from .conftest import MCPClient


def _start_resource_run_plan(mcp: MCPClient, project_id: int) -> str:
    plan_json = {
        "schema_version": "stackos.run-plan.v1",
        "key": "envelope-resource.run",
        "title": "Envelope resource run",
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


def test_mutating_create_carries_envelope(mcp_client: MCPClient, seeded_project: dict) -> None:
    """Every mutating tool wraps its data in {data, run_id, project_id}."""
    pid = seeded_project["data"]["id"]
    token = _start_resource_run_plan(mcp_client, pid)
    env = mcp_client.call_tool_structured(
        "resource.upsert",
        {
            "project_id": pid,
            "plugin_slug": "core",
            "resource_key": "learning",
            "title": "T",
            "data_json": {"body": "ok"},
            "run_token": token,
        },
    )
    assert set(env.keys()) >= {"data", "run_id", "project_id"}


def test_read_resource_returns_bare_data(mcp_client: MCPClient, seeded_project: dict) -> None:
    """Read tool returns the row data, not an envelope."""
    pid = seeded_project["data"]["id"]
    token = _start_resource_run_plan(mcp_client, pid)
    env = mcp_client.call_tool_structured(
        "resource.upsert",
        {
            "project_id": pid,
            "plugin_slug": "core",
            "resource_key": "learning",
            "title": "T",
            "data_json": {"body": "ok"},
            "run_token": token,
        },
    )
    record_id = env["data"]["id"]
    bare = mcp_client.call_tool_structured(
        "resource.get", {"record_id": record_id, "response_mode": "raw"}
    )
    # No 'data'/'run_id'/'project_id' wrapper — just the row.
    assert "data" not in bare
    assert bare["record"]["id"] == record_id
    assert bare["record"]["title"] == "T"


def test_list_returns_page_envelope(mcp_client: MCPClient, seeded_project: dict) -> None:
    """List reads return Page = {items, next_cursor, total_estimate}."""
    page = mcp_client.call_tool_structured(
        "resource.query",
        {"project_id": seeded_project["data"]["id"], "response_mode": "raw"},
    )
    assert set(page.keys()) >= {"records", "resources", "next_cursor", "total_estimate"}


def test_meta_enums_returns_bare_data(mcp_client: MCPClient) -> None:
    """meta.enums is a read tool — bare payload, no wrapper."""
    payload = mcp_client.call_tool_structured("meta.enums", {"response_mode": "raw"})
    assert "data" not in payload
    assert "runs_status" in payload
    assert "action_calls_status" in payload
