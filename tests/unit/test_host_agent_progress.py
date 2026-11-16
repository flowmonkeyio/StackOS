"""Bounded counter collection at the existing invocation lifetime boundary."""

import json
import sqlite3
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from stackos.host_mcp import service
from stackos.host_mcp.progress import CodexCounterReader, CodexStreamError, run_codex_process
from stackos.host_mcp.result import AgentRunResult, normalize_usage, usage_snapshot
from stackos.repositories.tracker.agent_stats import (
    _model_identity,
    provisional_receipt,
    summarize_receipts,
)
from stackos.repositories.tracker.receipt_projection import receipt_input


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
        return [value] if value else []

    def close(self):
        self.closed = True


def collect_native_history(root, batches, *, resumed=False, poll_times=None, final_count=None):
    """Exercise real native bytes through collection, handoff and projection."""
    root.mkdir()
    sid = "019a681b-1000-7000-8000-000000000001"
    rollout = root / "rollout.jsonl"
    with sqlite3.connect(root / "state_5.sqlite") as database:
        database.execute("CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT)")
        database.execute("INSERT INTO threads VALUES (?, ?)", (sid, str(rollout)))
    rollout.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": sid, "cwd": str(root)}}) + "\n"
    )
    baseline = 100 if resumed else 0
    invocation = call(resumed=resumed)
    invocation.session_id = sid
    invocation.previous_usage = normalize_usage(
        usage_snapshot("codex.turn", "session", baseline, 0, cache_read=0),
        session_id=sid,
        resumed=False,
    )
    invocation.reader = CodexCounterReader(sid, root, home=root, resumed=resumed)
    service._active_invocations[invocation.invocation_id] = invocation
    current = []
    try:
        for index, batch in enumerate(batches):
            with rollout.open("a") as target:
                for second, count in batch:
                    at = datetime(2026, 11, 8, 1, tzinfo=UTC) + timedelta(seconds=second)
                    target.write(
                        json.dumps(
                            {
                                "type": "event_msg",
                                "timestamp": at.isoformat(),
                                "payload": {
                                    "type": "token_count",
                                    "info": {
                                        "total_token_usage": {
                                            "input_tokens": baseline + count,
                                            "output_tokens": 0,
                                            "total_tokens": baseline + count,
                                            "cached_input_tokens": 0,
                                        }
                                    },
                                },
                            }
                        )
                        + "\n"
                    )
            now = datetime(2026, 11, 8, 1, tzinfo=UTC) + timedelta(
                seconds=poll_times[index] if poll_times else 100
            )
            service._sample_invocations(now)
            current.append((invocation.measured_at, invocation.usage["measured"]["input"]))
        if final_count is not None:
            service._finish(
                invocation,
                AgentRunResult(
                    0,
                    thread_id=sid,
                    usage=usage_snapshot(
                        "codex.turn", "session", baseline + final_count, 0, cache_read=0
                    ),
                ),
            )
            assert invocation.finished and invocation.reader is None
        projection = invocation.projection()
        stats = summarize_receipts(
            [{"exit_code": 0, "usage": invocation.usage, "execution": projection}],
            include_history=True,
        )
        history = [(p.at, [(i.kind, i.input) for i in p.intervals]) for p in stats.samples]
        return stats.reported_usage.input, history, current, projection
    finally:
        service._unregister(invocation)


@pytest.mark.parametrize("resumed", [False, True])
def test_real_collector_preserves_late_source_history_without_regressing_totals(tmp_path, resumed):
    ordered = [(0, 0), (40, 100), (70, 200), (100, 300)]
    reference = collect_native_history(tmp_path / "ordered", [ordered], resumed=resumed)
    same_read = collect_native_history(
        tmp_path / "same-read", [[ordered[i] for i in (0, 2, 1, 3)]], resumed=resumed
    )
    later_poll = collect_native_history(
        tmp_path / "later-poll",
        [ordered[:1] + ordered[2:3], ordered[1:2], ordered[3:]],
        resumed=resumed,
    )
    assert reference[:2] == same_read[:2] == later_poll[:2]
    assert [amount for _, amount in later_poll[2]] == [200, 200, 300]
    assert [intervals for _, intervals in reference[1]] == [
        [("baseline", 0)],
        [("checkpoint", 100)],
        [("checkpoint", 100)],
        [("checkpoint", 100)],
    ]


