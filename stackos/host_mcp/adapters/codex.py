"""Codex MCP lifecycle adapter."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from stackos.host_mcp.bridge import (
    MCP_SERVER_NAME,
    command_from_output_row,
    command_line_mentions,
    is_stackos_bridge_command,
    output_row_matches_server,
    resolve_bridge_command,
    token_preflight,
)
from stackos.host_mcp.discovery import resolve_cli_bins, subprocess_env_for_cli
from stackos.host_mcp.result import (
    AgentRunResult,
    AgentSandbox,
    HostMcpResult,
    looks_secretish,
    usage_snapshot,
    validate_agent_result,
)
from stackos.host_mcp.toml_config import read_mcp_config_ref

HOST_KEY = "codex"
SURFACE = "shared-config"
CODEX_BIN_ENV = "STACKOS_CODEX_BIN"
COMMON_CODEX_CLI_CANDIDATES = (
    "~/.local/bin/codex",
    "~/bin/codex",
    "~/.npm-global/bin/codex",
    "~/.volta/bin/codex",
    "~/.asdf/shims/codex",
    "~/.local/share/mise/shims/codex",
    "~/.nvm/versions/node/*/bin/codex",
    "~/.local/share/fnm/node-versions/*/installation/bin/codex",
    "~/Library/Application Support/fnm/node-versions/*/installation/bin/codex",
    "/opt/homebrew/bin/codex",
    "/usr/local/bin/codex",
)
MACOS_CHATGPT_BUNDLE_CANDIDATES = (
    "/Applications/ChatGPT.app/Contents/Resources/codex",
    "~/Applications/ChatGPT.app/Contents/Resources/codex",
)
MACOS_CODEX_APP_BUNDLE_CANDIDATES = (
    "/Applications/Codex.app/Contents/Resources/codex",
    "~/Applications/Codex.app/Contents/Resources/codex",
)


def run_agent(
    project_dir: Path,
    instructions: str,
    prompt: str,
    output_schema: dict[str, Any],
    *,
    sandbox: AgentSandbox = "workspace-write",
    model: str | None = None,
    provider: str | None = None,
    profile: str | None = None,
    resume_thread_id: str | None = None,
    on_session: Callable[[str], None] | None = None,
    mcp_config_ref: dict[str, str] | None = None,
) -> AgentRunResult:
    """Run supplied instructions in a new or explicitly selected native thread."""
    if not instructions.strip():
        return AgentRunResult(None, error="Project-agent instructions must not be empty.")

    codex_bin = _select_codex_bin()
    if codex_bin is None:
        return AgentRunResult(None, error="No usable Codex CLI was found by host discovery.")
    try:
        overrides = (
            task_mcp_overrides(project_dir, mcp_config_ref, codex_bin=codex_bin)[1]
            if mcp_config_ref is not None
            else []
        )
    except ValueError as error:
        return AgentRunResult(None, error=str(error))
    with TemporaryDirectory(prefix="stackos-agent-") as temporary:
        schema_path = Path(temporary) / "schema.json"
        result_path = Path(temporary) / "result.json"
        schema_path.write_text(json.dumps(output_schema), encoding="utf-8")
        command = [
            codex_bin,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--cd",
            str(project_dir),
            *overrides,
            *(["--model", model] if model is not None else []),
            *(["-c", f"model_provider={json.dumps(provider)}"] if provider is not None else []),
            *(
                ["--approve-for-me"]
                if sandbox == "workspace-write"
                else ["--sandbox", "read-only", "-c", 'approval_policy="never"']
            ),
            "--output-schema",
            str(schema_path),
            "-o",
            str(result_path),
            "-c",
            f"developer_instructions={json.dumps(instructions, ensure_ascii=False)}",
            *(["resume", resume_thread_id] if resume_thread_id is not None else []),
            "-",
        ]
        from stackos.host_mcp.progress import CodexStreamError, run_codex_process

        process = None
        thread_id = None
        execution_error = None

        def record_session(session_id: str) -> None:
            nonlocal thread_id
            thread_id = session_id
            if on_session is not None:
                on_session(session_id)

        try:
            if on_session is not None:
                process = run_codex_process(
                    command,
                    input=prompt,
                    cwd=project_dir,
                    env=subprocess_env_for_cli(codex_bin),
                    on_session=record_session,
                )
            else:
                process = subprocess.run(
                    command,
                    input=prompt,
                    cwd=project_dir,
                    env=subprocess_env_for_cli(codex_bin),
                    capture_output=True,
                    text=True,
                    check=False,
                )
        except CodexStreamError as exc:
            process = exc.process
            execution_error = str(exc)
        except (OSError, UnicodeError) as exc:
            execution_error = f"Codex process failed ({type(exc).__name__}): {exc}"
        exit_code = process.returncode if process is not None else None
        stdout = process.stdout if process is not None else ""
        stderr = process.stderr if process is not None else ""
        native_error = None
        terminal_error = None
        usage = None
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("type") == "thread.started":
                thread_id = event.get("thread_id")
            if isinstance(event, dict) and event.get("type") == "turn.completed":
                # Codex reports recoverable stream interruptions as error events.
                # A later completion clears those, but never a terminal turn failure.
                native_error = None
                counters = event.get("usage")
                # Native exec emits the thread's cumulative total (verified in
                # 0.167.0 usage_from_last_total), including on resume. Cached
                # input and reasoning output are already included subsets.
                usage = (
                    usage_snapshot(
                        "codex.turn",
                        "session",
                        counters.get("input_tokens"),
                        counters.get("output_tokens"),
                        cache_read=counters.get("cached_input_tokens"),
                        cache_write=counters.get("cache_write_input_tokens"),
                        reasoning=counters.get("reasoning_output_tokens"),
                    )
                    if isinstance(counters, dict)
                    else None
                )
            if isinstance(event, dict) and event.get("type") == "error":
                native_error = str(event.get("error") or event.get("message") or "Codex failed.")
            if isinstance(event, dict) and event.get("type") == "turn.failed":
                terminal_error = str(event.get("error") or event.get("message") or "Codex failed.")
        execution_error = (
            execution_error
            or terminal_error
            or native_error
            or (
                stderr.strip() or stdout.strip() or "Codex exited unsuccessfully."
                if exit_code != 0
                else None
            )
        )
        if execution_error and mcp_config_ref is not None:
            execution_error = "Codex task-routed execution failed; inspect native host diagnostics."
        # The primary final can still contain useful facts after native failure.
        # Retaining it never overrides the process/transport success boundary.
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if not isinstance(result, dict):
                raise ValueError("expected a JSON object")
        except (OSError, ValueError) as exc:
            if mcp_config_ref is not None:
                return AgentRunResult(
                    exit_code,
                    thread_id=thread_id,
                    usage=usage,
                    error="Invalid Codex task-routed result; inspect native host diagnostics.",
                )
            detail = f"Invalid Codex result: {exc}"
            native_detail = execution_error or stderr.strip() or stdout.strip()
            return AgentRunResult(
                exit_code,
                thread_id=thread_id,
                usage=usage,
                error=f"{native_detail}\n{detail}" if native_detail else detail,
            )
        return validate_agent_result(
            AgentRunResult(
                exit_code,
                thread_id=thread_id,
                result=result,
                usage=usage,
                error=execution_error,
            ),
            output_schema,
            "Codex",
        )


def task_mcp_overrides(
    project_dir: Path, reference: dict[str, str], *, codex_bin: str | None = None
) -> tuple[dict[str, str], list[str]]:
    """Serialize only route leaves over the selected host's effective servers."""
    import tomlkit

    identity, routes = read_mcp_config_ref(reference)
    binary = codex_bin or _select_codex_bin()
    if binary is None:
        raise ValueError("No usable Codex CLI was found by host discovery.")
    result = _run_codex(binary, ["--cd", str(project_dir), "mcp", "list", "--json"])
    try:
        rows = json.loads(result.stdout)
        if result.returncode or not isinstance(rows, list):
            raise ValueError
        existing = {row["name"]: row["transport"] for row in rows}
        if any(
            name not in existing
            or existing[name].get("type") != "stdio"
            or not existing[name].get("command")
            for name in routes
        ):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("Task MCP routes require existing native stdio servers") from None
    overrides = []
    for name, route in sorted(routes.items()):
        for field, value in sorted(route.items()):
            leaves = value.items() if field == "env" else [(None, value)]
            for key, leaf in leaves:
                # Native -c splits the key on dots; quotes become literal key
                # characters. Names/env keys were validated as bare identifiers.
                suffix = f".{key}" if key is not None else ""
                overrides.extend(
                    ["-c", f"mcp_servers.{name}.{field}{suffix}={tomlkit.item(leaf).as_string()}"]
                )
    return identity, overrides


