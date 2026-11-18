from __future__ import annotations

import errno
import json
import os
import pty
import select
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "foreground_harness.py"
SESSION = "00000000-0000-4000-8000-000000000880"
COMMAND = (sys.executable, "-E", str(FIXTURE))
NATIVE_FLAGS = ("--mcp-config", "file with spaces", "--permission-mode", "ask")


class Terminal:
    def __init__(self, pid: int, fd: int) -> None:
        self.pid = pid
        self.fd = fd
        self.received = b""
        self.unread = b""
        self.reaped = False

    def read_until(self, marker: bytes) -> bytes:
        deadline = time.monotonic() + 8
        while marker not in self.unread:
            if not select.select([self.fd], [], [], max(0, deadline - time.monotonic()))[0]:
                pytest.fail(f"Timed out waiting for {marker!r}: {self.received!r}")
            try:
                data = os.read(self.fd, 4096)
            except OSError as exc:
                if exc.errno != errno.EIO:
                    raise
                data = b""
            if not data:
                pytest.fail(f"Terminal closed before {marker!r}: {self.received!r}")
            self.received += data
            self.unread += data
        before, self.unread = self.unread.split(marker, 1)
        return before

    def native_start(self) -> dict:
        self.read_until(b"native {")
        return json.loads(b"{" + self.read_until(b"\n"))

    def send(self, line: str) -> None:
        os.write(self.fd, (line + "\n").encode())

    def pending_tool(self) -> int:
        self.read_until(b"tool and approval still pending ")
        return int(self.read_until(b"\n"))

    def exit_code(self) -> int:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.reaped = True
                return os.waitstatus_to_exitcode(status)
            time.sleep(0.01)
        pytest.fail(f"Foreground command did not exit: {self.received!r}")


