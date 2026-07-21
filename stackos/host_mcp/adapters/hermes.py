"""Hermes MCP lifecycle adapter."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from stackos.cli.daemon_processes import _daemon_health_ok
from stackos.config import get_settings
from stackos.host_mcp.bridge import (
    MCP_SERVER_NAME,
    resolve_bridge_command,
    token_preflight,
)
from stackos.host_mcp.discovery import resolve_cli_bin, subprocess_env_for_cli
from stackos.host_mcp.result import HostMcpResult, looks_secretish

HOST_KEY = "hermes"
SURFACE = "cli-config"
HERMES_BIN_ENV = "STACKOS_HERMES_BIN"
COMMON_HERMES_CLI_CANDIDATES = (
    "~/.local/bin/hermes",
    "~/bin/hermes",
    "/opt/homebrew/bin/hermes",
    "/usr/local/bin/hermes",
)
LOCAL_BRIDGE_FIELDS = frozenset({"command", "args", "enabled", "tools"})
UNSAFE_TRANSPORT_FIELDS = frozenset({"url", "headers", "auth", "env"})


def inspect(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    del home
    hermes_bin = resolve_hermes_bin()
    if hermes_bin is None:
        return _absent()
    command = resolve_bridge_command(runtime=HOST_KEY)
    return _inspect_registered_bridge(hermes_bin, command, server_name)


def register(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    hermes_bin = resolve_hermes_bin()
    if hermes_bin is None:
        return _absent()
    token_error = token_preflight(home)
    if token_error:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="token_missing",
            message=token_error,
            ok=False,
            available=True,
            blocking=True,
            repair="Run `stackos install` or desktop Repair before registering Hermes MCP.",
        )
    if not _daemon_is_ready():
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="register_failed",
            message="StackOS must be running before Hermes can safely discover MCP tools.",
            ok=False,
            available=True,
            blocking=True,
            repair="Start StackOS, then rerun desktop Repair.",
        )

    command = resolve_bridge_command(runtime=HOST_KEY)
    current = _inspect_registered_bridge(hermes_bin, command, server_name)
    if current.ok:
        return current
    if current.status in {"registered_unsafe", "config_unreadable"}:
        return current
    if current.status == "registered_stale" and not _remove_configured_server(
        hermes_bin,
        server_name,
    ):
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="remove_failed",
            message="Hermes MCP registration failed while removing the stale entry.",
            ok=False,
            available=True,
            blocking=True,
            repair=f"Run `hermes mcp remove {server_name}`, then rerun StackOS Repair.",
        )

    added = _run_hermes(
        hermes_bin,
        ["mcp", "add", server_name, "--command", command[0], "--args", *command[1:]],
        input_text="y\n",
    )
    if added.returncode == 0:
        registered = _inspect_registered_bridge(hermes_bin, command, server_name)
        if registered.ok:
            return HostMcpResult(
                host_key=HOST_KEY,
                surface=SURFACE,
                status="registered",
                message=f"Registered MCP '{server_name}' with Hermes.",
                ok=True,
                available=True,
                config_path=registered.config_path,
                command=command,
            )
        _remove_failed_local_registration(hermes_bin, command, server_name)
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="register_failed",
        message="Hermes did not save an enabled local StackOS MCP bridge.",
        ok=False,
        available=True,
        blocking=True,
        repair="Check the local StackOS service, then rerun desktop Repair.",
    )


def remove(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    del home
    hermes_bin = resolve_hermes_bin()
    if hermes_bin is None:
        return _absent(message="Hermes was not detected; skipped StackOS MCP removal.")
    entry, config_path, error = _read_configured_server(hermes_bin, server_name)
    if error:
        return _config_error(config_path)
    if entry is None:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="removed",
            message=f"MCP '{server_name}' not registered with Hermes; nothing to remove.",
            ok=True,
            available=True,
        )
    if not _remove_configured_server(hermes_bin, server_name):
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="remove_failed",
            message="Hermes MCP removal failed.",
            ok=False,
            available=True,
            blocking=True,
            repair=f"Run `hermes mcp remove {server_name}` and retry uninstall.",
        )
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="removed",
        message=f"Unregistered MCP '{server_name}' from Hermes.",
        ok=True,
        available=True,
    )


def resolve_hermes_bin(hermes_bin: str | None = None) -> str | None:
    return resolve_cli_bin(
        "hermes",
        explicit=hermes_bin,
        env_var=HERMES_BIN_ENV,
        common_candidates=COMMON_HERMES_CLI_CANDIDATES,
    )


def _daemon_is_ready() -> bool:
    settings = get_settings()
    return _daemon_health_ok(settings.host, settings.port, timeout=0.5)


def _run_hermes(
    hermes_bin: str,
    args: Sequence[str],
    *,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [hermes_bin, *args],
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env=subprocess_env_for_cli(hermes_bin),
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            [hermes_bin, *args],
            returncode=124,
            stdout=_text(exc.stdout),
            stderr=_text(exc.stderr) or "Hermes CLI timed out.",
        )
    except Exception as exc:
        return subprocess.CompletedProcess(
            [hermes_bin, *args],
            returncode=1,
            stdout="",
            stderr=f"{type(exc).__name__}: {exc}",
        )


def _inspect_registered_bridge(
    hermes_bin: str,
    command: Sequence[str],
    server_name: str,
) -> HostMcpResult:
    entry, config_path, error = _read_configured_server(hermes_bin, server_name)
    if error:
        return _config_error(config_path)
    if entry is None:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="available_unregistered",
            message="StackOS is not registered with Hermes.",
            ok=False,
            available=True,
            blocking=True,
            config_path=_path_text(config_path),
            repair="Start StackOS, then rerun desktop Repair.",
        )
    if looks_secretish(entry) or _has_unsafe_transport(entry):
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_unsafe",
            message="Hermes has a StackOS MCP entry that is not a safe local stdio bridge.",
            ok=False,
            available=True,
            blocking=True,
            config_path=_path_text(config_path),
            repair=(
                f"Remove the Hermes StackOS MCP entry with `hermes mcp remove {server_name}`, "
                "then rerun StackOS Repair."
            ),
            warnings=["unsafe Hermes MCP entry redacted"],
        )
    if _bridge_config_matches(entry, command):
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_current",
            message="Hermes StackOS MCP registration is healthy.",
            ok=True,
            available=True,
            config_path=_path_text(config_path),
            command=list(command),
        )
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="registered_stale",
        message="Hermes has a StackOS MCP entry, but it is not the expected enabled local bridge.",
        ok=False,
        available=True,
        blocking=True,
        config_path=_path_text(config_path),
        repair="Start StackOS, then rerun desktop Repair.",
        warnings=["stale Hermes MCP entry redacted"],
    )


def _read_configured_server(
    hermes_bin: str,
    server_name: str,
) -> tuple[dict[str, Any] | None, Path | None, str | None]:
    config_path = _hermes_config_path(hermes_bin)
    if config_path is None:
        return None, None, "path"
    try:
        raw = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, config_path, None
    except OSError:
        return None, config_path, "read"
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None, config_path, "parse"
    if loaded is None:
        return None, config_path, None
    if not isinstance(loaded, dict):
        return None, config_path, "root"
    servers = loaded.get("mcp_servers")
    if servers is None:
        return None, config_path, None
    if not isinstance(servers, dict):
        return None, config_path, "servers"
    entry = servers.get(server_name)
    if entry is None:
        return None, config_path, None
    if not isinstance(entry, dict):
        return None, config_path, "entry"
    return entry, config_path, None


def _hermes_config_path(hermes_bin: str) -> Path | None:
    result = _run_hermes(hermes_bin, ["config", "path"])
    if result.returncode != 0:
        return None
    paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(paths) != 1:
        return None
    return Path(paths[0]).expanduser()


def _bridge_config_matches(
    entry: dict[str, Any],
    command: Sequence[str],
    *,
    require_enabled: bool = True,
) -> bool:
    if not command or not set(entry).issubset(LOCAL_BRIDGE_FIELDS):
        return False
    args = entry.get("args")
    enabled = entry.get("enabled")
    if not isinstance(args, list) or not all(isinstance(value, str) for value in args):
        return False
    if not isinstance(enabled, bool):
        return False
    if entry.get("command") != command[0] or args != list(command[1:]):
        return False
    return enabled is True if require_enabled else True


def _has_unsafe_transport(entry: dict[str, Any]) -> bool:
    return not UNSAFE_TRANSPORT_FIELDS.isdisjoint(entry)


def _remove_configured_server(hermes_bin: str, server_name: str) -> bool:
    removed = _run_hermes(hermes_bin, ["mcp", "remove", server_name], input_text="y\n")
    if removed.returncode != 0:
        return False
    entry, _config_path, error = _read_configured_server(hermes_bin, server_name)
    return error is None and entry is None


def _remove_failed_local_registration(
    hermes_bin: str,
    command: Sequence[str],
    server_name: str,
) -> None:
    entry, _config_path, error = _read_configured_server(hermes_bin, server_name)
    if (
        error is None
        and entry is not None
        and _bridge_config_matches(
            entry,
            command,
            require_enabled=False,
        )
    ):
        _remove_configured_server(hermes_bin, server_name)


def _config_error(config_path: Path | None) -> HostMcpResult:
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="config_unreadable",
        message="Hermes MCP configuration could not be safely inspected.",
        ok=False,
        available=True,
        blocking=True,
        config_path=_path_text(config_path),
        repair="Check `hermes config path`, fix the local config, then rerun StackOS Repair.",
    )


def _path_text(path: Path | None) -> str | None:
    return str(path) if path is not None else None


def _absent(
    message: str = "Hermes was not detected; skipped StackOS MCP registration.",
) -> HostMcpResult:
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="absent",
        message=message,
        ok=True,
        available=False,
        advisory=True,
        repair="Install Hermes, then rerun StackOS Repair.",
    )


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)
