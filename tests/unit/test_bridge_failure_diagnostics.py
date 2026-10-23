"""Bridge diagnostics must preserve privacy, timing and replay boundaries."""

from __future__ import annotations

import io
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

import stackos.cli.daemon_commands as cli
from stackos.cli import app
from stackos.config import Settings
from stackos.mcp.bridge.diagnostics import bridge_failure_details, emit_bridge_diagnostic
from stackos.mcp.bridge.protocol import BridgeProtocolError

SECRET = "private-token-and-exception-sentinel"


def test_attempt_identity_spans_failure_recovery_and_startup(bridge, monkeypatch):
    _, install = bridge
    attempt = "b" * 32
    monkeypatch.setenv("STACKOS_BRIDGE_ATTEMPT_ID", attempt)
    answer = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
    install([httpx.ConnectError(SECRET), answer, httpx.ReadTimeout(SECRET)])
    result = invoke(count=2)
    receipts = events(result)
    assert {e["attempt_id"] for e in receipts} == {attempt}
    assert len({e["diagnostic_id"] for e in receipts}) == 3
    assert json.loads(result.stdout.splitlines()[-1])["error"]["data"]["attempt_id"] == attempt


def test_untrusted_attempt_value_is_not_logged(bridge, monkeypatch):
    _, install = bridge
    monkeypatch.setenv("STACKOS_BRIDGE_ATTEMPT_ID", SECRET)
    install([httpx.ReadTimeout(SECRET), httpx.ReadTimeout(SECRET)])
    result = invoke(count=2)
    assert len({e["attempt_id"] for e in events(result)}) == 1
    assert len(events(result)[0]["attempt_id"]) == 32


class FakeClient:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


@pytest.fixture
def bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    settings.ensure_dirs()
    settings.token_path.write_text(SECRET)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "_mcp_bridge_workspace_hints", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: FakeClient())
    calls: dict[str, Any] = {"handled": 0, "autostart": 0, "probes": 0}

    def install(outcomes: list[object], *, start: object = True, reachable: bool = False):
        remaining = iter(outcomes)

        class Proxy:
            def __init__(self, **_kwargs):
                pass

            def handle(self, *_args, **_kwargs):
                calls["handled"] += 1
                item = next(remaining)
                if isinstance(item, BaseException):
                    raise item
                return item

        def autostart(*_args):
            calls["autostart"] += 1
            if isinstance(start, BaseException):
                raise start
            return start, SECRET  # Legacy helper messages may contain private paths.

        def probe(*_args, **_kwargs):
            calls["probes"] += 1
            return reachable

        monkeypatch.setattr(cli, "AgentBridgeProxy", Proxy)
        monkeypatch.setattr(cli, "_autostart_bridge_daemon", autostart)
        monkeypatch.setattr(cli.daemon_processes, "_tcp_can_connect", probe)
        return calls

    return settings, install


def invoke(
    method: object = "initialize",
    *,
    notification: bool = False,
    count: int = 1,
    request_id: object = 1,
):
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if not notification:
        payload["id"] = request_id
    payload["params"] = {"private": SECRET}
    result = CliRunner().invoke(app, ["mcp-bridge"], input=(json.dumps(payload) + "\n") * count)
    assert SECRET not in result.stdout + result.stderr
    return result


def events(result) -> list[dict]:
    return [json.loads(line) for line in result.stderr.splitlines()]


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout(SECRET),
        httpx.WriteTimeout(SECRET),
        httpx.ReadError(SECRET),
        httpx.WriteError(SECRET),
        httpx.RemoteProtocolError(SECRET),
        BridgeProtocolError("invalid_json"),
        RuntimeError(SECRET),
        ExceptionGroup(SECRET, [httpx.ReadTimeout(SECRET)]),
    ],
)
def test_uncertain_failures_never_probe_start_or_replay(bridge, failure) -> None:
    _, install = bridge
    calls = install([failure])
    result = invoke()
    assert result.exit_code == 0
    assert calls == {"handled": 1, "autostart": 0, "probes": 0}
    response = json.loads(result.stdout)
    event = events(result)[0]
    assert response["error"]["code"] == -32000
    assert response["error"]["data"]["diagnostic_id"] == event["diagnostic_id"]
    assert event["autostart"] == "skipped"
    assert event["phase"] == "initialize"
    assert response["error"]["data"]["retry_automatically"] is False
    assert response["error"]["data"]["effect"] == "unknown"