@pytest.mark.parametrize("split", [False, True])
def test_real_collector_keeps_conflicting_timestamps_but_deduplicates_equal_counters(
    tmp_path, split
):
    points = [(0, 0), (40, 100), (40, 100), (40, 300), (70, 400), (100, 500)]
    batches = [points[:2], points[2:4], points[4:]] if split else [points]
    total, history, current, projection = collect_native_history(tmp_path / "conflict", batches)
    assert total == 500
    assert len(projection["live_usage"]["samples"]) == 5
    assert [intervals for _, intervals in history] == [
        [("baseline", 0)],
        [("reset", None)],
        [("baseline", 400)],
        [("checkpoint", 100)],
    ]
    if split:
        assert [amount for _, amount in current] == [100, 100, 500]


def test_real_collector_retains_newest_source_points_and_final_handoff(tmp_path):
    total, history, current, projection = collect_native_history(
        tmp_path / "bounded",
        [[(i * 5, i) for i in range(1, 121)], [(2, 0)]],
        poll_times=[600, 600],
    )
    points = projection["live_usage"]["samples"]
    assert total == 120 and len(points) == 120
    assert points[0]["at"] == "2026-11-08T01:00:05+00:00"
    assert points[-1]["at"] == "2026-11-08T01:10:00+00:00"
    assert [amount for _, amount in current] == [120, 120]
    total, history, _, projection = collect_native_history(
        tmp_path / "expired",
        [[(0, 0), (40, 100)], [(30, 50)]],
        poll_times=[100, 701],
    )
    assert total == 100 and not projection["live_usage"]["samples"]
    # The last known total remains a dated baseline when its raw tail expires.
    assert history == [("2026-11-08T01:00:40+00:00", [("baseline", 100)])]
    total, history, _, projection = collect_native_history(
        tmp_path / "final",
        [[(0, 0), (70, 200)], [(40, 100), (100, 300)]],
        resumed=True,
        final_count=320,
    )
    assert total == 320 and len(projection["live_usage"]["samples"]) == 5
    assert [intervals for _, intervals in history[:-1]] == [
        [("baseline", 0)],
        [("checkpoint", 100)],
        [("checkpoint", 100)],
        [("checkpoint", 100)],
    ]
    assert history[-1][1] == [("batched", 20)]


def test_native_read_bound_keeps_newest_source_points_before_collector(tmp_path):
    ordered = [(i * 5, i) for i in range(121)]
    reference = collect_native_history(tmp_path / "ordered-bound", [ordered], poll_times=[600])
    latest_first = collect_native_history(
        tmp_path / "latest-first-bound", [[ordered[-1], *ordered[:-1]]], poll_times=[600]
    )
    assert reference[:2] == latest_first[:2]
    assert latest_first[0] == 120 and len(latest_first[3]["live_usage"]["samples"]) == 120


def test_delayed_collector_keeps_each_source_checkpoint_and_fixed_resume_baseline():
    invocation = call(resumed=True)
    invocation.reader = Reader()
    invocation.reader.read = lambda: [counter(1), counter(2), counter(1), counter(3)]
    service._active_invocations[invocation.invocation_id] = invocation
    service._sample_invocations(datetime.fromisoformat(counter(3)[0]))
    assert [(p["at"], p["input"]) for p in invocation.samples] == [
        (counter(i)[0], i * 10) for i in (1, 2, 3)
    ]
    assert invocation.usage["measured"]["input"] == 30
    assert invocation.previous_usage["snapshot"]["input"] == 100


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
    assert result.ok and observed == [(110 if resumed else 220, True, True, None, None)]
    assert not service._active_invocations and not service.provisional_usage(1)


