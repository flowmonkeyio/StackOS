"""Setup reads use actual bound bridge scope, never caller-supplied portfolio authority."""

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


def test_setup_read_injected_project_and_foreign_rejection(mcp_client, tmp_path):
    other = _create_project(mcp_client, "other-setup")
    own = mcp_client.call_tool_structured(
        "workspace.bootstrap", {"cwd": str(tmp_path), "project_name": "Setup bound"}
    )["project_id"]
    proxy, client = _scoped_bridge(mcp_client, cwd=str(tmp_path))
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {"auto_bootstrap": False})
    result = _toolbox_call(proxy, client, "projectSetup.get", {})
    assert not result["result"].get("isError"), result
    data = _operation_data(_structured(result))
    assert data["project_id"] == own
    assert data["eligible"] is True
    assert data["status"] == "setup_needed"
    rejected = _toolbox_call(proxy, client, "projectSetup.get", {"project_id": other})
    assert _is_bridge_scope_error(rejected), rejected
    denied = _toolbox_call(proxy, client, "projectSetup.setReminders", {"dismiss_setup": True})
    assert denied["result"].get("isError"), denied
    unchanged = mcp_client.call_tool_structured(
        "projectSetup.get", {"project_id": own, "response_mode": "raw"}
    )
    assert unchanged["setup_reminder_dismissed_at"] is None
    foreign = mcp_client.call_tool_structured(
        "projectSetup.get", {"project_id": other, "response_mode": "raw"}
    )
    assert foreign["setup_reminder_dismissed_at"] is None
