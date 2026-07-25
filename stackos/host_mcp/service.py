"""One-brain lifecycle service for host MCP registrations."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Protocol

from stackos.host_mcp.adapters import claude_code, claude_desktop, codex, gemini_cli, hermes
from stackos.host_mcp.bridge import MCP_SERVER_NAME, default_home, token_preflight
from stackos.host_mcp.result import (
    HostMcpConnectionState,
    HostMcpResult,
    HostMcpSetupPolicy,
)

Action = Literal["inspect", "register", "repair", "remove"]
AdapterFn = Callable[[Path], HostMcpResult]
ForceAdapterFn = Callable[[Path, bool], HostMcpResult]
ProfileAdapterFn = Callable[[Path, str | None], HostMcpResult]


class HostMcpAdapter(Protocol):
    HOST_KEY: str

    def inspect(self, home: Path) -> HostMcpResult: ...

    def register(self, home: Path) -> HostMcpResult: ...

    def remove(self, home: Path) -> HostMcpResult: ...


@dataclass(frozen=True)
class HostMcpPolicy:
    host_key: str
    display_name: str
    setup_policy: HostMcpSetupPolicy


@dataclass(frozen=True)
class HostMcpDefinition:
    adapter: HostMcpAdapter
    policy: HostMcpPolicy
    repair: AdapterFn | None = None
    force_register: ForceAdapterFn | None = None
    inspect_profile: ProfileAdapterFn | None = None
    register_profile: ProfileAdapterFn | None = None
    remove_profile: ProfileAdapterFn | None = None


@dataclass(frozen=True)
class HostMcpAggregate:
    ok: bool
    results: list[HostMcpResult]

    @property
    def blocking_results(self) -> list[HostMcpResult]:
        return [result for result in self.results if result.blocking]

    def to_info(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "hosts": [result.to_info() for result in self.results],
            "blocking_hosts": [result.host_key for result in self.blocking_results],
        }

    def summary_lines(self) -> list[str]:
        return [
            f"{result.host_key}: {result.message}"
            + (f" Repair: {result.repair}" if result.repair and not result.ok else "")
            for result in self.results
        ]


def inspect_all(*, home: Path | None = None) -> HostMcpAggregate:
    home_dir = home or default_home()
    definitions = _definitions()
    with ThreadPoolExecutor(
        max_workers=len(definitions),
        thread_name_prefix="stackos-host-inspect",
    ) as executor:
        futures = [
            executor.submit(
                _call_adapter,
                definition.adapter.inspect,
                home_dir,
                definition.policy.host_key,
            )
            for definition in definitions
        ]
        results = [
            normalize_result(future.result(), definition.policy)
            for future, definition in zip(futures, definitions, strict=True)
        ]
    return _aggregate(results)


def repair_all(*, home: Path | None = None) -> HostMcpAggregate:
    home_dir = home or default_home()
    token_error = token_preflight(home_dir)
    if token_error:
        policy = HostMcpPolicy("all-hosts", "AI tool connections", "automatic")
        return _aggregate(
            [
                normalize_result(
                    HostMcpResult(
                        host_key="all-hosts",
                        surface="local-lifecycle",
                        status="token_missing",
                        message=token_error,
                        ok=False,
                        available=True,
                        blocking=True,
                        repair=(
                            "Run `stackos install` or desktop Repair before MCP host registration."
                        ),
                    ),
                    policy,
                )
            ]
        )
    results: list[HostMcpResult] = []
    for definition in _definitions():
        current = normalize_result(
            _call_adapter(
                definition.adapter.inspect,
                home_dir,
                definition.policy.host_key,
            ),
            definition.policy,
        )
        should_connect = (
            current.connection_state == "available"
            and definition.policy.setup_policy == "automatic"
        )
        should_repair = current.connection_state == "repair_needed" and current.repairable
        if should_connect or should_repair:
            lifecycle_action: Action = "register"
            if should_repair and definition.repair is not None:
                lifecycle_action = "repair"
            current = normalize_result(
                _invoke_adapter(definition, lifecycle_action, home_dir),
                definition.policy,
            )
        results.append(current)
    return _aggregate(results)


def remove_all(*, home: Path | None = None) -> HostMcpAggregate:
    home_dir = home or default_home()
    results = []
    for definition in _definitions():
        current = normalize_result(
            _call_adapter(
                definition.adapter.inspect,
                home_dir,
                definition.policy.host_key,
            ),
            definition.policy,
        )
        if current.selected and current.managed:
            current = normalize_result(
                _invoke_adapter(definition, "remove", home_dir),
                definition.policy,
            )
        results.append(current)
    return _aggregate(results)


def register_host(
    host_key: str,
    *,
    home: Path | None = None,
    force: bool = False,
    profile: str | None = None,
) -> HostMcpResult:
    return _single(host_key, action="register", home=home, force=force, profile=profile)


def remove_host(
    host_key: str,
    *,
    home: Path | None = None,
    profile: str | None = None,
) -> HostMcpResult:
    return _single(host_key, action="remove", home=home, profile=profile)


def inspect_host(
    host_key: str,
    *,
    home: Path | None = None,
    profile: str | None = None,
) -> HostMcpResult:
    return _single(host_key, action="inspect", home=home, profile=profile)


def _single(
    host_key: str,
    *,
    action: Action,
    home: Path | None,
    force: bool = False,
    profile: str | None = None,
) -> HostMcpResult:
    home_dir = home or default_home()
    definition = _definition_by_key(host_key)
    if definition is None:
        return normalize_result(
            HostMcpResult(
                host_key=host_key,
                surface="unknown",
                status="register_failed",
                message=f"Unknown MCP host {host_key!r}.",
                ok=False,
                available=False,
                blocking=True,
                repair=("Use one of: codex, claude-code, claude-desktop, gemini-cli, hermes."),
            ),
            HostMcpPolicy(host_key, host_key, "automatic"),
        )
    if profile is not None and definition.inspect_profile is None:
        return normalize_result(
            HostMcpResult(
                host_key=host_key,
                surface="host-lifecycle",
                status="register_failed",
                message=f"{definition.policy.display_name} does not support profile targeting.",
                ok=False,
                available=True,
                blocking=True,
                repair="Remove the profile selector and retry.",
            ),
            definition.policy,
        )
    if action == "inspect":
        return normalize_result(
            _invoke_adapter(definition, action, home_dir, profile=profile),
            definition.policy,
        )
    if action == "remove":
        current = normalize_result(
            _invoke_adapter(definition, "inspect", home_dir, profile=profile),
            definition.policy,
        )
        if current.selected and not current.managed:
            return current
        return normalize_result(
            _invoke_adapter(definition, action, home_dir, profile=profile),
            definition.policy,
        )
    return normalize_result(
        _invoke_adapter(
            definition,
            action,
            home_dir,
            force=force,
            profile=profile,
        ),
        definition.policy,
    )


def _definitions() -> tuple[HostMcpDefinition, ...]:
    return (
        HostMcpDefinition(
            codex,
            HostMcpPolicy("codex", "ChatGPT / Codex", "automatic"),
            force_register=lambda home, force: codex.register(home, force=force),
        ),
        HostMcpDefinition(
            claude_code,
            HostMcpPolicy("claude-code", "Claude Code", "automatic"),
        ),
        HostMcpDefinition(
            claude_desktop,
            HostMcpPolicy("claude-desktop", "Claude Desktop", "automatic"),
        ),
        HostMcpDefinition(
            gemini_cli,
            HostMcpPolicy("gemini-cli", "Gemini CLI", "automatic"),
        ),
        HostMcpDefinition(
            hermes,
            HostMcpPolicy("hermes", "Hermes", "explicit"),
            repair=hermes.repair,
            inspect_profile=lambda home, profile: hermes.inspect_profile(
                home,
                profile=profile,
            ),
            register_profile=lambda home, profile: hermes.register(
                home,
                profile=profile,
            ),
            remove_profile=lambda home, profile: hermes.remove(
                home,
                profile=profile,
            ),
        ),
    )


def _definition_by_key(host_key: str) -> HostMcpDefinition | None:
    for definition in _definitions():
        if definition.policy.host_key == host_key:
            return definition
    return None


def _call_adapter(
    fn: AdapterFn,
    home: Path,
    host_key: str | None = None,
) -> HostMcpResult:
    try:
        return fn(home)
    except Exception as exc:
        resolved_host_key = host_key or (
            getattr(fn, "__module__", "host").rsplit(".", maxsplit=1)[-1].replace("_", "-")
        )
        return HostMcpResult(
            host_key=resolved_host_key,
            surface="unknown",
            status="register_failed",
            message=f"Host MCP adapter failed: {type(exc).__name__}: {exc}",
            ok=False,
            available=True,
            blocking=True,
            repair=(
                "Run `stackos install --mcp-only` or inspect the "
                f"{MCP_SERVER_NAME} MCP host config."
            ),
        )


def _invoke_adapter(
    definition: HostMcpDefinition,
    action: Action,
    home: Path,
    *,
    force: bool = False,
    profile: str | None = None,
) -> HostMcpResult:
    adapter = definition.adapter
    if action == "repair":
        if definition.repair is None:
            raise ValueError(f"{definition.policy.host_key} has no bulk repair capability")
        return _call_adapter(
            definition.repair,
            home,
            definition.policy.host_key,
        )
    inspect_profile = definition.inspect_profile
    if action == "inspect" and profile is not None and inspect_profile is not None:
        return _call_adapter(
            lambda current_home: inspect_profile(current_home, profile),
            home,
            definition.policy.host_key,
        )
    register_profile = definition.register_profile
    if action == "register" and register_profile is not None:
        return _call_adapter(
            lambda current_home: register_profile(current_home, profile),
            home,
            definition.policy.host_key,
        )
    remove_profile = definition.remove_profile
    if action == "remove" and remove_profile is not None:
        return _call_adapter(
            lambda current_home: remove_profile(current_home, profile),
            home,
            definition.policy.host_key,
        )
    force_register = definition.force_register
    if action == "register" and force_register is not None:
        return _call_adapter(
            lambda current_home: force_register(current_home, force),
            home,
            definition.policy.host_key,
        )
    fn = {
        "inspect": adapter.inspect,
        "register": adapter.register,
        "remove": adapter.remove,
    }[action]
    return _call_adapter(fn, home, definition.policy.host_key)


def _aggregate(results: list[HostMcpResult]) -> HostMcpAggregate:
    return HostMcpAggregate(
        ok=not any(result.blocking for result in results),
        results=results,
    )


def normalize_result(result: HostMcpResult, policy: HostMcpPolicy) -> HostMcpResult:
    """Apply the shared host lifecycle policy to adapter-native facts."""

    configured_statuses = {
        "registered_current",
        "registered",
        "registered_stale",
        "registered_unsafe",
        "registered_unmanaged",
        "restart_required",
        "shadowed",
    }
    selected = (
        result.selected if result.selected is not None else result.status in configured_statuses
    )
    managed = (
        result.managed
        if result.managed is not None
        else result.status in {"registered_current", "registered", "restart_required"}
    )

    state: HostMcpConnectionState
    if result.status in {"registered_current", "registered"}:
        state = "connected"
    elif result.status in {"available_unregistered", "removed"}:
        state = "available"
    elif result.status == "absent":
        state = "unavailable"
    elif result.status == "restart_required":
        state = "restart_required"
    elif result.status == "unsupported_host_version":
        state = "update_required"
    elif result.status == "registered_stale" and managed:
        state = "repair_needed"
    elif result.status in {
        "registered_stale",
        "registered_unsafe",
        "registered_unmanaged",
        "shadowed",
        "config_unreadable",
    }:
        state = "review_required"
    else:
        state = "error"

    labels: dict[HostMcpConnectionState, str] = {
        "connected": "Connected",
        "available": "Available",
        "repair_needed": "Repair needed",
        "review_required": "Review required",
        "update_required": "Update needed",
        "restart_required": "Restart needed",
        "unavailable": "Not detected",
        "error": "Setup failed",
    }
    repairable = state == "repair_needed" and bool(selected and managed)
    blocking = state in {
        "repair_needed",
        "review_required",
        "update_required",
        "restart_required",
        "error",
    } and bool(selected or result.host_key == "all-hosts" or (state == "error" and result.blocking))
    ok = not blocking
    return replace(
        result,
        display_name=policy.display_name,
        connection_state=state,
        status_label=labels[state],
        selected=selected,
        managed=managed,
        repairable=repairable,
        setup_policy=policy.setup_policy,
        ok=ok,
        blocking=blocking,
        advisory=not blocking and state != "connected",
    )