def inspect(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    del home
    codex_bins = resolve_codex_bins()
    if not codex_bins:
        return _absent()
    command = resolve_bridge_command(runtime=HOST_KEY)
    unsupported: HostMcpResult | None = None
    for codex_bin in codex_bins:
        result = _inspect_with_bin(codex_bin, command, server_name)
        if result.status != "unsupported_host_version":
            return result
        unsupported = result
    return unsupported or _absent()


def _inspect_with_bin(
    codex_bin: str,
    command: Sequence[str],
    server_name: str,
) -> HostMcpResult:
    details = _run_codex(codex_bin, ["mcp", "get", server_name, "--json"])
    if details.returncode == 0:
        details_result = _json_details_result(details.stdout, command, server_name)
        if details_result is not None:
            return details_result
    listed = _run_codex(codex_bin, ["mcp", "list"])
    if listed.returncode != 0:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="unsupported_host_version",
            message="Codex MCP status could not be inspected.",
            ok=False,
            available=True,
            blocking=True,
            repair="Check `codex mcp --help`, then rerun `stackos install --mcp-only`.",
        )
    rows = _stackos_rows(listed.stdout, server_name)
    if not rows:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="available_unregistered",
            message="StackOS is not registered with Codex.",
            ok=False,
            available=True,
            blocking=True,
            repair="Run `stackos install --mcp-only` or desktop Repair.",
        )
    if any(looks_secretish(row) for row in rows):
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_unsafe",
            message="Codex has a StackOS MCP entry that appears to contain secret material.",
            ok=False,
            available=True,
            blocking=True,
            repair="Run `stackos install --mcp-only` or desktop Repair.",
            warnings=["unsafe Codex MCP entry redacted"],
            selected=True,
            managed=False,
        )
    bridge_rows = [row for row in rows if _line_is_bridge(row, command, server_name)]
    if bridge_rows:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_current",
            message="Codex StackOS MCP registration is healthy.",
            ok=True,
            available=True,
            command=list(command),
            selected=True,
            managed=True,
        )
    managed_rows = [row for row in rows if _line_is_managed_bridge(row)]
    if not managed_rows:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_unmanaged",
            message="Codex has an MCP entry named stackos that StackOS does not own.",
            ok=False,
            available=True,
            blocking=True,
            repair="Review the existing Codex MCP entry; StackOS left it unchanged.",
            warnings=["unmanaged Codex MCP entry redacted"],
            selected=True,
            managed=False,
        )
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="registered_stale",
        message="Codex has a StackOS MCP entry, but it is not the local stdio bridge.",
        ok=False,
        available=True,
        blocking=True,
        repair="Run `stackos install --mcp-only` or desktop Repair.",
        warnings=["stale Codex MCP entry redacted"],
        selected=True,
        managed=True,
    )


