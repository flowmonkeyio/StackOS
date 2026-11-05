"""Shared host MCP registration result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
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
        return AgentRunResult(
            result.exit_code, thread_id=result.thread_id, error=f"Invalid {host} result: {exc}"
        )
    return result


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
