"""Offline synthetic coverage for bounded host diagnostic events."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from stackos.artifacts.checked_output import CheckedStream, OutputPolicy, OutputRejected


def _modules():
    candidate = Path(__file__).resolve().parents[2]
    scripts = candidate / "plugins/engineering/scripts"
    sys.path.insert(0, str(scripts))
    try:
        def load(name, path):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            return module

        diagnostic = load("host_diagnostics_test", scripts / "host_diagnostics.py")
        reviewer = load("reviewer_isolation_diagnostics_test", scripts / "reviewer_isolation.py")
        delivery = load("delivery_diagnostics_test", scripts / "delivery.py")
    finally:
        sys.path.remove(str(scripts))
    assert Path(reviewer.__file__).resolve().is_relative_to(candidate)
    assert Path(delivery.__file__).resolve().is_relative_to(candidate)
    assert Path(diagnostic.__file__).resolve().is_relative_to(candidate)
    return diagnostic, reviewer, delivery


def _server(monkeypatch, reviewer, policy, message):
    reviewer.POLICY = policy
    reviewer.EVENT_STREAM = CheckedStream(policy)
    monkeypatch.setattr(reviewer.sys, "stdout", SimpleNamespace(buffer=io.BytesIO()))
    server = reviewer.Server.__new__(reviewer.Server)
    server.pending = bytearray(json.dumps(message).encode() + b"\n")
    server.deadline = 10**30
    server.notifications = []
    return server, reviewer.sys.stdout.buffer


def _observe_main(monkeypatch, reviewer, tmp_path, prompt, failure):
    snapshot, host = tmp_path / "snapshot", tmp_path / "host"
    snapshot.mkdir()
    host.mkdir()
    policy = OutputPolicy(("protected-observation-canary",))
    sink = io.BytesIO()
    monkeypatch.setattr(reviewer, "receive_private_policy", lambda _fd: policy)
    monkeypatch.setattr(reviewer.sys, "argv", [
        "reviewer_isolation.py", "--observe", "/synthetic/codex", str(snapshot),
        "1", str(host), "--policy-fd", "9",
    ])
    monkeypatch.setattr(reviewer.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(prompt)))
    monkeypatch.setattr(reviewer.sys, "stdout", SimpleNamespace(buffer=sink))
    monkeypatch.setattr(reviewer, "command_runtime", lambda: ({}, "/synthetic/bin"))
    monkeypatch.setattr(reviewer, "native_binary", lambda binary, **_kwargs: binary)

    features = {name: False for name in reviewer.DISABLED}
    features.update({name: True for name in reviewer.ENABLED})
    reviewer.FEATURE_NAMES_SHA256 = __import__("hashlib").sha256(
        "\n".join(sorted(features)).encode()
    ).hexdigest()
    profile = {
        "filesystem": {":minimal": "read", str(snapshot): "read"},
        "network": {"enabled": False},
    }
    config = {
        "permissions": {reviewer.PROFILE: profile},
        "default_permissions": reviewer.PROFILE,
        "features": features,
        "skills": {"include_instructions": False, "bundled": {"enabled": False}},
        "memories": {"use_memories": False, "generate_memories": False},
        "shell_environment_policy": {"inherit": "none", "set": {
            "PATH": "/synthetic/bin", "LANG": "C.UTF-8",
        }},
        "browser_use": {"allow_history_access": False, "default_origin_policy": {
            "access": "deny", "downloads": "deny", "uploads": "deny",
            "full_cdp_access": "deny",
        }},
        "computer_use": {"default_app_access": "deny"},
        "web_search": "disabled", "project_doc_max_bytes": 0,
    }

    class ServerStub:
        def __init__(self, *_args):
            pass

        def call(self, method, _params):
            return {
                "initialize": {},
                "configRequirements/read": {"requirements": {}},
                "permissionProfile/list": {"data": [{
                    "id": reviewer.PROFILE, "allowed": True,
                }]},
                "config/read": {"config": config},
                "skills/list": {"data": [{
                    "cwd": str(host), "skills": [], "errors": [],
                }]},
            }[method]

        def send(self, _message):
            pass

        def close(self):
            pass

    monkeypatch.setattr(reviewer, "Server", ServerStub)
    monkeypatch.setattr(reviewer.subprocess, "run", lambda command, **_kwargs: SimpleNamespace(
        stdout=(reviewer.HOST_VERSION + "\n" if command[-1] == "--version" else
                "".join(f"{name} x {'true' if enabled else 'false'}\n"
                        for name, enabled in features.items()))
    ))
    if failure == "dispatch":
        monkeypatch.setattr(reviewer, "outcome_command", lambda *_args: (_ for _ in ()).throw(
            RuntimeError("synthetic dispatch stop")
        ))
    elif failure == "result":
        monkeypatch.setattr(reviewer, "outcome_command", lambda *_args: {
            "exitCode": 0, "stdout": "protected-observation-canary", "stderr": "",
        })
    reviewer.main()
    return sink


@pytest.mark.parametrize(
    ("error", "retry", "expected"),
    [
        ({"codexErrorInfo": "rateLimitExceeded", "message": "private"}, True,
         ("rateLimitExceeded", None, True)),
        ({"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 503}},
          "details": "private"}, "true", ("httpConnectionFailed", 503, None)),
        ({"codexErrorInfo": {"futureCode": {}}, "message": "private"}, False,
         ("unknown", None, False)),
        ({"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": True}}}, 1,
         ("httpConnectionFailed", None, None)),
        ({"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 700}}}, None,
         ("httpConnectionFailed", None, None)),
        ({"codexErrorInfo": "rateLimitExceeded", "extra": "private"}, None,
         ("rateLimitExceeded", None, None)),
    ],
)
def test_receive_projects_only_finite_error_metadata_to_checked_event_sink(
    monkeypatch, error, retry, expected
):
    _, reviewer, _ = _modules()
    policy = OutputPolicy(("private-host-canary",))
    server, sink = _server(monkeypatch, reviewer, policy, {
        "method": "error", "params": {"error": error, "willRetry": retry}
    })
    result = server.receive()
    event = json.loads(sink.getvalue())
    assert result["method"] == "error"
    assert event == {
        "type": "host.diagnostic", "stage": "host_notification",
        "host_code": expected[0], "http_status": expected[1], "will_retry": expected[2],
    }
    assert b"private-host-canary" not in sink.getvalue()
    assert b"private" not in sink.getvalue()


def test_decoded_and_final_framed_canaries_are_rejected_before_event_write(monkeypatch):
    _, reviewer, _ = _modules()
    canary = "diagnostic-canary"
    policy = OutputPolicy((canary,))
    server, sink = _server(monkeypatch, reviewer, policy, {
        "method": "error", "params": {"error": {"message": canary}}
    })
    with pytest.raises(OutputRejected):
        server.receive()
    assert sink.getvalue() == b""

    policy = OutputPolicy(('", "right": "',))
    server, sink = _server(monkeypatch, reviewer, policy, {
        "method": "error", "params": {"error": {"codexErrorInfo": "other"}}
    })
    reviewer.EVENT_STREAM = CheckedStream(policy)
    with pytest.raises(OutputRejected):
        reviewer.emit({"left": "x", "right": "y"})
    assert sink.getvalue() == b""


def test_typed_denial_stays_denial_even_when_diagnostic_carries_retry_true(monkeypatch):
    _, reviewer, _ = _modules()
    policy = OutputPolicy(("protected-canary",))
    server, sink = _server(monkeypatch, reviewer, policy, {
        "method": "error", "params": {
            "error": {"codexErrorInfo": "cyberPolicy"}, "willRetry": True
        }
    })
    with pytest.raises(reviewer.HostRefusal):
        server.receive()
    events = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert events[0]["type"] == "host.diagnostic"
    assert events[0]["will_retry"] is True
    assert events[1]["type"] == "authorization.denied"
    assert events[1]["host_refusal"] is True


def test_terminal_turn_error_emits_completion_stage_without_retry_authority(monkeypatch):
    _, reviewer, _ = _modules()
    policy = OutputPolicy(("protected-canary",))
    server, sink = _server(monkeypatch, reviewer, policy, {
        "method": "turn/completed", "params": {
            "turn": {"error": {"codexErrorInfo": "serverOverloaded",
                                "message": "arbitrary terminal detail"},
                     "willRetry": True}
        }
    })
    result = server.receive()
    event = json.loads(sink.getvalue())
    assert result["method"] == "turn/completed"
    assert event == {
        "type": "host.diagnostic", "stage": "turn_completion",
        "host_code": "serverOverloaded", "http_status": None, "will_retry": None,
    }
    assert b"arbitrary terminal detail" not in sink.getvalue()


@pytest.mark.parametrize("stage, expected", [("tool_dispatch", "tool_dispatch"),
                                               ("unrecognized", "unknown")])
def test_generic_failure_reports_only_a_fixed_valid_or_unknown_stage(
    monkeypatch, stage, expected
):
    _, reviewer, _ = _modules()
    policy = OutputPolicy(("protected-canary",))
    reviewer.POLICY = policy
    reviewer.EVENT_STREAM = CheckedStream(policy)
    monkeypatch.setattr(reviewer.sys, "stdout", SimpleNamespace(buffer=io.BytesIO()))
    reviewer.DIAGNOSTIC_STAGE = stage
    reviewer.emit_failure("Host isolation or execution failed; no fallback was used.")
    event = json.loads(reviewer.sys.stdout.buffer.getvalue())
    assert event["stage"] == expected
    assert "protected-canary" not in event["message"]


@pytest.mark.parametrize("method, code", [("initialize", -32601), ("mystery", -32000)])
def test_rejected_rpc_is_allowlisted_and_remains_unclassified(monkeypatch, method, code):
    _, reviewer, _ = _modules()
    policy = OutputPolicy(("protected-canary",))
    server, sink = _server(monkeypatch, reviewer, policy, {
        "id": 1, "error": {"code": code, "message": "arbitrary RPC details"}
    })
    server.sequence = 0
    server.process = SimpleNamespace(stdin=io.BytesIO())
    server.input_stream = CheckedStream(policy)
    with pytest.raises(ValueError, match="rejected"):
        server.call(method, {})
    event = json.loads(sink.getvalue())
    assert event["type"] == "host.error"
    assert event["refusal_state"] == "unclassified_host_error"
    assert event["rpc_method"] == (method if method == "initialize" else "unknown")
    assert event["rpc_code"] == (code if code == -32601 else None)
    assert b"arbitrary RPC details" not in sink.getvalue()


@pytest.mark.parametrize(
    "extra",
    [
        [],
        [{"type": "observation.completed", "exit_code": 0, "stdout": "ok",
          "stderr": "", "refusal_state": "no_refusal_reported",
          "confinement": {"mode": "read_only", "verified": True, "network": False}},
         {"type": "observation.completed"}],
        [{"type": "host.diagnostic", "stage": "host_notification", "host_code": "other",
          "http_status": None, "will_retry": None, "unexpected": "x"}],
        [{"type": "host.diagnostic", "stage": "turn_completion", "host_code": "other",
          "http_status": None, "will_retry": None}],
        [{"type": "authorization.denied", "host_refusal": True}],
        [{
            "type": "host.error",
            "stage": "rpc_response",
            "refusal_state": "unclassified_host_error",
        }],
    ],
)
def test_observe_accepts_only_bounded_notification_before_single_completion(
    monkeypatch, tmp_path, extra
):
    _, _, delivery = _modules()
    completion = {
        "type": "observation.completed", "exit_code": 0, "stdout": "ok",
        "stderr": "", "refusal_state": "no_refusal_reported",
        "confinement": {"mode": "read_only", "verified": True, "network": False},
    }
    rows = [{"type": "host.diagnostic", "stage": "host_notification", "host_code": "other",
             "http_status": None, "will_retry": True}, *extra, completion]

    execute_calls = []

    def execute(_argv, _repo, _seconds, out, _err, _prompt=None):
        execute_calls.append(True)
        out.write(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        return {"exit_code": 0, "timed_out": False}

    monkeypatch.setattr(delivery, "_execute", execute)
    check = {"requirement": "synthetic", "argv": ["true"], "expected_stdout": "ok",
             "confinement": "read_only"}
    if not extra:
        receipt = delivery._observe(tmp_path, [check], 1)[0]
        assert execute_calls == [True]
        assert receipt["status"] == "complete"
        assert receipt["stdout"] == "ok"
        assert receipt["host_diagnostics"][0]["will_retry"] is True
    else:
        with pytest.raises(delivery.ObservationFailed) as raised:
            delivery._observe(tmp_path, [check], 1)
        assert execute_calls == [True]
        assert raised.value.receipts[0]["status"] == "held"
        retained = [json.loads(line) for line in
                    raised.value.receipts[0]["stdout"].splitlines()]
        assert retained == rows
        assert raised.value.receipts[0]["error"] == (
            "confined observation broker did not return a complete receipt"
        )


@pytest.mark.parametrize(
    ("prompt", "failure", "expected_stage", "expected_exception"),
    [
        (b'{"argv":[],"extra":true}', "admission", "observation_admission", ValueError),
        (b'{"argv":["true"]}', "dispatch", "observation_dispatch", RuntimeError),
        (b'{"argv":["true"]}', "result", "observation_result", OutputRejected),
    ],
)
def test_observe_main_records_admission_dispatch_and_result_failure_stage_with_stubs(
    monkeypatch, tmp_path, prompt, failure, expected_stage, expected_exception
):
    _, reviewer, _ = _modules()
    with pytest.raises(expected_exception):
        _observe_main(monkeypatch, reviewer, tmp_path, prompt, failure)
    assert expected_stage == reviewer.DIAGNOSTIC_STAGE


@pytest.mark.parametrize("custody", [{"exit_code": 1, "timed_out": False},
                                       {"exit_code": 0, "timed_out": True}])
def test_observe_diagnostic_never_overrides_failed_or_timed_out_custody(
    monkeypatch, tmp_path, custody
):
    _, _, delivery = _modules()
    rows = [
        {"type": "host.diagnostic", "stage": "host_notification", "host_code": "other",
         "http_status": None, "will_retry": True},
        {"type": "observation.completed", "exit_code": 0, "stdout": "ok",
         "stderr": "", "refusal_state": "no_refusal_reported",
         "confinement": {"mode": "read_only", "verified": True, "network": False}},
    ]

    execute_calls = []

    def execute(_argv, _repo, _seconds, out, _err, _prompt=None):
        execute_calls.append(True)
        out.write(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        return custody

    monkeypatch.setattr(delivery, "_execute", execute)
    check = {"requirement": "synthetic", "argv": ["true"], "expected_stdout": "ok",
             "confinement": "read_only"}
    with pytest.raises(delivery.ObservationFailed) as raised:
        delivery._observe(tmp_path, [check], 1)
    assert execute_calls == [True]
    assert raised.value.receipts[0]["status"] == "held"
    assert raised.value.receipts[0]["error"] == (
        "confined observation broker did not return a complete receipt"
    )


@pytest.mark.parametrize("include_completion", [False, True])
def test_normal_role_receipt_consumer_requires_actual_completion_and_retains_diagnostic(
    monkeypatch, tmp_path, include_completion
):
    _, _, delivery = _modules()
    policy = OutputPolicy(("protected-canary",))
    monkeypatch.setattr(delivery, "_OUTPUT_POLICY", policy)
    monkeypatch.setattr(delivery, "_ENVIRONMENT", {})
    monkeypatch.setattr(delivery, "_CONSOLE", None)
    monkeypatch.setattr(delivery.shutil, "which", lambda _name: "/synthetic/codex")
    monkeypatch.setattr(delivery._workspace, "native_binary", lambda binary, **_kw: binary)
    state = tmp_path / "review-state"
    (state / "outcome").mkdir(parents=True)
    manifest = {"selection_contract": "controller-source-outcome-v1", "files": [],
                "deleted": [], "changed_paths": [], "excluded_paths": []}
    (state / "outcome-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    digest = __import__("hashlib").sha256(
        (state / "outcome-manifest.json").read_bytes()
    ).hexdigest()
    rows = [
        {"type": "thread.started", "thread_id": "synthetic-thread"},
        {"type": "isolation.verified", "resolved_model": "synthetic-model",
         "outcome_manifest_sha256": digest},
        {"type": "host.diagnostic", "stage": "host_notification",
         "host_code": "rateLimitExceeded", "http_status": None, "will_retry": True},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}},
    ]
    if include_completion:
        rows.insert(3, {"type": "turn.completed", "usage_counts": {"output_count": 1}})

    def execute(_command, _repo, _seconds, out, _err, _prompt):
        out.write(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        return {"exit_code": 0, "timed_out": False, "group_leaked": False,
                "group_cleared": True}

    monkeypatch.setattr(delivery, "_execute", execute)
    receipt = delivery._run_role_impl(
        "reviewer", "synthetic-model", "safe prompt", tmp_path, 1,
        123 if include_completion else 124,
        review_state=state,
    )
    assert receipt["status"] == ("complete" if include_completion else "held")
    assert receipt["thread_id"] == "synthetic-thread"
    assert receipt["events"][2]["type"] == "host.diagnostic"
    assert receipt["events"][2]["will_retry"] is True
    if include_completion:
        assert receipt["answer"] == {}
        assert receipt["usage_counts"] == {"output_count": 1}
    else:
        assert "answer" not in receipt
        assert receipt["usage_counts"] is None


def test_authority_and_effect_dispositions_ignore_diagnostic_telemetry():
    _, _, delivery = _modules()
    recovery = delivery._recovery
    telemetry = {
        "type": "host.diagnostic", "stage": "host_notification",
        "host_code": "rateLimitExceeded", "http_status": None, "will_retry": True,
    }
    authority_receipts = [
        ({"refusal_state": "no_refusal_reported", "events": [{"type": "turn.completed"}]}, "clear"),
        ({"events": [{"type": "host.error", "refusal_state": "unclassified_host_error"}]},
         "unknown"),
        ({"events": [{"type": "authorization.denied", "host_refusal": True}]}, "denied"),
    ]
    for base, expected in authority_receipts:
        with_telemetry = {**base, "events": [*base["events"], telemetry]}
        assert recovery.authority_disposition(base) == expected
        assert recovery.authority_disposition(with_telemetry) == expected

    manifest = "a" * 64
    effect_receipts = [
        ({
            "role": "reviewer", "outcome_manifest": {"sha256": manifest},
            "thread_id": "synthetic-thread", "group_cleared": True,
            "group_leaked": False, "timed_out": False, "exit_code": 0,
            "events": [
                {"type": "isolation.verified", "profile": "stackos_outcome_review",
                 "outcome_manifest_sha256": manifest},
                {"type": "effects.reconciled", "outcome_manifest_sha256": manifest,
                 "scope": "read_only_outcome", "thread_closed": True,
                 "thread_id": "synthetic-thread"},
            ],
        }, "settled"),
        ({"role": "reviewer", "events": []}, "unknown"),
    ]
    for base, expected_state in effect_receipts:
        with_telemetry = {**base, "events": [*base["events"], telemetry]}
        assert recovery.role_effect_disposition("reviewer", base)["state"] == expected_state
        assert (
            recovery.role_effect_disposition("reviewer", with_telemetry)["state"]
            == expected_state
        )


def test_recovery_ledger_preserves_retry_then_final_cap_without_receipt_integration():
    _, _, delivery = _modules()
    recovery = delivery._recovery
    episode = {"id": "same-failure", "evidence_refs": ["failure-1"]}
    ledger = object.__new__(recovery.Ledger)
    ledger.value = {"events": []}
    ledger.allowance = recovery.Ledger.allowance.__get__(ledger)
    ledger.append = lambda kind, **facts: ledger.value["events"].append({"kind": kind, **facts})
    assert ledger.allowance(episode) == "retry"
    recovery.Ledger.consume(ledger, episode, "retry", "same_thread")
    assert ledger.allowance(episode) == "final"
    recovery.Ledger.consume(ledger, episode, "final", "same_thread")
    assert ledger.allowance(episode) == "stop"

