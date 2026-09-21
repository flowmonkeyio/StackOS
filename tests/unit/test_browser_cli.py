"""A session selector must disappear before native browser command execution."""

from __future__ import annotations

import json
import os
import pty
import select
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import typer

from stackos import browser_cli

SESSION_REF = "browser-session:project-7:persisted:main"


def _selection(executable: str = "/native/browse", cwd: str = "/native/work") -> dict:
    return {
        "project_id": 7,
        "session_ref": SESSION_REF,
        "native_cli": {
            "executable": executable,
            "cwd": cwd,
            "env": {
                "BROWSE_STATE_FILE": "/selected/state.json",
                "CHROMIUM_PROFILE": "/selected/profile",
                "TERM": "selected-term",
            },
        },
    }


class _ExecCalled(BaseException):
    pass


@pytest.mark.parametrize(
    "native_argv",
    [
        [],
        ["--help"],
        ["stop"],
        ["restart"],
        ["--force-restart", "url"],
        ["future-command", "--session", "native-session", "--", "--future"],
        ["js", "'quoted' \"value\"\n$HOME `literal`", "", "ümlaut"],
    ],
)
def test_selector_only_then_exact_native_exec(monkeypatch, native_argv: list[str]) -> None:
    request = Mock(return_value=_selection())
    chdir = Mock()
    execute = Mock(side_effect=_ExecCalled)
    monkeypatch.setattr(browser_cli, "_api_request", request)
    monkeypatch.setattr(browser_cli.os, "chdir", chdir)
    monkeypatch.setattr(browser_cli.os, "execve", execute)
    monkeypatch.setenv("TERM", "ordinary-term")
    monkeypatch.setenv("BROWSE_NO_AUTOSTART", "1")
    monkeypatch.setenv("BROWSE_STATE_FILE", "/wrong/state.json")
    monkeypatch.setenv("GSTACK_HOME", "/wrong/home")
    monkeypatch.setenv("CHROMIUM_PROFILE", "/wrong/profile")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/wrong/browser")
    with pytest.raises(_ExecCalled):
        browser_cli.main(["--session", SESSION_REF, *native_argv])
    request.assert_called_once_with(
        "POST",
        "/api/v1/operations/browser.session.status/call",
        body={"arguments": {"project_id": 7, "session_ref": SESSION_REF, "response_mode": "raw"}},
    )
    chdir.assert_called_once_with("/native/work")
    executable, argv, env = execute.call_args.args
    assert executable == "/native/browse"
    assert argv == [executable, *native_argv]
    assert env["BROWSE_STATE_FILE"] == "/selected/state.json"
    assert env["CHROMIUM_PROFILE"] == "/selected/profile"
    assert env["TERM"] == "selected-term"
    assert env["HOME"] == os.environ["HOME"]
    assert not {"BROWSE_NO_AUTOSTART", "GSTACK_HOME", "PLAYWRIGHT_BROWSERS_PATH"} & env.keys()


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--help"],
        ["url"],
        ["--session"],
        ["--ref", SESSION_REF],
        ["--session", "bad-ref"],
        ["--session", "browser-session:project-07:persisted:main"],
        ["--session", "browser-session:project-7:../wrong:main"],
    ],
)
def test_invalid_selector_has_no_request_or_exec(monkeypatch, capsys, argv) -> None:
    request, execute = Mock(), Mock()
    monkeypatch.setattr(browser_cli, "_api_request", request)
    monkeypatch.setattr(browser_cli.os, "execve", execute)
    assert browser_cli.main(argv) == 2
    request.assert_not_called()
    execute.assert_not_called()
    assert "stackos.browser --session" in capsys.readouterr().err


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"data": _selection()},
        {**_selection(), "project_id": 8},
        {**_selection(), "native_cli": None},
        {**_selection(), "native_cli": {"executable": [], "cwd": "/work", "env": {}}},
    ],
)
def test_invalid_metadata_never_executes(monkeypatch, capsys, payload) -> None:
    execute = Mock()
    monkeypatch.setattr(browser_cli, "_api_request", Mock(return_value=payload))
    monkeypatch.setattr(browser_cli.os, "execve", execute)
    assert browser_cli.main(["--session", SESSION_REF, "url"]) == 1
    execute.assert_not_called()
    assert "session" in capsys.readouterr().err


