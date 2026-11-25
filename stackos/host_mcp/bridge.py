"""Shared StackOS MCP bridge command and token preflight helpers."""

from __future__ import annotations

import os
import shlex
import sys
import urllib.error
import urllib.request
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


def daemon_preflight(*, timeout: float = 0.5) -> str | None:
    """Return a readable error when the local daemon is not ready."""

    try:
        from stackos.config import get_settings

        settings = get_settings()
        request = urllib.request.Request(
            f"http://{settings.host}:{settings.port}/api/v1/health",
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if 200 <= response.status < 300:
                return None
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return f"StackOS is not reachable: {type(exc).__name__}"
    return "StackOS health check did not return a ready response."


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
    """Compare recognized bridge settings without changing launcher ownership.

    Omitted settings mean the bridge's canonical defaults, not the inspecting
    process's environment. Runtime, workspace and launcher identities stay exact.
    Unknown or malformed options only match by literal equality.
    """
    if list(actual) == list(expected):
        return True
    expected_context = _bridge_context(expected)
    return expected_context is not None and expected_context == _bridge_context(actual)


def _bridge_context(command: Sequence[str]) -> tuple[list[str], dict[str, str | None]] | None:
    from stackos.config import Settings, _default_data_dir, _default_state_dir

    if not is_stackos_bridge_command(command) or "mcp-bridge" not in command:
        return None
    bridge_index = command.index("mcp-bridge")
    context: dict[str, str | None] = {
        "--host": Settings.model_fields["host"].default,
        "--port": str(Settings.model_fields["port"].default),
        "--data-dir": str(_default_data_dir()),
        "--state-dir": str(_default_state_dir()),
        "--runtime": None,
        "--workspace-root": None,
    }
    seen: set[str] = set()
    arguments = iter(command[bridge_index + 1 :])
    for argument in arguments:
        option, separator, value = argument.partition("=")
        if option not in context or option in seen:
            return None
        seen.add(option)
        if not separator:
            value = next(arguments, "")
        if not value or value.startswith("--"):
            return None
        if option == "--port":
            try:
                value = str(int(value))
            except ValueError:
                return None
        context[option] = value
    return list(command[: bridge_index + 1]), context


def command_line_mentions(expected: Sequence[str], line: str) -> bool:
    try:
        tokens = shlex.split(line)
    except ValueError:
        tokens = line.split()
    wanted = list(expected)
    if not wanted:
        return False
    for index, token in enumerate(tokens):
        if token != wanted[0]:
            continue
        # Native list rows can append status columns. Never trim a trailing
        # option: doing so would hide a custom setting or malformed command.
        for end in range(len(tokens), index, -1):
            if any(part.startswith("--") for part in tokens[end:]):
                break
            if command_matches(wanted, tokens[index:end]):
                return True
    return False


def is_stackos_bridge_command(
    command: Sequence[str],
    *,
    runtime: str | None = None,
) -> bool:
    """Recognize a daemon-owned local StackOS stdio bridge command."""

    if not command:
        return False
    lowered = [str(part).lower() for part in command]
    if "mcp-bridge" not in lowered:
        return False
    command_text = " ".join(lowered)
    packaged = "stackos" in Path(lowered[0]).name or "stackos.app" in command_text
    module = any(
        lowered[index : index + 3] == ["-m", "stackos", "mcp-bridge"]
        for index in range(max(0, len(lowered) - 2))
    )
    if not packaged and not module:
        return False
    if runtime is None:
        return True
    try:
        runtime_index = lowered.index("--runtime")
    except ValueError:
        return False
    return runtime_index + 1 < len(lowered) and lowered[runtime_index + 1] == runtime.lower()


def output_row_matches_server(line: str, server_name: str = MCP_SERVER_NAME) -> bool:
    stripped = line.strip().lstrip("*-•✓✔ ")
    if not stripped:
        return False
    first = stripped.split(maxsplit=1)[0].rstrip(":")
    return first == server_name


def command_from_output_row(
    line: str,
    server_name: str = MCP_SERVER_NAME,
) -> list[str]:
    """Extract a best-effort command suffix from one host MCP list row."""

    try:
        tokens = shlex.split(line)
    except ValueError:
        tokens = line.split()
    for index, token in enumerate(tokens):
        if token.strip("*-•✓✔:") == server_name:
            return tokens[index + 1 :]
    return []


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
