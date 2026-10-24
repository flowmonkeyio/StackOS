"""JSON-RPC and MCP protocol helpers for the agent bridge."""

from __future__ import annotations

import base64
import json
import math
import re
from typing import Any

from stackos import __version__

_PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
_CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"
_CLIENT_CAPABILITIES_META_KEY = "io.modelcontextprotocol/clientCapabilities"
_SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"
_NAME_BEARING_METHODS = {
    "prompts/get": "name",
    "resources/read": "uri",
    "tools/call": "name",
}
_HEADER_SAFE = re.compile(r"^[\x20-\x7E]*$")
_BASE64_SENTINEL = re.compile(r"^=\?base64\?.*\?=$")
_PROTOCOL_FAILURE_REASONS = frozenset(
    {"empty_response", "invalid_json", "invalid_envelope", "mismatched_id"}
)


class BridgeProtocolError(ValueError):
    """A malformed daemon response, without exposing its body or identifiers."""

    def __init__(self, reason: str) -> None:
        self.reason = reason if reason in _PROTOCOL_FAILURE_REASONS else "invalid_envelope"
        super().__init__("Daemon returned an invalid JSON-RPC response.")


def _bridge_encode_header_value(value: str) -> str:
    """Encode an MCP routing value that cannot round-trip as plain ASCII."""
    if (
        _HEADER_SAFE.fullmatch(value)
        and value == value.strip()
        and not _BASE64_SENTINEL.fullmatch(value)
    ):
        return value
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return f"=?base64?{encoded}?="


def _bridge_mcp_request_headers(payload: object) -> dict[str, str]:
    """Derive protocol 2026-07-28 HTTP routing headers from one request."""
    if not isinstance(payload, dict):
        return {}
    method = payload.get("method")
    params = payload.get("params")
    if not isinstance(method, str) or not isinstance(params, dict):
        return {}
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return {}
    protocol_version = meta.get(_PROTOCOL_VERSION_META_KEY)
    if not isinstance(protocol_version, str) or not protocol_version:
        return {}
    headers = {
        "MCP-Protocol-Version": protocol_version,
        "Mcp-Method": method,
    }
    name_field = _NAME_BEARING_METHODS.get(method)
    name = params.get(name_field) if name_field is not None else None
    if isinstance(name, str):
        headers["Mcp-Name"] = _bridge_encode_header_value(name)
    return headers


def _bridge_mcp_request_meta(payload: object) -> dict[str, Any] | None:
    """Extract reusable modern client metadata from one self-contained request."""
    if not isinstance(payload, dict):
        return None
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return None
    protocol_version = meta.get(_PROTOCOL_VERSION_META_KEY)
    if not isinstance(protocol_version, str) or not protocol_version:
        return None
    reusable = {_PROTOCOL_VERSION_META_KEY: protocol_version}
    for key in (_CLIENT_INFO_META_KEY, _CLIENT_CAPABILITIES_META_KEY):
        if key in meta:
            reusable[key] = meta[key]
    return reusable


def _bridge_response_text(text: str) -> str:
    """Extract a JSON-RPC body from either JSON or single-event SSE text."""
    stripped = text.strip()
    if not stripped.startswith(("event:", "data:", "id:", "retry:", ":")):
        return stripped
    data: list[str] = []
    event_finished = False
    for line in stripped.splitlines():
        if not line and data:
            event_finished = True
        if line.startswith("data:"):
            if event_finished:
                # This adapter accepts one response, not an arbitrary event stream.
                raise BridgeProtocolError("invalid_envelope")
            data.append(line.removeprefix("data:").removeprefix(" "))
    return "\n".join(data).strip() if data else stripped


def _bridge_reject_json_constant(_value: str) -> None:
    raise BridgeProtocolError("invalid_json")


def _bridge_validate_response_text(text: str, *, request_payload: object) -> str:
    """Validate the response envelope before forwarding any daemon text to stdio."""
    is_notification = (
        isinstance(request_payload, dict)
        and request_payload.get("jsonrpc") == "2.0"
        and isinstance(request_payload.get("method"), str)
        and "id" not in request_payload
    )
    if not text:
        if is_notification:
            return text
        raise BridgeProtocolError("empty_response")
    if is_notification:
        raise BridgeProtocolError("invalid_envelope")
    try:
        envelope = json.loads(text, parse_constant=_bridge_reject_json_constant)
    except (ValueError, RecursionError):
        raise BridgeProtocolError("invalid_json") from None
    if (
        not isinstance(envelope, dict)
        or envelope.get("jsonrpc") != "2.0"
        or "id" not in envelope
        or ("result" in envelope) == ("error" in envelope)
        or "method" in envelope
    ):
        raise BridgeProtocolError("invalid_envelope")
    response_id = envelope["id"]
    if response_id is not None and (
        isinstance(response_id, bool)
        or not isinstance(response_id, (str, int, float))
        or (isinstance(response_id, float) and not math.isfinite(response_id))
    ):
        raise BridgeProtocolError("invalid_envelope")
    if isinstance(request_payload, dict) and "id" in request_payload:
        request_id = request_payload["id"]
        if isinstance(request_id, bool) or response_id != request_id:
            raise BridgeProtocolError("mismatched_id")
    if "error" in envelope:
        error = envelope["error"]
        if (
            not isinstance(error, dict)
            or not isinstance(error.get("code"), int)
            or isinstance(error.get("code"), bool)
            or not isinstance(error.get("message"), str)
        ):
            raise BridgeProtocolError("invalid_envelope")
    if "\n" in text or "\r" in text:
        # stdio uses one JSON-RPC message per line, including for multiline SSE data.
        try:
            return json.dumps(envelope, allow_nan=False)
        except (ValueError, RecursionError):
            raise BridgeProtocolError("invalid_json") from None
    return text


def _bridge_negotiated_protocol_version(response_text: str) -> str | None:
    """Read the protocol revision selected by a legacy initialize response."""
    try:
        envelope = json.loads(response_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(envelope, dict):
        return None
    result = envelope.get("result")
    if not isinstance(result, dict):
        return None
    value = result.get("protocolVersion")
    return value if isinstance(value, str) and value else None


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
                "resultType": "complete",
                "_meta": {
                    _SERVER_INFO_META_KEY: {
                        "name": "stackos-agent-bridge",
                        "version": __version__,
                    }
                },
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
