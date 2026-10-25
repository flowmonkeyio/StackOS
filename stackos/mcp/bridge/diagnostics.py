"""Bounded, payload-free failure receipts for the stdio bridge.

Do not add exception text, traceback paths, request IDs, tool arguments, URLs,
headers or response bodies here. Even exception class names may be supplied
by third-party code, so only closed classifications are emitted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
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
        "request_read",
        "response_write",
        "initialize",
        "workspace_scope",
        "project_validation",
        "run_context",
    }
)
_PROTOCOL_REASONS = frozenset(
    {"empty_response", "invalid_json", "invalid_envelope", "mismatched_id"}
)

_LIFECYCLE_PHASES = _PHASES | {"stdin_eof", "cleanup_reached"}


def _build_identity() -> dict[str, str | None]:
    """Package identity, not publication or source-revision attestation."""
    from stackos import __version__

    result: dict[str, str | None] = {
        "version": __version__,
        "build_id": None,
        "build_info_sha256": None,
    }
    try:
        path = Path(sys.prefix).parent / "build-info.json"
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return result
            raw = stream.read(4097)
        if len(raw) <= 4096:
            value = json.loads(raw).get("buildId")
            if isinstance(value, str) and re.fullmatch(r"\d{8}T\d{6}Z", value):
                result.update(build_id=value, build_info_sha256=hashlib.sha256(raw).hexdigest())
    except (OSError, ValueError, AttributeError, RecursionError):
        pass
    return result


class BridgeLifecycle:
    """Producer observations only. A finally block is not an OS exit receipt."""

    def __init__(self, attempt_id: str, started_at: float) -> None:
        self.attempt_id = attempt_id
        self.started_at = started_at
        self.log_path: Path | None = None
        self.sequence = 0
        self.build = _build_identity()

    def stage(self, phase: str) -> None:
        self.sequence += 1
        _persist_bridge_event(
            {
                "event": "stackos.mcp_bridge.lifecycle",
                "diagnostic_id": uuid4().hex,
                "attempt_id": self.attempt_id,
                "observed_at": datetime.now(UTC)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                "phase": phase if phase in _LIFECYCLE_PHASES else "unknown",
                "sequence": self.sequence,
                "bridge_elapsed_ms": max(0, round((time.monotonic() - self.started_at) * 1000)),
                "timing_origin": "cli_entry",
                "build": self.build,
                "persistence": {"status": "persisted", "log": "mcp-bridge.log"},
            },
            self.log_path,
        )


def bridge_diagnostic_snapshot(log_path: Path, attempt_id: str) -> dict[str, Any]:
    """Bounded sanitized export of one attempt; never probe or infer liveness."""
    if not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
        raise ValueError("bridge attempt must be 32 lowercase hexadecimal characters")
    events: list[dict[str, Any]] = []
    partial = False
    unavailable = False
    for path in [*(log_path.with_name(f"{log_path.name}.{n}") for n in range(5, 0, -1)), log_path]:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                    unavailable = True
                    continue
                if info.st_size > 1024 * 1024:
                    stream.seek(info.st_size - 1024 * 1024)
                    stream.readline(4097)
                    partial = True
                raw = stream.read(1024 * 1024)
        except FileNotFoundError:
            continue
        except OSError:
            unavailable = True
            continue
        for line in raw.splitlines():
            if len(line) > 4096:
                partial = True
                continue
            try:
                value = json.loads(line)
            except (ValueError, UnicodeDecodeError, RecursionError):
                partial = True
                continue
            if not isinstance(value, dict):
                partial = True
                continue
            if value.get("attempt_id") != attempt_id:
                continue
            kind = value.get("event")
            if not isinstance(kind, str) or kind not in {
                "stackos.mcp_bridge.lifecycle",
                "stackos.mcp_bridge.failure",
                "stackos.mcp_bridge.startup_completed",
            }:
                partial = True
                continue
            observed = value.get("observed_at")
            if not isinstance(observed, str) or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", observed
            ):
                partial = True
                continue
            try:
                datetime.fromisoformat(observed)
            except ValueError:
                partial = True
                continue
            # Reconstruct fields; never copy a log record or arbitrary nested object.
            event: dict[str, Any] = {"event": kind, "observed_at": observed}
            phase = value.get("phase")
            if isinstance(phase, str) and phase in _LIFECYCLE_PHASES:
                event["phase"] = phase
            for key in ("sequence", "bridge_elapsed_ms", "startup_elapsed_ms", "elapsed_ms"):
                item = value.get(key)
                if type(item) is int and 0 <= item <= 2**53:
                    event[key] = item
            original = value.get("original")
            if isinstance(original, dict):
                code = original.get("code")
                if isinstance(code, str) and code in {
                    "connect_timeout",
                    "connect_error",
                    "read_timeout",
                    "write_timeout",
                    "pool_timeout",
                    "http_status",
                    "transport_protocol_error",
                    "transport_error",
                    "file_not_found",
                    "permission_denied",
                    "connection_refused",
                    "connection_reset",
                    "broken_pipe",
                    "timeout",
                    "os_error",
                    "exception_group",
                    "invalid_value",
                    "unexpected_error",
                    "invalid_daemon_response",
                }:
                    event["failure_code"] = code
            build = value.get("build")
            if isinstance(build, dict):
                event["build"] = {}
                for key, pattern in (
                    ("version", r"\d+\.\d+\.\d+"),
                    ("build_id", r"\d{8}T\d{6}Z"),
                    ("build_info_sha256", r"[0-9a-f]{64}"),
                ):
                    item = build.get(key)
                    event["build"][key] = (
                        item if isinstance(item, str) and re.fullmatch(pattern, item) else None
                    )
            events.append(event)
            if len(events) > 40:
                events.pop(0)
                partial = True
    last = events[-1] if events else None
    phase = last.get("phase") if last else None
    awaited = {
        "request_read": "host_input",
        "initialize": "daemon_initialize_response",
        "request": "bridge_request_handling",
        "response_write": "stdout_write_and_flush",
        "stdin_eof": "producer_cleanup",
        "cleanup_reached": "host_exit_and_outcome_receipt",
    }.get(phase or "", "next_producer_observation")
    observed_at = last["observed_at"] if last else None
    elapsed = (
        (datetime.now(UTC) - datetime.fromisoformat(observed_at)).total_seconds()
        if observed_at
        else None
    )
    return {
        "attempt_id": attempt_id,
        "evidence_status": "partial"
        if partial or unavailable
        else "retained"
        if events
        else "not_found",
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "last_confirmed_stage": phase
        or (
            "initialize_response_flushed"
            if last and last["event"].endswith("startup_completed")
            else None
        ),
        "last_observed_at": observed_at,
        "elapsed_since_observation_ms": max(0, round(elapsed * 1000))
        if elapsed is not None
        else None,
        "clock_skew_detected": elapsed is not None and elapsed < 0,
        "awaiting_evidence": awaited,
        "execution_outcome": "unresolved",
        "process_termination": "unobserved",
        "host_request_id": None,
        "host_thread_id": None,
        "host_turn_id": None,
        "limits": [
            "retained_events_do_not_prove_liveness",
            "transport_is_not_execution_progress",
            "deadline_does_not_prove_termination",
            "reconcile_host_outcome_before_replacement",
        ],
        "events": events,
    }


def bridge_attempt_id(value: str | None = None) -> str:
    """Accept only an opaque UUID from the supervisor; never log arbitrary input."""
    if value and re.fullmatch(r"[0-9a-f]{32}", value):
        return value
    return uuid4().hex


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
    bridge_started_at: float | None = None,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    """Automatically persist a safe receipt, then mirror it to stderr."""
    event = {
        "event": "stackos.mcp_bridge.failure",
        "diagnostic_id": uuid4().hex,
        "attempt_id": bridge_attempt_id(attempt_id),
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
    if bridge_started_at is not None:
        event["bridge_elapsed_ms"] = max(0, round((time.monotonic() - bridge_started_at) * 1000))
        event["timing_origin"] = "cli_entry"
    return _persist_bridge_event(event, log_path)


def emit_bridge_startup_completed(
    *,
    bridge_started_at: float,
    initialize_started_at: float,
    response_write_started_at: float,
    log_path: Path | None = None,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    """Record the first successful initialize response flush, without payloads."""
    completed_at = time.monotonic()
    return _persist_bridge_event(
        {
            "event": "stackos.mcp_bridge.startup_completed",
            "diagnostic_id": uuid4().hex,
            "attempt_id": bridge_attempt_id(attempt_id),
            "observed_at": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "timing_origin": "cli_entry",
            "startup_elapsed_ms": max(0, round((completed_at - bridge_started_at) * 1000)),
            "initialize_elapsed_ms": max(
                0, round((response_write_started_at - initialize_started_at) * 1000)
            ),
            "response_write_elapsed_ms": max(
                0, round((completed_at - response_write_started_at) * 1000)
            ),
            "persistence": {"status": "persisted", "log": "mcp-bridge.log"},
        },
        log_path,
    )


def _persist_bridge_event(event: dict[str, Any], log_path: Path | None) -> dict[str, Any]:
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
                    "attempt_id": diagnostic["attempt_id"],
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
