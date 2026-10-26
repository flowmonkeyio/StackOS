from __future__ import annotations

from dataclasses import replace
from typing import ClassVar

from pydantic import BaseModel, Field

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
