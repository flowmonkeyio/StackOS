"""Connected service startup through the existing CLI/install owners."""

from __future__ import annotations

import io
import json
import os
import plistlib
import sys
import types
from pathlib import Path

import pytest
import typer
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from stackos.cli import app, daemon_commands, daemon_processes, launchd
from stackos.config import Settings, get_settings
from stackos.host_mcp.bridge import resolve_bridge_command
from stackos.local_runtime.connection import LocalBinding, LocalConnectionStore
from stackos.local_runtime.service import create_app


@pytest.fixture
def selected(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("STACKOS_HOME", str(home))
    env_file = tmp_path / "runtime.env"
    env_file.write_text(
        "STACKOS_PROCESS_ROLE=runtime\nSTACKOS_ENVIRONMENT_ID=startup-proof\n"
        "STACKOS_SERVER_URL=http://127.0.0.1:19810\nSTACKOS_HOST=127.0.0.1\n"
        f"STACKOS_PORT=19811\nSTACKOS_DATA_DIR={tmp_path / 'data'}\n"
        f"STACKOS_STATE_DIR={tmp_path / 'state'}\n"
    )
    env_file.chmod(0o600)
    monkeypatch.setenv("STACKOS_ENV_FILE", str(env_file))
    return env_file


def test_serve_preserves_selection_and_uses_execution_app(selected, monkeypatch):
    settings = get_settings()
    calls = []

    def run(target, **kwargs):
        calls.append(target)
        assert target == "stackos.local_runtime.service:create_app"
        assert kwargs["port"] == 19811
        assert get_settings() == settings
        assert daemon_processes._read_pid_file(settings.pid_path) == os.getpid()
        assert (
            json.loads(settings.pid_path.read_text())["context"]["environment_id"]
            == "startup-proof"
        )

    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=run))
    result = CliRunner().invoke(app, ["serve"], catch_exceptions=False)
    assert result.exit_code == 0 and len(calls) == 1
    assert not settings.pid_path.exists()
    assert not (settings.data_dir / "stackos.db").exists()


def test_desktop_configuration_read_is_safe_and_creates_no_state(selected):
    result = CliRunner().invoke(app, ["runtime-config"], catch_exceptions=False)
    assert result.exit_code == 0
    config = json.loads(result.output)
    assert config["process_role"] == "runtime" and config["port"] == 19811
    assert config["server_url"] == "http://127.0.0.1:19810"
    assert set(config) == {
        "process_role",
        "environment_id",
        "server_url",
        "host",
        "port",
        "state_dir",
    }
    assert not get_settings().state_dir.exists()
    assert not get_settings().data_dir.exists()


@pytest.mark.parametrize("command", ["serve", "start", "stop", "restart"])
def test_selected_listener_cannot_be_overridden(selected, command, monkeypatch):
    monkeypatch.setattr(
        daemon_processes,
        "_discover_daemon_processes",
        lambda *_: pytest.fail("must reject before inspecting or controlling another listener"),
    )
    result = CliRunner().invoke(app, [command, "--port", "19812"])
    assert result.exit_code == 2
    assert "cannot be overridden" in result.output


def test_detached_spawn_keeps_file_without_mixed_overrides(selected, monkeypatch):
    settings = get_settings()
    observed = []
    monkeypatch.setattr(daemon_processes, "_tcp_can_connect", lambda *a, **kw: False)
    monkeypatch.setattr(daemon_processes, "_wait_for_daemon", lambda *a, **kw: True)

    def spawn(args, **kwargs):
        observed.append(kwargs["env"])
        assert args[3:] == [
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "19811",
            "--log-level",
            "INFO",
        ]
        return types.SimpleNamespace(pid=123)

    monkeypatch.setattr(daemon_processes.subprocess, "Popen", spawn)
    ok, _ = daemon_processes._spawn_detached_daemon(
        settings,
        settings.host,
        settings.port,
        log_level="INFO",
        log_path=settings.log_path,
        cwd=selected.parent,
    )
    assert ok
    assert observed[0]["STACKOS_ENV_FILE"] == str(selected)
    assert not any(f"STACKOS_{name.upper()}" in observed[0] for name in Settings.model_fields)


