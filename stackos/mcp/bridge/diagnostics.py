"""Bounded, payload-free failure receipts for the stdio bridge.

Do not add exception text, traceback paths, request IDs, tool arguments, URLs,
headers or response bodies here. Even exception class names may be supplied
by third-party code, so only closed classifications are emitted.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from stackos.config import get_state_dir
from stackos.logging import append_json_log

from .protocol import BridgeProtocolError

_PHASES = frozenset(
    {
        "configuration",
        "state_setup",
        "token_read",
        "workspace_hints",
        "client",
        "request",
        "initialize",
        "workspace_scope",
        "project_validation",
        "run_context",
    }
)
_PROTOCOL_REASONS = frozenset(
    {"empty_response", "invalid_json", "invalid_envelope", "mismatched_id"}
)


def _failure_code(exc: BaseException) -> str:
    if isinstance(exc, BridgeProtocolError):
        return "invalid_daemon_response"
    for kind, code in (
        (httpx.ConnectTimeout, "connect_timeout"),
        (httpx.ConnectError, "connect_error"),
        (httpx.ReadTimeout, "read_timeout"),
        (httpx.WriteTimeout, "write_timeout"),
        (httpx.PoolTimeout, "pool_timeout"),
        (httpx.HTTPStatusError, "http_status"),
        (httpx.ProtocolError, "transport_protocol_error"),
        (httpx.TransportError, "transport_error"),
        (FileNotFoundError, "file_not_found"),
        (PermissionError, "permission_denied"),
        (ConnectionRefusedError, "connection_refused"),
        (ConnectionResetError, "connection_reset"),
        (BrokenPipeError, "broken_pipe"),
        (TimeoutError, "timeout"),
        (OSError, "os_error"),
        (BaseExceptionGroup, "exception_group"),
        (ValueError, "invalid_value"),
    ):
        if isinstance(exc, kind):
            return code
    return "unexpected_error"


def bridge_failure_details(exc: BaseException) -> dict[str, Any]:
    """Keep bounded causal classifications, never raw exception attributes."""
    pending = [exc]
    seen: set[int] = set()
    causes: list[str] = []
    truncated = False
    while pending and len(seen) < 12:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        code = _failure_code(current)
        if code not in causes:
            causes.append(code)
        if isinstance(current, BaseExceptionGroup):
            # Never copy an unbounded group just to report its first causes.
            truncated = truncated or len(current.exceptions) > 12
            pending.extend(reversed(current.exceptions[:12]))
        cause = current.__cause__
        if cause is None and not current.__suppress_context__:
            cause = current.__context__
        if cause is not None:
            pending.append(cause)
    details: dict[str, Any] = {
        "code": _failure_code(exc),
        "cause_codes": causes,
        "causes_truncated": bool(pending) or truncated,
    }
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if isinstance(status, int) and 100 <= status <= 599:
            details["http_status"] = status
    if isinstance(exc, BridgeProtocolError) and exc.reason in _PROTOCOL_REASONS:
        details["reason"] = exc.reason
    return details


def emit_bridge_diagnostic(
    *,
    phase: str,
    started_at: float,
    original: BaseException,
    retry: BaseException | None = None,
    recovery: BaseException | None = None,
    autostart: str = "not_attempted",
    outcome: str = "failed",
    log_path: Path | None = None,
) -> dict[str, Any]:
    """Automatically persist a safe receipt, then mirror it to stderr."""
    event = {
        "event": "stackos.mcp_bridge.failure",
        "diagnostic_id": uuid4().hex,
        "observed_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "phase": phase if phase in _PHASES else "unknown",
        "elapsed_ms": max(0, round((time.monotonic() - started_at) * 1000)),
        "outcome": outcome if outcome in {"failed", "recovered"} else "failed",
        "autostart": autostart
        if autostart in {"not_attempted", "skipped", "failed", "succeeded", "error"}
        else "not_attempted",
        "original": bridge_failure_details(original),
        "retry": bridge_failure_details(retry) if retry is not None else None,
        "recovery_failure": bridge_failure_details(recovery) if recovery is not None else None,
        "persistence": {"status": "persisted", "log": "mcp-bridge.log"},
    }
    try:
        append_json_log(log_path or get_state_dir() / "mcp-bridge.log", event)
    except Exception as exc:
        event["persistence"] = {
            "status": "failed",
            "log": "mcp-bridge.log",
            "failure": bridge_failure_details(exc),
        }
    try:
        sys.stderr.write(json.dumps(event, sort_keys=True) + "\n")
        sys.stderr.flush()
    except Exception:
        # A closed diagnostic sink must not replace the primary protocol error.
        pass
    return event


def bridge_failure_response(request_id: object, diagnostic: dict[str, Any]) -> str:
    """Stable JSON-RPC failure with a correlation ref, not exception contents."""
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32000,
                "message": "Daemon request failed; inspect the matching bridge diagnostic.",
                "data": {
                    "diagnostic_id": diagnostic["diagnostic_id"],
                    "phase": diagnostic["phase"],
                    "failure": diagnostic["original"],
                    "retry_failure": diagnostic["retry"],
                    "recovery_failure": diagnostic["recovery_failure"],
                    "autostart": diagnostic["autostart"],
                    "persistence": diagnostic["persistence"],
                    "effect": "unknown",
                    "retry_automatically": False,
                },
            },
        }
    )