@pytest.mark.parametrize("failure", [False, True, "cancel"])
def test_lifetime_cleanup_success_error_cancel_and_no_idle_collector(
    monkeypatch, tmp_path, failure
):
    invocation_reader = Reader()
    retained = []

    def runner(*args, on_session, **kwargs):
        on_session("native")
        assert len(service.usage_executions()) == 1
        invocation = next(iter(service._active_invocations.values()))
        invocation.usage = normalize_usage(
            usage_snapshot("fixture", "invocation", 20, 2), session_id="native", resumed=False
        )
        invocation.measured_at = datetime.now(UTC).isoformat()
        invocation.samples.append({"at": invocation.measured_at, **invocation.usage["measured"]})
        if failure == "cancel":
            raise KeyboardInterrupt()
        if failure:
            raise RuntimeError("native failure")
        return AgentRunResult(0, thread_id="native", result={})

    monkeypatch.setattr(service, "agent_selection_error", lambda *a, **kw: None)
    monkeypatch.setattr(service, "_definition_by_key", lambda _: SimpleNamespace(run_agent=runner))
    monkeypatch.setattr(service, "CodexCounterReader", lambda *args, **kwargs: invocation_reader)

    def observe(invocation, active):
        if not active:
            retained.append(invocation.projection())

    kwargs = dict(
        execution_scope=service.AgentExecutionScope(1, "task", "child", "worker"),
        on_execution_change=observe,
    )
    if failure:
        with pytest.raises(KeyboardInterrupt if failure == "cancel" else RuntimeError):
            service.run_agent("codex", tmp_path, "instructions", "prompt", {}, **kwargs)
    else:
        assert service.run_agent("codex", tmp_path, "instructions", "prompt", {}, **kwargs).ok
    assert retained[0]["live_usage"]["samples"][0]["total"] == 22
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
    assert waiting.read() == [] and waiting.file is not None
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
    assert reader.read() == []
    offset = reader.cursor
    with file.open("a") as out:
        out.write(encoded[30:] + "\n")
    ((stamp, usage),) = reader.read()
    assert stamp == event["timestamp"] and usage["snapshot"]["cache_read"] == 90
    assert reader.cursor > offset and reader.read() == []
    # A delayed poll must preserve each source instant, rather than returning
    # only the last cumulative counter and losing the intervening minutes.
    with file.open("a") as out:
        for minute in (1, 2, 3):
            event["timestamp"] = f"2026-11-08T01:0{minute}:10Z"
            out.write(json.dumps(event) + "\n")
    assert [stamp for stamp, _ in reader.read()] == [
        f"2026-11-08T01:0{minute}:10Z" for minute in (1, 2, 3)
    ]
    resumed = CodexCounterReader(sid, tmp_path, home=tmp_path, resumed=True)
    assert resumed.cursor == file.stat().st_size and resumed.read() == []
    with file.open("a") as out:
        out.write(encoded + "\n")
    assert resumed.read()[0][1]["snapshot"]["total"] == 132
    resumed.close()
    reader.close()
    assert reader.file is None and not reader.pending
    mismatch = CodexCounterReader(sid, tmp_path / "different", home=tmp_path)
    assert mismatch.file is None


@pytest.fixture
def model_rollout(tmp_path):
    """Redacted native turn_context shape; only structured metadata is observed."""
    sid = "01a1b94c-eb2a-7280-b3c5-d55cd290801b"
    path = tmp_path / "rollout.jsonl"
    with sqlite3.connect(tmp_path / "state_5.sqlite") as db:
        db.execute("CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT)")
        db.execute("INSERT INTO threads VALUES (?, ?)", (sid, str(path)))
    path.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": sid, "cwd": str(tmp_path)}}) + "\n"
    )

    def append(model="observed-native", cwd=None):
        with path.open("a") as out:
            out.write(
                json.dumps(
                    {
                        "type": "turn_context",
                        "payload": {"cwd": str(cwd or tmp_path), "model": model, "turn_id": "turn"},
                    }
                )
                + "\n"
            )

    return sid, path, append


