"""workspace.* MCP tools for plugin-first repository binding."""

from __future__ import annotations

from .conftest import MCPClient


def test_workspace_resolve_unknown_requests_connect(
    mcp_client: MCPClient, seeded_project: dict
) -> None:
    resolved = mcp_client.call_tool_structured(
        "workspace.resolve",
        {"repo_fingerprint": "git:unknown", "response_mode": "raw"},
    )

    assert resolved["needs_connect"] is True
    assert resolved["project_id"] is None
    assert resolved["binding"] is None
    assert resolved["setup_state"]["state"] == "needs_workspace_binding"
    assert resolved["setup_state"]["project_scoped_tools_usable"] is False
    assert resolved["ui_urls"]["projects"] == "http://127.0.0.1:5180/"
    assert resolved["ui_health"]["daemon_reached"] is True
    assert resolved["ui_health"]["check_source"] == "mcp_daemon_response"
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


def test_workspace_bootstrap_rejects_generic_inferred_project_name(
    mcp_client: MCPClient,
) -> None:
    failed = mcp_client.call_tool_error(
        "workspace.bootstrap",
        {"cwd": "/tmp/Resources"},
    )

    assert failed["code"] == -32602
    assert "project_name or project_slug is required" in failed["data"]["detail"]
    assert failed["data"]["project_identity_required"] is True
    assert failed["data"]["recommended_arguments"]["project_name"]


def test_workspace_start_session_requests_project_name_for_generic_folder(
    mcp_client: MCPClient,
) -> None:
    started = mcp_client.call_tool_structured(
        "workspace.startSession",
        {
            "runtime": "claude-desktop",
            "cwd": "/tmp/Resources",
        },
    )

    assert started["project_id"] is None
    assert started["data"]["project_id"] is None
    assert started["data"]["workspace_binding_id"] is None
    assert started["data"]["needs_connect"] is True
    assert started["data"]["next_step"]["project_identity_required"] is True
    assert started["data"]["next_step"]["recommended_arguments"]["project_name"]


def test_workspace_start_session_without_identity_lists_named_workspaces_without_binding(
    mcp_client: MCPClient,
) -> None:
    bootstrapped = mcp_client.call_tool_structured(
        "workspace.bootstrap",
        {
            "project_name": "Flowmonkey",
            "workspace_alias": "flowmonkey",
        },
    )

    started = mcp_client.call_tool_structured(
        "workspace.startSession",
        {
            "runtime": "claude-desktop",
        },
    )

    assert bootstrapped["data"]["binding"]["binding_kind"] == "named"
    assert bootstrapped["data"]["binding"]["workspace_alias"] == "flowmonkey"
    assert bootstrapped["data"]["binding"]["last_known_root"] is None
    assert started["project_id"] is None
    assert started["data"]["project_id"] is None
    assert started["data"]["workspace_binding_id"] is None
    assert started["data"]["needs_connect"] is True
    assert started["data"]["next_step"]["status"] == "project_selection_required"
    assert [
        candidate["workspace_alias"] for candidate in started["data"]["candidate_workspaces"]
    ] == ["flowmonkey"]
    assert started["data"]["candidate_workspaces"][0]["project_id"] == bootstrapped["project_id"]
    assert started["data"]["candidate_workspaces"][0]["ui_urls"]["setup"] == (
        f"http://127.0.0.1:5180/projects/{bootstrapped['project_id']}/setup"
    )


def test_workspace_connect_named_workspace_by_alias_only_after_existing_binding(
    mcp_client: MCPClient,
) -> None:
    bootstrapped = mcp_client.call_tool_structured(
        "workspace.bootstrap",
        {
            "project_name": "Flowmonkey",
            "workspace_alias": "flowmonkey",
        },
    )

    connected = mcp_client.call_tool_structured(
        "workspace.connect",
        {
            "workspace_alias": "flowmonkey",
        },
    )

    assert connected["project_id"] == bootstrapped["project_id"]
    assert connected["data"]["id"] == bootstrapped["data"]["binding"]["id"]
    assert connected["data"]["binding_kind"] == "named"
    assert connected["data"]["workspace_alias"] == "flowmonkey"


def test_workspace_start_session_rebinds_when_alias_is_explicit(
    mcp_client: MCPClient,
) -> None:
    bootstrapped = mcp_client.call_tool_structured(
        "workspace.bootstrap",
        {
            "project_name": "Flowmonkey",
            "workspace_alias": "flowmonkey",
        },
    )

    started = mcp_client.call_tool_structured(
        "workspace.startSession",
        {
            "runtime": "claude-desktop",
            "workspace_alias": "flowmonkey",
        },
    )

    assert started["project_id"] == bootstrapped["project_id"]
    assert started["data"]["project_id"] == bootstrapped["project_id"]
    assert started["data"]["workspace_binding_id"] == bootstrapped["data"]["binding"]["id"]
    assert started["data"]["needs_connect"] is False
    assert started["data"]["setup_state"]["workspace_bound"] is True