def test_metadata_failure_retains_api_client_exit_without_retry(monkeypatch) -> None:
    request = Mock(side_effect=typer.Exit(code=7))
    execute = Mock()
    monkeypatch.setattr(browser_cli, "_api_request", request)
    monkeypatch.setattr(browser_cli.os, "execve", execute)
    assert browser_cli.main(["--session", SESSION_REF, "url"]) == 7
    request.assert_called_once()
    execute.assert_not_called()


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"BROWSE_STATE_FILE": "/state"},
        {"CHROMIUM_PROFILE": "/profile"},
        {"BROWSE_STATE_FILE": "relative", "CHROMIUM_PROFILE": "/profile"},
        {"BROWSE_STATE_FILE": "/state", "CHROMIUM_PROFILE": "relative"},
        {"BROWSE_STATE_FILE": "/state", "CHROMIUM_PROFILE": "/profile", "BROWSE_NO_AUTOSTART": "1"},
    ],
)
def test_context_cannot_fall_back_to_another_session_or_gate_recovery(monkeypatch, env) -> None:
    selection = _selection()
    selection["native_cli"]["env"] = env
    monkeypatch.setattr(browser_cli, "_api_request", Mock(return_value=selection))
    monkeypatch.setattr(browser_cli.os, "chdir", Mock())
    execute = Mock()
    monkeypatch.setattr(browser_cli.os, "execve", execute)
    assert browser_cli.main(["--session", SESSION_REF, "restart"]) == 1
    execute.assert_not_called()


def test_exec_failure_is_a_binding_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(browser_cli, "_api_request", Mock(return_value=_selection()))
    monkeypatch.setattr(browser_cli.os, "chdir", Mock(side_effect=FileNotFoundError("missing cwd")))
    execute = Mock()
    monkeypatch.setattr(browser_cli.os, "execve", execute)
    assert browser_cli.main(["--session", SESSION_REF, "url"]) == 1
    execute.assert_not_called()
    assert "missing cwd" in capsys.readouterr().err


def _process_command(tmp_path: Path, native_code: str, native_argv: list[str]) -> list[str]:
    native = tmp_path / "native browse"
    native.write_text(f"#!{sys.executable}\n" + native_code, encoding="utf-8")
    native.chmod(0o755)
    selection = _selection(str(native), str(tmp_path))
    # Only metadata is replaced; the real launcher executes the real native process.
    bootstrap = (
        "from stackos import browser_cli; "
        f"browser_cli._api_request = lambda *a, **kw: {selection!r}; "
        f"raise SystemExit(browser_cli.main({['--session', SESSION_REF, *native_argv]!r}))"
    )
    return [sys.executable, "-c", bootstrap]


def test_native_binary_streams_argv_cwd_and_nonzero_exit(tmp_path: Path) -> None:
    argv = ["future-command", "", "--session", "native", "--", "a\nb"]
    code = (
        "import os, sys, json\n"
        "os.write(1, json.dumps([sys.argv[1:], os.getcwd()]).encode() + b'\\n')\n"
        "os.write(1, sys.stdin.buffer.read())\n"
        "os.write(2, b'\\xffstderr\\x00\\r\\n')\n"
        "sys.exit(37)\n"
    )
    result = subprocess.run(
        _process_command(tmp_path, code, argv),
        input=b"\x00\xffstdin\r\n",
        capture_output=True,
        timeout=15,
    )
    header, binary = result.stdout.split(b"\n", 1)
    assert json.loads(header) == [argv, str(tmp_path)]
    assert binary == b"\x00\xffstdin\r\n"
    assert result.stderr == b"\xffstderr\x00\r\n"
    assert result.returncode == 37


def test_native_streams_before_exit_and_receives_signal(tmp_path: Path) -> None:
    code = "import os, signal\nos.write(1, b'ready\\n')\nsignal.pause()\n"
    with subprocess.Popen(
        _process_command(tmp_path, code, []), stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as process:
        try:
            assert process.stdout is not None
            assert select.select([process.stdout], [], [], 15)[0], "native stdout was buffered"
            assert process.stdout.readline() == b"ready\n"
            process.send_signal(signal.SIGTERM)
            assert process.wait(timeout=5) == -signal.SIGTERM
            assert process.stderr is not None
            assert process.stderr.read() == b""
        finally:
            if process.poll() is None:
                process.kill()


def test_native_inherits_terminal_fds(tmp_path: Path) -> None:
    code = "import os\nos.write(1, repr([os.isatty(i) for i in range(3)]).encode())\n"
    master, slave = pty.openpty()
    try:
        with subprocess.Popen(
            _process_command(tmp_path, code, []), stdin=slave, stdout=slave, stderr=slave
        ) as process:
            assert select.select([master], [], [], 15)[0]
            assert os.read(master, 4096) == b"[True, True, True]"
            assert process.wait(timeout=5) == 0
    finally:
        os.close(master)
        os.close(slave)