def test_launchd_roundtrip_matches_exact_selected_file(selected, monkeypatch):
    settings = get_settings()
    home = Path.home()
    payload = launchd._launchd_plist_content(
        settings, home=home, host=settings.host, port=settings.port, log_level="INFO"
    )
    environment = plistlib.loads(payload)["EnvironmentVariables"]
    assert environment["STACKOS_ENV_FILE"] == str(selected)
    assert not any(f"STACKOS_{name.upper()}" in environment for name in Settings.model_fields)
    target = launchd._launchd_plist_path(home)
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)
    assert launchd._launchd_context_ownership(
        home, settings=settings, host=settings.host, port=settings.port
    ).matches
    other = selected.with_name("other.env")
    other.write_bytes(selected.read_bytes())
    other.chmod(0o600)
    monkeypatch.setenv("STACKOS_ENV_FILE", str(other))
    assert not launchd._launchd_context_ownership(
        home, settings=get_settings(), host=settings.host, port=settings.port
    ).matches


def test_host_registration_preserves_file_and_host_label(selected):
    command = resolve_bridge_command(runtime="codex")
    assert command[-4:] == ["--env-file", str(selected), "--runtime", "codex"]
    assert "--state-dir" not in command and "--port" not in command
    selected.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        resolve_bridge_command(runtime="codex")


def test_real_host_config_write_preserves_sibling_tools(selected, monkeypatch):
    from stackos.host_mcp.adapters import claude_desktop

    settings = get_settings()
    settings.ensure_dirs()
    settings.token_path.write_text("synthetic-local-token")
    settings.token_path.chmod(0o600)
    target = claude_desktop.config_path(Path.home())
    target.parent.mkdir(parents=True)
    sibling = {"command": "existing-native-tool", "env": {"LOCAL_VALUE": "preserve"}}
    target.write_text(
        json.dumps({"mcpServers": {"existing": sibling}, "preferences": {"keep": True}})
    )
    monkeypatch.setattr(claude_desktop, "_available", lambda *_: True)
    monkeypatch.setattr(claude_desktop, "_claude_desktop_running", lambda: "not_running")
    result = claude_desktop.register(Path.home())
    assert result.ok, result.message
    saved = json.loads(target.read_text())
    assert saved["mcpServers"]["existing"] == sibling
    assert saved["preferences"] == {"keep": True}
    managed = saved["mcpServers"]["stackos"]
    assert managed["args"][-4:] == ["--env-file", str(selected), "--runtime", "claude-desktop"]
    assert "synthetic-local-token" not in target.read_text()
    assert claude_desktop.register(Path.home()).ok


def test_bridge_accepts_persisted_file_and_refuses_overlay(selected, monkeypatch):
    settings = get_settings()
    settings.ensure_dirs()
    settings.token_path.write_text("synthetic-local-token")
    settings.token_path.chmod(0o600)
    monkeypatch.delenv("STACKOS_ENV_FILE")
    observed = []

    def proxy(**kwargs):
        observed.append(kwargs)
        return types.SimpleNamespace()

    monkeypatch.setattr(daemon_commands, "AgentBridgeProxy", proxy)
    result = CliRunner().invoke(app, ["mcp-bridge", "--env-file", str(selected)], input="")
    assert result.exit_code == 0, result.output
    assert observed[0]["url"] == "http://127.0.0.1:19811/mcp"
    assert observed[0]["headers"]["Authorization"] == "Bearer synthetic-local-token"
    result = CliRunner().invoke(app, ["mcp-bridge", "--env-file", str(selected), "--port", "19812"])
    assert result.exit_code == 2 and "cannot be mixed" in result.output


