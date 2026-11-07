"""Shared host MCP registration result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal

from jsonschema import ValidationError, validate

AgentSandbox = Literal["read-only", "workspace-write", "normal"]


@dataclass(frozen=True)
class AgentRunResult:
    """One host process result; the caller interprets the agent's decision."""

    exit_code: int | None
    thread_id: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    usage: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.error is None and self.result is not None


def validate_agent_result(
    result: AgentRunResult, schema: dict[str, Any], host: str
) -> AgentRunResult:
    """All native adapters enforce the same caller-supplied result contract."""
    if result.error is not None or result.exit_code != 0:
        return result
    try:
        validate(result.result, schema)
    except ValidationError as exc:
        return replace(result, result=None, error=f"Invalid {host} result: {exc}")
    return result


def usage_snapshot(
    scope: Any,
    basis: Any,
    input_count: Any,
    output_count: Any,
    total: Any = None,
    *,
    cache_read: Any = None,
    cache_write: Any = None,
    reasoning: Any = None,
) -> dict[str, Any] | None:
    """Safe native counters, independent of the agent-authored result contract."""
    if not isinstance(basis, str) or basis not in {"invocation", "session"}:
        return None
    if not isinstance(scope, str) or not scope:
        return None
    if any(type(n) is not int or n < 0 for n in (input_count, output_count)):
        return None
    if total is None:
        total = input_count + output_count
    if type(total) is not int or total != input_count + output_count:
        return None
    # Details are subsets, independently available from the valid parent counts.
    details = {"cache_read": cache_read, "cache_write": cache_write, "reasoning": reasoning}
    for key, count in details.items():
        parent = output_count if key == "reasoning" else input_count
        if type(count) is not int or not 0 <= count <= parent:
            details[key] = None
    if (
        details["cache_read"] is not None
        and details["cache_write"] is not None
        and details["cache_read"] + details["cache_write"] > input_count
    ):
        details["cache_read"] = details["cache_write"] = None
    return {
        "unit": "tokens",
        "scope": scope,
        "basis": basis,
        "snapshot": {"input": input_count, "output": output_count, "total": total, **details},
    }


def normalize_usage(
    usage: Any, *, session_id: str | None, resumed: bool, previous: Any = None
) -> dict[str, Any]:
    """Measure one invocation; cumulative counters require a compatible baseline.

    Only measured is additive. A missing legacy baseline still retains a usable
    snapshot for the next same-session delta. Never bridge an unobserved call.
    """
    unavailable = {"status": "unavailable", "reason": "missing_or_unsupported", "measured": None}
    if not isinstance(usage, dict):
        return unavailable
    snapshot = usage.get("snapshot")
    if not isinstance(snapshot, dict) or type(snapshot.get("total")) is not int:
        return {**unavailable, "reason": "malformed"}
    safe = usage_snapshot(
        usage.get("scope"),
        usage.get("basis"),
        snapshot.get("input"),
        snapshot.get("output"),
        snapshot.get("total"),
        cache_read=snapshot.get("cache_read"),
        cache_write=snapshot.get("cache_write"),
        reasoning=snapshot.get("reasoning"),
    )
    if safe is None or usage.get("unit") != "tokens":
        return {**unavailable, "reason": "malformed"}
    measured = safe["snapshot"].copy()
    reason = None
    if safe["basis"] == "session":
        if not isinstance(session_id, str) or not session_id.strip():
            reason = "missing_session"
        elif resumed:
            if not isinstance(previous, dict) or any(
                previous.get(key) != value
                for key, value in {
                    **{k: safe[k] for k in ("unit", "basis", "scope")},
                    "session_id": session_id,
                }.items()
            ):
                reason = "missing_baseline"
            else:
                baseline = previous.get("snapshot")
                if not isinstance(baseline, dict) or any(
                    type(baseline.get(k)) is not int or baseline[k] < 0
                    for k in ("input", "output", "total")
                ):
                    reason = "missing_baseline"
                else:
                    prior = usage_snapshot(
                        safe["scope"],
                        safe["basis"],
                        baseline["input"],
                        baseline["output"],
                        baseline["total"],
                        cache_read=baseline.get("cache_read"),
                        cache_write=baseline.get("cache_write"),
                        reasoning=baseline.get("reasoning"),
                    )
                    if prior is None:
                        reason = "invalid_delta"
                    else:
                        counts = {
                            k: n - prior["snapshot"][k]
                            if n is not None and prior["snapshot"][k] is not None
                            else None
                            for k, n in measured.items()
                        }
                        delta = usage_snapshot(
                            safe["scope"],
                            safe["basis"],
                            counts["input"],
                            counts["output"],
                            counts["total"],
                            cache_read=counts["cache_read"],
                            cache_write=counts["cache_write"],
                            reasoning=counts["reasoning"],
                        )
                        if delta is None:
                            reason = "invalid_delta"
                        else:
                            measured = delta["snapshot"]
    return {
        **safe,
        "session_id": session_id,
        "status": "unavailable" if reason else "measured",
        "reason": reason,
        "measured": None if reason else measured,
    }


