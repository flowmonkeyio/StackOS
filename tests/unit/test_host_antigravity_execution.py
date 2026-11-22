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


@pytest.fixture(autouse=True)
def native_home(tmp_path, monkeypatch):
    monkeypatch.setattr(native, "default_home", lambda: tmp_path)


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
    assert (
        result.usage["snapshot"]["total"] == 210
    )  # Cache included once; result not added to steps.
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
    assert result.usage["snapshot"]["total"] == 210


def test_step_updates_count_once_and_resumed_basis_remains_unknown():
    events = documented_stream()[:-1]
    events.append(events[-1])
    partial = native.decode_result(encode(events))
    assert partial.usage["basis"] == "invocation"
    assert partial.usage["snapshot"]["total"] == 210
    resumed = native.decode_result(encode(documented_stream()), resumed=True)
    assert resumed.ok and resumed.usage is None
    previous = normalize_usage(partial.usage, session_id=partial.thread_id, resumed=False)
    measured = normalize_usage(
        resumed.usage, session_id=resumed.thread_id, resumed=True, previous=previous
    )
    assert measured["status"] == "unavailable" and measured["measured"] is None


def test_captured_1514_uncached_input_is_combined_with_cache_once():
    # Sanitized counters from the owned 2026-11-22 1.5.14 native MCP/scratch
    # conversation 9620b0fa-88af-423b-b1b3-3305b1eef9a3. No transcript content.
    # Native terminal usage equals the sum of these distinct completed steps.
    counts = [
        {
            "input_tokens": 10165,
            "output_tokens": 854,
            "cache_read_tokens": 0,
            "total_tokens": 11019,
        },
        {
            "input_tokens": 2184,
            "output_tokens": 229,
            "cache_read_tokens": 9821,
            "total_tokens": 2413,
        },
        {
            "input_tokens": 2504,
            "output_tokens": 349,
            "cache_read_tokens": 9956,
            "total_tokens": 2853,
        },
    ]
    events = documented_stream()[:1]
    for index, usage in enumerate(counts):
        events.append(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "exact-conversation",
                    "step_index": index,
                    "state": "DONE",
                    "usage": usage,
                },
            }
        )
    events.append(events[-1])
    partial = native.decode_result(encode(events))
    assert partial.usage["snapshot"]["input"] == 34630
    assert partial.usage["snapshot"]["cache_read"] == 19777
    assert partial.usage["snapshot"]["total"] == 36062
    terminal = documented_stream()[-1]
    terminal["result"]["usage"] = {key: sum(c[key] for c in counts) for key in counts[0]}
    events.append(terminal)
    assert native.decode_result(encode(events)).usage == partial.usage


def incomplete_usage_stream(*, terminal_recovers=False, missing="cache_read_tokens"):
    events = json.loads(json.dumps(documented_stream()))
    second = json.loads(json.dumps(events[1]))
    second["step_update"]["step_index"] = 4
    second["step_update"]["usage"].pop(missing)
    events.insert(2, second)
    if terminal_recovers:
        events[-1]["result"]["usage"] = {key: value * 2 for key, value in COUNTS.items()}
    else:
        events[-1]["result"]["usage"].pop(missing)
    return events


@pytest.mark.parametrize(
    "missing", ["input_tokens", "cache_read_tokens", "output_tokens", "total_tokens"]
)
@pytest.mark.parametrize("terminal_recovers", [False, True])
def test_mixed_incomplete_steps_need_authoritative_terminal_usage(missing, terminal_recovers):
    events = incomplete_usage_stream(terminal_recovers=terminal_recovers, missing=missing)
    partial = native.decode_result(encode(events[:-1]))
    assert partial.usage is None
    result = native.decode_result(encode(events))
    assert result.ok  # Missing telemetry does not invalidate the structured outcome.
    if terminal_recovers:
        assert result.usage["snapshot"]["total"] == 420
        assert result.usage["snapshot"]["cache_read"] == 160
    else:
        assert result.usage is None


@pytest.mark.parametrize("step_type", ["agent_response", "checkpoint"])
def test_completed_model_step_without_usage_invalidates_aggregate(step_type):
    events = incomplete_usage_stream()
    events[2]["step_update"]["step_type"] = step_type
    del events[2]["step_update"]["usage"]
    assert native.decode_result(encode(events)).usage is None


def test_incomplete_duplicate_does_not_retain_previous_valid_step_counts():
    events = incomplete_usage_stream()
    events[2]["step_update"]["step_index"] = events[1]["step_update"]["step_index"]
    assert native.decode_result(encode(events)).usage is None