def test_runtime_app_has_no_canonical_routes_database_or_scheduler(selected):
    settings = get_settings()
    from stackos.server import create_app as public_factory

    application = public_factory(settings)
    with TestClient(application) as client:
        response = client.get("/api/v1/health")
        assert response.json() == {
            "status": "ok",
            "process_role": "runtime",
            "environment_id": "startup-proof",
            "server_url": settings.server_url,
            "connection_state": "not_connected",
        }
        for route in ("/", "/mcp", "/api/v1/projects", "/api/v1/auth/ui-token"):
            assert client.get(route).status_code == 404
        assert not hasattr(application.state, "engine")
        assert not hasattr(application.state, "scheduler")
    assert not (settings.data_dir / "stackos.db").exists()
    assert not settings.seed_path.exists() and not settings.token_path.exists()


def test_foreign_saved_binding_refuses_startup_without_rebinding(selected):
    settings = get_settings()
    settings.ensure_dirs()
    folder = selected.parent / "folder"
    folder.mkdir()
    stat = folder.stat()
    binding = LocalBinding(
        server_url=settings.server_url,
        environment_id="another-environment",
        user_id="user",
        project_id=1,
        connection_id="computer",
        credential="synthetic-computer-token",
        root=str(folder),
        root_device=stat.st_dev,
        root_inode=stat.st_ino,
    )
    store = LocalConnectionStore(settings.state_dir)
    with store.reserve() as stream:
        stream.write(binding.model_dump_json())
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="differs"), TestClient(create_app(settings)):
        pass
    assert store.path.read_bytes() == before


def test_runtime_install_keeps_native_setup_and_skips_application_bootstrap(selected, monkeypatch):
    import stackos.db.migrate as migrations
    import stackos.install as installer

    settings = get_settings()
    native = Path.home() / "native-subscription-settings"
    native.write_text("preserve existing tools and credentials")
    for name in ("copy_skills", "copy_plugins", "ensure_browser_launcher", "ensure_gstack_runtime"):
        monkeypatch.setattr(
            installer, name, lambda *a, **kw: pytest.fail("native setup must be preserved")
        )
    monkeypatch.setattr(
        migrations, "upgrade_to_head", lambda *_: pytest.fail("no application database")
    )
    observed = []
    monkeypatch.setattr(
        installer,
        "repair_mcp_hosts",
        lambda **kw: (observed.append(resolve_bridge_command(runtime="codex")) or True, []),
    )
    result = CliRunner().invoke(app, ["install", "--skip-doctor"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert len(observed) == 1 and str(selected) in observed[0]
    assert not settings.seed_path.exists() and not (settings.data_dir / "stackos.db").exists()
    assert native.read_text() == "preserve existing tools and credentials"


def test_runtime_doctor_does_not_probe_local_application_state(selected, monkeypatch):
    monkeypatch.setattr(daemon_processes, "_daemon_health_ok", lambda *a, **kw: True)
    result = CliRunner().invoke(app, ["doctor", "--json"], catch_exceptions=False)
    payload = json.loads(result.output)
    assert payload["ok"] and payload["info"]["process_role"] == "runtime"
    assert payload["info"]["application_database"] is False
    assert payload["info"]["workflow_scheduler"] is False


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"process_role": "standalone"},
        {"environment_id": "foreign"},
        {"server_url": "http://127.0.0.1:19812"},
    ],
)
def test_runtime_health_requires_selected_identity(selected, monkeypatch, change):
    import urllib.request

    body = {
        "process_role": "runtime",
        "environment_id": "startup-proof",
        "server_url": "http://127.0.0.1:19810",
    } | change
    response = io.BytesIO(json.dumps(body).encode())
    response.status = 200
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: response)
    assert daemon_processes._daemon_health_ok("127.0.0.1", 19811) is (not change)


