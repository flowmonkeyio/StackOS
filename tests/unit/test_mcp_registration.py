"""MCP composition shares one operation owner per app without sharing app state."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from fastapi import FastAPI

from stackos.mcp.server import register_mcp
from stackos.mcp.tool_registry import ToolRegistry, assert_envelope_discipline
from stackos.mcp.tools import register_all
from stackos.operations import registry as operations_module
from stackos.operations.registry import OperationRegistry


def _assert_complete_contract(tools: ToolRegistry, operations: OperationRegistry) -> None:
    exposed = operations.by_surface("mcp")
    names = [tool.name for tool in tools.all()]
    assert names == [operation.name for operation in exposed], (
        "MCP must expose every enabled operation exactly once in canonical name order"
    )
    assert {"workspace.startSession", "hostMcp.status", "action.execute", "secret.set"} <= set(
        names
    )
    assert {"backup.status", "backup.configure", "backup.create"}.isdisjoint(names)
    assert_envelope_discipline(tools)
    for operation in exposed:
        tool = tools.get(operation.name)
        assert tool.handler is operation.handler, operation.name
        assert tool.input_model is operation.input_model, operation.name
        assert tool.output_model is operation.output_model, operation.name
        assert (
            tool.description,
            tool.read_only,
            tool.operation_name,
            tool.operation_category,
            tool.operation_grant_policy,
            tool.operation_secret_policy,
            tool.operation_purpose,
        ) == (
            operation.mcp_description,
            operation.read_only,
            operation.name,
            operation.category_name,
            operation.grant_policy,
            operation.secret_policy,
            operation.purpose,
        ), operation.name
        policy = operation.effective_response_policy
        assert tool.operation_response_policy == {
            "default_mode": policy.default_mode,
            "allowed_modes": list(policy.allowed_modes),
            "ack_safe": policy.ack_safe,
            "raw_only_reason": policy.raw_only_reason,
            "compact_notes": list(policy.compact_notes),
        }, operation.name
        assert tool.output_schema_model == dict[str, Any], operation.name
        assert tool.streaming is False, operation.name


@pytest.fixture
def registry_builds(monkeypatch):
    built = []
    build = operations_module.build_operation_registry

    def observed_build():
        operations = build()
        built.append(operations)
        return operations

    monkeypatch.setattr(operations_module, "build_operation_registry", observed_build)
    return built


def test_standalone_catalog_builds_once_and_preserves_the_full_operation_contract(registry_builds):
    tools = ToolRegistry()
    register_all(tools)

    assert len(registry_builds) == 1, "standalone catalog registration rebuilt operation families"
    _assert_complete_contract(tools, registry_builds[0])


def test_explicit_empty_operation_registry_does_not_fall_back_to_the_full_catalog(registry_builds):
    tools = ToolRegistry()
    register_all(tools, operation_registry=OperationRegistry())

    assert registry_builds == [], "a supplied registry must remain the registration authority"
    assert tools.all() == []


def test_apps_share_one_registry_with_their_dispatcher_and_keep_catalog_mutations_isolated(
    registry_builds,
):
    first, second = FastAPI(), FastAPI()
    register_mcp(first)
    register_mcp(second)

    assert len(registry_builds) == 2, "each app must build exactly one fresh operation registry"
    for app, operations in zip((first, second), registry_builds, strict=True):
        assert app.state.operation_registry is operations
        assert app.state.mcp_dispatcher._operation_registry is operations
        assert app.state.mcp_dispatcher._registry is app.state.mcp_registry
        _assert_complete_contract(app.state.mcp_registry, operations)

    assert first.state.operation_registry is not second.state.operation_registry
    assert first.state.mcp_registry is not second.state.mcp_registry
    first_tool = first.state.mcp_registry.get("action.execute")
    second_tool = second.state.mcp_registry.get("action.execute")
    assert first_tool is not second_tool
    assert first_tool.operation_response_policy is not None
    assert second_tool.operation_response_policy is not None
    original_description = second_tool.description
    original_modes = list(second_tool.operation_response_policy["allowed_modes"])
    first_tool.description = "Only this app"
    first_tool.operation_response_policy["allowed_modes"].append("test-only-mode")
    assert second_tool.description == original_description
    assert second_tool.operation_response_policy["allowed_modes"] == original_modes

    first.state.operation_registry.register(
        replace(first.state.operation_registry.get("meta.enums"), name="test.appOnly")
    )
    assert "test.appOnly" not in {item.name for item in second.state.operation_registry.all()}
    for app in (first, second):
        app.state.engine = object()
        app.state.settings = object()
        assert app.state.mcp_dispatcher._engine_resolver() is app.state.engine
        assert app.state.mcp_dispatcher._settings_resolver() is app.state.settings