def test_workspace_connect_new_named_workspace_requires_project_identity(
    mcp_client: MCPClient,
) -> None:
    failed = mcp_client.call_tool_error(
        "workspace.connect",
        {
            "workspace_alias": "new-client",
        },
    )

    assert failed["code"] == -32602
    assert "project_id, project_slug, or project_name is required" in failed["data"]["detail"]


def test_workspace_alias_rejects_non_named_repo_fingerprint_mix(
    mcp_client: MCPClient, seeded_project: dict
) -> None:
    failed = mcp_client.call_tool_error(
        "workspace.connect",
        {
            "project_id": seeded_project["data"]["id"],
            "repo_fingerprint": "path:abc123",
            "workspace_alias": "flowmonkey",
        },
    )

    assert failed["code"] == -32602
    assert "workspace_alias cannot be combined" in failed["data"]["detail"]


def test_workspace_connect_rejects_app_bundle_workspace_root(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    failed = mcp_client.call_tool_error(
        "workspace.connect",
        {
            "project_id": seeded_project["data"]["id"],
            "repo_fingerprint": "path:app-bundle",
            "last_known_root": "/Applications/StackOS.app/Contents/Resources",
        },
    )

    assert failed["code"] == -32602
    assert "usable project directory" in failed["data"]["detail"]


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
    assert started["data"]["ui_health"]["check_source"] == "mcp_daemon_response"


def test_workspace_connect_requires_rebind_flag_to_move_existing_binding(
    mcp_client: MCPClient, seeded_project: dict
) -> None:
    second_project = mcp_client.call_tool_structured(
        "project.create",
        {
            "slug": "second-test-project",
            "name": "Second Test Project",
            "domain": "second.example",
            "locale": "en-US",
        },
    )
    first_project_id = seeded_project["data"]["id"]
    second_project_id = second_project["data"]["id"]
    connected = mcp_client.call_tool_structured(
        "workspace.connect",
        {
            "project_id": first_project_id,
            "repo_fingerprint": "git:rebind-guard",
            "git_remote_url": "git@github.com:org/rebind-guard.git",
        },
    )

    failed = mcp_client.call_tool_error(
        "workspace.connect",
        {
            "project_id": second_project_id,
            "repo_fingerprint": "git:rebind-guard",
        },
    )

    assert failed["code"] == -32602
    assert failed["data"]["binding_id"] == connected["data"]["id"]
    assert failed["data"]["repo_fingerprint"] == "git:rebind-guard"
    assert failed["data"]["current_project_id"] == first_project_id
    assert failed["data"]["requested_project_id"] == second_project_id
    assert "rebind_existing=true" in failed["data"]["detail"]

    moved = mcp_client.call_tool_structured(
        "workspace.connect",
        {
            "project_id": second_project_id,
            "repo_fingerprint": "git:rebind-guard",
            "rebind_existing": True,
        },
    )

    assert moved["project_id"] == second_project_id
    assert moved["data"]["id"] == connected["data"]["id"]


def test_workspace_start_session_autobootstraps_unbound_directory(
    mcp_client: MCPClient,
) -> None:
    started = mcp_client.call_tool_structured(
        "workspace.startSession",
        {
            "runtime": "codex",
            "cwd": "/tmp/mcp-autobootstrap-project",
            "response_mode": "raw",
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
    suggestion = started["data"]["setup_state"]["profile_update_suggestion"]
    assert suggestion["tool"] == "workspace.updateProfile"
    assert suggestion["call_via"] == "toolbox.call"
    assert suggestion["recommended_arguments"]["binding_id"] == bindings[0]["id"]
    assert suggestion["recommended_arguments"]["project_id"] == started["project_id"]
    assert suggestion["recommended_arguments"]["framework"] == "<detected-framework-or-stack>"
    assert suggestion["recommended_arguments"]["content_model_json"]["content_heavy"] is False
    assert any("non-content SaaS" in item for item in suggestion["guidance"])

    updated = mcp_client.call_tool_structured(
        "workspace.updateProfile",
        {
            "binding_id": bindings[0]["id"],
            "project_id": started["project_id"],
            "framework": "vue",
            "content_model_json": {"content_heavy": False, "primary_runtime": "StackOS"},
        },
    )
    restarted = mcp_client.call_tool_structured(
        "workspace.startSession",
        {
            "runtime": "codex",
            "cwd": "/tmp/mcp-autobootstrap-project",
        },
    )

    assert updated["project_id"] == started["project_id"]
    assert updated["data"]["framework"] == "vue"
    assert restarted["data"]["setup_state"]["state"] == "bound_profile_configured"
    assert restarted["data"]["setup_state"]["profile_missing"] == []
    assert "profile_update_suggestion" not in restarted["data"]["setup_state"]


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
        {"cwd": "/tmp/stackos-rooted-site/packages/site", "response_mode": "raw"},
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
