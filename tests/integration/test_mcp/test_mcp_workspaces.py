"""workspace.* MCP tools for plugin-first repository binding."""

from __future__ import annotations

from .conftest import MCPClient


def test_workspace_resolve_unknown_requests_connect(
    mcp_client: MCPClient, seeded_project: dict
) -> None:
    resolved = mcp_client.call_tool_structured(
        "workspace.resolve",
        {"repo_fingerprint": "git:unknown"},
    )

    assert resolved["needs_connect"] is True
    assert resolved["project_id"] is None
    assert resolved["binding"] is None
    assert resolved["setup_state"]["state"] == "needs_workspace_binding"
    assert resolved["setup_state"]["project_scoped_tools_usable"] is False
    assert resolved["ui_urls"]["projects"] == "http://127.0.0.1:5180/"
    assert resolved["ui_health"]["daemon_reached"] is True
    assert resolved["next_step"]["recommended_tool"] == "workspace.bootstrap"
    assert [project["id"] for project in resolved["candidate_projects"]] == [
        seeded_project["data"]["id"]
    ]
    assert resolved["candidate_projects"][0]["ui_urls"]["setup"] == (
        f"http://127.0.0.1:5180/projects/{seeded_project['data']['id']}/setup"
    )


def test_workspace_bootstrap_creates_project_once_for_workspace_root(
    mcp_client: MCPClient,
) -> None:
    first = mcp_client.call_tool_structured(
        "workspace.bootstrap",
        {
            "cwd": "/tmp/mcp-bootstrap-project",
            "git_remote_url": "git@github.com:org/mcp-bootstrap-project.git",
            "framework": "nuxt",
        },
    )
    second = mcp_client.call_tool_structured(
        "workspace.bootstrap",
        {
            "cwd": "/tmp/mcp-bootstrap-project",
            "git_remote_url": "git@github.com:org/mcp-bootstrap-project.git",
        },
    )

    assert first["data"]["project_was_created"] is True
    assert first["data"]["binding_was_created"] is True
    assert first["data"]["project"]["slug"] == "mcp-bootstrap-project"
    assert second["data"]["project_was_created"] is False
    assert second["data"]["binding_was_created"] is False
    assert second["data"]["project_id"] == first["data"]["project_id"]
    assert second["data"]["binding"]["id"] == first["data"]["binding"]["id"]


def test_workspace_connect_list_and_start_session(
    mcp_client: MCPClient, seeded_project: dict
) -> None:
    project_id = seeded_project["data"]["id"]
    project_slug = seeded_project["data"]["slug"]

    connected = mcp_client.call_tool_structured(
        "workspace.connect",
        {
            "project_slug": project_slug,
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
    assert started["data"]["setup_state"]["state"] == "bound_profile_configured"
    assert started["data"]["setup_state"]["project_scoped_tools_usable"] is True
    assert started["data"]["setup_state"]["profile_missing"] == []
    assert (
        started["data"]["ui_urls"]["setup"] == f"http://127.0.0.1:5180/projects/{project_id}/setup"
    )
    assert started["data"]["ui_health"]["daemon_reached"] is True


def test_workspace_start_session_autobootstraps_unbound_directory(
    mcp_client: MCPClient,
) -> None:
    started = mcp_client.call_tool_structured(
        "workspace.startSession",
        {
            "runtime": "codex",
            "cwd": "/tmp/mcp-autobootstrap-project",
        },
    )
    bindings_payload = mcp_client.call_tool_structured(
        "workspace.listBindings",
        {"project_id": started["project_id"]},
    )
    bindings = bindings_payload["items"]

    assert started["project_id"] is not None
    assert started["data"]["needs_connect"] is False
    assert started["data"]["auto_bootstrap"] is True
    assert started["data"]["project_was_created"] is True
    assert started["data"]["binding_was_created"] is True
    assert started["data"]["workspace_binding_id"] == bindings[0]["id"]
    assert started["data"]["next_step"]["status"] == "ready"
    assert "operation.list" in started["data"]["next_step"]["recommended_tools"]
    assert started["data"]["setup_state"]["state"] == "bound_profile_incomplete"
    assert started["data"]["setup_state"]["profile_missing"] == [
        "framework",
        "content_model_json",
    ]


def test_workspace_resolves_by_current_directory_root(
    mcp_client: MCPClient, seeded_project: dict
) -> None:
    project_id = seeded_project["data"]["id"]
    connected = mcp_client.call_tool_structured(
        "workspace.connect",
        {
            "project_id": project_id,
            "repo_fingerprint": "path:rooted",
            "last_known_root": "/tmp/stackos-rooted-site",
        },
    )

    resolved = mcp_client.call_tool_structured(
        "workspace.resolve",
        {"cwd": "/tmp/stackos-rooted-site/packages/site"},
    )
    started = mcp_client.call_tool_structured(
        "workspace.startSession",
        {"runtime": "codex", "cwd": "/tmp/stackos-rooted-site/packages/site"},
    )

    assert connected["project_id"] == project_id
    assert resolved["project_id"] == project_id
    assert resolved["needs_connect"] is False
    assert started["project_id"] == project_id
    assert started["data"]["workspace_binding_id"] == connected["data"]["id"]


def test_workspace_connect_missing_project_returns_not_found(mcp_client: MCPClient) -> None:
    err = mcp_client.call_tool_error(
        "workspace.connect",
        {"project_id": 999, "repo_fingerprint": "git:abc123"},
    )

    assert err["code"] == -32004
    assert err["message"] == "NotFoundError"