@pytest.mark.parametrize("terminal_recovers", [False, True])
@pytest.mark.parametrize("native_failure", [False, True])
def test_native_incomplete_usage_invalidates_live_and_final_consumers(
    tmp_path, monkeypatch, terminal_recovers, native_failure
):
    events = incomplete_usage_stream(terminal_recovers=terminal_recovers)
    if native_failure:
        events[2]["step_update"]["tool_info"] = {"error": "required tool permission denied"}
    executable_fixture(tmp_path, monkeypatch, events=events)
    observed, handoffs = [], []
    observe_usage = service._observe_usage

    def observe(call, raw):
        observe_usage(call, raw)
        observed.append((call.projection(), service.provisional_usage(1)))

    monkeypatch.setattr(service, "_observe_usage", observe)
    result = run_agent(
        "antigravity",
        tmp_path,
        "complete instructions",
        "assignment packet",
        SCHEMA,
        execution_scope=service.AgentExecutionScope(1, "fixture", "child", "worker", 1, "delivery"),
        on_execution_change=lambda call, active: (
            handoffs.append((call.projection(), service.provisional_usage(1)))
            if not active
            else None
        ),
    )
    assert result.ok is (not native_failure)
    assert observed[0][0]["live_usage"]["usage"]["measured"]["total"] == 210
    assert observed[0][1][1, "child"][0][2]["measured"]["total"] == 210
    assert observed[1][0]["live_usage"] is None
    assert observed[1][1] == {}
    if terminal_recovers:
        assert result.usage["snapshot"]["total"] == 420
        assert handoffs[0][0]["live_usage"]["usage"]["measured"]["total"] == 420
        assert handoffs[0][1][1, "child"][0][2]["measured"]["total"] == 420
    else:
        assert result.usage is None
        assert handoffs[0][0]["live_usage"] is None
        assert handoffs[0][1] == {}
    assert not service._active_invocations


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
    events[-1]["result"]["usage"] = {**COUNTS, "thinking_tokens": 999}
    result = native.decode_result(encode(events))
    assert result.usage["snapshot"] == {
        "input": 180,
        "output": 30,
        "total": 210,
        "cache_read": 80,
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


def executable_fixture(
    tmp_path, monkeypatch, *, events=None, diagnostic="", pause=0, exit_code=0, expected_resume=None
):
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
        f"expected_resume = {expected_resume!r}\n"
        "actual_resume = None\n"
        "if '--conversation' in args: actual_resume = args[args.index('--conversation') + 1]\n"
        "assert actual_resume == expected_resume\n"
        "assert '--json-schema' in args and '--disable-slash-commands' in args\n"
        "assert 'complete instructions' in args[args.index('--print')+1]\n"
        "assert 'assignment packet' in args[args.index('--print')+1]\n"
        f"assert os.getcwd() == {str(tmp_path)!r}\n"
        f"print({encode(events)!r}, end='', flush=True)\n"
        f"print({diagnostic!r}, file=sys.stderr, flush=True)\n"
        f"open({str(tmp_path / 'emitted')!r}, 'w').close()\n"
        f"time.sleep({pause!r})\nsys.exit({exit_code!r})\n"
    )
    script.chmod(0o755)
    monkeypatch.setattr(native, "resolve_antigravity_bin", lambda: str(script))
    return script


