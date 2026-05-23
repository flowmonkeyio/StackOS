from __future__ import annotations

from content_stack.operations.registry import build_operation_registry


def test_operation_registry_documents_action_operations() -> None:
    registry = build_operation_registry()

    names = [item.name for item in registry.all()]
    assert names == ["action.describe", "action.execute", "action.validate"]

    described = registry.get("action.execute").describe_out()

    assert described.name == "action.execute"
    assert described.mutating is True
    assert described.surfaces["mcp"].enabled is True
    assert described.surfaces["rest"].enabled is True
    assert described.surfaces["cli"].enabled is True
    assert described.grant_policy == "run-plan-step-action-ref"
    assert "properties" in described.input_schema
    assert "project_id" in described.input_schema["properties"]
    assert "WriteEnvelope" in described.output_schema["title"]
    assert any("run_token" in item for item in described.prerequisites)
    assert described.examples[0].arguments["action_ref"] == "utils.sitemap.fetch"


def test_operation_registry_surface_filter() -> None:
    registry = build_operation_registry()

    assert [item.name for item in registry.by_surface("cli")] == [
        "action.describe",
        "action.execute",
        "action.validate",
    ]
    assert registry.list_out(surface="rest").items[0].surfaces["rest"].enabled is True
