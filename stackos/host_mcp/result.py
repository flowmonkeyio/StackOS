"""Shared host MCP registration result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

HostMcpStatus = Literal[
    "absent",
    "available_unregistered",
    "registered_current",
    "registered",
    "registered_stale",
    "registered_unsafe",
    "shadowed",
    "unsupported_host_version",
    "config_unreadable",
    "register_failed",
    "remove_failed",
    "removed",
    "restart_required",
    "token_missing",
]


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

    def to_info(self) -> dict[str, object]:
        return {
            "host_key": self.host_key,
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
