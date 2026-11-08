"""Bounded counter collection at the existing invocation lifetime boundary."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from stackos.host_mcp import service
from stackos.host_mcp.progress import CodexCounterReader, CodexStreamError, run_codex_process
from stackos.host_mcp.result import AgentRunResult, normalize_usage, usage_snapshot
from stackos.repositories.tracker.agent_stats import provisional_receipt, summarize_receipts


@pytest.fixture(autouse=True)
def lifetime():
    service.shutdown_usage()
    yield
    thread = service._collector
    service.shutdown_usage()
    if thread:
        thread.join(2)
        assert not thread.is_alive()


def call(pid=1, ticket="child", resumed=False):
    baseline = normalize_usage(
        usage_snapshot("codex.turn", "session", 100, 10, cache_read=80),
        session_id="native",
        resumed=False,
    )
    return service.ActiveInvocation(
        f"call-{pid}",
        service.AgentExecutionScope(pid, "task", ticket, "worker", 2, "delivery"),
        "codex",
        "selected",
        "2026-11-08T01:00:00+00:00",
        previous_usage=baseline,
        resumed=resumed,
        session_id="native",
    )


def counter(i, *, stamp=None):
    return (
        stamp or (datetime(2026, 11, 8, 1, tzinfo=UTC) + timedelta(seconds=i * 5)).isoformat(),
        usage_snapshot("codex.turn", "session", 100 + i * 10, 10 + i, cache_read=80 + i),
    )


class Reader:
    def __init__(self):
        self.value = None
        self.reads = 0
        self.closed = False

    def read(self):
        self.reads += 1
        value, self.value = self.value, None
        return value

    def close(self):
        self.closed = True


def test_single_owner_bounds_replaces_fixed_resume_baseline_and_project_scopes():
    first, second = call(resumed=True), call(2)
    first.reader, second.reader = Reader(), Reader()
    # Deterministic manual ticks test collection without wall-clock sleeps.
    service._active_invocations.update({first.invocation_id: first, second.invocation_id: second})
    for i in range(1, 151):
        first.reader.value, second.reader.value = counter(i), counter(i)
        service._sample_invocations(datetime.fromisoformat(counter(i)[0]))
    assert len(first.samples) == len(second.samples) == 120
    assert first.usage["measured"]["input"] == 1500
    assert second.usage["measured"]["input"] == 1600
    assert first.previous_usage["snapshot"]["input"] == 100
    assert service.usage_executions(1)[0]["project_id"] == 1
    assert len(service.usage_executions(2)) == 1
    assert len(service.usage_executions()) == 2
    measurement = first.measured_at
    service._sample_invocations(datetime.fromisoformat(measurement))
    assert first.measured_at == measurement and len(first.samples) == 120
    first.reader.value = counter(1)  # reconnect/repeated old checkpoint
    service._sample_invocations(datetime.fromisoformat(measurement))
    assert first.measured_at == measurement
    service._sample_invocations(datetime.fromisoformat(measurement) + timedelta(seconds=601))
    assert not first.samples and first.usage["measured"]["input"] == 1500
    assert first.measured_at == measurement
    reader = first.reader
    service._unregister(first)
    assert reader.closed and not first.samples and first.usage is None
    assert service.active_execution(1, "task") is None
    assert len(service.usage_executions()) == 1
    service._unregister(second)
    reads = reader.reads
    service._sample_invocations(datetime.fromisoformat(counter(2)[0]))
    assert reader.reads == reads and service.usage_executions() == []


def test_final_receipt_wins_before_unregister_and_late_attach_is_ignored(tmp_path):
    invocation = call(resumed=True)
    invocation.reader = Reader()
    invocation.reader.value = counter(2)
    service._active_invocations[invocation.invocation_id] = invocation
    service._sample_invocations(datetime.fromisoformat(counter(2)[0]))
    pending = {"agent": "worker", "launch": {"agent": "worker"}}
    evidence = {"automatic_delivery": {"attempt": 2, "delivery": pending}}
    receipts = provisional_receipt(1, "child", evidence, service.provisional_usage())
    assert len(receipts) == 1 and summarize_receipts(receipts).reported_usage.total == 22
    assert "usage" not in pending
    assert provisional_receipt(2, "child", evidence, service.provisional_usage()) == [pending]
    final = {**pending, "exit_code": 0, "usage": invocation.usage}
    evidence["automatic_delivery"]["delivery"] = final
    assert provisional_receipt(1, "child", evidence, service.provisional_usage()) == [final]
    assert (
        summarize_receipts(
            provisional_receipt(1, "child", evidence, service.provisional_usage())
        ).reported_usage.total
        == 22
    )
    service._unregister(invocation)
    service._attach_session(invocation, "late", tmp_path, None)
    assert invocation.reader is None
    assert provisional_receipt(1, "child", evidence, service.provisional_usage()) == [final]


def test_request_counter_copy_survives_exit_and_newer_recorded_handoff_wins():
    invocation = call()
    invocation.usage = normalize_usage(
        usage_snapshot("fixture", "invocation", 20, 2, cache_read=10),
        session_id="native",
        resumed=False,
    )
    service._active_invocations[invocation.invocation_id] = invocation
    captured = service.provisional_usage(1)
    pending = {"launch": {"agent": "worker"}}
    evidence = {"automatic_delivery": {"attempt": 2, "delivery": pending}}
    service._unregister(invocation)
    assert not service.provisional_usage()
    assert (
        summarize_receipts(provisional_receipt(1, "child", evidence, captured)).reported_usage.total
        == 22
    )
    final_usage = normalize_usage(
        usage_snapshot("fixture", "invocation", 30, 3, cache_read=15),
        session_id="native",
        resumed=False,
    )
    # The native-exit receipt already has final measured counters but result
    # validation has not added exit_code/result yet. An older copy cannot win.
    pending["usage"] = final_usage
    assert (
        summarize_receipts(provisional_receipt(1, "child", evidence, captured)).reported_usage.total
        == 33
    )
    captured[(1, "child")][0][2]["measured"]["input"] = 999
    assert pending["usage"] == final_usage


@pytest.mark.parametrize("resumed", [False, True])
def test_exit_handoff_uses_final_native_counters_and_always_releases_snapshot(
    monkeypatch, tmp_path, resumed
):
    baseline = call().previous_usage
    reader = Reader()
    observed = []

    def runner(*args, on_session, **kwargs):
        on_session("native")
        invocation = next(iter(service._active_invocations.values()))
        invocation.usage = normalize_usage(
            usage_snapshot("codex.turn", "session", 150, 15, cache_read=100),
            session_id="native",
            resumed=resumed,
            previous=baseline,
        )
        return AgentRunResult(
            0,
            thread_id="native",
            result={},
            usage=usage_snapshot("codex.turn", "session", 200, 20, cache_read=160),
        )

    def changed(invocation, active):
        if active:
            return
        # Final totals can exceed the last five-second checkpoint. They use the
        # same fixed prior invocation baseline, not that last checkpoint.
        service._attach_session(invocation, "late", tmp_path, None)
        observed.append(
            (
                invocation.usage["measured"]["total"],
                reader.closed,
                bool(invocation.samples),
                service.active_execution(1, "task"),
                invocation.reader,
            )
        )
        raise RuntimeError("optional observer failure")

    monkeypatch.setattr(service, "agent_selection_error", lambda *a, **kw: None)
    monkeypatch.setattr(service, "_definition_by_key", lambda _: SimpleNamespace(run_agent=runner))
    monkeypatch.setattr(service, "CodexCounterReader", lambda *a, **kw: reader)
    result = service.run_agent(
        "codex",
        tmp_path,
        "Instructions",
        "Prompt",
        {},
        execution_scope=service.AgentExecutionScope(1, "task", "child", "worker", 2, "delivery"),
        previous_usage=baseline,
        resume_thread_id="native" if resumed else None,
        on_execution_change=changed,
    )
    assert result.ok and observed == [(110 if resumed else 220, True, False, None, None)]
    assert not service._active_invocations and not service.provisional_usage(1)


@pytest.mark.parametrize("failure", [False, True, "cancel"])
def test_lifetime_cleanup_success_error_cancel_and_no_idle_collector(
    monkeypatch, tmp_path, failure
):
    invocation_reader = Reader()

    def runner(*args, on_session, **kwargs):
        on_session("native")
        assert len(service.usage_executions()) == 1
        if failure == "cancel":
            raise KeyboardInterrupt()
        if failure:
            raise RuntimeError("native failure")
        return AgentRunResult(0, thread_id="native", result={})

    monkeypatch.setattr(service, "agent_selection_error", lambda *a, **kw: None)
    monkeypatch.setattr(service, "_definition_by_key", lambda _: SimpleNamespace(run_agent=runner))
    monkeypatch.setattr(service, "CodexCounterReader", lambda *args, **kwargs: invocation_reader)
    kwargs = dict(execution_scope=service.AgentExecutionScope(1, "task", "child", "worker"))
    if failure:
        with pytest.raises(KeyboardInterrupt if failure == "cancel" else RuntimeError):
            service.run_agent("codex", tmp_path, "instructions", "prompt", {}, **kwargs)
    else:
        assert service.run_agent("codex", tmp_path, "instructions", "prompt", {}, **kwargs).ok
    assert invocation_reader.closed
    thread = service._collector
    if thread:
        thread.join(2)
        assert not thread.is_alive()
    assert service._collector is None and not service.usage_executions()


def test_exact_rollout_cursor_partial_lines_and_counter_only_parsing(tmp_path):
    sid = "01a1b94c-eb2a-7280-b3c5-d55cd290801b"
    created = datetime.fromtimestamp((UUID(sid).int >> 80) / 1000, UTC).astimezone()
    folder = tmp_path / "sessions" / created.strftime("%Y/%m/%d")
    folder.mkdir(parents=True)
    file = folder / f"rollout-{created.strftime('%Y-%m-%dT%H-%M-%S')}-{sid}.jsonl"
    with sqlite3.connect(tmp_path / "state_5.sqlite") as database:
        database.execute("CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT)")
        database.execute("INSERT INTO threads VALUES (?, ?)", (sid, str(file)))
    waiting = CodexCounterReader(sid, tmp_path, home=tmp_path)
    assert waiting.file is None and waiting.path == file
    file.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": sid, "cwd": str(tmp_path)}}) + "\n"
    )
    reader = CodexCounterReader(sid, tmp_path, home=tmp_path)
    assert waiting.read() is None and waiting.file is not None
    waiting.close()
    event = {
        "type": "event_msg",
        "timestamp": "2026-11-08T01:00:10Z",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": 120,
                    "cached_input_tokens": 90,
                    "output_tokens": 12,
                    "total_tokens": 132,
                }
            },
        },
    }
    encoded = json.dumps(event)
    with file.open("a") as out:
        out.write("not json transcript bytes\n" + encoded[:30])
    assert reader.read() is None
    offset = reader.cursor
    with file.open("a") as out:
        out.write(encoded[30:] + "\n")
    stamp, usage = reader.read()
    assert stamp == event["timestamp"] and usage["snapshot"]["cache_read"] == 90
    assert reader.cursor > offset and reader.read() is None
    resumed = CodexCounterReader(sid, tmp_path, home=tmp_path, resumed=True)
    assert resumed.cursor == file.stat().st_size and resumed.read() is None
    with file.open("a") as out:
        out.write(encoded + "\n")
    assert resumed.read()[1]["snapshot"]["total"] == 132
    resumed.close()
    reader.close()
    assert reader.file is None and not reader.pending
    mismatch = CodexCounterReader(sid, tmp_path / "different", home=tmp_path)
    assert mismatch.file is None


def test_process_exposes_identity_before_exit_preserves_output_and_error(tmp_path):
    import os
    import sys

    identities = []
    command = [
        sys.executable,
        "-c",
        "import json,sys,time; "
        "print(json.dumps({'type':'thread.started','thread_id':'native'}),flush=True); "
        "time.sleep(.1); print('final'); print('diagnostic',file=sys.stderr); sys.exit(3)",
    ]
    result = run_codex_process(
        command,
        input="",
        cwd=tmp_path,
        env=dict(os.environ),
        on_session=lambda sid: identities.append(sid),
    )
    assert identities == ["native"] and result.returncode == 3
    assert result.stdout.endswith("final\n") and result.stderr == "diagnostic\n"


def test_stream_decoding_failure_is_not_a_successful_partial_native_result(tmp_path):
    import os
    import sys

    with pytest.raises(CodexStreamError) as caught:
        run_codex_process(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff')"],
            input="",
            cwd=tmp_path,
            env=dict(os.environ),
            on_session=lambda _: None,
        )
    assert isinstance(caught.value.__cause__, UnicodeDecodeError)
    assert "UnicodeDecodeError" in str(caught.value)
    assert isinstance(caught.value.process.returncode, int)