@dataclass(frozen=True)
class AgentCapabilities:
    """Native execution facts attached to the existing host registry."""

    sandboxes: tuple[AgentSandbox, ...] = ()
    review_sandbox: AgentSandbox | None = None
    models: tuple[str, ...] = ()
    custom_models: bool = False
    provider_selection: bool = False
    profile_selection: bool = False
    requires_provider_model: bool = False
    auth_status_command: tuple[str, ...] = ()
    notes: str = "Headless agent execution is unsupported."
    sources: tuple[str, ...] = ()
    checked_on: str = "2026-11-05"


HostMcpStatus = Literal[
    "absent",
    "available_unregistered",
    "registered_current",
    "registered",
    "registered_stale",
    "registered_unsafe",
    "registered_unmanaged",
    "shadowed",
    "unsupported_host_version",
    "config_unreadable",
    "register_failed",
    "remove_failed",
    "removed",
    "restart_required",
    "token_missing",
]

HostMcpConnectionState = Literal[
    "connected",
    "available",
    "repair_needed",
    "review_required",
    "update_required",
    "restart_required",
    "unavailable",
    "error",
]

HostMcpSetupPolicy = Literal["automatic", "explicit"]


@dataclass(frozen=True)
class HostMcpResult:
    host_key: str
    surface: str
    status: HostMcpStatus
    message: str
    ok: bool
    available: bool
    advisory: bool = False
    blocking: bool = False
    needs_restart: bool = False
    command: list[str] = field(default_factory=list)
    config_path: str | None = None
    repair: str | None = None
    warnings: list[str] = field(default_factory=list)
    display_name: str | None = None
    connection_state: HostMcpConnectionState | None = None
    status_label: str | None = None
    selected: bool | None = None
    managed: bool | None = None
    repairable: bool = False
    setup_policy: HostMcpSetupPolicy | None = None
    target: dict[str, str] | None = None
    targets: list[dict[str, str]] = field(default_factory=list)
    agent_execution: dict[str, Any] = field(default_factory=dict)

    def to_info(self) -> dict[str, object]:
        return {
            "host_key": self.host_key,
            "agent_execution": self.agent_execution,
            "surface": self.surface,
            "status": self.status,
            "message": self.message,
            "ok": self.ok,
            "available": self.available,
            "advisory": self.advisory,
            "blocking": self.blocking,
            "needs_restart": self.needs_restart,
            "command": redact_command(self.command),
            "config_path": self.config_path,
            "repair": self.repair,
            "warnings": self.warnings,
            "display_name": self.display_name or self.host_key,
            "connection_state": self.connection_state,
            "status_label": self.status_label,
            "selected": bool(self.selected),
            "managed": bool(self.managed),
            "repairable": self.repairable,
            "setup_policy": self.setup_policy,
            "target": self.target,
            "targets": self.targets,
        }


def looks_secretish(value: object) -> bool:
    lowered = str(value).lower()
    return any(
        token in lowered
        for token in (
            "token",
            "secret",
            "password",
            "authorization",
            "bearer ",
            "api_key",
            "apikey",
            "access_key",
        )
    )


def redact_command(command: list[str]) -> list[str]:
    if looks_secretish(command):
        return ["<redacted: secret-like MCP command>"]
    return command