@pytest.mark.parametrize("method", ["tools/call", "tools/list", "notifications/initialized"])
def test_other_methods_do_not_replay_even_connect_failure(bridge, method: str) -> None:
    _, install = bridge
    calls = install([httpx.ConnectError(SECRET)])
    result = invoke(method, notification=method.startswith("notifications/"))
    assert result.exit_code == 0
    assert calls == {"handled": 1, "autostart": 0, "probes": 0}
    if method.startswith("notifications/"):
        assert result.stdout == ""
    assert len(events(result)) == 1


@pytest.mark.parametrize("method", ["initialize", "ping"])
def test_idless_handshake_notification_never_replays(bridge, method: str) -> None:
    _, install = bridge
    calls = install([httpx.ConnectError(SECRET)])
    result = invoke(method, notification=True)
    assert result.exit_code == 0
    assert result.stdout == ""
    assert calls == {"handled": 1, "autostart": 0, "probes": 0}


@pytest.mark.parametrize("method", ["initialize", "ping"])
def test_connect_failure_can_recover_once_without_logging_helper_text(bridge, method: str) -> None:
    _, install = bridge
    answer = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
    calls = install([httpx.ConnectError(SECRET), answer, httpx.ConnectError(SECRET)])
    result = invoke(method, count=2)
    assert result.exit_code == 0
    assert calls == {"handled": 3, "autostart": 1, "probes": 1}
    output = [json.loads(line) for line in result.stdout.splitlines()]
    assert output[0]["result"] == {}
    assert output[1]["error"]["code"] == -32000
    assert [event["outcome"] for event in events(result) if "outcome" in event] == [
        "recovered",
        "failed",
    ]


def test_retry_failure_preserves_original_and_retry_classifications(bridge) -> None:
    _, install = bridge
    calls = install([httpx.ConnectTimeout(SECRET), httpx.ReadTimeout(SECRET)])
    result = invoke()
    event = events(result)[0]
    assert calls == {"handled": 2, "autostart": 1, "probes": 1}
    assert event["original"]["code"] == "connect_timeout"
    assert event["retry"]["code"] == "read_timeout"
    assert json.loads(result.stdout)["error"]["data"]["retry_failure"] == event["retry"]


def test_autostart_exception_does_not_replace_original_failure(bridge) -> None:
    _, install = bridge
    calls = install([httpx.ConnectError(SECRET)], start=PermissionError(SECRET))
    result = invoke()
    event = events(result)[0]
    assert calls["handled"] == 1
    assert event["original"]["code"] == "connect_error"
    assert event["retry"] is None
    assert event["recovery_failure"]["code"] == "permission_denied"


def test_reachable_listener_is_not_restarted(bridge) -> None:
    _, install = bridge
    calls = install([httpx.ConnectError(SECRET)], reachable=True)
    result = invoke()
    assert calls == {"handled": 1, "autostart": 0, "probes": 1}
    assert events(result)[0]["autostart"] == "skipped"


def test_http_unauthorized_reports_only_status_without_autostart(bridge) -> None:
    _, install = bridge
    response = httpx.Response(
        401, text=SECRET, request=httpx.Request("POST", f"https://private.invalid/{SECRET}")
    )
    calls = install([httpx.HTTPStatusError(SECRET, request=response.request, response=response)])
    result = invoke()
    assert calls == {"handled": 1, "autostart": 0, "probes": 0}
    assert events(result)[0]["original"]["http_status"] == 401


@pytest.mark.parametrize(
    "phase,patcher,code",
    [
        ("configuration", lambda m: m.setattr(cli, "get_settings", fail), 1),
        ("state_setup", lambda m: m.setattr(Settings, "ensure_dirs", fail), 1),
        ("token_read", lambda m: m.setattr(Path, "read_text", fail), 1),
        ("workspace_hints", lambda m: m.setattr(cli, "_mcp_bridge_workspace_hints", fail), 1),
        ("client", lambda m: m.setattr(httpx, "Client", fail), 1),
    ],
)
def test_preloop_errors_have_safe_phase_and_no_protocol_noise(
    bridge, monkeypatch: pytest.MonkeyPatch, phase: str, patcher: Callable, code: int
) -> None:
    patcher(monkeypatch)
    result = invoke()
    assert result.exit_code == code
    assert result.stdout == ""
    assert events(result)[0]["phase"] == phase


def fail(*_args, **_kwargs):
    raise PermissionError(SECRET)