@pytest.mark.parametrize("replaced", [False, True])
def test_stop_cleans_only_the_observed_terminated_pid(selected, monkeypatch, replaced):
    settings = get_settings()
    settings.ensure_dirs()
    settings.pid_path.write_text("1234\n")
    monkeypatch.setattr(
        daemon_commands, "_launchd_plist_for_requested_context", lambda *a, **kw: None
    )
    monkeypatch.setattr(daemon_processes, "_discover_daemon_processes", lambda *a: ([1234], []))

    def terminate(*args, **kwargs):
        if replaced:
            settings.pid_path.write_text("5678\n")
        return True, "owned process termination observed"

    monkeypatch.setattr(daemon_processes, "_terminate_daemon_processes", terminate)
    result = CliRunner().invoke(app, ["stop"], catch_exceptions=False)
    assert result.exit_code == 0
    if replaced:
        assert settings.pid_path.read_text() == "5678\n"
    else:
        assert not settings.pid_path.exists()


def test_runtime_discovery_refuses_foreign_listener_without_selected_pid(selected, monkeypatch):
    settings = get_settings()
    settings.ensure_dirs()
    monkeypatch.setattr(daemon_processes, "_listener_pids", lambda *_: [12345])
    monkeypatch.setattr(daemon_processes, "_pid_command", lambda *_: "python -m stackos serve")
    monkeypatch.setattr(daemon_processes, "_pid_is_running", lambda *_: True)
    assert daemon_processes._discover_daemon_processes(settings, settings.port) == ([], [12345])


@pytest.mark.parametrize("command", ["stop", "restart"])
def test_foreign_runtime_blocks_launchd_control(selected, monkeypatch, command):
    monkeypatch.setattr(
        daemon_commands, "_launchd_plist_for_requested_context", lambda *a, **kw: selected
    )
    monkeypatch.setattr(launchd, "_loaded_launchd_plist", lambda *_: selected)
    monkeypatch.setattr(launchd, "_launchd_loaded", lambda: (True, "loaded"))
    monkeypatch.setattr(daemon_processes, "_discover_daemon_processes", lambda *a: ([], [12345]))
    monkeypatch.setattr(launchd, "_launchd_bootout", lambda *a, **kw: pytest.fail("foreign unload"))
    monkeypatch.setattr(
        daemon_processes,
        "_terminate_daemon_processes",
        lambda *a, **kw: pytest.fail("foreign signal"),
    )
    monkeypatch.setattr(
        daemon_processes, "_spawn_detached_daemon", lambda *a, **kw: pytest.fail("foreign takeover")
    )
    result = CliRunner().invoke(app, [command, "--force"], catch_exceptions=False)
    assert result.exit_code != 0


def _owned_runtime(selected, monkeypatch):
    settings = get_settings()
    settings.ensure_dirs()
    monkeypatch.setattr(daemon_processes, "_listener_pids", lambda *_: [12345])
    monkeypatch.setattr(daemon_processes, "_pid_command", lambda *_: "python -m stackos serve")
    monkeypatch.setattr(daemon_processes, "_pid_is_running", lambda *_: True)
    monkeypatch.setattr(daemon_processes, "_pid_start_time", lambda *_: "process-birth-A")
    daemon_processes._write_pid_file(settings.pid_path, 12345, settings=settings)
    return settings


@pytest.mark.parametrize(
    "mismatch",
    [
        "environment_id",
        "server_url",
        "data_dir",
        "state_dir",
        "env_file",
        "process_birth",
        "legacy_pid",
        "missing_birth",
        "unknown_command",
    ],
)
def test_runtime_ownership_refuses_foreign_or_uncertain_identity(selected, monkeypatch, mismatch):
    settings = _owned_runtime(selected, monkeypatch)
    assert daemon_processes._discover_daemon_processes(settings, settings.port) == ([12345], [])
    record = json.loads(settings.pid_path.read_text())
    if mismatch in record["context"]:
        record["context"][mismatch] = "different-selected-context"
    elif mismatch == "process_birth":
        monkeypatch.setattr(daemon_processes, "_pid_start_time", lambda *_: "process-birth-B")
    elif mismatch == "missing_birth":
        record["process_started_at"] = None
    elif mismatch == "legacy_pid":
        record = 12345
    else:
        monkeypatch.setattr(daemon_processes, "_pid_command", lambda *_: None)
    settings.pid_path.write_text(json.dumps(record))
    assert daemon_processes._discover_daemon_processes(settings, settings.port) == ([], [12345])


