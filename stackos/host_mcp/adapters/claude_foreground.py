"""Admit stock Claude Code on the existing controlling terminal."""

from __future__ import annotations

import json
import shlex
import sys
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time_ns
from uuid import uuid4

from stackos.host_mcp.accounts import HostAccountError
from stackos.host_mcp.adapters.claude_accounts import (
    ClaudeAccountError,
    read_native_object,
    resume_transcript,
    selected_profile,
    transcript_writer,
)
from stackos.host_mcp.foreground import (
    ForegroundError,
    ForegroundLaunch,
    ForegroundObservation,
    ForegroundRequest,
)
from stackos.host_mcp.subscription_native import run_capture

_OPTIONS = {
    "--add-dir",
    "--agent",
    "--agents",
    "--allowedTools",
    "--allowed-tools",
    "--append-system-prompt",
    "--disallowedTools",
    "--disallowed-tools",
    "--effort",
    "--mcp-config",
    "--model",
    "-n",
    "--name",
    "--permission-mode",
    "--plugin-dir",
    "--setting-sources",
    "--system-prompt",
    "--tools",
}
_SWITCHES = {"--ax-screen-reader", "--strict-mcp-config", "--verbose"}
_HOOK = """\
import json
import os
import sys
from pathlib import Path

path, expected = Path(sys.argv[1]), sys.argv[2]
event = json.loads(sys.stdin.buffer.read(65537))
kind = event.get('hook_event_name')
if kind == 'UserPromptSubmit':
    if event.get('session_id') != expected or not event.get('prompt_id'):
        print('Launch the intended managed conversation before sending a turn.', file=sys.stderr)
        sys.exit(2)
if kind in ('UserPromptSubmit', 'Stop', 'StopFailure', 'SessionStart', 'SessionEnd'):
    keys = ('session_id', 'cwd', 'transcript_path', 'source', 'reason', 'prompt_id')
    record = {key: event.get(key) for key in keys}
    name = 'TurnSettled' if kind in ('Stop', 'StopFailure') else kind
    target = path / (name + '.json')
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(record))
    os.replace(temporary, target)
"""


def _native_options(args: tuple[str, ...]) -> tuple[str, ...]:
    options = iter(args)
    for value in options:
        key, equals, operand = value.partition("=")
        if value in _SWITCHES:
            continue
        if key in _OPTIONS:
            argument = operand if equals else next(options, None)
            if argument is not None and not argument.startswith("-"):
                continue
        raise ForegroundError(
            "A Claude Code argument conflicts with exact managed terminal launch."
        )
    return args


def _write_observer(directory: Path, expected: str) -> Path:
    plugin = directory / "plugin"
    manifest = plugin / ".claude-plugin"
    hooks = plugin / "hooks"
    manifest.mkdir(parents=True)
    hooks.mkdir()
    (manifest / "plugin.json").write_text(
        json.dumps({"name": "stackos-managed-terminal", "version": "1.0.0"})
    )
    script = directory / "observe.py"
    script.write_text(_HOOK)
    command = shlex.join([sys.executable, str(script), str(directory), expected])
    definitions = {
        name: [{"hooks": [{"type": "command", "command": command, "timeout": 3}]}]
        for name in ("SessionStart", "UserPromptSubmit", "Stop", "StopFailure", "SessionEnd")
    }
    (hooks / "hooks.json").write_text(json.dumps({"hooks": definitions}))
    return plugin


def _record(path: Path) -> dict | None:
    try:
        return read_native_object(path)
    except (FileNotFoundError, ValueError, RecursionError):
        return None


def _ensure_inactive(binary: str, env: dict[str, str], cwd: Path, session_id: str) -> None:
    result = run_capture(binary, ["agents", "--json"], cwd=cwd, env=env)
    try:
        if result is None or result.returncode:
            raise ValueError
        sessions = json.loads(result.stdout)
        if not isinstance(sessions, list) or any(not isinstance(row, dict) for row in sessions):
            raise ValueError
    except (ValueError, RecursionError):
        raise ForegroundError(
            "Claude Code could not confirm that the local session is inactive."
        ) from None
    if any(row.get("sessionId") == session_id for row in sessions):
        raise ForegroundError(
            "The requested Claude Code conversation has an active native session."
        )