@pytest.mark.parametrize("sink_failure", ["write", "flush"])
@pytest.mark.parametrize("daemon_failure", [False, True])
def test_response_sink_failure_is_not_initialization_failure(
    bridge, monkeypatch, sink_failure, daemon_failure
) -> None:
    settings, install = bridge
    calls = install(
        [
            httpx.ReadTimeout(SECRET)
            if daemon_failure
            else json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
        ]
    )

    class Sink(io.StringIO):
        def write(self, value):
            if sink_failure == "write":
                raise BrokenPipeError(SECRET)
            return super().write(value)

        def flush(self):
            if sink_failure == "flush":
                raise BrokenPipeError(SECRET)

    monkeypatch.setattr("sys.stdin", io.StringIO('{"id":1,"method":"initialize"}\n'))
    monkeypatch.setattr("sys.stdout", Sink())
    monkeypatch.setattr("sys.stderr", io.StringIO())
    with pytest.raises(cli.typer.Exit) as exc:
        cli.mcp_bridge()
    assert exc.value.exit_code == 1
    saved = [json.loads(line) for line in settings.bridge_log_path.read_text().splitlines()]
    assert [event["phase"] for event in saved] == (
        ["initialize", "response_write"] if daemon_failure else ["response_write"]
    )
    assert saved[-1]["original"]["code"] == "broken_pipe"
    assert saved[-1]["bridge_elapsed_ms"] >= saved[-1]["elapsed_ms"]
    assert SECRET not in json.dumps(saved)
    assert calls == {"handled": 1, "autostart": 0, "probes": 0}


def test_startup_timing_includes_setup_and_flush_and_persists_once(bridge, monkeypatch) -> None:
    settings, _ = bridge
    clock = [1.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def hints(*_args, **_kwargs):
        clock[0] = 2.0
        return {}

    class Proxy:
        def __init__(self, **_kwargs):
            pass

        def handle(self, *_args, **_kwargs):
            clock[0] += 2.0
            return '{"jsonrpc":"2.0","id":1,"result":{}}'

    class Sink(io.StringIO):
        def flush(self):
            clock[0] += 2.0

    monkeypatch.setattr(cli, "_mcp_bridge_workspace_hints", hints)
    monkeypatch.setattr(cli, "AgentBridgeProxy", Proxy)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"id":1,"method":"initialize"}\n' * 2))
    output = Sink()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("sys.stderr", io.StringIO())
    cli.mcp_bridge()
    saved = [json.loads(line) for line in settings.bridge_log_path.read_text().splitlines()]
    assert len(saved) == 1
    event = saved[0]
    assert event["event"] == "stackos.mcp_bridge.startup_completed"
    assert event["timing_origin"] == "cli_entry"
    assert event["startup_elapsed_ms"] == 5000
    assert event["initialize_elapsed_ms"] == 2000
    assert event["response_write_elapsed_ms"] == 2000
    assert len(output.getvalue().splitlines()) == 2
    assert settings.bridge_log_path.stat().st_mode & 0o777 == 0o600


def test_daemon_error_response_does_not_claim_startup_completed(bridge) -> None:
    settings, install = bridge
    install(['{"jsonrpc":"2.0","id":1,"error":{"code":-32603,"message":"safe"}}'])
    result = invoke()
    assert result.exit_code == 0
    assert json.loads(result.stdout)["error"]["code"] == -32603
    assert result.stderr == ""
    assert not settings.bridge_log_path.exists()


def test_read_error_after_initialize_does_not_inherit_initialize_phase(bridge, monkeypatch) -> None:
    settings, install = bridge
    calls = install(['{"jsonrpc":"2.0","id":1,"result":{}}'])

    def broken_input():
        yield '{"id":1,"method":"initialize"}\n'
        raise OSError(SECRET)

    monkeypatch.setattr("sys.stdin", broken_input())
    monkeypatch.setattr("sys.stdout", io.StringIO())
    monkeypatch.setattr("sys.stderr", io.StringIO())
    with pytest.raises(cli.typer.Exit):
        cli.mcp_bridge()
    saved = [json.loads(line) for line in settings.bridge_log_path.read_text().splitlines()]
    assert saved[0]["event"] == "stackos.mcp_bridge.startup_completed"
    assert saved[1]["phase"] == "request_read"
    assert saved[1]["original"]["code"] == "os_error"
    assert SECRET not in json.dumps(saved)
    assert calls == {"handled": 1, "autostart": 0, "probes": 0}


