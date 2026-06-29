"""Shared StackOS MCP bridge command and token preflight helpers."""

from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path

MCP_SERVER_NAME = "stackos"
PACKAGED_CLI_ENV = "STACKOS_PACKAGED_CLI"
BRIDGE_COMMAND_ENV = "STACKOS_MCP_BRIDGE_COMMAND"
WORKSPACE_ROOT_ENV = "STACKOS_WORKSPACE_ROOT"


def default_home() -> Path:
    return Path(os.environ.get("STACKOS_HOME") or Path.home()).expanduser()


def token_path(home: Path | None = None) -> Path:
    if os.environ.get("STACKOS_STATE_DIR"):
        try:
            from stackos.config import get_settings

            return get_settings().token_path
        except Exception:
            pass
    return (home or default_home()) / ".local" / "state" / "stackos" / "auth.token"


def token_preflight(home: Path | None = None) -> str | None:
    path = token_path(home)
    if not path.is_file():
        return f"auth token missing at {path} — run `stackos install` or desktop Repair first."
    return None


def resolve_bridge_command(
    *,
    runtime: str | None = None,
    workspace_root: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Return the local stdio command host MCP clients should execute."""

    settings_args = _settings_args()
    workspace_root_arg = _workspace_root_args(workspace_root)
    packaged_cli = os.environ.get(PACKAGED_CLI_ENV)
    if packaged_cli and os.access(packaged_cli, os.X_OK):
        return _with_runtime(
            [packaged_cli, "mcp-bridge", *settings_args, *workspace_root_arg],
            runtime,
        )
    override = os.environ.get(BRIDGE_COMMAND_ENV)
    if override:
        return _with_runtime(
            [part for part in override.split(os.pathsep) if part] + workspace_root_arg,
            runtime,
        )
    return _with_runtime(
        [sys.executable, "-m", "stackos", "mcp-bridge", *settings_args, *workspace_root_arg],
        runtime,
    )


def command_matches(expected: Sequence[str], actual: Sequence[str]) -> bool:
    return list(actual) == list(expected)


def command_line_mentions(expected: Sequence[str], line: str) -> bool:
    try:
        tokens = shlex.split(line)
    except ValueError:
        tokens = line.split()
    wanted = list(expected)
    if not wanted:
        return False
    for index in range(0, len(tokens) - len(wanted) + 1):
        if tokens[index : index + len(wanted)] == wanted:
            return True
    return False


def output_row_matches_server(line: str, server_name: str = MCP_SERVER_NAME) -> bool:
    stripped = line.strip().lstrip("*-•✓✔ ")
    if not stripped:
        return False
    first = stripped.split(maxsplit=1)[0].rstrip(":")
    return first == server_name


def _with_runtime(command: list[str], runtime: str | None) -> list[str]:
    if not runtime:
        return command
    return [*command, "--runtime", runtime]


def _settings_args() -> list[str]:
    args: list[str] = []
    try:
        from stackos.config import get_settings

        settings = get_settings()
    except Exception:
        return args
    if os.environ.get("STACKOS_HOST"):
        args.extend(["--host", settings.host])
    if os.environ.get("STACKOS_PORT"):
        args.extend(["--port", str(settings.port)])
    if os.environ.get("STACKOS_DATA_DIR"):
        args.extend(["--data-dir", str(settings.data_dir)])
    if os.environ.get("STACKOS_STATE_DIR"):
        args.extend(["--state-dir", str(settings.state_dir)])
    return args


def _workspace_root_args(workspace_root: str | os.PathLike[str] | None) -> list[str]:
    root = workspace_root or os.environ.get(WORKSPACE_ROOT_ENV)
    if root is None:
        return []
    value = str(Path(root).expanduser())
    return ["--workspace-root", value] if value else []
