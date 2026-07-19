"""MCP tool metadata, envelope discipline, and wire conversion."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, get_origin

import mcp.types as mcp_types
from pydantic import BaseModel

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope, verb_is_mutating
from stackos.mcp.streaming import ProgressEmitter

ToolHandler = Callable[[Any, MCPContext, ProgressEmitter], Awaitable[Any]]


@dataclass
class ToolSpec:
    """Static metadata and handler for a registered MCP tool."""

    name: str
    description: str
    input_model: type[MCPInput]
    output_model: Any
    handler: ToolHandler
    streaming: bool = False
    read_only: bool | None = None
    operation_name: str | None = None
    operation_category: str | None = None
    operation_grant_policy: str | None = None
    operation_secret_policy: str | None = None
    operation_purpose: str | None = None
    operation_response_policy: dict[str, Any] | None = None
    output_schema_model: Any | None = None

    def __post_init__(self) -> None:
        if self.read_only is None:
            self.read_only = not verb_is_mutating(self.name)


class ToolRegistry:
    """Hold every registered ``ToolSpec`` with stable name ordering."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise RuntimeError(f"duplicate tool registration: {spec.name!r}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        return self._tools[name]

    def all(self) -> list[ToolSpec]:
        return [self._tools[k] for k in sorted(self._tools)]

    def __contains__(self, name: object) -> bool:  # pragma: no cover - ergonomics
        return isinstance(name, str) and name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


def _is_write_envelope(tp: Any) -> bool:
    origin = get_origin(tp) or tp
    if origin is WriteEnvelope:
        return True
    try:
        return isinstance(origin, type) and issubclass(origin, WriteEnvelope)
    except TypeError:  # pragma: no cover - non-class origin
        return False


def assert_envelope_discipline(registry: ToolRegistry) -> None:
    """Require every mutating tool to declare a ``WriteEnvelope`` output."""
    offenders: list[str] = []
    for spec in registry.all():
        if spec.read_only:
            continue
        if not _is_write_envelope(spec.output_model):
            offenders.append(spec.name)
    if offenders:
        raise RuntimeError(
            "MCP envelope discipline violated — mutating tools must return WriteEnvelope[...]: "
            + ", ".join(offenders)
        )


def _input_schema(model: type[MCPInput]) -> dict[str, Any]:
    return model.model_json_schema(mode="serialization")


def _operation_input_schema(spec: ToolSpec) -> dict[str, Any]:
    schema = _input_schema(spec.input_model)
    policy = spec.operation_response_policy
    if not isinstance(policy, dict):
        return schema
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return schema
    response_mode = properties.get("response_mode")
    if not isinstance(response_mode, dict):
        return schema
    allowed = policy.get("allowed_modes")
    modes = _response_mode_schema_values(allowed if isinstance(allowed, list) else [])
    if modes:
        response_mode["enum"] = modes
    default_mode = policy.get("default_mode")
    if isinstance(default_mode, str):
        response_mode["default"] = default_mode
    return schema


def _response_mode_schema_values(allowed_modes: list[Any]) -> list[str]:
    values: list[str] = []
    if "compact" in allowed_modes:
        values.append("compact")
    if "raw" in allowed_modes:
        values.extend(["raw", "standard", "verbose"])
    if "ack" in allowed_modes:
        values.append("ack")
    return values


def _output_schema(model: Any) -> dict[str, Any]:
    from pydantic import TypeAdapter

    if isinstance(model, type) and issubclass(model, BaseModel):
        return model.model_json_schema(mode="serialization")
    origin = get_origin(model)
    if origin is list:
        inner = TypeAdapter(model).json_schema(mode="serialization")
        defs = inner.pop("$defs", None)
        wrapped: dict[str, Any] = {
            "type": "object",
            "properties": {"items": inner},
            "required": ["items"],
        }
        if defs:
            wrapped["$defs"] = defs
        return wrapped
    return TypeAdapter(model).json_schema(mode="serialization")


def _to_tool(spec: ToolSpec) -> mcp_types.Tool:
    annotations = mcp_types.ToolAnnotations(
        readOnlyHint=spec.read_only,
        idempotentHint=spec.read_only,
        title=spec.name,
    )
    meta: dict[str, Any] = {"streaming": spec.streaming}
    if spec.operation_name is not None:
        meta["operation_name"] = spec.operation_name
    if spec.operation_category is not None:
        meta["operation_category"] = spec.operation_category
    if spec.operation_grant_policy is not None:
        meta["grant_policy"] = spec.operation_grant_policy
    if spec.operation_secret_policy is not None:
        meta["secret_policy"] = spec.operation_secret_policy
    if spec.operation_purpose is not None:
        meta["purpose"] = spec.operation_purpose
    if spec.operation_response_policy is not None:
        meta["response_policy"] = spec.operation_response_policy
    return mcp_types.Tool(
        name=spec.name,
        description=spec.description,
        inputSchema=_operation_input_schema(spec)
        if spec.operation_name is not None
        else _input_schema(spec.input_model),
        outputSchema=_output_schema(spec.output_schema_model or spec.output_model),
        annotations=annotations,
        _meta=meta,
    )


def _result_to_json(result: Any) -> dict[str, Any]:
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json", by_alias=True)
    if isinstance(result, list):
        return {"items": [_item_to_json(x) for x in result]}
    if isinstance(result, dict):
        return {
            k: (_result_to_json(v) if isinstance(v, BaseModel) else v) for k, v in result.items()
        }
    return {"value": result}


def _item_to_json(item: Any) -> Any:
    if isinstance(item, BaseModel):
        return item.model_dump(mode="json", by_alias=True)
    if isinstance(item, dict):
        return {
            k: (_item_to_json(v) if not isinstance(v, str | int | float | bool | type(None)) else v)
            for k, v in item.items()
        }
    if isinstance(item, list):
        return [_item_to_json(x) for x in item]
    return item


__all__ = ["ToolHandler", "ToolRegistry", "ToolSpec", "assert_envelope_discipline"]
