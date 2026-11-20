"""No normal MCP discovery/call provides backup filesystem or key authority."""

import pytest


def test_backup_operations_are_absent_from_agent_discovery(mcp_client):
    payload = mcp_client.call_tool_structured(
        "operation.list", {"surface": "mcp", "response_mode": "raw"}
    )
    assert not any(item["name"].startswith("backup.") for item in payload["items"])


@pytest.mark.parametrize("name", ["backup.status", "backup.configure", "backup.create"])
def test_backup_operations_cannot_be_called_through_mcp(mcp_client, name):
    response = mcp_client.call_tool_error(name, {})
    assert response["code"] == -32601
    assert response["message"] == "MethodNotFoundError"