@pytest.mark.parametrize("resume", [None, "exact-conversation", "different-conversation"])
@pytest.mark.parametrize("exit_code", [0, 2])
def test_registered_native_caller_and_exact_resume(tmp_path, monkeypatch, resume, exit_code):
    executable_fixture(tmp_path, monkeypatch, exit_code=exit_code, expected_resume=resume)
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
    spawn = control.spawn

    def ready_spawn(*args, **kwargs):
        import time

        child = spawn(*args, **kwargs)
        deadline = time.monotonic() + 2
        while (
            not (tmp_path / "emitted").exists()
            and child.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert (tmp_path / "emitted").exists(), "fixture must emit before testing drain retention"
        return child

    monkeypatch.setattr(control, "spawn", ready_spawn)
    result = native.run_agent(
        tmp_path, "complete instructions", "assignment packet", SCHEMA, process_control=control
    )
    assert not result.ok and "external deadline" in result.error
    assert result.thread_id == ("exact-conversation" if events else None)
    if events:
        assert result.usage["snapshot"]["total"] == 210
    assert control.retirement_status() == "stopped"


def test_cancellation_retains_observed_session_and_usage(tmp_path, monkeypatch):
    executable_fixture(tmp_path, monkeypatch, events=documented_stream()[:-1], pause=30)
    control = ManagedProcessControl()
    identities = []
    result = native.run_agent(
        tmp_path,
        "complete instructions",
        "assignment packet",
        SCHEMA,
        process_control=control,
        on_session=identities.append,
        on_usage=lambda _: control.stop(),
    )
    assert not result.ok and result.thread_id == "exact-conversation"
    assert identities == ["exact-conversation"]
    assert result.usage["snapshot"]["total"] == 210
    assert control.retirement_status() == "stopped"


def test_early_init_and_live_usage_reach_invocation_before_interruption(tmp_path, monkeypatch):
    from threading import Event, Thread

    executable_fixture(tmp_path, monkeypatch, events=documented_stream()[:-1], pause=30)
    attached = Event()
    calls, handoffs, results = [], [], []

    def observe(call):
        calls.append(call)
        attached.set()

    worker = Thread(
        target=lambda: results.append(
            run_agent(
                "antigravity",
                tmp_path,
                "complete instructions",
                "assignment packet",
                SCHEMA,
                execution_scope=service.AgentExecutionScope(
                    1, "fixture", "child", "worker", 1, "delivery"
                ),
                on_session_attached=observe,
                on_execution_change=lambda call, active: (
                    handoffs.append(call.projection()) if not active else None
                ),
            )
        )
    )
    worker.start()
    try:
        assert attached.wait(2), "native init must be published before exit"
        call = calls[0]
        assert call.thread_id == "exact-conversation" and not call.finished
        # Wait for the subsequent DONE observation, independently of process exit.
        import time

        deadline = time.monotonic() + 2
        while call.usage is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert call.usage["measured"]["total"] == 210
    finally:
        for call in list(service._active_invocations.values()):
            if call.scope.task_key == "fixture":
                service.retire_invocation(call)
        worker.join(4)
    assert not worker.is_alive()
    assert not results[0].ok and results[0].thread_id == "exact-conversation"
    assert handoffs[0]["thread_id"] == "exact-conversation"
    assert handoffs[0]["live_usage"]["usage"]["measured"]["total"] == 210
    assert not service._active_invocations


def test_antigravity_session_persistence_failure_retires_native_process(tmp_path, monkeypatch):
    executable_fixture(tmp_path, monkeypatch, events=documented_stream()[:-1], pause=30)
    seen = []

    def persist(call):
        seen.append(call)
        raise RuntimeError("identity commit failed")

    result = run_agent(
        "antigravity",
        tmp_path,
        "complete instructions",
        "assignment packet",
        SCHEMA,
        execution_scope=service.AgentExecutionScope(1, "fixture", "child", "worker", 1, "delivery"),
        on_session_attached=persist,
    )
    assert not result.ok and "identity commit failed" in result.error
    assert result.thread_id == "exact-conversation"
    assert seen[0].process_control.retirement_status() == "stopped"
    assert not service._active_invocations


def test_interrupted_init_resumes_exact_retained_conversation(tmp_path, monkeypatch):
    from threading import Event, Thread

    executable_fixture(tmp_path, monkeypatch, events=documented_stream()[:-1], pause=30)
    scope = service.AgentExecutionScope(1, "fixture", "child", "worker", 1, "delivery")
    retained, handoffs, results = [], [], []
    attached = Event()

    def observe(call):
        retained.append(call.thread_id)
        attached.set()

    worker = Thread(
        target=lambda: results.append(
            run_agent(
                "antigravity",
                tmp_path,
                "complete instructions",
                "assignment packet",
                SCHEMA,
                execution_scope=scope,
                on_session_attached=observe,
            )
        )
    )
    worker.start()
    try:
        assert attached.wait(2)
    finally:
        for call in list(service._active_invocations.values()):
            if call.scope.task_key == "fixture":
                service.retire_invocation(call)
        worker.join(4)
    assert not worker.is_alive()
    first = results[0]
    assert not first.ok and retained == ["exact-conversation"]
    assert first.thread_id == retained[0]
    assert not service._active_invocations
    executable_fixture(tmp_path, monkeypatch, expected_resume=retained[0])
    resumed = run_agent(
        "antigravity",
        tmp_path,
        "complete instructions",
        "assignment packet",
        SCHEMA,
        resume_thread_id=retained[0],
        execution_scope=scope,
        previous_usage=normalize_usage(first.usage, session_id=first.thread_id, resumed=False),
        on_session_attached=lambda call: retained.append(call.thread_id),
        on_execution_change=lambda call, active: (
            handoffs.append(call.projection()) if not active else None
        ),
    )
    assert resumed.ok and resumed.thread_id == "exact-conversation"
    assert retained == ["exact-conversation", "exact-conversation"]
    assert resumed.usage is None  # The old receipt is not a native baseline.
    assert handoffs[0]["live_usage"] is None
    assert not service._active_invocations


def test_session_local_policy_keeps_scoped_mcp_and_sandbox_controls(tmp_path, monkeypatch):
    from stackos.host_mcp.result import AgentRunResult

    commands = []
    monkeypatch.setattr(native, "resolve_antigravity_bin", lambda: "agy")
    monkeypatch.setattr(
        native,
        "execute_json_agent",
        lambda command, *a, **kw: commands.append(command) or AgentRunResult(1),
    )
    native.run_agent(tmp_path, "role", "packet", SCHEMA)
    settings = json.loads(commands[0][commands[0].index("--settings") + 1])
    assert settings["toolPermission"] == "proceed-in-sandbox"
    assert settings["enableTerminalSandbox"] is True
    assert settings["allowNonWorkspaceAccess"] is False
    assert settings["permissions"]["allow"] == [
        "mcp(stackos/workspace_startSession)",
        "mcp(stackos/workspace_resolve)",
        "mcp(stackos/toolbox_describe)",
        "mcp(stackos/toolbox_call)",
        "mcp(stackos/browser_runtime_status)",
        "mcp(stackos/browser_profile_list)",
        "mcp(stackos/browser_profile_create)",
        "mcp(stackos/browser_session_list)",
        "mcp(stackos/browser_session_start)",
        "mcp(stackos/browser_session_status)",
        "mcp(stackos/browser_session_stop)",
    ]
    assert not any(
        rule in {"command(*)", "mcp(*)", "mcp(stackos/*)"}
        for rule in settings["permissions"]["allow"]
    )


def test_session_policy_preserves_saved_ask_and_deny_without_writes(tmp_path):
    path = tmp_path / ".gemini/antigravity-cli/settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "permissions": {
                    "deny": ["mcp(stackos/toolbox_call)"],
                    "ask": ["command(git push)"],
                    "allow": ["command(git status)"],
                }
            }
        )
    )
    before = path.read_bytes()
    settings = native._session_settings("normal", tmp_path)
    assert settings["permissions"]["deny"] == ["mcp(stackos/toolbox_call)"]
    assert settings["permissions"]["ask"] == ["command(git push)"]
    assert settings["permissions"]["allow"][0] == "command(git status)"
    assert settings["agentMode"] == ""
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "value", ["broken", "[]", '{"permissions":false}', '{"toolPermission":"strict"}']
)
def test_unusable_native_permission_settings_fail_before_discovery(tmp_path, monkeypatch, value):
    path = tmp_path / ".gemini/antigravity-cli/settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(value)
    monkeypatch.setattr(native, "resolve_antigravity_bin", lambda: pytest.fail("must not discover"))
    result = native.run_agent(tmp_path, "role", "packet", SCHEMA)
    assert not result.ok and result.exit_code is None