def register(
    home: Path,
    *,
    server_name: str = MCP_SERVER_NAME,
    force: bool = False,
) -> HostMcpResult:
    codex_bin = _select_codex_bin()
    if codex_bin is None:
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
            repair="Run `stackos install` or desktop Repair before registering Codex MCP.",
        )

    current = inspect(home, server_name=server_name)
    if current.ok and not force:
        command = resolve_bridge_command(runtime=HOST_KEY)
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_current",
            message=f"MCP '{server_name}' already registered with Codex CLI",
            ok=True,
            available=True,
            command=command,
            selected=True,
            managed=True,
        )
    if current.selected and not current.managed:
        return current
    replaces_current_entry = current.status == "registered_stale" or (
        current.status == "registered_current" and force
    )
    if replaces_current_entry:
        removed = _run_codex(codex_bin, ["mcp", "remove", server_name])
        if removed.returncode != 0:
            return HostMcpResult(
                host_key=HOST_KEY,
                surface=SURFACE,
                status="remove_failed",
                message="Codex MCP registration failed while removing the stale entry.",
                ok=False,
                available=True,
                blocking=True,
                repair=f"Run `codex mcp remove {server_name}`, then rerun StackOS Repair.",
            )
    if not replaces_current_entry and current.status != "available_unregistered":
        return current
    command = resolve_bridge_command(runtime=HOST_KEY)
    added = _run_codex(codex_bin, ["mcp", "add", server_name, "--", *command])
    if added.returncode != 0:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="register_failed",
            message="Codex MCP registration failed.",
            ok=False,
            available=True,
            blocking=True,
            repair="Check `codex mcp add --help`, then rerun `stackos install --mcp-only`.",
        )
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="registered",
        message=f"Registered MCP '{server_name}' with Codex.",
        ok=True,
        available=True,
        command=command,
        selected=True,
        managed=True,
    )


