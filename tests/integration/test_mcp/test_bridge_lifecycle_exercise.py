"""Actual consumer/producer exits and persisted evidence, without a real daemon."""

import json
import signal
import stat
from pathlib import Path

import pytest

from scripts.diagnostics import bridge_lifecycle as lifecycle
from scripts.diagnostics.bridge_lifecycle import SCENARIOS, exercise


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_lifecycle_evidence_distinguishes_observed_causes(tmp_path: Path, scenario: str):
    root = tmp_path / scenario
    result = exercise(root, scenario)
    events = [json.loads(line) for line in (root / "lifecycle.jsonl").read_text().splitlines()]
    producer = [
        json.loads(line) for line in (root / "state/mcp-bridge.log").read_text().splitlines()
    ]
    assert {e["attempt_id"] for e in events + producer} == {result["attempt_id"]}
    assert events[-1]["event"] == "attempt_finished"
    assert events[-1]["reaped_children"] == 2
    assert {e["child"] for e in events if e["event"] == "child_exited"} == {"producer", "consumer"}
    assert not any(e["event"].startswith("cleanup_") for e in events)
    assert not any(e["event"] == "exercise_deadline_expired" for e in events)
    assert all(e["timing_origin"] == "supervisor_before_spawn" for e in events)
    assert all(e["timing_origin"] == "cli_entry" for e in producer)
    for path in (root / "lifecycle.jsonl", root / "state/mcp-bridge.log"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert "isolated-fixture-only" not in path.read_text()
        assert "clientInfo" not in path.read_text()

    consumer_events = [e["event"] for e in events if e["actor"] == "consumer"]
    failures = [e for e in producer if e["event"] == "stackos.mcp_bridge.failure"]
    if scenario == "success":
        assert result["consumer_returncode"] == result["producer_returncode"] == 0
        assert consumer_events[-1] == "response_received"
        assert any(e["event"] == "stackos.mcp_bridge.startup_completed" for e in producer)
        assert not failures
    elif scenario in {
        "consumer_timeout",
        "cancellation",
        "consumer_exit",
        "cancellation_ack_pending",
    }:
        expected = {
            "consumer_timeout": 21,
            "cancellation": 20,
            "consumer_exit": -signal.SIGKILL,
            "cancellation_ack_pending": 20,
        }
        assert result["consumer_returncode"] == expected[scenario]
        assert result["producer_returncode"] != 0
        assert failures[-1]["phase"] == "response_write"
        assert failures[-1]["original"]["code"] == "broken_pipe"
        exited = next(
            i
            for i, e in enumerate(events)
            if e["event"] == "child_exited" and e["child"] == "consumer"
        )
        assert next(i for i, e in enumerate(events) if e["event"] == "response_written") > exited
        if scenario == "consumer_timeout":
            assert consumer_events[-2:] == ["deadline_armed", "deadline_expired"]
            assert "cancellation_received" not in consumer_events
        elif scenario in {"cancellation", "cancellation_ack_pending"}:
            assert consumer_events[-1] == "cancellation_received"
            assert "deadline_expired" not in consumer_events
            if scenario == "cancellation_ack_pending":
                assert "cancellation_acknowledged" in consumer_events
                alive = next(
                    i
                    for i, e in enumerate(events)
                    if e["event"] == "acknowledged_child_still_running"
                )
                assert events[alive]["running"] is True
                assert alive < exited
        else:
            assert consumer_events[-1] == "request_written"
            assert any(
                e.get("signal") == signal.SIGKILL for e in events if e["event"] == "child_exited"
            )
    elif scenario == "cancellation_response_race":
        assert any(e["event"] == "cancellation_sent" for e in events)
        assert result["consumer_returncode"] in {0, 20}
        if result["consumer_returncode"] == 0:
            assert consumer_events[-1] == "response_received"
            assert "cancellation_received" not in consumer_events
        else:
            assert consumer_events[-1] == "cancellation_received"
        assert result["producer_returncode"] in {0, 1}
    elif scenario == "bridge_exit":
        assert result["producer_returncode"] == 7
        assert result["consumer_returncode"] == 22
        assert consumer_events[-1] == "producer_eof"
        assert failures[-1]["phase"] == "token_read"
        assert failures[-1]["original"]["code"] == "file_not_found"
    else:
        assert result["producer_returncode"] == 0  # protocol error != process crash
        assert result["consumer_returncode"] == 25
        assert failures[-1]["phase"] == "initialize"
        expected = "read_timeout" if scenario == "bridge_timeout" else "invalid_daemon_response"
        assert failures[-1]["original"]["code"] == expected
        assert any(e.get("attempt_matches") is True for e in events)


def test_existing_evidence_directory_is_never_overwritten(tmp_path: Path):
    with pytest.raises(FileExistsError):
        exercise(tmp_path, "success")


@pytest.mark.parametrize("failure", ["deadline", "log_sink", "consumer_spawn"])
def test_cleanup_reaps_owned_children_even_when_recording_fails(tmp_path, monkeypatch, failure):
    children = []
    popen = lifecycle.subprocess.Popen
    append = lifecycle.append_json_log

    def spawn(*args, **kwargs):
        if failure == "consumer_spawn" and children:
            raise OSError("synthetic spawn failure")
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    def write(path, event):
        if failure == "log_sink" and len(children) == 2:
            raise OSError("synthetic unavailable log sink")
        append(path, event)

    monkeypatch.setattr(lifecycle.subprocess, "Popen", spawn)
    monkeypatch.setattr(lifecycle, "append_json_log", write)
    monkeypatch.setattr(lifecycle, "ATTEMPT_SECONDS", 0.1)
    if failure == "deadline":
        assert exercise(tmp_path / failure, "success")["completed"] is False
    else:
        with pytest.raises(OSError):
            exercise(tmp_path / failure, "success")
    assert len(children) == (1 if failure == "consumer_spawn" else 2)
    assert all(child.poll() is not None for child in children)
    assert all(
        stream.closed
        for child in children
        for stream in (child.stdin, child.stdout)
        if stream is not None
    )
