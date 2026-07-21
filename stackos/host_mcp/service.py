"""One-brain lifecycle service for host MCP registrations."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from stackos.host_mcp.adapters import claude_code, claude_desktop, codex, gemini_cli, hermes
from stackos.host_mcp.bridge import MCP_SERVER_NAME, default_home, token_preflight
from stackos.host_mcp.result import HostMcpResult

Action = Literal["inspect", "register", "remove"]
AdapterFn = Callable[[Path], HostMcpResult]


class HostMcpAdapter(Protocol):
    HOST_KEY: str

    def inspect(self, home: Path) -> HostMcpResult: ...

    def register(self, home: Path) -> HostMcpResult: ...

    def remove(self, home: Path) -> HostMcpResult: ...


@dataclass(frozen=True)
class HostMcpAggregate:
    ok: bool
    results: list[HostMcpResult]

    @property
    def blocking_results(self) -> list[HostMcpResult]:
        return [result for result in self.results if result.blocking or not result.ok]

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


def inspect_all(
    *,
    home: Path | None = None,
    host_keys: Sequence[str] | None = None,
) -> HostMcpAggregate:
    home_dir = home or default_home()
    adapters, invalid_host_keys = _selected_adapters(host_keys)
    if invalid_host_keys:
        return _invalid_host_aggregate(invalid_host_keys)
    if not adapters:
        return _aggregate([])
    with ThreadPoolExecutor(
        max_workers=len(adapters),
        thread_name_prefix="stackos-host-inspect",
    ) as executor:
        futures = [
            executor.submit(_call_adapter, adapter.inspect, home_dir) for adapter in adapters
        ]
        results = [future.result() for future in futures]
    return _aggregate(results)


def repair_all(
    *,
    home: Path | None = None,
    host_keys: Sequence[str] | None = None,
) -> HostMcpAggregate:
    home_dir = home or default_home()
    adapters, invalid_host_keys = _selected_adapters(host_keys)
    if invalid_host_keys:
        return _invalid_host_aggregate(invalid_host_keys)
    token_error = token_preflight(home_dir)
    if token_error:
        return _aggregate(
            [
                HostMcpResult(
                    host_key="all-hosts",
                    surface="local-lifecycle",
                    status="token_missing",
                    message=token_error,
                    ok=False,
                    available=True,
                    blocking=True,
                    repair="Run `stackos install` or desktop Repair before MCP host registration.",
                )
            ]
        )
    results = [_call_adapter(adapter.register, home_dir) for adapter in adapters]
    return _aggregate(results)


def remove_all(
    *,
    home: Path | None = None,
    host_keys: Sequence[str] | None = None,
) -> HostMcpAggregate:
    home_dir = home or default_home()
    adapters, invalid_host_keys = _selected_adapters(host_keys)
    if invalid_host_keys:
        return _invalid_host_aggregate(invalid_host_keys)
    results = [_call_adapter(adapter.remove, home_dir) for adapter in adapters]
    return _aggregate(results)


def register_host(
    host_key: str,
    *,
    home: Path | None = None,
    force: bool = False,
) -> HostMcpResult:
    return _single(host_key, action="register", home=home, force=force)


def remove_host(host_key: str, *, home: Path | None = None) -> HostMcpResult:
    return _single(host_key, action="remove", home=home)


def inspect_host(host_key: str, *, home: Path | None = None) -> HostMcpResult:
    return _single(host_key, action="inspect", home=home)


def _single(
    host_key: str,
    *,
    action: Action,
    home: Path | None,
    force: bool = False,
) -> HostMcpResult:
    home_dir = home or default_home()
    adapter = _adapter_by_key(host_key)
    if adapter is None:
        return _unknown_host_result(host_key)
    if action == "inspect":
        return _call_adapter(adapter.inspect, home_dir)
    if action == "remove":
        return _call_adapter(adapter.remove, home_dir)
    if action == "register" and host_key == "codex":
        return _call_adapter(
            lambda current_home: codex.register(current_home, force=force), home_dir
        )
    return _call_adapter(adapter.register, home_dir)


def _adapters() -> tuple[HostMcpAdapter, ...]:
    return (codex, hermes, claude_code, claude_desktop, gemini_cli)


def supported_host_keys() -> tuple[str, ...]:
    return tuple(adapter.HOST_KEY for adapter in _adapters())


def _selected_adapters(
    host_keys: Sequence[str] | None,
) -> tuple[tuple[HostMcpAdapter, ...], tuple[str, ...]]:
    adapters = _adapters()
    if host_keys is None:
        return adapters, ()
    requested = tuple(dict.fromkeys(host_keys))
    available = {adapter.HOST_KEY: adapter for adapter in adapters}
    invalid = tuple(host_key for host_key in requested if host_key not in available)
    return tuple(available[host_key] for host_key in requested if host_key in available), invalid


def _invalid_host_aggregate(host_keys: Sequence[str]) -> HostMcpAggregate:
    return _aggregate([_unknown_host_result(host_key) for host_key in host_keys])


def _unknown_host_result(host_key: str) -> HostMcpResult:
    return HostMcpResult(
        host_key=host_key,
        surface="unknown",
        status="unsupported_host_version",
        message=f"Unknown MCP host {host_key!r}.",
        ok=False,
        available=False,
        blocking=True,
        repair=f"Use one of: {', '.join(supported_host_keys())}.",
    )


def _adapter_by_key(host_key: str) -> HostMcpAdapter | None:
    for adapter in _adapters():
        if getattr(adapter, "HOST_KEY", None) == host_key:
            return adapter
    return None


def _call_adapter(fn: AdapterFn, home: Path) -> HostMcpResult:
    try:
        return fn(home)
    except Exception as exc:
        host_key = getattr(fn, "__module__", "host").rsplit(".", maxsplit=1)[-1].replace("_", "-")
        return HostMcpResult(
            host_key=host_key,
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


def _aggregate(results: list[HostMcpResult]) -> HostMcpAggregate:
    return HostMcpAggregate(
        ok=not any(result.blocking or not result.ok for result in results),
        results=results,
    )