def remove(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    codex_bin = _select_codex_bin()
    if codex_bin is None:
        return _absent(message="Codex was not detected; skipped StackOS MCP removal.")
    current = inspect(home, server_name=server_name)
    if current.status in {"available_unregistered", "absent", "removed"}:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="removed",
            message=f"MCP '{server_name}' not registered with Codex; nothing to remove.",
            ok=True,
            available=True,
        )
    if not current.managed:
        return current
    removed = _run_codex(codex_bin, ["mcp", "remove", server_name])
    if removed.returncode != 0:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="remove_failed",
            message="Codex MCP removal failed.",
            ok=False,
            available=True,
            blocking=True,
            repair=f"Run `codex mcp remove {server_name}` and retry uninstall.",
        )
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="removed",
        message=f"Unregistered MCP '{server_name}' from Codex.",
        ok=True,
        available=True,
    )


def _absent(
    message: str = "Codex was not detected; skipped StackOS MCP registration.",
) -> HostMcpResult:
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="absent",
        message=message,
        ok=True,
        available=False,
        advisory=True,
        repair="Install Codex, then run `stackos install --mcp-only` or desktop Repair.",
    )


def resolve_codex_bin(codex_bin: str | None = None) -> str | None:
    candidates = resolve_codex_bins(codex_bin)
    return candidates[0] if candidates else None


def resolve_codex_bins(codex_bin: str | None = None) -> list[str]:
    return resolve_cli_bins(
        "codex",
        explicit=codex_bin,
        env_var=CODEX_BIN_ENV,
        preferred_candidates=(MACOS_CHATGPT_BUNDLE_CANDIDATES if sys.platform == "darwin" else ()),
        common_candidates=COMMON_CODEX_CLI_CANDIDATES,
        app_bundle_candidates=(
            MACOS_CODEX_APP_BUNDLE_CANDIDATES if sys.platform == "darwin" else ()
        ),
    )


def _select_codex_bin() -> str | None:
    """Return the first preferred Codex executable whose MCP surface responds."""

    for candidate in resolve_codex_bins():
        if _run_codex(candidate, ["mcp", "list"]).returncode == 0:
            return candidate
    return None


