"""Claude Code MCP registration helpers.

Claude Code owns its MCP registry. StackOS should use the `claude mcp`
management surface when it is available, and treat old JSON files as legacy
diagnostics/cleanup only.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

MCP_SERVER_NAME = "stackos"
DEFAULT_SCOPE = "user"
CLAUDE_BIN_ENV = "STACKOS_CLAUDE_BIN"
COMMON_CLAUDE_CLI_CANDIDATES = (
    "~/.local/bin/claude",
    "~/bin/claude",
    "~/.npm-global/bin/claude",
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
)
MACOS_CLAUDE_APP_BUNDLE_CANDIDATES = (
    "/Applications/cmux.app/Contents/Resources/bin/claude",
    "/Applications/Claude.app/Contents/Resources/bin/claude",
    "/Applications/Claude Code.app/Contents/Resources/bin/claude",
)
SUPPORTED_LOGIN_SHELLS = {"bash", "sh", "zsh"}

ClaudeMcpStatus = Literal[
    "healthy",
    "registered",
    "removed",
    "claude_absent",
    "unsupported_cli",
    "missing",
    "stale",
    "registration_failed",
    "token_missing",
]


@dataclass(frozen=True)
class ClaudeMcpResult:
    """Safe, operator-readable Claude MCP state."""

    ok: bool
    status: ClaudeMcpStatus
    message: str
    claude_bin: str | None = None
    scope: str | None = None
    transport: str | None = None
    command: list[str] = field(default_factory=list)
    returncode: int | None = None
    repair: str | None = None
    legacy_json_present: bool = False
    legacy_json_cleaned: bool = False
    legacy_json_error: str | None = None

    def to_info(self) -> dict[str, object]:
        command: list[str] = self.command
        if _looks_secretish(command):
            command = ["<redacted: secret-like MCP command>"]
        return {
            "available": self.claude_bin is not None,
            "status": self.status,
            "message": self.message,
            "claude_bin": self.claude_bin,
            "scope": self.scope,
            "transport": self.transport,
            "command": command,
            "returncode": self.returncode,
            "repair": self.repair,
            "legacy_json_present": self.legacy_json_present,
            "legacy_json_cleaned": self.legacy_json_cleaned,
            "legacy_json_error": self.legacy_json_error,
        }


def resolve_bridge_command(*, runtime: str | None = None) -> list[str]:
    """Return the command Claude should execute for StackOS MCP stdio.

    Packaged desktop wrappers export `STACKOS_PACKAGED_CLI`, which points at
    the stable `resources/stackos/bin/stackos` launcher. Source/dev and pipx
    installs can safely use the active Python environment.
    """

    from stackos.host_mcp.bridge import resolve_bridge_command as _resolve_bridge_command

    return _resolve_bridge_command(runtime=runtime)


def resolve_claude_bin(claude_bin: str | None = None) -> str | None:
    """Return a Claude CLI executable visible from terminal or macOS GUI apps."""

    from stackos.host_mcp.discovery import resolve_cli_bin

    return resolve_cli_bin(
        "claude",
        explicit=claude_bin,
        env_var=CLAUDE_BIN_ENV,
        common_candidates=_expand_candidate_paths(COMMON_CLAUDE_CLI_CANDIDATES),
        app_bundle_candidates=(
            MACOS_CLAUDE_APP_BUNDLE_CANDIDATES if sys.platform == "darwin" else ()
        ),
    )


def inspect(
    *,
    home: Path | None = None,
    expected_command: Sequence[str] | None = None,
    server_name: str = MCP_SERVER_NAME,
    claude_bin: str | None = None,
    timeout_seconds: int = 15,
) -> ClaudeMcpResult:
    """Inspect the Claude-visible StackOS MCP registration."""

    home_dir = home or _default_home()
    legacy = _legacy_state(home_dir, server_name)
    resolved_claude = resolve_claude_bin(claude_bin)
    if resolved_claude is None:
        return ClaudeMcpResult(
            ok=True,
            status="claude_absent",
            message="Claude Code CLI not found; skipping Claude MCP registration.",
            repair="Install Claude Code, then run `stackos install --mcp-only` or desktop Repair.",
            legacy_json_present=legacy.present,
            legacy_json_error=legacy.error,
        )

    result = _run_claude(
        resolved_claude,
        ["mcp", "get", server_name],
        timeout_seconds=timeout_seconds,
    )
    if result.returncode != 0:
        status: ClaudeMcpStatus = (
            "missing" if "No MCP server named" in result.stderr else "unsupported_cli"
        )
        return ClaudeMcpResult(
            ok=False,
            status=status,
            message=(
                "StackOS is not registered with Claude Code."
                if status == "missing"
                else "Claude Code MCP status could not be inspected."
            ),
            claude_bin=resolved_claude,
            returncode=result.returncode,
            repair="Run `stackos install --mcp-only` or desktop Repair.",
            legacy_json_present=legacy.present,
            legacy_json_error=legacy.error,
        )

    parsed = _parse_claude_get(result.stdout)
    command = parsed.command
    expected = list(expected_command or resolve_bridge_command())
    stale_reasons: list[str] = []
    if parsed.scope != DEFAULT_SCOPE:
        stale_reasons.append(f"scope is {parsed.scope!r}, expected {DEFAULT_SCOPE!r}")
    if parsed.transport != "stdio":
        stale_reasons.append(f"transport is {parsed.transport!r}, expected 'stdio'")
    if command != expected:
        stale_reasons.append("command does not match the expected StackOS mcp-bridge command")
    if _looks_secretish(command):
        stale_reasons.append("command appears to contain a token or auth header")

    if stale_reasons:
        repair = "Run `stackos install --mcp-only` or desktop Repair."
        if parsed.scope and parsed.scope != DEFAULT_SCOPE:
            repair = (
                f"Remove the shadowing Claude {parsed.scope} scope entry with "
                f"`claude mcp remove {server_name} --scope {parsed.scope}`, "
                "then run `stackos install --mcp-only` or desktop Repair."
            )
        return ClaudeMcpResult(
            ok=False,
            status="stale",
            message="Claude Code has a StackOS MCP entry, but it is stale: "
            + "; ".join(stale_reasons),
            claude_bin=resolved_claude,
            scope=parsed.scope,
            transport=parsed.transport,
            command=command,
            returncode=result.returncode,
            repair=repair,
            legacy_json_present=legacy.present,
            legacy_json_error=legacy.error,
        )

    return ClaudeMcpResult(
        ok=True,
        status="healthy",
        message="Claude Code StackOS MCP registration is healthy.",
        claude_bin=resolved_claude,
        scope=parsed.scope,
        transport=parsed.transport,
        command=command,
        returncode=result.returncode,
        legacy_json_present=legacy.present,
        legacy_json_error=legacy.error,
    )


def register(
    *,
    home: Path | None = None,
    bridge_command: Sequence[str] | None = None,
    server_name: str = MCP_SERVER_NAME,
    claude_bin: str | None = None,
    timeout_seconds: int = 20,
) -> ClaudeMcpResult:
    """Register StackOS with Claude Code through the Claude CLI."""

    home_dir = home or _default_home()
    legacy = _legacy_state(home_dir, server_name)
    token_error = _token_preflight(home_dir)
    if token_error is not None:
        return ClaudeMcpResult(
            ok=False,
            status="token_missing",
            message=token_error,
            repair="Run `stackos install` or desktop Repair before registering Claude MCP.",
            legacy_json_present=legacy.present,
            legacy_json_error=legacy.error,
        )

    resolved_claude = resolve_claude_bin(claude_bin)
    if resolved_claude is None:
        return ClaudeMcpResult(
            ok=True,
            status="claude_absent",
            message=(
                "Claude Code CLI not found; skipped Claude MCP registration. "
                "Install Claude Code, then run `stackos install --mcp-only` or desktop Repair."
            ),
            repair="Install Claude Code, then run `stackos install --mcp-only` or desktop Repair.",
            legacy_json_present=legacy.present,
            legacy_json_error=legacy.error,
        )

    command = list(bridge_command or resolve_bridge_command())
    removed = _run_claude(
        resolved_claude,
        ["mcp", "remove", server_name, "--scope", DEFAULT_SCOPE],
        timeout_seconds=timeout_seconds,
    )
    if removed.returncode != 0 and "No MCP server named" not in removed.stderr:
        return ClaudeMcpResult(
            ok=False,
            status="registration_failed",
            message="Claude Code MCP registration failed while removing the stale user entry.",
            claude_bin=resolved_claude,
            returncode=removed.returncode,
            repair=(
                "Check `claude mcp remove stackos --scope user`, "
                "then rerun `stackos install --mcp-only`."
            ),
            legacy_json_present=legacy.present,
            legacy_json_error=legacy.error,
        )
    result = _run_claude(
        resolved_claude,
        [
            "mcp",
            "add",
            "--scope",
            DEFAULT_SCOPE,
            "--transport",
            "stdio",
            server_name,
            "--",
            *command,
        ],
        timeout_seconds=timeout_seconds,
    )
    if result.returncode != 0:
        return ClaudeMcpResult(
            ok=False,
            status="registration_failed",
            message="Claude Code MCP registration failed.",
            claude_bin=resolved_claude,
            returncode=result.returncode,
            repair="Check `claude mcp add --help`, then rerun `stackos install --mcp-only`.",
            legacy_json_present=legacy.present,
            legacy_json_error=legacy.error,
        )

    inspected = inspect(
        home=home_dir,
        expected_command=command,
        server_name=server_name,
        claude_bin=resolved_claude,
        timeout_seconds=timeout_seconds,
    )
    cleaned = _cleanup_legacy_json(home_dir, server_name) if inspected.ok else _LegacyCleanup()
    if not inspected.ok:
        return inspected
    return ClaudeMcpResult(
        ok=True,
        status="registered",
        message=f"Registered MCP '{server_name}' with Claude Code using {DEFAULT_SCOPE} scope.",
        claude_bin=resolved_claude,
        scope=inspected.scope,
        transport=inspected.transport,
        command=inspected.command,
        returncode=result.returncode,
        legacy_json_present=legacy.present,
        legacy_json_cleaned=cleaned.cleaned,
        legacy_json_error=legacy.error or cleaned.error,
    )


def remove(
    *,
    home: Path | None = None,
    server_name: str = MCP_SERVER_NAME,
    claude_bin: str | None = None,
    timeout_seconds: int = 20,
) -> ClaudeMcpResult:
    """Remove the StackOS user-scope Claude MCP registration."""

    home_dir = home or _default_home()
    resolved_claude = resolve_claude_bin(claude_bin)
    legacy = _legacy_state(home_dir, server_name)
    cleaned = _LegacyCleanup()
    if resolved_claude is not None:
        result = _run_claude(
            resolved_claude,
            ["mcp", "remove", server_name, "--scope", DEFAULT_SCOPE],
            timeout_seconds=timeout_seconds,
        )
        if result.returncode != 0 and "No MCP server named" not in result.stderr:
            return ClaudeMcpResult(
                ok=False,
                status="registration_failed",
                message="Claude Code MCP removal failed.",
                claude_bin=resolved_claude,
                returncode=result.returncode,
                repair="Run `claude mcp remove stackos --scope user` and retry uninstall.",
                legacy_json_present=legacy.present,
                legacy_json_error=legacy.error,
            )
    cleaned = _cleanup_legacy_json(home_dir, server_name)
    return ClaudeMcpResult(
        ok=True,
        status="removed" if resolved_claude is not None else "claude_absent",
        message=(
            f"Removed MCP '{server_name}' from Claude Code user scope."
            if resolved_claude is not None
            else "Claude Code CLI not found; skipped Claude MCP removal."
        ),
        claude_bin=resolved_claude,
        legacy_json_present=legacy.present,
        legacy_json_cleaned=cleaned.cleaned,
        legacy_json_error=legacy.error or cleaned.error,
    )


@dataclass(frozen=True)
class _ClaudeGet:
    scope: str | None
    transport: str | None
    command: list[str]


@dataclass(frozen=True)
class _LegacyState:
    present: bool = False
    error: str | None = None


@dataclass(frozen=True)
class _LegacyCleanup:
    cleaned: bool = False
    error: str | None = None


def _run_claude(
    claude_bin: str,
    args: Sequence[str],
    *,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    env = _claude_subprocess_env()
    try:
        return subprocess.run(
            [claude_bin, *args],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            [claude_bin, *args],
            returncode=124,
            stdout=_completed_output_text(exc.stdout),
            stderr=_completed_output_text(exc.stderr) or "Claude CLI timed out.",
        )
    except Exception as exc:
        return subprocess.CompletedProcess(
            [claude_bin, *args],
            returncode=1,
            stdout="",
            stderr=f"{type(exc).__name__}: {exc}",
        )


def _expand_candidate_paths(candidates: Sequence[str]) -> list[str]:
    home = _default_home()
    expanded: list[str] = []
    for candidate in candidates:
        if candidate == "~":
            expanded.append(str(home))
        elif candidate.startswith("~/"):
            expanded.append(str(home / candidate[2:]))
        else:
            expanded.append(candidate)
    return expanded


def _claude_subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(_claude_path_entries(env.get("PATH", "")))
    return env


def _claude_path_entries(current_path: str) -> list[str]:
    entries: list[str] = []
    for candidate in _expand_candidate_paths(COMMON_CLAUDE_CLI_CANDIDATES):
        path = Path(candidate)
        _append_path_entry(entries, str(path.parent if path.name == "claude" else path))
    for entry in _login_shell_path_entries():
        _append_path_entry(entries, entry)
    for entry in current_path.split(os.pathsep):
        _append_path_entry(entries, entry)
    return entries


def _append_path_entry(entries: list[str], entry: str) -> None:
    cleaned = entry.strip()
    if not cleaned:
        return
    if cleaned == "~":
        cleaned = str(_default_home())
    elif cleaned.startswith("~/"):
        cleaned = str(_default_home() / cleaned[2:])
    if cleaned not in entries:
        entries.append(cleaned)


def _login_shell_path_entries() -> list[str]:
    shell = os.environ.get("SHELL")
    if not shell:
        return []
    shell_path = Path(shell)
    if shell_path.name not in SUPPORTED_LOGIN_SHELLS or not shell_path.is_absolute():
        return []
    if not os.access(shell, os.X_OK):
        return []
    try:
        result = subprocess.run(
            [shell, "-lc", 'printf "%s\\n" "$PATH"'],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    first_line = result.stdout.splitlines()[0] if result.stdout.splitlines() else ""
    return first_line.split(os.pathsep) if first_line else []


def _completed_output_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _parse_claude_get(stdout: str) -> _ClaudeGet:
    scope: str | None = None
    transport: str | None = None
    command: str | None = None
    args: list[str] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Scope:"):
            raw = stripped.split(":", 1)[1].strip().lower()
            scope = raw.split()[0] if raw else None
        elif stripped.startswith("Type:"):
            transport = stripped.split(":", 1)[1].strip().lower() or None
        elif stripped.startswith("Command:"):
            command = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Args:"):
            raw_args = stripped.split(":", 1)[1].strip()
            args = raw_args.split() if raw_args else []
    return _ClaudeGet(
        scope=scope,
        transport=transport,
        command=[command, *args] if command else [],
    )


def _looks_secretish(command: Sequence[str]) -> bool:
    haystack = " ".join(command).lower()
    return any(token in haystack for token in ("bearer", "authorization", "token=", "api_key"))


def _token_preflight(home: Path) -> str | None:
    from stackos.host_mcp.bridge import token_preflight

    return token_preflight(home)


def _default_home() -> Path:
    override = os.environ.get("STACKOS_HOME")
    return Path(override) if override else Path.home()


def _legacy_path(home: Path) -> Path:
    return home / ".claude" / "mcp.json"


def _legacy_state(home: Path, server_name: str) -> _LegacyState:
    target = _legacy_path(home)
    if not target.exists():
        return _LegacyState()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        servers = payload.get("mcpServers", {}) if isinstance(payload, dict) else {}
        return _LegacyState(present=isinstance(servers, dict) and server_name in servers)
    except Exception as exc:
        return _LegacyState(present=True, error=str(exc))


def _cleanup_legacy_json(home: Path, server_name: str) -> _LegacyCleanup:
    target = _legacy_path(home)
    if not target.exists():
        return _LegacyCleanup()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return _LegacyCleanup(error=f"legacy {target} is not a JSON object")
        servers = payload.get("mcpServers")
        if not isinstance(servers, dict) or server_name not in servers:
            return _LegacyCleanup()
        del servers[server_name]
        fd, tmp = tempfile.mkstemp(prefix=".mcp.", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(tmp, target)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return _LegacyCleanup(cleaned=True)
    except Exception as exc:
        return _LegacyCleanup(error=str(exc))


def _cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage StackOS Claude Code MCP registration.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("register")
    sub.add_parser("remove")
    sub.add_parser("inspect")
    args = parser.parse_args(argv)
    if args.command == "register":
        result = register()
    elif args.command == "remove":
        result = remove()
    else:
        result = inspect()
    print(result.message)
    if result.repair and result.status not in {"healthy", "registered", "removed"}:
        print(f"repair: {result.repair}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