@pytest.mark.parametrize("cache", [None, True, -1])
def test_unknown_required_cache_bucket_leaves_full_usage_unknown(cache):
    events = json.loads(json.dumps(documented_stream()))
    events.pop(1)
    events[-1]["result"]["usage"]["cache_read_tokens"] = cache
    assert native.decode_result(encode(events)).usage is None


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
        "cache_read_tokens": 80,
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
    assert result.thread_id == "exact-conversation" and result.usage["snapshot"]["total"] == 230
    assert calls == [0.01, 2]
    assert all(pipe.closed for pipe in (child.stdin, child.stdout, child.stderr))


@pytest.mark.parametrize(
    "selection",
    [
        {"sandbox": "read-only"},
        {"provider": "google"},
        {"profile": "work"},
        {"model": "gemini-3.8-flash-high", "model_reasoning_effort": "max"},
        {"resume_thread_id": "latest"},
    ],
)
def test_unsupported_choices_reject_before_discovery(tmp_path, monkeypatch, selection):
    monkeypatch.setattr(native, "resolve_antigravity_bin", lambda: pytest.fail("must not discover"))
    assert not run_agent("antigravity", tmp_path, "role", "packet", SCHEMA, **selection).ok


def test_unknown_model_rejects_after_current_native_catalog_read(tmp_path, monkeypatch):
    from dataclasses import asdict

    definition = service._definition_by_key("antigravity")
    reads = []
    monkeypatch.setattr(
        service,
        "execution_catalog",
        lambda *a, **kw: reads.append(True) or asdict(definition.policy.agent_execution),
    )
    monkeypatch.setattr(
        native, "execute_json_agent", lambda *a, **kw: pytest.fail("must not launch")
    )
    result = run_agent(
        "antigravity", tmp_path, "role", "packet", SCHEMA, model="not-a-native-model"
    )
    assert not result.ok and "native model catalog" in result.error
    assert reads == [True]


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
