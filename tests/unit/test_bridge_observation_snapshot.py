"""Retained producer evidence is neither a host outcome nor permission to retry."""

import io
import json
import os
import subprocess
import sys
import time

import pytest
from typer.testing import CliRunner

import stackos.cli.daemon_commands as cli
import stackos.cli.doctor_commands as doctor
from stackos.cli import app
from stackos.config import Settings
from stackos.mcp.bridge.diagnostics import (
    BridgeLifecycle,
    _build_identity,
    bridge_diagnostic_snapshot,
)

ATTEMPT = "a" * 32
PRIVATE = "token-private-request-body-and-secret-run-capability"


def test_pending_then_late_response_and_restart_are_not_host_completion(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    settings.ensure_dirs()
    settings.token_path.write_text(PRIVATE)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setenv("STACKOS_BRIDGE_ATTEMPT_ID", ATTEMPT)
    monkeypatch.setattr(cli, "_mcp_bridge_workspace_hints", lambda *a, **k: {})
    snapshots = []

    class Proxy:
        def __init__(self, **kwargs):
            assert kwargs["client_session_id"] == f"stackos-bridge:{ATTEMPT}"

        def handle(self, *args, **kwargs):
            # The receipt exists while handling is still in progress. A consumer
            # observation deadline could already have expired; it grants no retry.
            pending = bridge_diagnostic_snapshot(settings.bridge_log_path, ATTEMPT)
            assert pending["last_confirmed_stage"] == "initialize"
            assert pending["awaiting_evidence"] == "daemon_initialize_response"
            assert pending["execution_outcome"] == "unresolved"
            snapshots.append(pending)
            return '{"jsonrpc":"2.0","id":1,"result":{}}'

    monkeypatch.setattr(cli, "AgentBridgeProxy", Proxy)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"id":1,"method":"initialize"}\n'))
    monkeypatch.setattr("sys.stdout", io.StringIO())
    monkeypatch.setattr("sys.stderr", io.StringIO())
    cli.mcp_bridge()
    saved = bridge_diagnostic_snapshot(settings.bridge_log_path, ATTEMPT)
    assert saved["last_confirmed_stage"] == "cleanup_reached"
    assert saved["process_termination"] == "unobserved"
    assert saved["execution_outcome"] == "unresolved"
    assert any(e["event"].endswith("startup_completed") for e in saved["events"])
    assert all(saved[k] is None for k in ("host_request_id", "host_thread_id", "host_turn_id"))
    assert PRIVATE not in json.dumps(saved)
    # A fresh process recovers exactly the same observations without a daemon.
    script = (
        "import json,sys; from pathlib import Path; "
        "from stackos.mcp.bridge.diagnostics import bridge_diagnostic_snapshot; "
        "print(json.dumps(bridge_diagnostic_snapshot(Path(sys.argv[1]),sys.argv[2])))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(settings.bridge_log_path), ATTEMPT],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout)["events"] == saved["events"]
    assert len(snapshots) == 1


@pytest.mark.parametrize("sink_failure", ["write", "flush"])
def test_lost_acknowledgment_retains_failure_without_replay(tmp_path, monkeypatch, sink_failure):
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    settings.ensure_dirs()
    settings.token_path.write_text(PRIVATE)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setenv("STACKOS_BRIDGE_ATTEMPT_ID", ATTEMPT)
    monkeypatch.setattr(cli, "_mcp_bridge_workspace_hints", lambda *a, **k: {})
    handled = []

    class Proxy:
        def __init__(self, **kwargs):
            pass

        def handle(self, *args, **kwargs):
            handled.append(1)
            return '{"jsonrpc":"2.0","id":1,"result":{}}'

    class Sink(io.StringIO):
        def write(self, value):
            if sink_failure == "write":
                raise BrokenPipeError(PRIVATE)
            return super().write(value)

        def flush(self):
            if sink_failure == "flush":
                raise BrokenPipeError(PRIVATE)

    monkeypatch.setattr(cli, "AgentBridgeProxy", Proxy)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"id":1,"method":"initialize"}\n'))
    monkeypatch.setattr("sys.stdout", Sink())
    monkeypatch.setattr("sys.stderr", io.StringIO())
    with pytest.raises(cli.typer.Exit):
        cli.mcp_bridge()
    snapshot = bridge_diagnostic_snapshot(settings.bridge_log_path, ATTEMPT)
    assert handled == [1]
    failures = [e for e in snapshot["events"] if "failure_code" in e]
    assert failures[-1]["phase"] == "response_write"
    assert failures[-1]["failure_code"] == "broken_pipe"
    assert snapshot["execution_outcome"] == "unresolved"
    assert not any(e["event"].endswith("startup_completed") for e in snapshot["events"])


