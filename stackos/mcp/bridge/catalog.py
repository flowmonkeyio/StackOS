"""Tool catalog filtering and schema shaping for the agent bridge."""

from __future__ import annotations

import json
from typing import Any

from .constants import (
    _AGENT_COMPACT_DEFAULT_TOOL_NAMES,
    _AGENT_RESPONSE_MODE_FIELD,
    _AGENT_VISIBLE_TOOL_ORDER,
    _TOOLBOX_CALL_TOOL,
    _TOOLBOX_DESCRIBE_TOOL,
)


def _bridge_toolbox_specs() -> list[dict[str, Any]]:
    """Return bridge-local virtual tools shown to agent MCP clients."""
    flexible_object = {"type": "object", "additionalProperties": True}
    return [
        {
            "name": _TOOLBOX_DESCRIBE_TOOL,
            "description": (
                "Describe hidden StackOS daemon tools available through the bridge. "
                "Use this before toolbox.call when setup, a run-plan step, or the current "
                "run-plan step mentions a tool that is not listed directly."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tool_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional exact daemon tool names to describe.",
                    },
                    "run_id": {
                        "type": "integer",
                        "description": "Run id used to refresh run-plan grants.",
                    },
                    "include_schemas": {
                        "type": "boolean",
                        "description": "When true and tool_names is omitted, include schemas.",
                        "default": False,
                    },
                },
                "additionalProperties": False,
            },
            "outputSchema": flexible_object,
            "_meta": {"stackos_bridge_virtual": True},
        },
        {
            "name": _TOOLBOX_CALL_TOOL,
            "description": (
                "Call one hidden daemon tool by name. The bridge permits setup tools and "
                "the active run-plan step's allowed tools only; pass run_id so "
                "the bridge can refresh grants and inject the run token when available."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "Exact daemon tool name."},
                    "arguments": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "Arguments for the daemon tool.",
                    },
                    "run_id": {
                        "type": "integer",
                        "description": "Run id for step-scoped run-plan grants.",
                    },
                },
                "required": ["tool_name", "arguments"],
                "additionalProperties": False,
            },
            "outputSchema": flexible_object,
            "_meta": {"stackos_bridge_virtual": True},
        },
    ]


def _bridge_tool_catalog(response_text: str) -> dict[str, dict[str, Any]]:
    try:
        envelope = json.loads(response_text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(envelope, dict):
        return {}
    result = envelope.get("result")
    if not isinstance(result, dict):
        return {}
    tools = result.get("tools")
    if not isinstance(tools, list):
        return {}
    catalog: dict[str, dict[str, Any]] = {}
    for tool in tools:
        if isinstance(tool, dict) and isinstance(tool.get("name"), str):
            catalog[tool["name"]] = tool
    return catalog


def _bridge_filter_tool_list_response(
    response_text: str,
    *,
    scoped_project_id: int | None = None,
    injected_fields: set[str] | frozenset[str] | None = None,
) -> str:
    """Filter daemon ``tools/list`` down to the agent-facing bridge surface."""
    try:
        envelope = json.loads(response_text)
    except json.JSONDecodeError:
        return response_text
    if not isinstance(envelope, dict):
        return response_text
    result = envelope.get("result")
    if not isinstance(result, dict):
        return response_text
    tools = result.get("tools")
    if not isinstance(tools, list):
        return response_text

    by_name = {
        tool["name"]: tool
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    }
    filtered = [by_name[name] for name in _AGENT_VISIBLE_TOOL_ORDER if name in by_name]
    injected = set(injected_fields or ())
    if scoped_project_id is not None:
        injected.add("project_id")
    filtered = [_bridge_agent_tool_schema(tool, injected_fields=injected) for tool in filtered]
    filtered.extend(_bridge_toolbox_specs())
    result["tools"] = filtered
    return json.dumps(envelope, default=str)


def _bridge_agent_tool_schema(
    tool: dict[str, Any],
    *,
    injected_fields: set[str],
) -> dict[str, Any]:
    clone = _bridge_relax_injected_schema(tool, injected_fields=injected_fields)
    if clone.get("name") in _AGENT_COMPACT_DEFAULT_TOOL_NAMES:
        clone = _bridge_add_response_mode_schema(clone)
    return clone


def _bridge_relax_injected_schema(
    tool: dict[str, Any],
    *,
    injected_fields: set[str],
) -> dict[str, Any]:
    """Make bridge-injected fields optional in advertised schemas."""
    clone = json.loads(json.dumps(tool, default=str))
    schema = clone.get("inputSchema")
    if not isinstance(schema, dict):
        return clone
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return clone
    present = injected_fields & set(properties)
    if not present:
        return clone
    required = schema.get("required")
    if isinstance(required, list):
        schema["required"] = [item for item in required if item not in present]
    for field in present:
        prop = properties.get(field)
        if isinstance(prop, dict):
            description = prop.get("description")
            suffix = "Injected from the current workspace by the StackOS bridge."
            prop["description"] = f"{description} {suffix}".strip() if description else suffix
    return clone


def _bridge_add_response_mode_schema(tool: dict[str, Any]) -> dict[str, Any]:
    clone = json.loads(json.dumps(tool, default=str))
    schema = clone.get("inputSchema")
    if not isinstance(schema, dict):
        return clone
    properties = schema.setdefault("properties", {})
    if not isinstance(properties, dict):
        return clone
    properties.setdefault(
        _AGENT_RESPONSE_MODE_FIELD,
        {
            "type": "string",
            "enum": ["compact", "standard", "verbose"],
            "default": "compact",
            "description": (
                "Agent response shape. compact is default; standard/verbose returns "
                "the full daemon payload for diagnostics."
            ),
        },
    )
    return clone


def _bridge_tool_accepts_project_id(catalog: dict[str, dict[str, Any]], tool_name: str) -> bool:
    return _bridge_tool_accepts_field(catalog, tool_name, "project_id")


def _bridge_tool_accepts_field(
    catalog: dict[str, dict[str, Any]],
    tool_name: str,
    field: str,
) -> bool:
    schema = catalog.get(tool_name, {}).get("inputSchema")
    if not isinstance(schema, dict):
        return False
    properties = schema.get("properties")
    return isinstance(properties, dict) and field in properties