def test_exact_native_model_observation_is_structured_and_conflicts_are_sticky(
    tmp_path, model_rollout
):
    sid, path, append = model_rollout
    reader = CodexCounterReader(sid, tmp_path, home=tmp_path)
    try:
        with path.open("a") as out:
            out.write(
                json.dumps({"type": "response_item", "payload": {"model": "prose-guess"}}) + "\n"
            )
        assert reader.read() == []
        assert reader.model_observation is None
        append()
        reader.read()
        assert reader.model_observation == {
            "status": "observed",
            "model": "observed-native",
            "source": "codex.turn_context",
            "session_id": sid,
        }
        append()
        reader.read()
        assert reader.model_observation["status"] == "observed"
        append("different-native")
        reader.read()
        append()
        reader.read()
        assert reader.model_observation == {
            "status": "ambiguous",
            "model": None,
            "source": "codex.turn_context",
            "session_id": sid,
        }
    finally:
        reader.close()


@pytest.mark.parametrize(
    "invalid", ["cwd", "session", "metadata-session", "metadata-cwd", "missing", "unsafe"]
)
def test_native_model_requires_exact_identity_and_valid_structured_context(
    tmp_path, model_rollout, invalid
):
    sid, path, append = model_rollout
    if invalid.startswith("metadata"):
        path.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "foreign-session" if invalid == "metadata-session" else sid,
                        "cwd": str(tmp_path / "foreign" if invalid == "metadata-cwd" else tmp_path),
                    },
                }
            )
            + "\n"
        )
    append(
        None
        if invalid == "missing"
        else "unsafe model"
        if invalid == "unsafe"
        else "observed-native",
        tmp_path / "foreign" if invalid == "cwd" else None,
    )
    reader = CodexCounterReader(
        "01a1b94c-eb2a-7280-b3c5-d55cd290801c" if invalid == "session" else sid,
        tmp_path,
        home=tmp_path,
    )
    try:
        reader.read()
        if invalid in {"session", "metadata-session", "metadata-cwd"}:
            assert reader.model_observation is None
        else:
            assert reader.model_observation["status"] == "ambiguous"
    finally:
        reader.close()


def test_resumed_model_observation_never_uses_previous_invocation_prefix(tmp_path, model_rollout):
    sid, _, append = model_rollout
    append("old-native")
    reader = CodexCounterReader(sid, tmp_path, home=tmp_path, resumed=True)
    try:
        reader.read()
        assert reader.model_observation is None
        append("new-native")
        reader.read()
        assert reader.model_observation["model"] == "new-native"
    finally:
        reader.close()


def test_model_context_cannot_attribute_earlier_unknown_usage(tmp_path, model_rollout):
    sid, path, append = model_rollout
    reader = CodexCounterReader(sid, tmp_path, home=tmp_path)
    try:
        with path.open("a") as out:
            out.write(
                json.dumps(
                    {
                        "type": "event_msg",
                        "timestamp": "2026-11-08T01:00:10Z",
                        "payload": {
                            "type": "token_count",
                            "info": {"total_token_usage": {"input_tokens": 10, "output_tokens": 0}},
                        },
                    }
                )
                + "\n"
            )
        assert reader.read()[0][1]["snapshot"]["total"] == 10
        assert reader.model_observation is None
        append()
        reader.read()
        assert reader.model_observation["status"] == "ambiguous"
    finally:
        reader.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"model": "native", "cwd": 42},
        None,
        {"cwd": "/foreign"},
        {"model": "native", "cwd": "\u0000"},
    ],
)
def test_invalid_later_context_cannot_keep_an_earlier_observed_model(
    tmp_path, model_rollout, payload
):
    sid, path, append = model_rollout
    reader = CodexCounterReader(sid, tmp_path, home=tmp_path)
    try:
        append()
        reader.read()
        assert reader.model_observation["status"] == "observed"
        with path.open("a") as out:
            out.write(json.dumps({"type": "turn_context", "payload": payload}) + "\n")
        reader.read()
        assert reader.model_observation["status"] == "ambiguous"
    finally:
        reader.close()


def test_unreadable_bounded_context_prevents_whole_invocation_attribution(tmp_path, model_rollout):
    sid, path, append = model_rollout
    reader = CodexCounterReader(sid, tmp_path, home=tmp_path)
    try:
        append()
        reader.read()
        assert reader.model_observation["status"] == "observed"
        with path.open("a") as out:
            out.write(
                json.dumps(
                    {
                        "type": "turn_context",
                        "payload": {
                            "padding": "x" * (64 * 1024),
                            "model": "different",
                            "cwd": str(tmp_path),
                        },
                    }
                )
                + "\n"
            )
        reader.read()
        reader.read()
        assert reader.model_observation["status"] == "ambiguous"
    finally:
        reader.close()


