from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest
from typer.testing import CliRunner

import stackos.cli as cli_module
from stackos.cli import app
from stackos.config import Settings


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    data = home / ".local" / "share" / "stackos"
    state = home / ".local" / "state" / "stackos"
    state.mkdir(parents=True)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("STACKOS_HOME", str(home))
    monkeypatch.setenv("STACKOS_DATA_DIR", str(data))
    monkeypatch.setenv("STACKOS_STATE_DIR", str(state))
    return home


def test_serve_writes_and_removes_pid_file(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid_path = sandbox / ".local" / "state" / "stackos" / "daemon.pid"

    def fake_run(*_args: object, **kwargs: object) -> None:
        assert pid_path.read_text(encoding="utf-8") == f"{os.getpid()}\n"
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["port"] == 5199

    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=fake_run))

    result = CliRunner().invoke(
        app,
        ["serve", "--host", "127.0.0.1", "--port", "5199"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.stdout
    assert not pid_path.exists()


def test_discover_daemon_processes_classifies_listener_pids(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        data_dir=sandbox / ".local" / "share" / "stackos",
        state_dir=sandbox / ".local" / "state" / "stackos",
    )
    settings.ensure_dirs()
    settings.pid_path.write_text("123\n", encoding="utf-8")

    commands = {
        123: f"{sys.executable} -m stackos serve --port 5180",
        456: "/usr/bin/python3 -m something_else serve --port 5180",
    }
    monkeypatch.setattr(cli_module, "_listener_pids", lambda _port: [123, 456])
    monkeypatch.setattr(cli_module, "_pid_command", lambda pid: commands.get(pid))
    monkeypatch.setattr(cli_module, "_pid_is_running", lambda _pid: True)

    daemons, blockers = cli_module._discover_daemon_processes(settings, 5180)

    assert daemons == [123]
    assert blockers == [456]


def test_wait_for_daemon_uses_health_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int, float]] = []

    def fake_health(host: str, port: int, *, timeout: float) -> bool:
        calls.append((host, port, timeout))
        return len(calls) == 2

    monkeypatch.setattr(cli_module, "_daemon_health_ok", fake_health)
    monkeypatch.setattr(cli_module.time, "sleep", lambda _seconds: None)

    assert cli_module._wait_for_daemon("127.0.0.1", 5180, timeout=1.0) is True
    assert calls == [("127.0.0.1", 5180, 0.25), ("127.0.0.1", 5180, 0.25)]


def test_cli_restart_stops_existing_daemon_and_starts_new(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []

    def fake_terminate(
        pids: list[int],
        *,
        timeout: float,
        force: bool,
    ) -> tuple[bool, str]:
        events.append(("terminate", {"pids": pids, "timeout": timeout, "force": force}))
        return True, "stopped daemon pid(s): 111"

    def fake_spawn(
        _settings: Settings,
        host: str,
        port: int,
        *,
        log_level: str,
        log_path: Path,
        cwd: Path,
        ready_timeout: float,
    ) -> tuple[bool, str]:
        events.append(
            (
                "spawn",
                {
                    "host": host,
                    "port": port,
                    "log_level": log_level,
                    "log_path": log_path,
                    "cwd": cwd,
                    "ready_timeout": ready_timeout,
                },
            )
        )
        return True, "started daemon pid=222; url=http://127.0.0.1:5180; log=/tmp/daemon.log"

    monkeypatch.setattr(cli_module, "_discover_daemon_processes", lambda *_args: ([111], []))
    monkeypatch.setattr(cli_module, "_terminate_daemon_processes", fake_terminate)
    monkeypatch.setattr(cli_module, "_spawn_detached_daemon", fake_spawn)

    result = CliRunner().invoke(
        app,
        ["restart", "--timeout", "0.5"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.stdout
    assert "stopped daemon pid(s): 111" in result.stdout
    assert "started daemon pid=222" in result.stdout
    assert events[0] == ("terminate", {"pids": [111], "timeout": 0.5, "force": False})
    assert events[1][0] == "spawn"


def test_cli_restart_refuses_non_stackos_listener(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = sandbox
    monkeypatch.setattr(cli_module, "_discover_daemon_processes", lambda *_args: ([], [999]))

    result = CliRunner().invoke(app, ["restart"])

    assert result.exit_code == 1
    assert "non-StackOS process pid(s): 999" in result.stderr