class ClaudeForeground:
    def prepare(self, request: ForegroundRequest) -> ForegroundLaunch:
        args = _native_options(request.native_args)
        resources = ExitStack()
        try:

            def profile():
                return selected_profile(request.executable, request.selection, request.cwd)

            env, paths = profile()
            expected = request.session_id if request.session_id is not None else str(uuid4())
            fd = resources.enter_context(transcript_writer(expected))
            transcript = None
            if request.session_id is not None:
                transcript = resume_transcript(paths, expected, request.cwd)
                _ensure_inactive(request.executable, env, request.cwd, expected)
            directory = Path(
                resources.enter_context(TemporaryDirectory(prefix="stackos-claude-tty-"))
            )
            observer = _write_observer(directory, expected)
            selector = ("--resume", str(transcript)) if transcript else ("--session-id", expected)
            prepared_ms = time_ns() // 1_000_000
            principal = request.selection.principal_key
            admitted = False

            def matches(event: dict) -> bool:
                cwd = event.get("cwd")
                route = event.get("transcript_path")
                if not isinstance(cwd, str) or not isinstance(route, str):
                    return False
                path = Path(route)
                return (
                    Path(cwd).resolve() == request.cwd.resolve()
                    and path.is_absolute()
                    and path.name == f"{expected}.jsonl"
                    and path.resolve().parent.parent == paths.projects.resolve()
                    and (transcript is None or path.resolve() == transcript)
                )

            def observe(pid: int, code: int | None) -> ForegroundObservation:
                if code is not None and admitted:
                    end = _record(directory / "SessionEnd.json")
                    prompt = _record(directory / "UserPromptSubmit.json")
                    turn = _record(directory / "TurnSettled.json")
                    turn_settled = prompt is None or (
                        turn is not None
                        and turn.get("session_id") == expected
                        and turn.get("prompt_id") == prompt.get("prompt_id")
                    )
                    settled = bool(
                        end
                        and end.get("session_id") == expected
                        and matches(end)
                        and end.get("reason") == "prompt_input_exit"
                        and turn_settled
                    )
                    if settled:
                        _ensure_inactive(request.executable, env, request.cwd, expected)
                    return ForegroundObservation(expected, principal, settled)

                start = _record(directory / "SessionStart.json")
                if not start or start.get("session_id") != expected or not matches(start):
                    return ForegroundObservation()
                if code is not None:
                    return ForegroundObservation(expected)
                row = _record(paths.config / "sessions" / f"{pid}.json")
                if not row:
                    return ForegroundObservation()
                stamp = row.get("startedAt")
                if (
                    row.get("pid") != pid
                    or row.get("sessionId") != expected
                    or row.get("kind") != "interactive"
                    or type(stamp) is not int
                    or stamp < prepared_ms
                    or row.get("cwd") != str(request.cwd.resolve())
                ):
                    raise ForegroundError(
                        "The native Claude Code process did not retain the exact session."
                    )
                if row.get("status") != "idle":
                    return ForegroundObservation()
                if profile() != (env, paths):
                    raise ForegroundError(
                        "The Claude Code profile changed during terminal startup."
                    )
                return ForegroundObservation(expected, principal)

            def admit(enable: bool) -> None:
                nonlocal admitted
                admitted = enable

            return ForegroundLaunch(
                (request.executable, *selector, "--plugin-dir", str(observer), *args),
                env,
                observe,
                admit,
                close=resources.close,
                inherited_fds=(fd,),
                tty_job_control=True,
            )
        except BaseException as exc:
            resources.close()
            if isinstance(exc, (ClaudeAccountError, HostAccountError)):
                raise ForegroundError(str(exc)) from None
            raise
