"""Claude/Grok Messages streams at the real process and shared receipt boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stackos.host_mcp import service
from stackos.host_mcp.adapters import claude_code, grok
from stackos.host_mcp.agent_execution import ManagedProcessControl
from stackos.host_mcp.progress import MessagesStream
from stackos.host_mcp.result import normalize_usage

SCHEMA = {"type": "object", "required": ["summary"], "properties": {"summary": {"type": "string"}}}


def events(host, session="native-a"):
    model = "claude-opus-5-5" if host == "claude-code" else "grok-4.7-build"
    terminal = {
        "type": "result",
        "subtype": "success",
        "session_id": session,
        "is_error": False,
        "result": '{"summary":"done"}',
        "stop_reason": "end_turn",
        "structured_output": {"summary": "done"},
        "usage": {
            "input_tokens": 10,
            "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 0,
            "output_tokens": 5,
        },
        "modelUsage": {
            model: {
                "inputTokens": 10,
                "cacheReadInputTokens": 20,
                "cacheCreationInputTokens": 0,
                "outputTokens": 5,
            }
        },
    }
    return [{"type": "system", "subtype": "init", "session_id": session, "model": model}, terminal]


def executable(
    tmp_path,
    monkeypatch,
    host,
    rows,
    *,
    wait=False,
    stderr="",
    exit_code=0,
    release_after_result=None,
    stderr_before_result=False,
    stderr_after_gate=False,
):
    import sys

    path = tmp_path / "native"
    diagnostic = (
        f"sys.stderr.buffer.write({stderr!r}); sys.stderr.buffer.flush()\n"
        if isinstance(stderr, bytes)
        else f"print({stderr!r}, file=sys.stderr, flush=True)\n"
    )
    path.write_text(
        f"#!{sys.executable}\nimport json,sys,time\n"
        "if '--version' in sys.argv:\n print('2.1.330 (Claude Code)'); sys.exit(0)\n"
        f"rows = {rows!r}\n"
        "print(json.dumps(rows[0]), flush=True)\n"
        + (diagnostic if stderr_before_result else "")
        + ("time.sleep(30)\n" if wait else "")
        + "for row in rows[1:]: print(json.dumps(row), flush=True)\n"
        + ("" if stderr_before_result or stderr_after_gate else diagnostic)
        + (
            "from pathlib import Path\n"
            f"gate = Path({str(release_after_result)!r})\n"
            "while not gate.exists(): time.sleep(.01)\n"
            if release_after_result is not None
            else ""
        )
        + (diagnostic if stderr_after_gate else "")
        + f"sys.exit({exit_code})\n"
    )
    path.chmod(0o700)
    monkeypatch.setattr(claude_code.claude_mcp, "resolve_claude_bin", lambda: str(path))
    monkeypatch.setattr(grok, "resolve_grok_bin", lambda _: str(path))
    monkeypatch.setattr(
        service,
        "execution_catalog",
        lambda definition, **_: {**service.asdict(definition.policy.agent_execution)},
    )
    return path


@pytest.mark.parametrize("host", ["claude-code", "grok"])
def test_messages_early_identity_interruption_exact_resume(tmp_path, monkeypatch, host):
    rows = events(host)
    executable(tmp_path, monkeypatch, host, rows, wait=True)
    control = ManagedProcessControl()
    observed = []

    def attached(session):
        assert control._process.poll() is None
        observed.append(session)
        assert control.stop() == "stopped"

    adapter = claude_code if host == "claude-code" else grok
    result = adapter.run_agent(
        tmp_path, "role", "work", SCHEMA, process_control=control, on_session=attached
    )
    assert observed == ["native-a"]
    assert not result.ok and result.thread_id == "native-a"
    assert result.usage is None
    executable(tmp_path, monkeypatch, host, rows)
    result = service.run_agent(
        host,
        tmp_path,
        "role",
        "resume",
        SCHEMA,
        resume_thread_id=observed[0],
        execution_scope=service.AgentExecutionScope(1, "task", "ticket", "role"),
    )
    assert result.ok, result.error
    assert result.thread_id == observed[0]
    assert result.model_observation["model"] == rows[0]["model"]


@pytest.mark.parametrize("host", ["claude-code", "grok"])
@pytest.mark.parametrize(
    "fault", ["missing_init", "missing_id", "different_id", "late", "missing_result"]
)
def test_messages_reject_incomplete_or_conflicting_protocol(tmp_path, monkeypatch, host, fault):
    rows = events(host)
    if fault == "missing_init":
        rows = rows[1:]
    elif fault == "missing_id":
        del rows[0]["session_id"]
    elif fault == "different_id":
        rows[-1]["session_id"] = "native-b"
    elif fault == "late":
        rows.append(rows[0])
    elif fault == "missing_result":
        rows = rows[:1]
    executable(tmp_path, monkeypatch, host, rows)
    adapter = claude_code if host == "claude-code" else grok
    result = adapter.run_agent(tmp_path, "role", "work", SCHEMA)
    assert not result.ok and result.error
    assert result.usage is None


@pytest.mark.parametrize("host", ["claude-code", "grok"])
def test_messages_persistence_failure_retires_child(tmp_path, monkeypatch, host):
    executable(tmp_path, monkeypatch, host, events(host), wait=True)
    control = ManagedProcessControl()

    def persist(_):
        raise RuntimeError("durability failure")

    adapter = claude_code if host == "claude-code" else grok
    result = adapter.run_agent(
        tmp_path, "role", "work", SCHEMA, process_control=control, on_session=persist
    )
    assert not result.ok and "durability failure" in result.error
    assert control.retirement_status() == "stopped"


@pytest.mark.parametrize("host", ["claude-code", "grok"])
def test_messages_usage_and_model_reach_existing_live_and_final_owner(tmp_path, monkeypatch, host):
    rows = events(host)
    executable(tmp_path, monkeypatch, host, rows)
    live = []
    final = []
    original = service._observe_usage

    def observe(call, raw):
        original(call, raw)
        live.append((call.thread_id, call.model_observation, call.usage))

    monkeypatch.setattr(service, "_observe_usage", observe)
    result = service.run_agent(
        host,
        tmp_path,
        "role",
        "work",
        SCHEMA,
        execution_scope=service.AgentExecutionScope(1, "task", "ticket", "delivery"),
        on_execution_change=lambda call, active: (
            final.append(call.projection()) if not active else None
        ),
    )
    assert result.ok, result.error
    assert live[-1][0] == "native-a"
    assert live[-1][1]["model"] == rows[0]["model"]
    assert live[-1][2]["measured"]["total"] == 35
    assert final[-1]["live_usage"]["usage"]["measured"]["total"] == 35
    assert len(final[-1]["live_usage"]["samples"]) == 1
    assert result.model_observation["status"] == "observed"


@pytest.mark.parametrize("fault", ["warning", "native", "exit"])
def test_grok_messages_incomplete_stderr_invalidates_live_usage(tmp_path, monkeypatch, fault):
    rows = events("grok")
    if fault == "native":
        rows[-1].update(is_error=True, errors=["denied"])
    executable(
        tmp_path,
        monkeypatch,
        "grok",
        rows,
        stderr="warning: incomplete usage",
        exit_code=int(fault == "exit"),
    )
    updates = []
    result = grok.run_agent(
        tmp_path, "role", "work", SCHEMA, on_session=lambda _: None, on_usage=updates.append
    )
    assert result.ok is (fault == "warning")
    assert result.usage is None
    assert updates[-1] is None


@pytest.mark.parametrize(
    "warning_position,fault",
    [
        ("before", None),
        ("after", None),
        (None, None),
        ("before", "native"),
        ("before", "exit"),
        ("before", "cancel"),
        ("unreadable", "decode"),
    ],
)
def test_grok_messages_withhold_usage_until_complete_process_observation(
    tmp_path, monkeypatch, warning_position, fault
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    rows = events("grok")
    if fault == "native":
        rows[-1].update(is_error=True, errors=["denied"])
    gate = tmp_path / "release"
    executable(
        tmp_path,
        monkeypatch,
        "grok",
        rows,
        stderr=b"\xff"
        if fault == "decode"
        else "warning: incomplete usage"
        if warning_position
        else "",
        stderr_before_result=warning_position == "before",
        stderr_after_gate=fault == "decode",
        release_after_result=gate,
        exit_code=int(fault == "exit"),
    )
    result_seen = Event()
    feed = MessagesStream.feed

    def observe_result(stream, line):
        feed(stream, line)
        if stream.terminal is not None:
            result_seen.set()

    monkeypatch.setattr(MessagesStream, "feed", observe_result)
    usage_updates = []
    observe_usage = service._observe_usage

    def usage(call, raw):
        observe_usage(call, raw)
        usage_updates.append(raw)

    monkeypatch.setattr(service, "_observe_usage", usage)
    attached = []
    handoffs = []
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(
            service.run_agent,
            "grok",
            tmp_path,
            "role",
            "work",
            SCHEMA,
            execution_scope=service.AgentExecutionScope(909001, "fixture", "warning", "review"),
            on_session_attached=attached.append,
            on_execution_change=lambda call, active: (
                handoffs.append(call.projection()) if not active else None
            ),
        )
        try:
            assert result_seen.wait(10), "Native result event was not observed"
            assert not future.done(), "Child exited before live consumer inspection"
            assert attached[0].process_control._process.poll() is None
            live = service.usage_executions(909001)
            assert len(live) == 1 and live[0]["thread_id"] == "native-a"
            assert live[0]["model_observation"]["model"] == rows[0]["model"]
            assert live[0]["live_usage"] is None
            assert all(update is None for update in usage_updates)
            if fault == "cancel":
                assert service.retire_invocation(attached[0]) == "stopped"
        finally:
            gate.touch()
        result = future.result(timeout=10)
    assert result.ok is (fault is None)
    if fault == "decode":
        assert "UnicodeDecodeError" in result.error
    if warning_position is not None:
        assert result.usage is None and handoffs[-1]["live_usage"] is None
        assert usage_updates[-1] is None
    else:
        assert result.usage["snapshot"]["total"] == 35
        assert handoffs[-1]["live_usage"]["usage"]["measured"]["total"] == 35
        assert len(handoffs[-1]["live_usage"]["samples"]) == 1


@pytest.mark.parametrize("host", ["claude", "grok"])
def test_captured_messages_fresh_resume_accounting_and_native_model(host):
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/claude_grok_streams_20261122.json").read_text()
    )["hosts"][host]
    decode = (
        (lambda stdout: claude_code._agent_result(stdout, native_version=fixture["version"]))
        if host == "claude"
        else grok._message_result
    )
    key = "claude-code" if host == "claude" else "grok"
    previous = None
    totals = []
    sessions = []
    for run in fixture["runs"]:
        observed = []
        stream = MessagesStream(key, decode, on_session=observed.append)
        for row in run["events"]:
            stream.feed(json.dumps(row))
        result = stream.result()
        assert result.ok, result.error
        assert observed == [result.thread_id]
        sessions.append(result.thread_id)
        usage = normalize_usage(
            result.usage,
            session_id=result.thread_id,
            resumed=run["phase"] == "resume",
            previous=previous,
        )
        assert usage["status"] == "measured"
        totals.append(usage["measured"]["total"])
        previous = usage
        assert result.model_observation["model"] == (
            "claude-opus-5-5" if host == "claude" else "grok-4.7-build"
        )
        assert usage["basis"] == ("session" if host == "claude" else "invocation")
        if host == "grok":
            assert usage["measured"]["reasoning"] is None
    assert sessions[0] == sessions[1]
    assert totals == ([3182, 6696] if host == "claude" else [18662, 18811])


@pytest.mark.parametrize("host", ["claude-code", "grok"])
def test_messages_mixed_models_and_unproven_counters_stay_unknown(host):
    rows = events(host)
    rows[-1]["modelUsage"]["other-model"] = dict(next(iter(rows[-1]["modelUsage"].values())))
    decode = (
        (lambda stdout: claude_code._agent_result(stdout, native_version="2.1.330"))
        if host == "claude-code"
        else grok._message_result
    )
    result = MessagesStream(host, decode).decode_output("\n".join(map(json.dumps, rows)))
    assert result.model_observation["status"] == "ambiguous"
    if host == "grok":
        rows[-1]["usage"] = dict.fromkeys(rows[-1]["usage"], 0)
    else:
        rows[-1]["modelUsage"] = {}
    result = MessagesStream(host, decode).decode_output("\n".join(map(json.dumps, rows)))
    assert result.usage is None