def _run_codex(codex_bin: str, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [codex_bin, *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
            env=subprocess_env_for_cli(codex_bin),
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            [codex_bin, *args],
            returncode=124,
            stdout=_text(exc.stdout),
            stderr=_text(exc.stderr) or "Codex CLI timed out.",
        )
    except Exception as exc:
        return subprocess.CompletedProcess(
            [codex_bin, *args],
            returncode=1,
            stdout="",
            stderr=f"{type(exc).__name__}: {exc}",
        )


def _stackos_rows(stdout: str, server_name: str) -> list[str]:
    return [
        line.strip() for line in stdout.splitlines() if output_row_matches_server(line, server_name)
    ]


def _line_is_bridge(
    line: str,
    command: Sequence[str],
    server_name: str = MCP_SERVER_NAME,
) -> bool:
    normalized = line.strip()
    if not output_row_matches_server(normalized, server_name):
        return False
    lowered = normalized.lower()
    forbidden = ("/mcp", "--url", "--bearer-token-env-var", "authorization", "bearer")
    if any(token in lowered for token in forbidden):
        return False
    return command_line_mentions(command, normalized)


def _line_is_managed_bridge(line: str) -> bool:
    return is_stackos_bridge_command(
        command_from_output_row(line),
    )


def _json_details_result(
    stdout: str,
    command: Sequence[str],
    server_name: str,
) -> HostMcpResult | None:
    """Classify Codex's machine-readable MCP record without exposing its contents."""

    try:
        payload = json.loads(stdout)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("name") not in (None, server_name):
        return None
    transport = payload.get("transport")
    if not isinstance(transport, dict):
        return None
    if _transport_is_unsafe(transport):
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_unsafe",
            message="Codex has a StackOS MCP entry with unsafe connection settings.",
            ok=False,
            available=True,
            blocking=True,
            repair="Review and remove the entry manually before StackOS Repair.",
            warnings=["unsafe Codex MCP entry redacted"],
            selected=True,
            managed=False,
        )

    actual_command = _transport_command(transport)
    if (
        payload.get("enabled") is True
        and transport.get("type") == "stdio"
        and actual_command == list(command)
    ):
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_current",
            message="Codex StackOS MCP registration is healthy.",
            ok=True,
            available=True,
            command=list(command),
            selected=True,
            managed=True,
        )
    if actual_command is not None and _command_is_managed_bridge(actual_command):
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_stale",
            message="Codex has a StackOS-owned MCP entry that needs reconciliation.",
            ok=False,
            available=True,
            blocking=True,
            repair="Run `stackos install --mcp-only` or desktop Repair.",
            warnings=["stale Codex MCP entry redacted"],
            selected=True,
            managed=True,
        )
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="registered_unmanaged",
        message="Codex has an MCP entry named stackos that StackOS does not own.",
        ok=False,
        available=True,
        blocking=True,
        repair="Review the existing Codex MCP entry; StackOS left it unchanged.",
        warnings=["unmanaged Codex MCP entry redacted"],
        selected=True,
        managed=False,
    )


def _transport_is_unsafe(transport: dict[str, object]) -> bool:
    expected_fields = {"type", "command", "args", "env", "env_vars", "cwd"}
    if set(transport) - expected_fields:
        return True
    if transport.get("env") not in (None, {}):
        return True
    if transport.get("env_vars") not in (None, []):
        return True
    if transport.get("cwd") is not None:
        return True
    return looks_secretish(transport)


def _transport_command(transport: dict[str, object]) -> list[str] | None:
    executable = transport.get("command")
    args = transport.get("args")
    if not isinstance(executable, str) or not isinstance(args, list):
        return None
    if not all(isinstance(arg, str) for arg in args):
        return None
    return [executable, *args]


def _command_is_managed_bridge(command: Sequence[str]) -> bool:
    return is_stackos_bridge_command(command)


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)