@pytest.mark.parametrize("failed", [False, True])
def test_final_read_retains_observation_without_a_live_counter_poll(
    tmp_path, model_rollout, monkeypatch, failed
):
    sid, _, append = model_rollout
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    handoffs = []

    def runner(*args, on_session, **kwargs):
        on_session(sid)
        append()
        return AgentRunResult(
            1 if failed else 0,
            thread_id=sid,
            result=None if failed else {},
            usage=usage_snapshot("codex.turn", "session", 10, 2),
        )

    def changed(invocation, active):
        if not active:
            handoffs.append(invocation.projection())

    monkeypatch.setattr(service, "agent_selection_error", lambda *a, **kw: None)
    monkeypatch.setattr(service, "_definition_by_key", lambda _: SimpleNamespace(run_agent=runner))
    result = service.run_agent(
        "codex",
        tmp_path,
        "Instructions",
        "Prompt",
        {},
        execution_scope=service.AgentExecutionScope(1, "task", "child", "worker", 1, "delivery"),
        on_execution_change=changed,
    )
    assert result.model_observation["model"] == "observed-native"
    assert handoffs[0]["model_observation"] == result.model_observation
    assert handoffs[0]["live_usage"]["usage"]["measured"]["total"] == 12
    assert not service._active_invocations


@pytest.mark.parametrize("loss", ["none", "malformed-context", "reader-error", "truncated-context"])
def test_context_loss_stays_ambiguous_through_live_final_and_retained_attribution(
    tmp_path, model_rollout, monkeypatch, loss
):
    sid, path, append = model_rollout
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    live, handoffs = [], []

    def runner(*args, on_session, **kwargs):
        on_session(sid)
        append("model-a")
        with path.open("a") as out:
            out.write(
                json.dumps(
                    {
                        "type": "event_msg",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "payload": {
                            "type": "token_count",
                            "info": {"total_token_usage": {"input_tokens": 10, "output_tokens": 0}},
                        },
                    }
                )
                + "\n"
            )
        service._sample_invocations()
        live.append(deepcopy(service.usage_executions(1)[0]))
        if loss in {"malformed-context", "truncated-context"}:
            with path.open("a") as out:
                out.write('{"type":"turn_context","payload":{"model":"model-b"')
                if loss == "malformed-context":
                    out.write("\n")
        elif loss == "reader-error":

            def unreadable(self):
                raise OSError("fixture reader became unavailable")

            monkeypatch.setattr(CodexCounterReader, "read", unreadable)
        service._sample_invocations()
        live.append(deepcopy(service.usage_executions(1)[0]))
        # A later valid context must not erase the gap.
        if loss == "malformed-context":
            append("model-a")
            service._sample_invocations()
        return AgentRunResult(
            0, thread_id=sid, result={}, usage=usage_snapshot("codex.turn", "session", 30, 0)
        )

    def changed(invocation, active):
        if not active:
            handoffs.append(invocation.projection())

    monkeypatch.setattr(service, "agent_selection_error", lambda *a, **kw: None)
    monkeypatch.setattr(service, "_definition_by_key", lambda _: SimpleNamespace(run_agent=runner))
    result = service.run_agent(
        "codex",
        tmp_path,
        "Instructions",
        "Prompt",
        {},
        execution_scope=service.AgentExecutionScope(1, "task", "child", "worker", 1, "delivery"),
        model="requested",
        on_execution_change=changed,
    )
    assert result.ok and result.usage["snapshot"]["total"] == 30
    assert live[0]["model_observation"]["model"] == "model-a"
    assert live[1]["live_usage"]["usage"]["measured"]["total"] == 10
    expected_status = "observed" if loss == "none" else "ambiguous"
    assert live[1]["model_observation"]["status"] == (
        "observed" if loss == "truncated-context" else expected_status
    )  # An incomplete live line can still complete before native exit.
    assert result.model_observation["status"] == expected_status
    assert handoffs[0]["model_observation"] == result.model_observation
    assert handoffs[0]["live_usage"]["usage"]["measured"]["total"] == 30
    launch = {"host_key": "codex", "model": "requested"}
    expected = ("model-a", "observed") if loss == "none" else (None, None)
    assert _model_identity(receipt_input({"launch": launch, "execution": live[1]})) == (
        ("model-a", "observed") if loss == "truncated-context" else expected
    )
    retained = receipt_input({**asdict(result), "launch": launch, "execution": handoffs[0]})
    assert _model_identity(retained) == expected
    assert not service._active_invocations


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


