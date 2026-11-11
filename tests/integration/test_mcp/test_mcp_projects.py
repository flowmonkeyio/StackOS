"""project.*, budget.*, and schedule.* exercise via MCP."""

from __future__ import annotations

import pytest

from .conftest import MCPClient


def test_project_create_returns_envelope(mcp_client: MCPClient) -> None:
    """project.create returns ``{data, run_id, project_id}`` envelope."""
    env = mcp_client.call_tool_structured(
        "project.create",
        {
            "slug": "p-create",
            "name": "P Create",
            "domain": "create.example",
            "locale": "en-US",
        },
    )
    assert "data" in env
    assert "project_id" in env
    assert env["data"]["slug"] == "p-create"


def test_project_get_by_slug(mcp_client: MCPClient, seeded_project: dict) -> None:
    """project.get accepts slug or id."""
    proj = mcp_client.call_tool_structured("project.get", {"id_or_slug": "test-project"})
    assert proj["data"]["slug"] == "test-project"
    proj2 = mcp_client.call_tool_structured(
        "project.get", {"id_or_slug": seeded_project["data"]["id"]}
    )
    assert proj2["data"]["id"] == seeded_project["data"]["id"]
    proj3 = mcp_client.call_tool_structured(
        "project.get", {"project_id": seeded_project["data"]["id"]}
    )
    assert proj3["data"]["slug"] == "test-project"


def test_project_list_after_create(mcp_client: MCPClient, seeded_project: dict) -> None:
    """project.list returns the seeded project."""
    page = mcp_client.call_tool_structured("project.list", {})
    assert page["total_estimate"] >= 1
    slugs = [p["slug"] for p in page["items"]]
    assert "test-project" in slugs


def test_project_update_returns_envelope(mcp_client: MCPClient, seeded_project: dict) -> None:
    """project.update returns the new state under ``data``."""
    pid = seeded_project["data"]["id"]
    env = mcp_client.call_tool_structured(
        "project.update", {"project_id": pid, "patch": {"name": "Renamed"}}
    )
    assert env["data"]["name"] == "Renamed"


@pytest.mark.parametrize("name", ["", "  ", "x" * 201, None, 7])
def test_project_name_rejects_through_mcp(mcp_client, seeded_project, name):
    pid = seeded_project["data"]["id"]
    original = mcp_client.call_tool_structured("project.get", {"project_id": pid})
    error = mcp_client.call_tool_error(
        "project.update", {"project_id": pid, "patch": {"name": name}}
    )
    assert error["code"] == -32602
    assert mcp_client.call_tool_structured("project.get", {"project_id": pid}) == original


def test_project_move_and_name_use_same_mcp_owner(mcp_client, seeded_project):
    pid = seeded_project["data"]["id"]
    other = mcp_client.call_tool_structured(
        "project.create", {"slug": "other-order", "name": "Other", "domain": "test"}
    )["data"]["id"]
    updated = mcp_client.call_tool_structured(
        "project.update", {"project_id": pid, "patch": {"name": "  Renamed  "}}
    )
    assert updated["data"]["name"] == "Renamed"
    moved = mcp_client.call_tool_structured(
        "project.move", {"project_id": other, "placement": "before", "anchor_id": pid}
    )
    assert moved["data"]["id"] == other
    assert [p["id"] for p in mcp_client.call_tool_structured("project.list", {})["items"]] == [
        other,
        pid,
    ]
    assert (
        mcp_client.call_tool_error(
            "project.move", {"project_id": pid, "placement": "top", "scope": "catalog"}
        )["code"]
        == -32007
    )


def test_project_update_preserves_workspace_binding(mcp_client: MCPClient) -> None:
    bootstrapped = mcp_client.call_tool_structured(
        "workspace.bootstrap",
        {
            "cwd": "/tmp/project-update-binding",
            "project_name": "Original Workspace Name",
        },
    )
    binding = bootstrapped["data"]["binding"]
    project_id = bootstrapped["project_id"]

    updated = mcp_client.call_tool_structured(
        "project.update",
        {"project_id": project_id, "patch": {"name": "Renamed Workspace"}},
    )
    resolved = mcp_client.call_tool_structured(
        "workspace.resolve",
        {"cwd": "/tmp/project-update-binding"},
    )

    assert updated["data"]["name"] == "Renamed Workspace"
    assert resolved["project_id"] == project_id
    assert resolved["data"]["binding"]["id"] == binding["id"]
    assert resolved["data"]["binding"]["project_id"] == project_id
    assert resolved["data"]["binding"]["repo_fingerprint"] == binding["repo_fingerprint"]
    assert resolved["data"]["binding"]["last_known_root"] == binding["last_known_root"]


def test_schedule_set_toggle_and_remove(mcp_client: MCPClient, seeded_project: dict) -> None:
    """MCP uses the same inert-draft, scoped-toggle and true-removal contract."""
    pid = seeded_project["data"]["id"]
    created = mcp_client.call_tool_structured(
        "schedule.set",
        {
            "project_id": pid,
            "kind": "weekly-review",
            "cron_expr": "15 3 * * *",
            "enabled": False,
        },
    )
    job_id = created["data"]["id"]

    disabled = mcp_client.call_tool_structured(
        "schedule.toggle", {"project_id": pid, "job_id": job_id, "enabled": False}
    )
    assert disabled["data"]["enabled"] is False

    invalid = mcp_client.call_tool_error(
        "schedule.toggle", {"project_id": pid, "job_id": job_id, "enabled": True}
    )
    assert invalid["code"] == -32602
    assert mcp_client.call_tool_structured("schedule.runs", {"project_id": pid})["items"] == []

    for operation in ("schedule.toggle", "schedule.remove"):
        arguments = {"project_id": pid + 1, "job_id": job_id}
        if operation == "schedule.toggle":
            arguments["enabled"] = False
        assert mcp_client.call_tool_error(operation, arguments)["code"] == -32004
    removed = mcp_client.call_tool_structured(
        "schedule.remove", {"project_id": pid, "job_id": job_id}
    )
    assert removed["data"] == {"project_id": pid, "job_id": job_id, "removed": True}


def test_schedule_role_null_survives_mcp_storage(mcp_client, seeded_project, mcp_app, tmp_path):
    from sqlmodel import Session

    from stackos.db.models import ScheduledJob

    execution = {"project_dir": str(tmp_path), "delivery_agent": {"model": None}}
    result = mcp_client.call_tool_structured(
        "schedule.set",
        {
            "project_id": seeded_project["data"]["id"],
            "kind": "role-clear",
            "cron_expr": "0 9 * * *",
            "execution_json": execution,
        },
    )
    with Session(mcp_app.state.engine) as session:
        assert session.get(ScheduledJob, result["data"]["id"]).execution_json == execution


def test_project_create_duplicate_slug_returns_conflict(
    mcp_client: MCPClient, seeded_project: dict
) -> None:
    """Creating a second project with the seeded slug returns -32008."""
    err = mcp_client.call_tool_error(
        "project.create",
        {
            "slug": "test-project",
            "name": "Duplicate",
            "domain": "dup.example",
            "locale": "en-US",
        },
    )
    assert err["code"] == -32008
    assert err["message"] == "ConflictError"