def test_missing_token_preserves_exit_seven_without_private_path(bridge) -> None:
    settings, _ = bridge
    settings.token_path.unlink()
    result = invoke()
    assert result.exit_code == 7
    assert result.stdout == ""
    assert str(settings.token_path) not in result.stderr
    assert events(result)[0]["original"]["code"] == "file_not_found"


def test_nested_exception_group_is_bounded_and_cyclic_causes_are_safe() -> None:
    cause = PermissionError(SECRET)
    cause.__cause__ = cause
    grouped = ExceptionGroup(SECRET, [cause] + [ValueError(SECRET) for _ in range(100)])
    details = bridge_failure_details(grouped)
    assert SECRET not in json.dumps(details)
    assert details["causes_truncated"] is True
    assert "permission_denied" in details["cause_codes"]
    assert len(details["cause_codes"]) <= 12


def test_diagnostic_sink_failure_does_not_mask_primary_error(monkeypatch) -> None:
    class ClosedSink:
        def write(self, _text):
            raise OSError(SECRET)

    monkeypatch.setattr("sys.stderr", ClosedSink())
    diagnostic = emit_bridge_diagnostic(
        phase="request", started_at=time.monotonic(), original=ValueError(SECRET)
    )
    assert diagnostic["original"]["code"] == "invalid_value"


@pytest.mark.parametrize("sink_kind", ["closed", "flush_failure"])
def test_actual_closed_or_unflushable_sink_preserves_primary_receipt(
    monkeypatch, sink_kind
) -> None:
    class BadFlush(io.StringIO):
        def flush(self):
            raise ValueError(SECRET)

    if sink_kind == "closed":
        sink = io.StringIO()
        sink.close()
    else:
        sink = BadFlush()
    monkeypatch.setattr("sys.stderr", sink)
    diagnostic = emit_bridge_diagnostic(
        phase="request", started_at=time.monotonic(), original=httpx.ReadTimeout(SECRET)
    )
    assert diagnostic["original"]["code"] == "read_timeout"


@pytest.mark.parametrize("method", [[], {}])
def test_malformed_method_failure_stays_per_request_without_replay(bridge, method) -> None:
    _, install = bridge
    calls = install([httpx.ConnectError(SECRET)])
    result = invoke(method)
    assert result.exit_code == 0
    assert json.loads(result.stdout)["error"]["code"] == -32000
    assert calls == {"handled": 1, "autostart": 0, "probes": 0}


def test_explicit_null_id_is_not_silenced_as_a_notification(bridge) -> None:
    _, install = bridge
    install([httpx.ReadTimeout(SECRET)])
    result = invoke(request_id=None)
    response = json.loads(result.stdout)
    assert response["id"] is None
    assert response["error"]["code"] == -32000


def test_valid_multiline_sse_is_one_stdout_json_rpc_line(bridge, monkeypatch) -> None:
    class Client(FakeClient):
        def post(self, url, **_kwargs):
            return httpx.Response(
                200,
                text='data: {"jsonrpc": "2.0",\ndata: "id": 1, "result": {}}\n\n',
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: Client())
    result = invoke()
    assert result.exit_code == 0
    assert len(result.stdout.splitlines()) == 1
    assert json.loads(result.stdout.splitlines()[0])["result"] == {}
    assert [event["event"] for event in events(result)] == ["stackos.mcp_bridge.startup_completed"]


def test_nested_oversized_group_reports_dropped_causes() -> None:
    repeated = ValueError(SECRET)
    nested = ExceptionGroup(SECRET, [repeated] * 100)
    details = bridge_failure_details(ExceptionGroup(SECRET, [nested]))
    assert details["causes_truncated"] is True


def test_private_exception_class_name_is_not_logged_and_safe_leaf_is_retained() -> None:
    private_type = type(SECRET, (RuntimeError,), {})
    error = private_type(SECRET)
    error.__cause__ = ConnectionRefusedError(SECRET)
    details = bridge_failure_details(error)
    assert SECRET not in json.dumps(details)
    assert details["cause_codes"] == ["unexpected_error", "connection_refused"]


def test_non_loopback_connect_error_never_autostarts(bridge, monkeypatch) -> None:
    _, install = bridge
    calls = install([httpx.ConnectError(SECRET)])
    monkeypatch.setattr(cli.daemon_processes, "_is_loopback_host", lambda _host: False)
    result = invoke()
    assert result.exit_code == 0
    assert calls == {"handled": 1, "autostart": 0, "probes": 0}
