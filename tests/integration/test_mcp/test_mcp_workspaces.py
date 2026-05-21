"""workspace.* MCP tools for plugin-first repository binding."""

from __future__ import annotations

from .conftest import MCPClient


def test_workspace_resolve_unknown_requests_connect(mcp_client: MCPClient) -> None:
    resolved = mcp_client.call_tool_structured(
        "workspace.resolve",
        {"repo_fingerprint": "git:unknown"},
    )

    assert resolved["needs_connect"] is True
    assert resolved["project_id"] is None
    assert resolved["binding"] is None


def test_workspace_connect_list_and_start_session(
    mcp_client: MCPClient, seeded_project: dict
) -> None:
    project_id = seeded_project["data"]["id"]

    connected = mcp_client.call_tool_structured(
        "workspace.connect",
        {
            "project_id": project_id,
            "repo_fingerprint": "git:abc123",
            "git_remote_url": "git@github.com:org/site.git",
            "normalized_repo_name": "org/site",
            "last_known_root": "/tmp/site",
            "framework": "nuxt",
            "content_model_json": {"primary_resource": "content-piece"},
        },
    )
    bindings_payload = mcp_client.call_tool_structured(
        "workspace.listBindings",
        {"project_id": project_id},
    )
    bindings = bindings_payload["items"]
    started = mcp_client.call_tool_structured(
        "workspace.startSession",
        {
            "runtime": "codex",
            "cwd": "/tmp/site",
            "repo_fingerprint": "git:abc123",
            "thread_id": "thread-1",
        },
    )

    assert connected["project_id"] == project_id
    assert connected["data"]["framework"] == "nuxt"
    assert [row["repo_fingerprint"] for row in bindings] == ["git:abc123"]
    assert started["project_id"] == project_id
    assert started["data"]["workspace_binding_id"] == connected["data"]["id"]


def test_workspace_connect_missing_project_returns_not_found(mcp_client: MCPClient) -> None:
    err = mcp_client.call_tool_error(
        "workspace.connect",
        {"project_id": 999, "repo_fingerprint": "git:abc123"},
    )

    assert err["code"] == -32004
    assert err["message"] == "NotFoundError"
