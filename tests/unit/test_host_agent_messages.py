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


def executable(tmp_path, monkeypatch, host, rows, *, wait=False, stderr="", exit_code=0):
    import sys

    path = tmp_path / "native"
    path.write_text(
        f"#!{sys.executable}\nimport json,sys,time\n"
        "if '--version' in sys.argv:\n print('2.1.330 (Claude Code)'); sys.exit(0)\n"
        f"rows = {rows!r}\n"
        "print(json.dumps(rows[0]), flush=True)\n"
        + ("time.sleep(30)\n" if wait else "")
        + "for row in rows[1:]: print(json.dumps(row), flush=True)\n"
        + f"print({stderr!r}, file=sys.stderr)\nsys.exit({exit_code})\n"
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
