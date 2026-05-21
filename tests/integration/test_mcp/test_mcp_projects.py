"""project.*, budget.*, and schedule.* exercise via MCP."""

from __future__ import annotations

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
    assert proj["slug"] == "test-project"
    proj2 = mcp_client.call_tool_structured(
        "project.get", {"id_or_slug": seeded_project["data"]["id"]}
    )
    assert proj2["id"] == seeded_project["data"]["id"]


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


def test_project_set_active_writes_envelope(mcp_client: MCPClient, seeded_project: dict) -> None:
    """project.setActive returns an envelope with project_id."""
    pid = seeded_project["data"]["id"]
    env = mcp_client.call_tool_structured("project.setActive", {"project_id": pid})
    assert env["project_id"] == pid
    assert env["data"]["is_active"] is True


def test_schedule_set_toggle_and_remove(mcp_client: MCPClient, seeded_project: dict) -> None:
    """schedule.remove mirrors REST DELETE by disabling the scheduled job."""
    pid = seeded_project["data"]["id"]
    created = mcp_client.call_tool_structured(
        "schedule.set",
        {
            "project_id": pid,
            "kind": "weekly-review",
            "cron_expr": "15 3 * * *",
            "enabled": True,
        },
    )
    job_id = created["data"]["id"]

    disabled = mcp_client.call_tool_structured(
        "schedule.toggle", {"job_id": job_id, "enabled": False}
    )
    assert disabled["data"]["enabled"] is False

    reenabled = mcp_client.call_tool_structured(
        "schedule.toggle", {"job_id": job_id, "enabled": True}
    )
    assert reenabled["data"]["enabled"] is True

    removed = mcp_client.call_tool_structured("schedule.remove", {"job_id": job_id})
    assert removed["data"]["enabled"] is False


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
