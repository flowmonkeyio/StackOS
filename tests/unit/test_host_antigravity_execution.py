"""Independent documented agy 1.5.13 envelopes, never live model evidence.

Source: https://antigravity.google/docs/cli/headless/ (2026-11-21), init,
step_update and result examples. Counts/session IDs/content are synthetic.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from stackos.host_mcp import run_agent, service
from stackos.host_mcp.adapters import antigravity_execution as native
from stackos.host_mcp.adapters.retired_gemini import remove_owned_registration
from stackos.host_mcp.agent_execution import ManagedProcessControl
from stackos.host_mcp.result import normalize_usage

SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}
COUNTS = {
    "input_tokens": 100,
    "output_tokens": 30,
    "total_tokens": 130,
    "thinking_tokens": 20,
    "cache_read_tokens": 80,
}


def documented_stream(*, status="SUCCESS", structured=True):
    result = {
        "conversation_id": "exact-conversation",
        "status": status,
        "response": '{"summary":"untrusted assistant text"}',
        "usage": COUNTS,
    }
    if structured:
        result["structured_output"] = {"summary": "native schema value"}
    return [
        {
            "event": "init",
            "conversation_id": "exact-conversation",
            "init": {"cwd": "/synthetic/project", "permission_mode": "request-review"},
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": "exact-conversation",
                "step_index": 3,
                "state": "DONE",
                "step_type": "agent_response",
                "usage": COUNTS,
            },
        },
        {"event": "result", "result": result},
    ]


def encode(events):
    return "\n".join(json.dumps(event) for event in events) + "\n"


@pytest.mark.parametrize("status", ["SUCCESS", "ERROR", "FAILED", "CANCELLED", "UNKNOWN"])
@pytest.mark.parametrize("structured", [True, False])
def test_terminal_status_and_native_schema_are_independent(status, structured):
    result = native.decode_result(encode(documented_stream(status=status, structured=structured)))
    assert result.ok is (status == "SUCCESS" and structured)
    assert result.thread_id == "exact-conversation"
    assert result.usage["snapshot"]["total"] == 130  # Result is not added to steps.
    if result.ok:
        assert result.result == {"summary": "native schema value"}


@pytest.mark.parametrize(
    "fault", ["truncated", "invalid", "duplicate", "identity", "schema", "missing_terminal_id"]
)
def test_partial_or_conflicting_stream_cannot_succeed(fault):
    events = documented_stream()
    if fault == "truncated":
        events.pop()
    elif fault == "duplicate":
        events.append(events[-1])
    elif fault == "identity":
        events[-1]["result"]["conversation_id"] = "foreign"
    elif fault == "schema":
        events[-1]["result"]["structured_output"] = None
    elif fault == "missing_terminal_id":
        del events[-1]["result"]["conversation_id"]
    result = native.decode_result(encode(events) + ("broken\n" if fault == "invalid" else ""))
    assert not result.ok and result.error
    assert result.thread_id == "exact-conversation"
    assert result.usage["snapshot"]["total"] == 130


def test_step_updates_count_once_and_resumed_basis_remains_unknown():
    events = documented_stream()[:-1]
    events.append(events[-1])
    partial = native.decode_result(encode(events))
    assert partial.usage["basis"] == "invocation"
    assert partial.usage["snapshot"]["total"] == 130
    resumed = native.decode_result(encode(documented_stream()), resumed=True)
    assert resumed.ok and resumed.usage is None
    previous = normalize_usage(partial.usage, session_id=partial.thread_id, resumed=False)
    measured = normalize_usage(
        resumed.usage, session_id=resumed.thread_id, resumed=True, previous=previous
    )
    assert measured["status"] == "unavailable" and measured["measured"] is None


@pytest.mark.parametrize(
    "counts",
    [
        None,
        [],
        {"input_tokens": True, "output_tokens": 2},
        {"input_tokens": 10, "output_tokens": 2, "total_tokens": 99},
    ],
)
def test_unproven_or_malformed_counters_stay_unknown(counts):
    events = documented_stream()
    events.pop(1)
    events[-1]["result"]["usage"] = counts
    result = native.decode_result(encode(events))
    assert result.ok and result.usage is None


def test_invalid_optional_details_do_not_invent_cache_write_or_erase_base():
    events = documented_stream()
    events[-1]["result"]["usage"] = {**COUNTS, "cache_read_tokens": True, "thinking_tokens": 999}
    result = native.decode_result(encode(events))
    assert result.usage["snapshot"] == {
        "input": 100,
        "output": 30,
        "total": 130,
        "cache_read": None,
        "cache_write": None,
        "reasoning": None,
    }


@pytest.mark.parametrize(
    "field,value", [("cwd", "/foreign"), ("permission_mode", "always-proceed")]
)
def test_native_init_mismatch_cannot_succeed(field, value):
    events = documented_stream()
    events[0]["init"][field] = value
    result = native.decode_result(encode(events), project_dir=Path("/synthetic/project"))
    assert not result.ok and "unexpected native" in result.error


def executable_fixture(tmp_path, monkeypatch, *, events=None, diagnostic="", pause=0, exit_code=0):
    # Exercise the actual shared Popen/deadline boundary and registered runner.
    script = tmp_path / "agy-fixture"
    events = json.loads(json.dumps(events if events is not None else documented_stream()))
    if events:
        events[0]["init"]["cwd"] = str(tmp_path.resolve())
    script.write_text(
        f"#!{sys.executable}\nimport json, os, sys, time\n"
        "args = sys.argv[1:]\n"
        "assert '--dangerously-skip-permissions' not in args\n"
        "assert '--continue' not in args and '-c' not in args\n"
        "assert '--json-schema' in args and '--disable-slash-commands' in args\n"
        "assert 'complete instructions' in args[args.index('--print')+1]\n"
        "assert 'assignment packet' in args[args.index('--print')+1]\n"
        f"assert os.getcwd() == {str(tmp_path)!r}\n"
        f"print({encode(events)!r}, end='', flush=True)\n"
        f"print({diagnostic!r}, file=sys.stderr, flush=True)\n"
        f"time.sleep({pause!r})\nsys.exit({exit_code!r})\n"
    )
    script.chmod(0o755)
    monkeypatch.setattr(native, "resolve_antigravity_bin", lambda: str(script))
    return script


@pytest.mark.parametrize("resume", [None, "exact-conversation", "different-conversation"])
@pytest.mark.parametrize("exit_code", [0, 2])
def test_registered_native_caller_and_exact_resume(tmp_path, monkeypatch, resume, exit_code):
    executable_fixture(tmp_path, monkeypatch, exit_code=exit_code)
    result = run_agent(
        "antigravity",
        tmp_path,
        "complete instructions",
        "assignment packet",
        SCHEMA,
        resume_thread_id=resume,
    )
    assert result.ok is (exit_code == 0 and resume != "different-conversation")
    assert result.thread_id == "exact-conversation"
    assert result.exit_code == exit_code


@pytest.mark.parametrize(
    "diagnostic",
    [
        "Tool soft-denied in headless mode",
        "Permission denied for run_command",
        "Tool requires confirmation; allow it in permissions.allow",
        "approval required",
    ],
)
def test_zero_exit_soft_denial_still_fails(tmp_path, monkeypatch, diagnostic):
    executable_fixture(tmp_path, monkeypatch, diagnostic=diagnostic)
    result = run_agent(
        "antigravity", tmp_path, "complete instructions", "assignment packet", SCHEMA
    )
    assert result.exit_code == 0 and not result.ok
    assert "soft-denied" in result.error and result.thread_id == "exact-conversation"


@pytest.mark.parametrize("events", [[], documented_stream()[:-1], documented_stream()])
def test_external_auth_deadline_preserves_observation_and_retires_process(
    tmp_path, monkeypatch, events
):
    executable_fixture(tmp_path, monkeypatch, events=events, pause=30)
    monkeypatch.setattr(native, "RUN_TIMEOUT_SECONDS", 0.2)
    control = ManagedProcessControl()
    result = native.run_agent(
        tmp_path, "complete instructions", "assignment packet", SCHEMA, process_control=control
    )
    assert not result.ok and "external deadline" in result.error
    assert result.thread_id == ("exact-conversation" if events else None)
    if events:
        assert result.usage["snapshot"]["total"] == 130
    assert control.retirement_status() == "stopped"


def test_cancellation_retains_observed_session_and_usage(tmp_path, monkeypatch):
    from threading import Timer

    executable_fixture(tmp_path, monkeypatch, events=documented_stream()[:-1], pause=30)
    control = ManagedProcessControl()
    timer = Timer(0.2, control.stop)
    timer.start()
    try:
        result = native.run_agent(
            tmp_path, "complete instructions", "assignment packet", SCHEMA, process_control=control
        )
    finally:
        timer.join()
    assert not result.ok and result.thread_id == "exact-conversation"
    assert result.usage["snapshot"]["total"] == 130
    assert control.retirement_status() == "stopped"


def test_unconfirmed_retirement_returns_bounded_latest_observation(tmp_path, monkeypatch):
    from io import StringIO
    from types import SimpleNamespace

    from stackos.host_mcp.agent_execution import execute_json_agent

    partial = documented_stream()[:-1]
    later = json.loads(json.dumps(partial))
    later[-1]["step_update"]["usage"] = {
        "input_tokens": 110,
        "output_tokens": 40,
        "total_tokens": 150,
    }
    calls = []

    def communicate(*args, **kwargs):
        calls.append(kwargs["timeout"])
        output = encode(partial if len(calls) == 1 else later).encode()
        raise subprocess.TimeoutExpired(["fixture"], kwargs["timeout"], output=output)

    child = SimpleNamespace(
        communicate=communicate,
        returncode=None,
        stdin=StringIO(),
        stdout=StringIO(),
        stderr=StringIO(),
    )
    control = ManagedProcessControl()
    monkeypatch.setattr(control, "spawn", lambda *a, **kw: child)
    monkeypatch.setattr(control, "stop", lambda: "unconfirmed")
    result = execute_json_agent(
        [sys.executable],
        tmp_path,
        "",
        SCHEMA,
        "Antigravity",
        native.decode_result,
        process_control=control,
        timeout=0.01,
    )
    assert not result.ok and result.exit_code is None
    assert "retirement unconfirmed" in result.error
    assert result.thread_id == "exact-conversation" and result.usage["snapshot"]["total"] == 150
    assert calls == [0.01, 2]
    assert all(pipe.closed for pipe in (child.stdin, child.stdout, child.stderr))


@pytest.mark.parametrize(
    "selection",
    [
        {"sandbox": "read-only"},
        {"model": "not-a-native-model"},
        {"provider": "google"},
        {"profile": "work"},
        {"model": "gemini-3.8-flash-high", "model_reasoning_effort": "max"},
        {"resume_thread_id": "latest"},
    ],
)
def test_unsupported_choices_reject_before_discovery(tmp_path, monkeypatch, selection):
    monkeypatch.setattr(native, "resolve_antigravity_bin", lambda: pytest.fail("must not discover"))
    assert not run_agent("antigravity", tmp_path, "role", "packet", SCHEMA, **selection).ok


def test_retired_gemini_is_never_aliased_or_launched(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("retired launch"))
    assert service.agent_review_sandbox("antigravity") == "normal"
    result = run_agent("gemini-cli", tmp_path, "historical role", "packet", SCHEMA)
    assert not result.ok
    assert all(row.policy.host_key != "gemini-cli" for row in service._definitions())


@pytest.mark.parametrize("foreign", [False, True])
def test_retired_cleanup_only_removes_owned_wiring(tmp_path, foreign):
    path = tmp_path / ".gemini/settings.json"
    path.parent.mkdir()
    entry = {
        "command": "foreign" if foreign else "stackos",
        "args": ["mcp-bridge", "--runtime", "gemini-cli"],
    }
    value = {"theme": "keep", "mcpServers": {"stackos": entry, "foreign": {"command": "other"}}}
    path.write_text(json.dumps(value))
    original = path.read_bytes()
    result = remove_owned_registration(tmp_path)
    if foreign:
        assert result is None and path.read_bytes() == original
    else:
        assert result.ok and result.status == "removed"
        assert json.loads(path.read_text()) == {
            "theme": "keep",
            "mcpServers": {"foreign": {"command": "other"}},
        }
        assert remove_owned_registration(tmp_path) is None


@pytest.mark.parametrize(
    "extra",
    [
        {"env": {"FOREIGN_SETTING": "keep"}},
        {"serverUrl": "https://example.invalid/mcp"},
        {"args": ["mcp-bridge", "--runtime", "antigravity"]},
    ],
)
def test_retired_cleanup_preserves_ambiguous_entries_byte_for_byte(tmp_path, extra):
    path = tmp_path / ".gemini/settings.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "stackos": {
                        "command": "stackos",
                        "args": ["mcp-bridge", "--runtime", "gemini-cli"],
                        **extra,
                    }
                }
            }
        )
    )
    before = path.read_bytes()
    assert remove_owned_registration(tmp_path) is None and path.read_bytes() == before
