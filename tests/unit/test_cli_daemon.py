from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest
from typer.testing import CliRunner

import stackos.cli.daemon_commands as daemon_cli
import stackos.cli.daemon_processes as daemon_processes
import stackos.cli.launchd as launchd_cli
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
        # Callback query values must never enter Uvicorn's access log. The
        # application-owned timing logger records method/path only.
        assert kwargs["access_log"] is False

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
    monkeypatch.setattr(daemon_processes, "_listener_pids", lambda _port: [123, 456])
    monkeypatch.setattr(daemon_processes, "_pid_command", lambda pid: commands.get(pid))
    monkeypatch.setattr(daemon_processes, "_pid_is_running", lambda _pid: True)

    daemons, blockers = daemon_processes._discover_daemon_processes(settings, 5180)

    assert daemons == [123]
    assert blockers == [456]


def test_discover_daemon_processes_removes_stale_pid_file(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        data_dir=sandbox / ".local" / "share" / "stackos",
        state_dir=sandbox / ".local" / "state" / "stackos",
    )
    settings.ensure_dirs()
    settings.pid_path.write_text("123\n", encoding="utf-8")

    monkeypatch.setattr(daemon_processes, "_listener_pids", lambda _port: [])
    monkeypatch.setattr(daemon_processes, "_pid_is_running", lambda _pid: False)

    daemons, blockers = daemon_processes._discover_daemon_processes(settings, 5180)

    assert daemons == []
    assert blockers == []
    assert not settings.pid_path.exists()


def test_wait_for_daemon_uses_health_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int, float]] = []

    def fake_health(host: str, port: int, *, timeout: float) -> bool:
        calls.append((host, port, timeout))
        return len(calls) == 2

    monkeypatch.setattr(daemon_processes, "_daemon_health_ok", fake_health)
    monkeypatch.setattr(daemon_processes.time, "sleep", lambda _seconds: None)

    assert daemon_processes._wait_for_daemon("127.0.0.1", 5180, timeout=1.0) is True
    assert calls == [("127.0.0.1", 5180, 0.25), ("127.0.0.1", 5180, 0.25)]


def test_mcp_bridge_workspace_hints_rejects_filesystem_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STACKOS_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

    assert daemon_cli._mcp_bridge_workspace_hints(Path("/")) == {}


@pytest.mark.parametrize("source", ["cwd", "explicit", "env", "claude"])
def test_mcp_bridge_workspace_hints_rejects_app_bundle_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    app_resources = tmp_path / "StackOS.app" / "Contents" / "Resources"
    app_resources.mkdir(parents=True)
    safe_root = tmp_path / "safe-project"
    safe_root.mkdir()
    monkeypatch.delenv("STACKOS_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    cwd = app_resources if source == "cwd" else safe_root
    explicit_root = app_resources if source == "explicit" else None
    if source == "env":
        monkeypatch.setenv("STACKOS_WORKSPACE_ROOT", str(app_resources))
    if source == "claude":
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(app_resources))

    hints = daemon_cli._mcp_bridge_workspace_hints(cwd, workspace_root=explicit_root)

    assert hints == {}


def test_mcp_bridge_workspace_hints_prefers_claude_project_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "site"
    project_dir.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.setattr(daemon_cli, "_git_output", lambda *_args: None)

    hints = daemon_cli._mcp_bridge_workspace_hints(Path("/"))

    assert hints["cwd"] == str(project_dir.resolve())
    assert hints["repo_fingerprint"].startswith("path:")


def test_mcp_bridge_workspace_hints_prefers_explicit_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explicit_root = tmp_path / "chosen-workspace"
    explicit_root.mkdir()
    claude_root = tmp_path / "claude-workspace"
    claude_root.mkdir()
    monkeypatch.setenv("STACKOS_WORKSPACE_ROOT", str(claude_root))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(claude_root))
    monkeypatch.setattr(daemon_cli, "_git_output", lambda *_args: None)

    hints = daemon_cli._mcp_bridge_workspace_hints(
        Path("/"),
        workspace_root=explicit_root,
    )

    assert hints["cwd"] == str(explicit_root.resolve())


def test_mcp_bridge_workspace_hints_uses_env_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "operator-selected"
    project_dir.mkdir()
    monkeypatch.setenv("STACKOS_WORKSPACE_ROOT", str(project_dir))
    monkeypatch.setattr(daemon_cli, "_git_output", lambda *_args: None)

    hints = daemon_cli._mcp_bridge_workspace_hints(Path("/"))

    assert hints["cwd"] == str(project_dir.resolve())