def test_session_persistence_failure_stops_native_process(tmp_path):
    import os
    import sys

    def persist(_):
        raise RuntimeError("identity commit failed")

    with pytest.raises(CodexStreamError, match="identity commit failed") as caught:
        run_codex_process(
            [
                sys.executable,
                "-c",
                "import json,time; "
                "print(json.dumps({'type':'thread.started','thread_id':'native'}),flush=True); "
                "time.sleep(.2)",
            ],
            input="",
            cwd=tmp_path,
            env=dict(os.environ),
            on_session=persist,
        )
    assert caught.value.process.returncode != 0
    assert '"thread_id": "native"' in caught.value.process.stdout


@pytest.mark.parametrize("fence", ["resume", "cancelled", "finished", "retired", "competing"])
def test_session_attachment_does_not_notify_rejected_identity(tmp_path, fence):
    invocation = call()
    invocation.session_id = None
    service._active_invocations[invocation.invocation_id] = invocation
    if fence == "cancelled":
        invocation.cancelled.set()
    elif fence == "finished":
        invocation.finished = True
    elif fence == "retired":
        service._unregister(invocation)
    elif fence == "competing":
        owner = call(2)
        service._active_invocations[owner.invocation_id] = owner
    observed = []
    service._attach_session(
        invocation, "native", tmp_path, "expected" if fence == "resume" else None, observed.append
    )
    assert observed == [] and invocation.session_id is None


@pytest.mark.parametrize("failure", [False, True])
def test_required_session_callback_is_once_and_failure_is_not_observability(
    tmp_path, monkeypatch, failure
):
    reader = Reader()
    observed = []

    def attached(invocation):
        # SQL owners can take the registry lock from another thread: the host
        # must have released it before calling required persistence.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(1) as pool:
            assert pool.submit(service.active_execution, 1, "task").result(timeout=2)
        observed.append(invocation.thread_id)
        if failure:
            raise RuntimeError("commit failed")

    def runner(*args, on_session, **kwargs):
        on_session("native")
        on_session("native")
        return AgentRunResult(0, thread_id="different-result", result={})

    monkeypatch.setattr(service, "agent_selection_error", lambda *a, **kw: None)
    monkeypatch.setattr(service, "_definition_by_key", lambda _: SimpleNamespace(run_agent=runner))
    monkeypatch.setattr(service, "CodexCounterReader", lambda *a, **kw: reader)
    original_stop = service.ManagedProcessControl.stop

    def stop(control):
        from concurrent.futures import ThreadPoolExecutor

        def lock_available():
            acquired = service.managed_launch_lock.acquire(timeout=1)
            if acquired:
                service.managed_launch_lock.release()
            return acquired

        with ThreadPoolExecutor(1) as pool:
            assert pool.submit(lock_available).result(timeout=2), "cleanup held launch lock"
        return original_stop(control)

    monkeypatch.setattr(service.ManagedProcessControl, "stop", stop)
    kwargs = dict(
        execution_scope=service.AgentExecutionScope(1, "task", "child", "worker", 2, "delivery"),
        on_session_attached=attached,
    )
    if failure:
        with pytest.raises(RuntimeError, match="commit failed"):
            service.run_agent("codex", tmp_path, "instructions", "prompt", {}, **kwargs)
    else:
        result = service.run_agent("codex", tmp_path, "instructions", "prompt", {}, **kwargs)
        assert not result.ok and result.error == "Native session identity mismatch"
    assert observed == ["native"] and reader.closed
    assert not service._active_invocations and not service.provisional_usage(1)
