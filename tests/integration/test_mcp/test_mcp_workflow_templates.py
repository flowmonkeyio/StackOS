"""MCP tests for StackOS workflow template tools."""

from __future__ import annotations

from pathlib import Path

from .conftest import MCPClient


def _template_json(key: str = "company.review") -> dict:
    return {
        "schema_version": "stackos.workflow-template.v1",
        "key": key,
        "name": "Company Review",
        "version": "0.1.0",
        "steps": [{"id": "review", "title": "Review"}],
        "outputs": [{"key": "summary", "type": "object"}],
    }


def test_workflow_template_read_tools_are_callable(
    mcp_client: MCPClient,
    tmp_path: Path,
) -> None:
    override = tmp_path / ".stackos" / "workflows" / "project-memory-review.yaml"
    override.parent.mkdir(parents=True)
    override.write_text(
        """
schema_version: stackos.workflow-template.v1
key: core.project-memory-review
name: Repo Project Memory Review
version: 0.1.0
steps:
  - id: review
    title: Review from repo
outputs:
  - key: summary
    type: object
""",
        encoding="utf-8",
    )

    listing = mcp_client.call_tool_structured(
        "workflowTemplate.list",
        {"repo_root": str(tmp_path), "include_shadowed": True},
    )
    described = mcp_client.call_tool_structured(
        "workflowTemplate.describe",
        {"key": "core.project-memory-review", "repo_root": str(tmp_path)},
    )
    validation = mcp_client.call_tool_structured(
        "workflowTemplate.validate",
        {"template_json": _template_json()},
    )

    sources = {
        item["source"]
        for item in listing["templates"]
        if item["key"] == "core.project-memory-review"
    }
    assert sources == {"plugin", "repo"}
    assert "media-buying.campaign-launch" in {item["key"] for item in listing["templates"]}
    assert described["summary"]["source"] == "repo"
    assert described["summary"]["name"] == "Repo Project Memory Review"
    assert validation["valid"] is True
    assert validation["template"]["key"] == "company.review"

    media_listing = mcp_client.call_tool_structured(
        "workflowTemplate.list",
        {"plugin_slug": "media-buying"},
    )
    assert {item["key"] for item in media_listing["templates"]} >= {
        "media-buying.campaign-launch",
        "media-buying.performance-diagnosis",
    }


def test_workflow_template_validate_rejects_secrets(mcp_client: MCPClient) -> None:
    template = _template_json()
    template["metadata"] = {"api_key": "value"}

    validation = mcp_client.call_tool_structured(
        "workflowTemplate.validate",
        {"template_json": template},
    )

    assert validation["valid"] is False
    assert "must not contain secrets" in validation["errors"][0]["message"]


def test_workflow_template_writes_are_registered_but_not_system_granted(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    for tool_name, arguments in [
        (
            "workflowTemplate.save",
            {"project_id": project_id, "template_json": _template_json()},
        ),
        (
            "workflowTemplate.fork",
            {
                "project_id": project_id,
                "key": "core.project-memory-review",
                "new_key": "company.project-memory-review",
            },
        ),
    ]:
        err = mcp_client.call_tool_error(tool_name, arguments)
        assert err["code"] == -32007
        assert err["message"] == "ToolNotGrantedError"
