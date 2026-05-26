"""JSON-RPC and MCP protocol helpers for the agent bridge."""

from __future__ import annotations

import json
from typing import Any


def _bridge_response_text(text: str) -> str:
    """Extract a JSON-RPC body from either JSON or single-event SSE text."""
    stripped = text.strip()
    if not stripped.startswith("event:"):
        return stripped
    for line in stripped.splitlines():
        if line.startswith("data:"):
            return line.removeprefix("data:").strip()
    return stripped


def bridge_error(request_id: object, code: int, message: str) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
    )


def _bridge_tool_call_name(payload: dict[str, Any]) -> str | None:
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    name = params.get("name")
    return name if isinstance(name, str) else None


def _bridge_tool_call_arguments(payload: dict[str, Any]) -> dict[str, Any]:
    params = payload.get("params")
    if not isinstance(params, dict):
        return {}
    arguments = params.get("arguments")
    return arguments if isinstance(arguments, dict) else {}


def _bridge_as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _bridge_tool_result(request_id: object, structured: dict[str, Any], *, is_error: bool) -> str:
    text = json.dumps(structured, default=str, sort_keys=True)
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "structuredContent": structured,
                "isError": is_error,
            },
        },
        default=str,
    )


def _bridge_call_error(
    request_id: object,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> str:
    return _bridge_tool_result(
        request_id,
        {"code": code, "message": message, "data": data or {}},
        is_error=True,
    )


def _bridge_make_tool_call_payload(
    request_id: object,
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
        default=str,
    )


def _bridge_structured_content(response_text: str) -> dict[str, Any] | None:
    try:
        envelope = json.loads(response_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(envelope, dict):
        return None
    result = envelope.get("result")
    if not isinstance(result, dict):
        return None
    structured = result.get("structuredContent")
    return structured if isinstance(structured, dict) else result


def _bridge_extract_project_id(response_text: str) -> int | None:
    structured = _bridge_structured_content(response_text)
    if structured is None:
        return None
    value = _bridge_as_int(structured.get("project_id"))
    if value is not None:
        return value
    data = structured.get("data")
    if isinstance(data, dict):
        value = _bridge_as_int(data.get("project_id"))
        if value is not None:
            return value
    binding = structured.get("binding")
    if isinstance(binding, dict):
        return _bridge_as_int(binding.get("project_id"))
    return None


def _bridge_replace_tool_call_arguments(
    payload: dict[str, Any],
    *,
    arguments: dict[str, Any],
) -> str:
    cloned = json.loads(json.dumps(payload, default=str))
    params = cloned.setdefault("params", {})
    if isinstance(params, dict):
        params["arguments"] = arguments
    return json.dumps(cloned, default=str)
