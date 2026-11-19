"""Give the stock Claude workspace dialog exactly one operator decision."""

from __future__ import annotations

import errno
import os
import pty
import re
import select
import signal
import termios
from contextlib import suppress
from pathlib import Path
from time import monotonic, sleep

from stackos.host_mcp.adapters.claude_accounts import ClaudeProfile, workspace_trusted
from stackos.host_mcp.foreground import ForegroundError

_TITLE = b"Permission Required: Accessing workspace:"
_DIALOG = (
    _TITLE,
    b"Quick safety check:",
    b"y. Yes, I trust this folder",
    b"n. No, exit",
    b"Enter y/n:",
)
_ANSI = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")
_TIMEOUT = 15.0


def confirm_workspace(binary: str, env: dict[str, str], paths: ClaudeProfile, cwd: Path) -> bool:
    if workspace_trusted(paths, cwd, include_parents=True):
        return False
    home = Path(env.get("HOME") or Path.home())
    if cwd.resolve() == home.resolve():
        raise ForegroundError(
            "Claude Code does not save workspace trust for your home directory. "
            "Run the managed launcher from the project directory."
        )

    # Only this setup child uses an isolated PTY. It never receives a prompt,
    # resume selector, user settings overrides, or a stream of operator input.
    pid, master = pty.fork()
    if pid == 0:
        try:
            os.chdir(cwd)
            os.execve(
                binary, [binary, "--safe-mode", "--ax-screen-reader", "--strict-mcp-config"], env
            )
        finally:
            os._exit(127)

    def interrupted(_signal: int, _frame: object) -> None:
        raise ForegroundError("Claude Code workspace trust was interrupted.")

    handlers = {}
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
            handlers[sig] = signal.signal(sig, interrupted)
        deadline = monotonic() + _TIMEOUT
        output = bytearray()
        while monotonic() < deadline:
            chunk = _read(master, deadline)
            if chunk is None:
                break
            output.extend(chunk)
            if all(marker in output for marker in _DIALOG):
                rendered = _ANSI.sub(b"", output).split(_TITLE, 1)[1].split(_DIALOG[1], 1)[0]
                folder = Path(os.fsdecode(b"".join(rendered.splitlines()).strip()))
                if not folder.is_absolute() or folder.resolve() != cwd.resolve():
                    raise ForegroundError("Claude Code asked to trust a different workspace.")
                _choose(master, paths, cwd)
                return True
            if len(output) > 65536:
                break
        raise ForegroundError("Claude Code did not show its native workspace trust decision.")
    finally:
        try:
            # Hanging up the consent-only PTY also prevents any further input
            # even if the native process is still unwinding after its choice.
            os.close(master)
            _retire(pid)
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)


def _choose(master: int, paths: ClaudeProfile, cwd: Path) -> None:
    termios.tcflush(0, termios.TCIFLUSH)
    print(f"Claude Code asks whether you trust this workspace:\n{cwd}", flush=True)
    print("Trust lets Claude Code read, edit, and execute files here.", flush=True)
    try:
        answer = input("Trust this workspace in Claude Code [y/N]? ").strip().lower()
    except EOFError:
        answer = ""
    termios.tcflush(0, termios.TCIFLUSH)
    accept = answer in {"y", "yes"}
    os.write(master, b"y\r" if accept else b"n\r")
    deadline = monotonic() + _TIMEOUT
    while monotonic() < deadline:
        if accept and workspace_trusted(paths, cwd):
            return
        if _read(master, min(deadline, monotonic() + 0.1)) is None:
            break
    if not accept:
        raise ForegroundError("Claude Code workspace trust was declined.")
    if not workspace_trusted(paths, cwd):
        raise ForegroundError("Claude Code did not save workspace trust for the selected profile.")


def _read(master: int, deadline: float) -> bytes | None:
    if not select.select([master], [], [], max(0, deadline - monotonic()))[0]:
        return b""
    try:
        return os.read(master, 8192) or None
    except OSError as exc:
        if exc.errno == errno.EIO:
            return None
        raise


def _retire(pid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if os.waitpid(pid, os.WNOHANG)[0]:
            return
        with suppress(ProcessLookupError):
            os.kill(pid, sig)
        deadline = monotonic() + 1.5
        while monotonic() < deadline:
            if os.waitpid(pid, os.WNOHANG)[0]:
                return
            sleep(0.025)
    raise ForegroundError("Claude Code's workspace trust process did not retire.")
