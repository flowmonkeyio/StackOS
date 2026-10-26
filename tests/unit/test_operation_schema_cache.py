from __future__ import annotations

from dataclasses import replace
from typing import ClassVar

import pytest
from pydantic import BaseModel, Field

from stackos.mcp.server import ToolRegistry
from stackos.operations import spec as operation_spec_module
from stackos.operations.adapters.mcp import register_mcp_operations
from stackos.operations.registry import build_operation_registry
from stackos.operations.spec import OperationResponsePolicy, _schema_for


def test_schema_reuse_preserves_nested_mutation_isolation() -> None:
    class Contract(BaseModel):
        calls: ClassVar[int] = 0
        values: list[str] = Field(default_factory=list)

        @classmethod
        def model_json_schema(cls, **kwargs):
            cls.calls += 1
            return super().model_json_schema(**kwargs)

    first = _schema_for(Contract)
    first["properties"]["values"]["items"]["type"] = "integer"
    assert _schema_for(Contract)["properties"]["values"]["items"]["type"] == "string"
    assert Contract.calls == 1


def test_rebuilt_model_gets_fresh_schema() -> None:
    class Contract(BaseModel):
        value: str

    assert _schema_for(Contract)["title"] == "Contract"
    Contract.model_config["title"] = "Rebuilt contract"
    Contract.model_rebuild(force=True)
    assert _schema_for(Contract)["title"] == "Rebuilt contract"


def test_response_policy_overlays_do_not_leak_between_operations() -> None:
    original = build_operation_registry().get("action.run")
    raw = replace(
        original,
        response_policy=OperationResponsePolicy(default_mode="raw", allowed_modes=("raw",)),
    )
    assert raw.describe_out().input_schema["properties"]["response_mode"]["enum"] == [
        "raw",
        "standard",
        "verbose",
    ]
    normal = original.describe_out()
    assert normal.input_schema["properties"]["response_mode"]["enum"] == [
        "compact",
        "raw",
        "standard",
        "verbose",
    ]
    normal.output_schema.clear()
    assert original.describe_out().output_schema


def test_non_model_output_schema_remains_supported() -> None:
    assert _schema_for(list[str]) == {"items": {"type": "string"}, "type": "array"}


def test_mcp_registration_preserves_response_policy_without_building_schemas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = build_operation_registry()
    expected_policies = {
        name: operations.get(name).describe_out().response_policy.model_dump(mode="json")
        for name in ("action.run", "communication.send", "resource.upsert")
    }

    def unexpected_schema(_model):
        pytest.fail("MCP registration must not construct operation schemas")

    monkeypatch.setattr(operation_spec_module, "_schema_for", unexpected_schema)
    tools = ToolRegistry()
    register_mcp_operations(tools, operations)

    assert {tool.name for tool in tools.all()} == {
        operation.name for operation in operations.by_surface("mcp")
    }
    for name, expected in expected_policies.items():
        tool = tools.get(name)
        operation = operations.get(name)
        assert tool.operation_response_policy == expected
        assert tool.handler is operation.handler
        assert tool.input_model is operation.input_model
        assert tool.output_model is operation.output_model
