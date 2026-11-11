"""Real in-process bridge routing for bounded project management."""

from .test_mcp_bridge_agent_path import (
    _create_project,
    _initialize,
    _is_bridge_scope_error,
    _operation_data,
    _scoped_bridge,
    _structured,
    _tool_call,
    _toolbox_call,
)


def test_bound_bridge_moves_and_renames_only_its_project(mcp_client, tmp_path):
    other = _create_project(mcp_client, "other-visible")
    boot = mcp_client.call_tool_structured(
        "workspace.bootstrap", {"cwd": str(tmp_path), "project_name": "Bound project"}
    )
    own = boot["project_id"]
    proxy, client = _scoped_bridge(mcp_client, cwd=str(tmp_path))
    _initialize(proxy, client)
    started = _tool_call(proxy, client, "workspace.startSession", {"auto_bootstrap": False})
    assert not started["result"].get("isError"), started
    assert proxy.scoped_project_id == own
    renamed = _toolbox_call(
        proxy, client, "project.update", {"patch": {"name": "  Bound renamed  "}}
    )
    assert not renamed["result"].get("isError"), renamed
    assert _operation_data(_structured(renamed))["name"] == "Bound renamed"
    moved = _toolbox_call(
        proxy, client, "project.move", {"placement": "before", "anchor_id": other}
    )
    assert not moved["result"].get("isError"), moved
    assert _operation_data(_structured(moved))["id"] == own
    assert [p["id"] for p in mcp_client.call_tool_structured("project.list", {})["items"]] == [
        own,
        other,
    ]
    for name, arguments in (
        ("project.move", {"project_id": other, "placement": "top"}),
        ("project.update", {"project_id": other, "patch": {"name": "Foreign"}}),
        ("project.move", {"placement": "top", "scope": "catalog"}),
        ("project.update", {"patch": {"is_active": False}}),
    ):
        rejected = _toolbox_call(proxy, client, name, arguments)
        assert _is_bridge_scope_error(rejected), rejected
    resolved = _tool_call(proxy, client, "workspace.resolve", {})
    binding = _operation_data(_structured(resolved))["binding"]
    assert binding["id"] == boot["data"]["binding"]["id"]
    assert binding["last_known_root"] == str(tmp_path)
    assert (
        mcp_client.call_tool_structured("project.get", {"project_id": other})["data"]["name"]
        == "Other Visible"
    )