@contextmanager
def foreground_terminal(argv: list[str], cwd: Path, env: dict[str, str]) -> Iterator[Terminal]:
    pid, fd = pty.fork()
    if not pid:
        try:
            os.chdir(cwd)
            os.execve(argv[0], argv, env)
        finally:
            os._exit(127)
    terminal = Terminal(pid, fd)
    try:
        yield terminal
    finally:
        if not terminal.reaped:
            with suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        os.close(fd)


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.cwd = tmp_path / "workspace"
        self.cwd.mkdir()
        self.data = tmp_path / "data"
        self.data.mkdir()
        self.env = dict(os.environ, STACKOS_DATA_DIR=str(self.data), FIXTURE_MCP="unchanged")
        self.refs = json.loads(self.call("setup"))

    def call(self, *args: str) -> str:
        result = subprocess.run(
            [*COMMAND, *args], cwd=self.cwd, env=self.env, capture_output=True, text=True, timeout=8
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    def select(self, principal: str, revision: int) -> None:
        assert int(self.call("select", self.refs[principal], str(revision - 1))) == revision

    def assert_status(self, phase: str, *, requested: int, confirmed: int | None) -> None:
        deadline = time.monotonic() + 8
        while True:
            status = json.loads(self.call("cli", "harness", "status", "codex"))
            if status["phase"] == phase:
                assert status["requested_revision"] == requested
                assert status["confirmed_revision"] == confirmed
                return
            assert time.monotonic() < deadline, status

    def terminal(self, *args: str, observe_shell: bool = False):
        mode = "shell" if observe_shell else "cli"
        argv = [*COMMAND, mode, "harness", "run", "codex", *args, "--", *NATIVE_FLAGS]
        return foreground_terminal(argv, self.cwd, self.env)


def test_quota_failure_waits_for_native_retirement_then_switches_exact_session(tmp_path):
    harness = Harness(tmp_path)
    harness.select("a", 1)
    with harness.terminal() as terminal:
        a = terminal.native_start()
        assert a["principal"] == "a"
        assert a["session"] == SESSION
        harness.assert_status("confirmed", requested=1, confirmed=1)
        terminal.send("commit")
        terminal.read_until(b"effect committed\r\n")
        terminal.send("quota")
        terminal.read_until(b'"rate_limit_exceeded"')
        live_tool = terminal.pending_tool()
        harness.select("b", 2)
        harness.assert_status("pending", requested=2, confirmed=1)
        assert os.getpgid(a["pid"]) == terminal.pid
        assert os.getpgid(live_tool) == terminal.pid
        with harness.terminal("--session", SESSION) as duplicate:
            duplicate.read_until(b"foreground harness is already running")
            assert duplicate.exit_code() != 0

        terminal.send("finish")
        terminal.read_until(b"Selection requested at revision 2.")
        b = terminal.native_start()
        assert b["principal"] == "b"
        assert b["history"] == "initial-marker\n"
        assert b["draft"] == "unsent draft"
        harness.assert_status("confirmed", requested=2, confirmed=2)
        terminal.send("continue")
        terminal.read_until(b"continued as b\r\n")

        harness.select("a", 3)
        harness.assert_status("pending", requested=3, confirmed=2)
        terminal.send("finish")
        returned = terminal.native_start()
        assert returned["principal"] == "a"
        assert returned["history"] == "initial-marker\ncontinued-as-b\n"
        assert returned["draft"] == "unsent draft"
        harness.assert_status("confirmed", requested=3, confirmed=3)
        for native in (a, b, returned):
            assert native["session"] == SESSION
            assert native["tty"] == a["tty"]
            assert native["foreground"]
            assert native["cwd"] == str(harness.cwd.resolve())
            assert native["args"] == list(NATIVE_FLAGS)
            assert native["mcp"] == "unchanged"
        assert len({a["pid"], b["pid"], returned["pid"]}) == 3
        terminal.send("finish")
        assert terminal.exit_code() == 0
    assert (harness.data / "effects").read_text() == "committed\n"


def test_resume_refusal_keeps_prior_exact_route_and_allows_explicit_recovery(tmp_path):
    harness = Harness(tmp_path)
    history = harness.data / f"{SESSION}.history"
    history.write_text("existing conversation\n")
    harness.select("a", 1)
    with harness.terminal("--session", SESSION) as terminal:
        assert terminal.native_start()["history"] == "existing conversation\n"
        harness.assert_status("confirmed", requested=1, confirmed=1)
        (harness.data / "refuse-b").touch()
        harness.select("b", 2)
        terminal.send("finish")
        terminal.read_until(b"native rejected the exact resume\r\n")
        terminal.read_until(f"stackos harness run codex --session {SESSION}".encode())
        assert terminal.exit_code() == 4
    harness.assert_status("failed", requested=2, confirmed=1)
    assert not (harness.data / "effects").exists()

    harness.select("a", 3)
    with harness.terminal("--session", SESSION) as terminal:
        recovered = terminal.native_start()
        assert (recovered["session"], recovered["principal"], recovered["history"]) == (
            SESSION,
            "a",
            "existing conversation\n",
        )
        terminal.send("finish")
        assert terminal.exit_code() == 0


@pytest.mark.parametrize("interrupt", [False, True], ids=["unsettled-exit", "terminal-interrupt"])
def test_pending_quota_work_never_triggers_a_successor_on_unverified_exit(tmp_path, interrupt):
    harness = Harness(tmp_path)
    harness.select("a", 1)
    with harness.terminal() as terminal:
        assert terminal.native_start()["principal"] == "a"
        harness.assert_status("confirmed", requested=1, confirmed=1)
        terminal.send("quota")
        live_tool = terminal.pending_tool()
        harness.select("b", 2)
        harness.assert_status("pending", requested=2, confirmed=1)
        assert os.getpgid(live_tool) == terminal.pid
        if interrupt:
            os.write(terminal.fd, b"\x03")
            terminal.read_until(b"native received interrupt\r\n")
        else:
            terminal.send("unsettled")
        terminal.read_until(f"stackos harness run codex --session {SESSION}".encode())
        assert terminal.exit_code() == (130 if interrupt else 75)
    harness.assert_status("failed", requested=2, confirmed=1)
    assert len(list(harness.data.glob("observation-*"))) == 1
    assert not (harness.data / "effects").exists()


def test_rejected_destination_cannot_accept_terminal_input(tmp_path):
    harness = Harness(tmp_path)
    harness.select("a", 1)
    with harness.terminal(observe_shell=True) as terminal:
        assert terminal.native_start()["principal"] == "a"
        harness.assert_status("confirmed", requested=1, confirmed=1)
        terminal.send("commit")
        terminal.read_until(b"effect committed\r\n")
        (harness.data / "cached-a").touch()
        hold = harness.data / "hold-b-observation"
        hold.touch()
        harness.select("b", 2)
        terminal.send("finish")
        cached = terminal.native_start()
        assert cached["principal"] == "a"
        harness.assert_status("pending", requested=2, confirmed=1)
        terminal.send("continue\nfinish")
        hold.unlink()
        terminal.read_until(f"stackos harness run codex --session {SESSION}".encode())
        terminal.read_until(b"shell returned 1\r\n")
        terminal.send("operator-marker")
        terminal.read_until(b"shell received ")
        assert terminal.read_until(b"\r\n") == b"operator-marker"
        assert terminal.exit_code() == 1
    assert (harness.data / f"{SESSION}.history").read_text() == "initial-marker\n"
    assert (harness.data / "effects").read_text() == "committed\n"
    assert b"Native launch did not confirm the selected account." in terminal.received
    harness.assert_status("failed", requested=2, confirmed=1)


@pytest.mark.parametrize("target", ["native", "launcher"])
def test_signal_before_live_observation_retains_exact_recovery_id(tmp_path, target):
    harness = Harness(tmp_path)
    harness.select("a", 1)
    (harness.data / "observe-only-on-exit").touch()
    with harness.terminal() as terminal:
        native = terminal.native_start()
        assert native["session"] == SESSION
        harness.select("b", 2)
        harness.assert_status("pending", requested=2, confirmed=None)
        os.kill(native["pid"] if target == "native" else terminal.pid, signal.SIGTERM)
        terminal.read_until(f"stackos harness run codex --session {SESSION}".encode())
        assert terminal.exit_code() == 143
    status = json.loads(harness.call("cli", "harness", "status", "codex"))
    assert status == {
        "phase": "failed",
        "session_id": SESSION,
        "requested_revision": 2,
        "confirmed_revision": None,
    }
    assert len(list(harness.data.glob("observation-*"))) == 1


def test_unenrolled_host_executes_stock_cli_and_refuses_unsupported_managed_resume(tmp_path):
    executable = tmp_path / "stock-codex"
    executable.write_text(
        '#!/bin/sh\nprintf "stock %s|%s|%s|%s\\n" "$PWD" "$FIXTURE_MCP" "$1" "$2"\nexit 13\n'
    )
    executable.chmod(0o700)
    env = dict(
        os.environ,
        STACKOS_DATA_DIR=str(tmp_path / "data"),
        STACKOS_CODEX_BIN=str(executable),
        FIXTURE_MCP="unchanged",
    )
    command = [sys.executable, "-E", "-m", "stackos", "harness", "run", "codex"]
    with foreground_terminal(
        [*command, "--", "--mcp-config", "file with spaces"], tmp_path, env
    ) as term:
        expected = f"stock {tmp_path.resolve()}|unchanged|--mcp-config|file with spaces\r\n"
        term.read_until(expected.encode())
        assert term.exit_code() == 13
    with foreground_terminal([*command, "--session", SESSION], tmp_path, env) as term:
        term.read_until(b"Managed exact-session launch is unavailable")
        assert term.exit_code() == 1
    no_tty = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, timeout=8)
    assert no_tty.returncode == 1
    assert b"foreground of an interactive terminal" in no_tty.stderr
    assert not no_tty.stdout
