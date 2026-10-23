"""Automatic durable receipts and bounded startup without a running daemon."""

from __future__ import annotations

import fcntl
import io
import json
import multiprocessing
import os
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from stackos.cli import app
from stackos.config import Settings, get_state_dir
from stackos.logging import append_json_log
from stackos.mcp.bridge.diagnostics import bridge_failure_response, emit_bridge_diagnostic
from stackos.mcp.bridge.proxy import AgentBridgeProxy


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKOS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("STACKOS_DATA_DIR", str(tmp_path / "data"))


def test_receipt_survives_closed_stderr_and_contains_only_safe_facts(tmp_path, monkeypatch):
    sink = io.StringIO()
    sink.close()
    monkeypatch.setattr(sys, "stderr", sink)
    receipt = emit_bridge_diagnostic(
        phase="initialize",
        started_at=time.monotonic(),
        original=httpx.ReadTimeout("SECRET"),
    )
    path = tmp_path / "state" / "mcp-bridge.log"
    assert json.loads(path.read_text()) == receipt
    assert "SECRET" not in path.read_text()
    assert receipt["persistence"]["status"] == "persisted"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_failed_persistence_is_visible_in_stderr_and_rpc_without_hiding_primary_error(
    tmp_path,
    monkeypatch,
    capsys,
):
    def fail(*args):
        raise OSError("SECRET private path")

    monkeypatch.setattr("stackos.mcp.bridge.diagnostics.append_json_log", fail)
    receipt = emit_bridge_diagnostic(
        phase="initialize",
        started_at=time.monotonic(),
        original=httpx.ReadTimeout("SECRET"),
    )
    emitted = capsys.readouterr().err
    assert "SECRET" not in emitted
    assert json.loads(emitted) == receipt
    assert receipt["original"]["code"] == "read_timeout"
    assert receipt["persistence"]["status"] == "failed"
    reply = json.loads(bridge_failure_response(1, receipt))
    assert reply["error"]["data"]["persistence"] == receipt["persistence"]
    assert reply["error"]["data"]["retry_automatically"] is False


def test_rotation_retains_bounded_complete_events(tmp_path, monkeypatch):
    monkeypatch.setattr("stackos.logging._LOG_FILE_BYTES", 80)
    path = tmp_path / "bridge.log"
    for number in range(20):
        append_json_log(path, {"number": number, "padding": "x" * 40})
    files = [path, *[Path(f"{path}.{index}") for index in range(1, 6)]]
    assert [json.loads(file.read_text())["number"] for file in files] == list(range(19, 13, -1))
    assert all(file.stat().st_size <= 80 for file in files)
    assert all(stat.S_IMODE(file.stat().st_mode) == 0o600 for file in files)
    assert not Path(f"{path}.6").exists()


def _write_events(path, worker):
    for index in range(10):
        append_json_log(Path(path), {"worker": worker, "index": index})


def test_multiple_bridge_processes_append_without_loss(tmp_path):
    path = tmp_path / "bridge.log"
    ctx = multiprocessing.get_context("spawn")
    processes = [ctx.Process(target=_write_events, args=(str(path), worker)) for worker in range(3)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join()
        assert process.exitcode == 0
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(events) == 30
    assert {(event["worker"], event["index"]) for event in events} == {
        (worker, index) for worker in range(3) for index in range(10)
    }


@pytest.mark.parametrize("target", ["log", "lock"])
def test_symlink_targets_are_rejected_without_modifying_destination(tmp_path, target):
    path = tmp_path / "bridge.log"
    destination = tmp_path / "private"
    destination.write_text("unchanged")
    link = path if target == "log" else Path(f"{path}.lock")
    link.symlink_to(destination)
    with pytest.raises(OSError):
        append_json_log(path, {"event": "safe"})
    assert destination.read_text() == "unchanged"


def test_contended_log_lock_does_not_hang_protocol(tmp_path):
    path = tmp_path / "bridge.log"
    with Path(f"{path}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            append_json_log(path, {"event": "safe"})
        assert time.monotonic() - started < 2


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "301"])
def test_invalid_timeout_rejected_but_logging_path_still_resolves(value, monkeypatch, tmp_path):
    monkeypatch.setenv("STACKOS_BRIDGE_STARTUP_TIMEOUT_SECONDS", value)
    with pytest.raises(ValidationError):
        Settings()
    assert get_state_dir() == tmp_path / "state"


def test_timeout_default_override_and_dotenv_state_resolution(tmp_path, monkeypatch):
    assert Settings().bridge_startup_timeout_seconds == 15
    monkeypatch.setenv("STACKOS_BRIDGE_STARTUP_TIMEOUT_SECONDS", "2.5")
    assert Settings().bridge_startup_timeout_seconds == 2.5
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STACKOS_STATE_DIR")
    (tmp_path / ".env").write_text(
        f"STACKOS_STATE_DIR={tmp_path / 'custom'}\nSTACKOS_PORT=invalid\n"
    )
    assert get_state_dir() == tmp_path / "custom"
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize(
    "method,tool,bounded",
    [
        ("initialize", None, True),
        ("ping", None, True),
        ("tools/list", None, True),
        ("tools/call", "workspace.resolve", True),
        ("tools/call", "action.run", False),
        ("tools/call", "toolbox.call", False),
    ],
)
def test_startup_timeouts_do_not_limit_long_running_tool_calls(method, tool, bounded):
    seen = []

    def respond(request):
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    proxy = AgentBridgeProxy(url="http://localhost/mcp", headers={}, startup_timeout_seconds=2.5)
    with httpx.Client(transport=httpx.MockTransport(respond), timeout=None) as client:
        proxy.request_daemon(
            client,
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": {"name": tool},
                }
            ),
        )
    assert seen[0]["read"] == (2.5 if bounded else None)