def test_owned_wedged_runtime_can_restart_without_health(selected, monkeypatch):
    import signal

    _owned_runtime(selected, monkeypatch)
    signals = []
    monkeypatch.setattr(daemon_processes.os, "kill", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(daemon_processes, "_wait_for_pids_to_exit", lambda *a, **kw: True)
    monkeypatch.setattr(
        daemon_processes,
        "_daemon_health_ok",
        lambda *a, **kw: pytest.fail("wedged health is not ownership"),
    )
    monkeypatch.setattr(
        daemon_processes,
        "_spawn_detached_daemon",
        lambda *a, **kw: (True, "started selected runtime"),
    )
    result = CliRunner().invoke(app, ["restart"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert signals == [(12345, signal.SIGTERM)]


def test_reused_runtime_pid_is_rechecked_before_signals(selected, monkeypatch):
    settings = _owned_runtime(selected, monkeypatch)
    assert daemon_processes._runtime_process_owned(settings, 12345)
    monkeypatch.setattr(daemon_processes, "_pid_start_time", lambda *_: "reused-process")
    monkeypatch.setattr(
        daemon_processes.os, "kill", lambda *a: pytest.fail("foreign process signaled")
    )
    ok, message = daemon_processes._terminate_daemon_processes(
        [12345], timeout=0, force=True, settings=settings
    )
    assert not ok and "ownership" in message


def test_runtime_force_stop_rechecks_process_after_wait(selected, monkeypatch):
    import signal

    settings = _owned_runtime(selected, monkeypatch)
    signals = []
    monkeypatch.setattr(daemon_processes.os, "kill", lambda pid, sig: signals.append((pid, sig)))

    def wait(*args, **kwargs):
        monkeypatch.setattr(daemon_processes, "_pid_start_time", lambda *_: "reused-process")
        return False

    monkeypatch.setattr(daemon_processes, "_wait_for_pids_to_exit", wait)
    ok, message = daemon_processes._terminate_daemon_processes(
        [12345], timeout=0, force=True, settings=settings
    )
    assert not ok and "identity changed" in message
    assert signals == [(12345, signal.SIGTERM)]


@pytest.mark.parametrize("command", ["stop", "restart"])
def test_foreign_loaded_job_is_not_unloaded_for_owned_listener(selected, monkeypatch, command):
    _owned_runtime(selected, monkeypatch)
    monkeypatch.setattr(
        daemon_commands, "_launchd_plist_for_requested_context", lambda *a, **kw: selected
    )
    monkeypatch.setattr(launchd, "_loaded_launchd_plist", lambda *_: selected)
    monkeypatch.setattr(launchd, "_launchd_loaded", lambda: (True, "job = {\n\tpid = 67890\n}"))
    monkeypatch.setattr(
        launchd, "_launchd_bootout", lambda *a, **kw: pytest.fail("foreign job unload")
    )
    monkeypatch.setattr(
        daemon_processes,
        "_terminate_daemon_processes",
        lambda *a, **kw: pytest.fail("foreign signal"),
    )
    result = CliRunner().invoke(app, [command], catch_exceptions=False)
    assert result.exit_code != 0 and "job ownership is uncertain" in result.output


@pytest.mark.parametrize("reported", ["pid = 12345", "missing", "pid = 12345\npid = 67890"])
def test_runtime_launchd_control_requires_one_confirmed_owned_job_pid(
    selected, monkeypatch, reported
):
    settings = _owned_runtime(selected, monkeypatch)
    monkeypatch.setattr(launchd, "_launchd_loaded", lambda: (True, reported))
    if reported == "pid = 12345":
        daemon_commands._require_runtime_launchd_process(settings, [12345])
    else:
        with pytest.raises(typer.Exit) as exc:
            daemon_commands._require_runtime_launchd_process(settings, [12345])
        assert exc.value.exit_code == 1