@pytest.mark.parametrize("bad", [PRIVATE, [PRIVATE], {"secret": PRIVATE}, None, 123])
def test_export_reconstructs_allowlisted_fields_only(tmp_path, bad):
    log = tmp_path / "mcp-bridge.log"
    event = {
        "event": "stackos.mcp_bridge.lifecycle",
        "attempt_id": ATTEMPT,
        "observed_at": "2026-10-23T23:00:00Z",
        "phase": bad,
        "original": {"code": bad},
        "build": {"version": bad, "build_id": bad},
        "client_session_id": PRIVATE,
        "body": PRIVATE,
        "host_thread_id": PRIVATE,
        "sequence": bad,
    }
    log.write_text(json.dumps(event) + "\n")
    result = bridge_diagnostic_snapshot(log, ATTEMPT)
    assert PRIVATE not in json.dumps(result)
    assert result["host_thread_id"] is None


def test_bounded_rotations_corruption_and_symlink_are_disclosed(tmp_path):
    log = tmp_path / "mcp-bridge.log"
    producer = BridgeLifecycle(ATTEMPT, time.monotonic())
    producer.log_path = log
    producer.stage("initialize")
    log.rename(log.with_name(log.name + ".1"))
    log.write_text("x" * (1024 * 1024 + 100) + "\n{" + PRIVATE)
    target = tmp_path / "unrelated"
    target.write_text(PRIVATE)
    log.with_name(log.name + ".2").symlink_to(target)
    result = bridge_diagnostic_snapshot(log, ATTEMPT)
    assert result["evidence_status"] == "partial"
    assert result["last_confirmed_stage"] == "initialize"
    assert PRIVATE not in json.dumps(result)
    assert target.read_text() == PRIVATE


def test_attempt_only_doctor_avoids_health_credentials_and_registration(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    monkeypatch.setattr(doctor, "get_settings", lambda: settings)
    monkeypatch.setattr(doctor, "get_state_dir", lambda: settings.state_dir)

    def forbidden(*args, **kwargs):
        pytest.fail("attempt snapshot must not inspect unrelated state or start a probe")

    monkeypatch.setattr(doctor.daemon_processes, "_tcp_can_connect", forbidden)
    monkeypatch.setattr(doctor, "_check_credentials_decrypt", forbidden)
    monkeypatch.setattr(doctor, "_check_mcp_hosts", forbidden)
    result = CliRunner().invoke(app, ["doctor", "--json", "--bridge-attempt", ATTEMPT])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["info"]["bridge_diagnostics"]["evidence_status"] == "not_found"
    invalid = CliRunner().invoke(app, ["doctor", "--bridge-attempt", PRIVATE])
    assert invalid.exit_code == 2
    assert PRIVATE not in invalid.stdout + invalid.stderr


def test_snapshot_survives_deep_json_and_invalid_runtime_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKOS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("STACKOS_BRIDGE_STARTUP_TIMEOUT_SECONDS", "not-a-number")
    (tmp_path / "mcp-bridge.log").write_text("[" * 1500 + "0" + "]" * 1500 + "\n")
    result = CliRunner().invoke(app, ["doctor", "--json", "--bridge-attempt", ATTEMPT])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["info"]["bridge_diagnostics"]["evidence_status"] == "partial"


@pytest.mark.parametrize("kind", ["fifo", "symlink", "deep_json"])
def test_build_identity_read_cannot_wait_on_special_file_or_leak(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(sys, "prefix", str(tmp_path / ".venv"))
    build = tmp_path / "build-info.json"
    if kind == "fifo":
        os.mkfifo(build)
    elif kind == "symlink":
        private = tmp_path / "private"
        private.write_text(PRIVATE)
        build.symlink_to(private)
    else:
        build.write_text("[" * 1500 + "0" + "]" * 1500)
    start = time.monotonic()
    identity = _build_identity()
    assert time.monotonic() - start < 1
    assert identity["build_id"] is None
    assert PRIVATE not in json.dumps(identity)
