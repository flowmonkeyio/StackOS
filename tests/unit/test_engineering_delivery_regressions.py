"""Focused regressions for the bounded four-session delivery controller."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from stackos.cli.engineering_delivery import module as delivery
from stackos.workflows.run_plan_schema import find_run_plan_secret_paths


@pytest.fixture(autouse=True)
def _fresh_delivery_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    # Direct helper tests share the imported controller module. Mirror the
    # controller's invocation wrapper so each case captures its own env policy.
    monkeypatch.setattr(delivery, "_OUTPUT_POLICY", None)
    monkeypatch.setattr(delivery, "_ENVIRONMENT", None)
    monkeypatch.setattr(delivery, "_CONSOLE", None)


def test_worker_environment_is_an_explicit_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/safe/bin")
    monkeypatch.setenv("HOME", "/safe/home")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-worker-secret")
    monkeypatch.setenv("PYTHONPATH", "/injected/modules")
    monkeypatch.setenv("PYTHONSTARTUP", "/injected/startup.py")

    environment, secrets = delivery._worker_environment()

    assert environment["PATH"] == "/safe/bin"
    assert environment["HOME"] == "/safe/home"
    assert set(environment) <= delivery._WORKER_ENV_KEYS
    assert "fake-worker-secret" in secrets
    assert "OPENAI_API_KEY" not in environment
    assert "PYTHONPATH" not in environment
    assert "PYTHONSTARTUP" not in environment


def test_execute_rejects_protected_argv_before_child_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FAKE_API_TOKEN", 'fake-secret"\\line')
    stdout, stderr = io.BytesIO(), io.BytesIO()
    bare = 'fake-secret"\\line'
    escaped = json.dumps(bare)[1:-1]
    command = [
        sys.executable,
        "-c",
        "import json; print(json.dumps({'message': "
        + repr("value=" + bare + " escaped=" + escaped)
        + "})); print("
        + repr("stderr=" + bare + " escaped=" + escaped)
        + ", file=__import__('sys').stderr)",
    ]

    with pytest.raises(delivery.OutputRejected):
        delivery._execute(command, tmp_path, 5, stdout, stderr, prompt="structured")

    # Rejection occurs at decoded argv admission, before a child can dispatch.
    assert stdout.getvalue() == b""
    assert stderr.getvalue() == b""


def test_execute_rejects_policy_larger_than_local_pipe_capacity_before_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FAKE_API_TOKEN", "synthetic-" + "x" * 600)
    launches = []
    monkeypatch.setattr(delivery.subprocess, "Popen", lambda *a, **k: launches.append((a, k)))
    stdout, stderr = io.BytesIO(), io.BytesIO()

    with pytest.raises(delivery.OutputRejected):
        delivery._execute([sys.executable, "-c", "pass"], tmp_path, 1,
                          stdout, stderr, prompt="benign prompt")

    assert launches == []
    assert stdout.getvalue() == stderr.getvalue() == b""


def test_completion_receipt_serializes_safe_usage_and_omits_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_API_TOKEN", 'fake-secret"\\line')
    secret = 'fake-secret"\\line'
    escaped = json.dumps(secret)[1:-1]
    usage = {
        "input_tokens": 120,
        "cached_input_tokens": 30,
        "cache_write_input_tokens": 5,
        "output_tokens": 42,
        "reasoning_output_tokens": 11,
    }
    event = {
        "type": "turn.completed",
        "usage": usage,
        "api_key": secret,
        "nested": {"credential": "labelled-secret", "note": f"bare={secret}; escaped={escaped}"},
    }

    receipt = delivery._sanitize({"events": [event]}, known_secrets=(secret,))

    assert receipt["events"][0]["usage_counts"] == {
        "input_count": 120,
        "cached_input_count": 30,
        "cache_write_input_count": 5,
        "output_count": 42,
        "reasoning_output_count": 11,
    }
    assert "usage" not in receipt["events"][0]
    assert "api_key" not in receipt["events"][0]
    assert "credential" not in receipt["events"][0]["nested"]
    retained_text = receipt["events"][0]["nested"]["note"]
    assert "fake-secret" not in retained_text
    assert "[redacted]" in retained_text
    assert find_run_plan_secret_paths(receipt) == []

    nonnumeric = delivery._sanitize(
        {"type": "turn.completed", "usage": {"input_tokens": "secret-like-token"}},
        known_secrets=(secret,),
    )
    assert "usage_counts" not in nonnumeric
    assert nonnumeric["usage"] == {"redacted_field_count": 1}


def test_observation_failure_keeps_prior_and_failed_check_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = 0

    def execute(_argv, _repo, _seconds, stdout, stderr):
        nonlocal calls
        calls += 1
        stdout.write(b"first output" if calls == 1 else b"failed output")
        stderr.write(b"diagnostic")
        if calls == 2:
            raise ValueError("transport parse failed")
        return {"exit_code": 0, "timed_out": False}

    monkeypatch.setattr(delivery, "_execute", execute)
    checks = [
        {"requirement": "first", "argv": ["cmd", "1"], "expected_stdout": "first output"},
        {"requirement": "second", "argv": ["cmd", "2"], "expected_stdout": "expected"},
    ]

    with pytest.raises(delivery.ObservationFailed) as raised:
        delivery._observe(tmp_path, checks, 5)

    receipts = raised.value.receipts
    assert len(receipts) == 2
    assert receipts[0]["status"] == "complete"
    assert receipts[0]["stdout"] == "first output"
    assert receipts[1]["status"] == "held"
    assert receipts[1]["stdout"] == "failed output"
    assert receipts[1]["stderr"] == "diagnostic"
    assert receipts[1]["exit_code"] is None
    assert receipts[1]["error"] == "transport parse failed"
    assert receipts[1]["started_at"] and receipts[1]["ended_at"]


def _receipt_boundary(
    *,
    step_receipts: list[dict[str, object]],
    plan_receipt: dict[str, object] | None = None,
    write_error: Exception | None = None,
) -> tuple[list[tuple[str, dict[str, object]]], object]:
    calls: list[tuple[str, dict[str, object]]] = []
    read_index = 0

    def call(operation: str, arguments: dict[str, object]) -> dict[str, object]:
        nonlocal read_index
        calls.append((operation, arguments))
        if operation == "runPlan.get":
            return plan_receipt or {"id": 12, "project_id": 7, "run_id": 29}
        if operation == "runPlan.getStep":
            receipt = step_receipts[min(read_index, len(step_receipts) - 1)]
            read_index += 1
            return receipt
        if operation == "runPlan.recordStep":
            if write_error is not None:
                raise write_error
            return {}
        raise AssertionError(f"unexpected operation: {operation}")

    return calls, call


def _saved_receipt(
    *,
    status: str = "complete",
    result: object | None = None,
    error: object = None,
    project_id: int = 7,
    plan_id: int = 12,
    step_id: str = "executor",
) -> dict[str, object]:
    return {
        "run_plan_id": plan_id,
        "project_id": project_id,
        "step_id": step_id,
        "status": status,
        "error": error,
        "result_json": {"answer": "done", "count": 1} if result is None else result,
    }


def test_record_accepts_exact_saved_result_without_another_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, call = _receipt_boundary(step_receipts=[_saved_receipt()])
    monkeypatch.setattr(delivery, "_call", call)

    delivery._record(
        7, 12, "run-token", "executor", "complete", {"count": 1, "answer": "done"}, run_id=29
    )

    assert [operation for operation, _ in calls] == ["runPlan.get", "runPlan.getStep"]
    assert calls[0][1] == {"project_id": 7, "run_plan_id": 12}
    assert calls[1][1] == {
        "project_id": 7,
        "run_plan_id": 12,
        "step_id": "executor",
    }


def test_record_accepts_exact_canonical_receipt_after_ambiguous_write_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, call = _receipt_boundary(
        step_receipts=[
            _saved_receipt(status="running", result={}),
            _saved_receipt(),
        ],
        write_error=RuntimeError("response lost after commit"),
    )
    monkeypatch.setattr(delivery, "_call", call)

    delivery._record(
        7, 12, "run-token", "executor", "complete", {"answer": "done", "count": 1}, run_id=29
    )

    assert [operation for operation, _ in calls] == [
        "runPlan.get",
        "runPlan.getStep",
        "runPlan.recordStep",
        "runPlan.get",
        "runPlan.getStep",
    ]
    assert sum(operation == "runPlan.recordStep" for operation, _ in calls) == 1


@pytest.mark.parametrize(
    "plan_receipt, step_receipts, write_error",
    [
        ({"id": 12, "project_id": 8, "run_id": 29}, [_saved_receipt()], None),
        ({"id": 13, "project_id": 7, "run_id": 29}, [_saved_receipt()], None),
        ({"id": 12, "project_id": 7, "run_id": 30}, [_saved_receipt()], None),
        (None, [_saved_receipt(plan_id=13)], None),
        (None, [_saved_receipt(step_id="reviewer")], None),
        (
            None,
            [_saved_receipt(status="running", result={}), _saved_receipt(status="failed")],
            RuntimeError("response lost"),
        ),
        (
            None,
            [
                _saved_receipt(status="running", result={}),
                _saved_receipt(result={"answer": "done", "count": True}),
            ],
            RuntimeError("response lost"),
        ),
        (
            None,
            [_saved_receipt(status="running", result={}), _saved_receipt(error="persisted error")],
            RuntimeError("response lost"),
        ),
        (
            None,
            [
                _saved_receipt(status="running", result={}),
                {key: value for key, value in _saved_receipt().items() if key != "error"},
            ],
            RuntimeError("response lost"),
        ),
    ],
    ids=[
        "project-identity",
        "plan-identity",
        "run-identity",
        "step-plan-identity",
        "step-key-identity",
        "status",
        "result",
        "error",
        "missing-error",
    ],
)
def test_record_rejects_conflicting_receipt_without_a_second_write(
    monkeypatch: pytest.MonkeyPatch,
    plan_receipt: dict[str, object] | None,
    step_receipts: list[dict[str, object]],
    write_error: Exception | None,
) -> None:
    calls, call = _receipt_boundary(
        step_receipts=step_receipts,
        plan_receipt=plan_receipt,
        write_error=write_error,
    )
    monkeypatch.setattr(delivery, "_call", call)

    expected_exception = ValueError if write_error is None else RuntimeError
    with pytest.raises(expected_exception) as raised:
        delivery._record(
            7, 12, "run-token", "executor", "complete", {"answer": "done", "count": 1}, run_id=29
        )

    if write_error is not None:
        assert raised.value is write_error
    writes = [operation for operation, _ in calls].count("runPlan.recordStep")
    assert writes == (0 if write_error is None else 1)


def test_record_preserves_original_write_exception_when_receipt_stays_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = RuntimeError("write response is uncertain")
    calls, call = _receipt_boundary(
        step_receipts=[
            _saved_receipt(status="running", result={}),
            _saved_receipt(status="running", result={}),
        ],
        write_error=original,
    )
    monkeypatch.setattr(delivery, "_call", call)

    with pytest.raises(RuntimeError) as raised:
        delivery._record(
            7, 12, "run-token", "executor", "complete", {"answer": "done", "count": 1}, run_id=29
        )

    assert raised.value is original
    assert [operation for operation, _ in calls].count("runPlan.recordStep") == 1