def test_real_stalled_initialize_times_out_once_and_persists_after_process_exit(tmp_path):
    received = []
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            release.wait(5)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = Settings()
    settings.ensure_dirs()
    settings.token_path.write_text("SECRET")
    env = {**os.environ, "STACKOS_BRIDGE_STARTUP_TIMEOUT_SECONDS": "0.1"}
    try:
        result = subprocess.run(
            [sys.executable, "-m", "stackos", "mcp-bridge", "--port", str(server.server_port)],
            input='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n',
            text=True,
            capture_output=True,
            timeout=10,
            env=env,
        )
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)
    assert result.returncode == 0, result.stderr
    assert len(received) == 1
    receipt = json.loads(settings.bridge_log_path.read_text())
    assert receipt["original"]["code"] == "read_timeout"
    assert receipt["autostart"] == "skipped"
    assert receipt["persistence"]["status"] == "persisted"
    reply = json.loads(result.stdout)
    assert reply["error"]["data"]["diagnostic_id"] == receipt["diagnostic_id"]
    assert "SECRET" not in result.stdout + result.stderr + settings.bridge_log_path.read_text()


def test_invalid_configuration_automatically_persists_before_bridge_setup(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "stackos", "mcp-bridge"],
        text=True,
        capture_output=True,
        timeout=10,
        env={**os.environ, "STACKOS_BRIDGE_STARTUP_TIMEOUT_SECONDS": "invalid-SECRET"},
    )
    assert result.returncode == 1
    assert result.stdout == ""
    receipt = json.loads((tmp_path / "state" / "mcp-bridge.log").read_text())
    assert receipt["phase"] == "configuration"
    assert receipt["persistence"]["status"] == "persisted"
    assert "SECRET" not in result.stderr


def test_parse_error_is_automatically_logged_without_request_contents():
    settings = Settings()
    settings.ensure_dirs()
    settings.token_path.write_text("SECRET")
    result = CliRunner().invoke(app, ["mcp-bridge"], input='{"secret":"SECRET",invalid}\n')
    assert result.exit_code == 0
    assert json.loads(result.stdout)["error"]["code"] == -32700
    receipt = json.loads(settings.bridge_log_path.read_text())
    assert receipt["phase"] == "request"
    assert receipt["persistence"]["status"] == "persisted"
    assert "SECRET" not in settings.bridge_log_path.read_text()


def test_workspace_failure_log_id_is_returned_with_scope_protection(tmp_path):
    def fail(request):
        raise httpx.ReadTimeout("SECRET")

    path = tmp_path / "custom" / "bridge.log"
    proxy = AgentBridgeProxy(
        url="http://localhost/mcp",
        headers={},
        cwd="/project",
        deliberate_workspace_root=True,
        diagnostic_log_path=path,
        attempt_id="c" * 32,
    )
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        proxy._ensure_workspace_scope(client)
    receipt = json.loads(path.read_text())
    assert receipt["phase"] == "workspace_scope"
    assert receipt["attempt_id"] == "c" * 32
    assert receipt["diagnostic_id"] in proxy.workspace_scope_error
    assert "persistence=persisted" in proxy.workspace_scope_error
    assert proxy.scoped_project_id is None
    assert proxy.workspace_scope_checked is False
    _, error = proxy._scope_workspace_arguments("workspace.startSession", {"global_session": True})
    assert error is not None


def test_internal_validation_failures_persist_and_clear_stale_grants(tmp_path):
    def fail(request):
        raise httpx.ReadTimeout("SECRET")

    path = tmp_path / "bridge.log"
    proxy = AgentBridgeProxy(url="http://localhost/mcp", headers={}, diagnostic_log_path=path)
    proxy.global_session = True
    proxy.scoped_project_id = 14
    proxy.allowed_by_run[1] = {"action.execute"}
    proxy.tokens_by_run[1] = "SECRET"
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        error = proxy._validate_project(client, 14)
        assert proxy._refresh_run_context(client, 1) is None
    receipts = [json.loads(line) for line in path.read_text().splitlines()]
    assert [receipt["phase"] for receipt in receipts] == ["project_validation", "run_context"]
    assert {receipt["attempt_id"] for receipt in receipts} == {proxy.attempt_id}
    assert all(receipt["persistence"]["status"] == "persisted" for receipt in receipts)
    assert error["diagnostic_id"] == receipts[0]["diagnostic_id"]
    assert 1 not in proxy.allowed_by_run
    assert 1 not in proxy.tokens_by_run
    assert "SECRET" not in path.read_text()