def test_launchd_bootout_waits_until_job_is_unloaded(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist = sandbox / "Library" / "LaunchAgents" / "com.stackos.daemon.plist"
    loaded_sequence = iter([(True, "loaded"), (True, "loaded"), (False, "not loaded")])
    events: list[list[str]] = []

    def fake_loaded() -> tuple[bool, str]:
        return next(loaded_sequence)

    def fake_launchctl(args: list[str]) -> tuple[bool, str]:
        events.append(args)
        return True, "booted out"

    monkeypatch.setattr(launchd_cli, "_launchd_loaded", fake_loaded)
    monkeypatch.setattr(launchd_cli, "_launchd_service", lambda: "gui/501/com.stackos.daemon")
    monkeypatch.setattr(launchd_cli, "_launchctl", fake_launchctl)
    monkeypatch.setattr(launchd_cli.time, "sleep", lambda _seconds: None)

    ok, message = launchd_cli._launchd_bootout(plist)

    assert ok is True
    assert message == "booted out"
    assert events == [["bootout", "gui/501/com.stackos.daemon"]]


def test_launchd_bootout_uses_caller_timeout_for_slow_launchd_handoff(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist = sandbox / "Library" / "LaunchAgents" / "com.stackos.daemon.plist"
    observed: list[float] = []

    monkeypatch.setattr(launchd_cli, "_launchd_loaded", lambda: (True, "loaded"))
    monkeypatch.setattr(launchd_cli, "_launchd_service", lambda: "gui/501/com.stackos.daemon")
    monkeypatch.setattr(launchd_cli, "_launchctl", lambda _args: (True, "booted out"))
    monkeypatch.setattr(
        launchd_cli,
        "_wait_for_launchd_unloaded",
        lambda *, timeout: observed.append(timeout) or True,
    )

    ok, message = launchd_cli._launchd_bootout(plist, wait_timeout=20.0)

    assert ok is True
    assert message == "booted out"
    assert observed == [20.0]


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

    monkeypatch.setattr(daemon_processes, "_discover_daemon_processes", lambda *_args: ([111], []))
    monkeypatch.setattr(daemon_processes, "_terminate_daemon_processes", fake_terminate)
    monkeypatch.setattr(daemon_processes, "_spawn_detached_daemon", fake_spawn)

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


def test_cli_stop_stops_existing_daemon(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict[str, object]] = []

    def fake_terminate(
        pids: list[int],
        *,
        timeout: float,
        force: bool,
    ) -> tuple[bool, str]:
        events.append({"pids": pids, "timeout": timeout, "force": force})
        return True, "stopped daemon pid(s): 111"

    monkeypatch.setattr(daemon_processes, "_discover_daemon_processes", lambda *_args: ([111], []))
    monkeypatch.setattr(daemon_processes, "_terminate_daemon_processes", fake_terminate)

    result = CliRunner().invoke(
        app,
        ["stop", "--timeout", "0.5"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.stdout
    assert "stop: stopped daemon pid(s): 111" in result.stdout
    assert events == [{"pids": [111], "timeout": 0.5, "force": False}]


def test_cli_stop_boots_out_loaded_launchd_before_stopping(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist = sandbox / "Library" / "LaunchAgents" / "com.stackos.daemon.plist"
    events: list[tuple[str, object]] = []

    def fake_bootout(path: Path, *, wait_timeout: float) -> tuple[bool, str]:
        assert wait_timeout == 0.5
        events.append(("bootout", path))
        return True, "launchd job unloaded"

    monkeypatch.setattr(launchd_cli, "_loaded_launchd_plist", lambda _home: plist)
    monkeypatch.setattr(launchd_cli, "_launchd_bootout", fake_bootout)
    monkeypatch.setattr(daemon_processes, "_discover_daemon_processes", lambda *_args: ([], []))
    monkeypatch.setattr(daemon_processes, "_tcp_can_connect", lambda *_args, **_kwargs: False)

    result = CliRunner().invoke(
        app,
        ["stop", "--timeout", "0.5"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.stdout
    assert "stop: unloaded launchd job" in result.stdout
    assert events == [("bootout", plist)]


def test_cli_stop_no_running_daemon_is_ok(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = sandbox
    monkeypatch.setattr(daemon_processes, "_discover_daemon_processes", lambda *_args: ([], []))
    monkeypatch.setattr(daemon_processes, "_tcp_can_connect", lambda *_args, **_kwargs: False)

    result = CliRunner().invoke(app, ["stop"], catch_exceptions=False)

    assert result.exit_code == 0, result.stdout
    assert "stop: no running daemon found" in result.stdout


def test_cli_restart_uses_loaded_launchd_job(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist = sandbox / "Library" / "LaunchAgents" / "com.stackos.daemon.plist"
    events: list[tuple[str, object]] = []

    def fake_bootout(path: Path, *, wait_timeout: float) -> tuple[bool, str]:
        assert wait_timeout == 0.5
        events.append(("bootout", path))
        return True, "launchd job unloaded"

    def fake_terminate(
        pids: list[int],
        *,
        timeout: float,
        force: bool,
    ) -> tuple[bool, str]:
        events.append(("terminate", {"pids": pids, "timeout": timeout, "force": force}))
        return True, "stopped daemon pid(s): 111"

    def fake_bootstrap(path: Path) -> tuple[bool, str]:
        events.append(("bootstrap", path))
        return True, "launchd job loaded"

    def fail_spawn(*_args: object, **_kwargs: object) -> tuple[bool, str]:
        raise AssertionError("launchd-owned restart should not spawn detached daemon")

    monkeypatch.setattr(launchd_cli, "_installed_launchd_plist", lambda _home: plist)
    monkeypatch.setattr(launchd_cli, "_launchd_loaded", lambda: (True, "launchd job loaded"))
    monkeypatch.setattr(launchd_cli, "_launchd_bootout", fake_bootout)
    monkeypatch.setattr(daemon_processes, "_discover_daemon_processes", lambda *_args: ([111], []))
    monkeypatch.setattr(daemon_processes, "_terminate_daemon_processes", fake_terminate)
    monkeypatch.setattr(launchd_cli, "_launchd_bootstrap", fake_bootstrap)
    monkeypatch.setattr(daemon_processes, "_wait_for_daemon", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(daemon_processes, "_spawn_detached_daemon", fail_spawn)

    result = CliRunner().invoke(
        app,
        ["restart", "--timeout", "0.5"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.stdout
    assert "restart: unloaded launchd job" in result.stdout
    assert "restart: stopped daemon pid(s): 111" in result.stdout
    assert "restart: launchd job loaded; url=http://127.0.0.1:5180" in result.stdout
    assert events == [
        ("bootout", plist),
        ("terminate", {"pids": [111], "timeout": 0.5, "force": False}),
        ("bootstrap", plist),
    ]


def test_cli_restart_ignores_stale_zombie_pid_before_launchd_bootstrap(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist = sandbox / "Library" / "LaunchAgents" / "com.stackos.daemon.plist"
    pid_path = sandbox / ".local" / "state" / "stackos" / "daemon.pid"
    pid_path.write_text("123\n", encoding="utf-8")
    events: list[tuple[str, object]] = []

    def fake_bootout(path: Path, *, wait_timeout: float) -> tuple[bool, str]:
        assert wait_timeout == 0.5
        events.append(("bootout", path))
        return True, "launchd job unloaded"

    def fake_bootstrap(path: Path) -> tuple[bool, str]:
        events.append(("bootstrap", path))
        return True, "launchd job loaded"

    def fail_terminate(*_args: object, **_kwargs: object) -> tuple[bool, str]:
        raise AssertionError("stale zombie pid must not be terminated")

    def fail_spawn(*_args: object, **_kwargs: object) -> tuple[bool, str]:
        raise AssertionError("launchd-owned restart should not spawn detached daemon")

    monkeypatch.setattr(launchd_cli, "_installed_launchd_plist", lambda _home: plist)
    monkeypatch.setattr(launchd_cli, "_launchd_loaded", lambda: (True, "launchd job loaded"))
    monkeypatch.setattr(launchd_cli, "_launchd_bootout", fake_bootout)
    monkeypatch.setattr(launchd_cli, "_launchd_bootstrap", fake_bootstrap)
    monkeypatch.setattr(daemon_processes, "_listener_pids", lambda _port: [])
    monkeypatch.setattr(daemon_processes.os, "kill", lambda _pid, _signal: None)
    monkeypatch.setattr(daemon_processes, "_pid_is_zombie", lambda _pid: True)
    monkeypatch.setattr(daemon_processes, "_tcp_can_connect", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(daemon_processes, "_terminate_daemon_processes", fail_terminate)
    monkeypatch.setattr(daemon_processes, "_wait_for_daemon", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(daemon_processes, "_spawn_detached_daemon", fail_spawn)

    result = CliRunner().invoke(
        app,
        ["restart", "--timeout", "0.5"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.stdout
    assert "restart: unloaded launchd job" in result.stdout
    assert "restart: launchd job loaded; url=http://127.0.0.1:5180" in result.stdout
    assert events == [("bootout", plist), ("bootstrap", plist)]
    assert not pid_path.exists()


def test_cli_restart_hands_off_detached_daemon_to_installed_launchd(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist = sandbox / "Library" / "LaunchAgents" / "com.stackos.daemon.plist"
    events: list[tuple[str, object]] = []
    discover_calls = 0

    def fake_discover(*_args: object) -> tuple[list[int], list[int]]:
        nonlocal discover_calls
        discover_calls += 1
        return ([111], [])

    def fake_terminate(
        pids: list[int],
        *,
        timeout: float,
        force: bool,
    ) -> tuple[bool, str]:
        events.append(("terminate", {"pids": pids, "timeout": timeout, "force": force}))
        return True, "stopped daemon pid(s): 111"

    def fake_bootout(_path: Path, **_kwargs: object) -> tuple[bool, str]:
        raise AssertionError("unloaded launchd job must not be booted out")

    def fake_bootstrap(path: Path) -> tuple[bool, str]:
        events.append(("bootstrap", path))
        return True, "launchd job loaded"

    def fail_spawn(*_args: object, **_kwargs: object) -> tuple[bool, str]:
        raise AssertionError("packaged launchd lifecycle must not spawn detached daemon")

    monkeypatch.setattr(launchd_cli, "_installed_launchd_plist", lambda _home: plist)
    monkeypatch.setattr(launchd_cli, "_launchd_loaded", lambda: (False, "not loaded"))
    monkeypatch.setattr(launchd_cli, "_launchd_bootout", fake_bootout)
    monkeypatch.setattr(daemon_processes, "_discover_daemon_processes", fake_discover)
    monkeypatch.setattr(daemon_processes, "_terminate_daemon_processes", fake_terminate)
    monkeypatch.setattr(launchd_cli, "_launchd_bootstrap", fake_bootstrap)
    monkeypatch.setattr(daemon_processes, "_wait_for_daemon", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(daemon_processes, "_spawn_detached_daemon", fail_spawn)

    result = CliRunner().invoke(
        app,
        ["restart", "--timeout", "0.5"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.stdout
    assert "restart: stopped daemon pid(s): 111" in result.stdout
    assert "restart: launchd job loaded; url=http://127.0.0.1:5180" in result.stdout
    assert discover_calls == 1
    assert events == [
        ("terminate", {"pids": [111], "timeout": 0.5, "force": False}),
        ("bootstrap", plist),
    ]


def test_cli_restart_refuses_launchd_blocker_before_bootout(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist = sandbox / "Library" / "LaunchAgents" / "com.stackos.daemon.plist"

    def fail_bootout(_path: Path, **_kwargs: object) -> tuple[bool, str]:
        raise AssertionError("restart must not unload launchd when a blocker is already known")

    monkeypatch.setattr(launchd_cli, "_installed_launchd_plist", lambda _home: plist)
    monkeypatch.setattr(launchd_cli, "_launchd_loaded", lambda: (True, "launchd job loaded"))
    monkeypatch.setattr(launchd_cli, "_launchd_bootout", fail_bootout)
    monkeypatch.setattr(daemon_processes, "_discover_daemon_processes", lambda *_args: ([], [999]))

    result = CliRunner().invoke(app, ["restart"], catch_exceptions=False)

    assert result.exit_code == 1
    assert "non-StackOS process pid(s): 999" in result.stderr


def test_cli_restart_restores_launchd_when_termination_fails(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist = sandbox / "Library" / "LaunchAgents" / "com.stackos.daemon.plist"
    events: list[tuple[str, object]] = []

    def fake_bootout(path: Path, **_kwargs: object) -> tuple[bool, str]:
        events.append(("bootout", path))
        return True, "launchd job unloaded"

    def fake_bootstrap(path: Path) -> tuple[bool, str]:
        events.append(("bootstrap", path))
        return True, "launchd job loaded"

    def fake_terminate(
        pids: list[int],
        *,
        timeout: float,
        force: bool,
    ) -> tuple[bool, str]:
        events.append(("terminate", {"pids": pids, "timeout": timeout, "force": force}))
        return False, "daemon did not stop before timeout"

    monkeypatch.setattr(launchd_cli, "_installed_launchd_plist", lambda _home: plist)
    monkeypatch.setattr(launchd_cli, "_launchd_loaded", lambda: (True, "launchd job loaded"))
    monkeypatch.setattr(launchd_cli, "_launchd_bootout", fake_bootout)
    monkeypatch.setattr(launchd_cli, "_launchd_bootstrap", fake_bootstrap)
    monkeypatch.setattr(daemon_processes, "_discover_daemon_processes", lambda *_args: ([111], []))
    monkeypatch.setattr(daemon_processes, "_terminate_daemon_processes", fake_terminate)

    result = CliRunner().invoke(
        app,
        ["restart", "--timeout", "0.5"],
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "restart: daemon did not stop before timeout" in result.stderr
    assert "restart: restored launchd job after failed restart" in result.stderr
    assert events == [
        ("bootout", plist),
        ("terminate", {"pids": [111], "timeout": 0.5, "force": False}),
        ("bootstrap", plist),
    ]


def test_cli_restart_refuses_non_stackos_listener(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = sandbox
    monkeypatch.setattr(daemon_processes, "_discover_daemon_processes", lambda *_args: ([], [999]))

    result = CliRunner().invoke(app, ["restart"])

    assert result.exit_code == 1
    assert "non-StackOS process pid(s): 999" in result.stderr
