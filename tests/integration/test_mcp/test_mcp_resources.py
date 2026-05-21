"""MCP tests for generic StackOS resources and artifacts."""

from __future__ import annotations

from .conftest import MCPClient


def test_resource_and_artifact_read_tools_are_callable(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    pid = seeded_project["data"]["id"]
    headers = mcp_client._headers()

    record_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{pid}/resource-records",
        json={
            "plugin_slug": "core",
            "resource_key": "learning",
            "external_id": "lesson-1",
            "data_json": {
                "body": "Previous run context matters.",
                "api_key": "should-not-leak",
            },
        },
        headers=headers,
    )
    record_resp.raise_for_status()
    record_id = record_resp.json()["data"]["id"]

    artifact_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{pid}/artifacts",
        json={
            "plugin_slug": "utils",
            "kind": "image",
            "uri": "/generated-assets/read-tool.png",
        },
        headers=headers,
    )
    artifact_resp.raise_for_status()
    artifact_id = artifact_resp.json()["data"]["id"]

    schema = mcp_client.call_tool_structured(
        "resource.get", {"resource_key": "learning", "plugin_slug": "core"}
    )
    assert schema["resource"]["key"] == "learning"

    record = mcp_client.call_tool_structured("resource.get", {"record_id": record_id})
    assert record["record"]["data_json"]["body"] == "Previous run context matters."
    assert record["record"]["data_json"]["api_key"] == "[redacted]"

    resources = mcp_client.call_tool_structured(
        "resource.query", {"project_id": pid, "plugin_slug": "core"}
    )
    assert [item["id"] for item in resources["records"]] == [record_id]
    assert resources["records"][0]["data_json"]["api_key"] == "[redacted]"

    artifact = mcp_client.call_tool_structured("artifact.get", {"artifact_id": artifact_id})
    assert artifact["uri"] == "/generated-assets/read-tool.png"

    artifacts = mcp_client.call_tool_structured("artifact.query", {"project_id": pid})
    assert [item["id"] for item in artifacts["items"]] == [artifact_id]


def test_resource_and_artifact_writes_are_registered_but_not_system_granted(
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
            "data_json": {"body": "not through system grant"},
        },
    )
    artifact_err = mcp_client.call_tool_error(
        "artifact.create",
        {"project_id": pid, "kind": "image", "uri": "/generated-assets/nope.png"},
    )

    assert resource_err["code"] == -32007
    assert resource_err["data"]["tool"] == "resource.upsert"
    assert artifact_err["code"] == -32007
    assert artifact_err["data"]["tool"] == "artifact.create"
