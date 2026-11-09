"""Real invocation-owned POSIX workers and deterministic native launch barriers."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event

import pytest

from stackos.host_mcp import service
from stackos.host_mcp.adapters import claude_code, codex, gemini_cli, grok, hermes
from stackos.host_mcp.agent_execution import InvocationCancelled, ManagedProcessControl
from stackos.host_mcp.result import AgentRunResult, usage_snapshot


@pytest.fixture(autouse=True)
def clean_registry():
    yield
    service.shutdown_usage()


@pytest.mark.parametrize("force", [False, True])
def test_real_owned_group_stops_parent_child_and_leaves_other_tasks_and_external_alive(
    tmp_path, monkeypatch, force
):
    ready = Event()
    control = ManagedProcessControl()
    external = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    other = ManagedProcessControl()
    other_process = other.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
    child_code = "import time; print('ready', flush=True); time.sleep(30)"
    # The parent owns and reaps its child even on forced parent termination: child exits
    # first on TERM; the parent's TERM handler reaps it then either exits or waits for KILL.
    script = (
        "import subprocess,sys,signal,time\n"
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}],stdout=subprocess.PIPE)\n"
        "child.stdout.readline()\nchild.stdout.close()\n"
        "def stop(*args):\n"
        " child.wait(timeout=2)\n"
        + (" signal.signal(signal.SIGTERM,signal.SIG_IGN)\n" if force else " sys.exit(0)\n")
        + "signal.signal(signal.SIGTERM,stop)\n"
        + f"open({str(tmp_path / 'ready')!r},'w').write(str(child.pid))\n"
        + "while True: time.sleep(1)\n"
    )
    definition = service._definition_by_key("codex")

    def run(*args, process_control, **kwargs):
        process = process_control.spawn([sys.executable, "-c", script])
        ready.set()
        process.wait(timeout=10)
        return AgentRunResult(process.returncode, thread_id="owned-native")

    monkeypatch.setattr(service, "_definition_by_key", lambda _: replace(definition, run_agent=run))
    monkeypatch.setattr(service, "agent_selection_error", lambda *a, **kw: None)
    scope = service.AgentExecutionScope(42, "owned-A", "child-A", "worker", 3, "delivery")
    other_call = service.ActiveInvocation(
        "B",
        replace(scope, task_key="owned-B"),
        "codex",
        None,
        "2026-11-09T00:00:00+00:00",
        process_control=other,
    )
    service._register(other_call)
    try:
        with ThreadPoolExecutor() as workers:
            future = workers.submit(
                service.run_agent,
                "codex",
                tmp_path,
                "bounded worker",
                "",
                {},
                execution_scope=scope,
            )
            assert ready.wait(3)
            # Explicit bounded readiness; no process discovery or session/PID matching.
            from time import monotonic, sleep

            deadline = monotonic() + 3
            while not (tmp_path / "ready").exists() and monotonic() < deadline:
                sleep(0.01)
            assert (tmp_path / "ready").exists()
            calls = service.cancel_task_invocations(42, "owned-A")
            assert len(calls) == 1 and calls[0].scope.attempt == 3
            control = calls[0].process_control
            assert not service.cancel_task_invocations(41, "owned-A")
            assert control.stop() == "stopped", control.stop_error
            result = future.result(timeout=3)
            assert result.exit_code == (-9 if force else 0)
            assert calls[0].retired.is_set()
            child_pid = int((tmp_path / "ready").read_text())
            with pytest.raises(ProcessLookupError):
                os.kill(child_pid, 0)
            assert service.active_execution(42, "owned-A") is None
            assert other_process.poll() is None and external.poll() is None
            assert service.active_execution(42, "owned-B") is not None
    finally:
        control.stop(timeout=0.1)
        other.stop(timeout=0.1)
        external.terminate()
        external.wait(timeout=3)


def test_cancel_before_registration_and_before_handle_never_spawns(tmp_path, monkeypatch):
    control = ManagedProcessControl()
    control.cancelled.set()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("fenced spawn"))
    with pytest.raises(InvocationCancelled):
        control.spawn(["never-launch"])
    assert control.stop() == "prevented"
    control = ManagedProcessControl(lambda: False)
    with pytest.raises(InvocationCancelled):
        control.spawn(["never-launch"])
    monkeypatch.setattr(service, "agent_selection_error", lambda *a, **kw: None)
    result = service.run_agent(
        "codex",
        tmp_path,
        "role",
        "",
        {},
        execution_scope=service.AgentExecutionScope(42, "A", "t", "worker", 1, "delivery"),
        launch_allowed=lambda: False,
    )
    assert "before registration" in result.error
    assert not service.usage_executions()


@pytest.mark.parametrize("adapter", [claude_code, grok, gemini_cli, hermes])
def test_all_shared_adapters_forward_same_private_control(adapter, tmp_path, monkeypatch):
    control = ManagedProcessControl()
    if adapter is claude_code:
        monkeypatch.setattr(adapter.claude_mcp, "resolve_claude_bin", lambda: "/fake/native")
    elif adapter is grok:
        monkeypatch.setattr(adapter, "resolve_grok_bin", lambda *_: "/fake/native")
    elif adapter is gemini_cli:
        monkeypatch.setattr(adapter, "resolve_gemini_bin", lambda: "/fake/native")
    else:
        monkeypatch.setattr(adapter, "resolve_hermes_bin", lambda: "/fake/native")
    seen = []

    def execute(*args, **kwargs):
        seen.append(kwargs["process_control"])
        return AgentRunResult(1, error="controlled")

    monkeypatch.setattr(adapter, "execute_json_agent", execute)
    adapter.run_agent(
        tmp_path,
        "role",
        "bounded",
        {},
        process_control=control,
        model="selected/model",
        provider="selected",
    )
    assert seen == [control]


def test_codex_stream_forwards_control_and_preserves_partial_session(tmp_path, monkeypatch):
    from stackos.host_mcp import progress

    control = ManagedProcessControl()
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: "/fake/codex")

    def stream(command, **kwargs):
        assert kwargs["process_control"] is control
        kwargs["on_session"]("partial-native-session")
        return subprocess.CompletedProcess(
            command,
            -15,
            json.dumps({"type": "thread.started", "thread_id": "partial-native-session"}),
            "",
        )

    monkeypatch.setattr(progress, "run_codex_process", stream)
    result = codex.run_agent(tmp_path, "role", "bounded", {}, process_control=control)
    assert result.thread_id == "partial-native-session" and result.usage is None


def test_interrupted_call_keeps_last_observed_counters_and_retires_reader(tmp_path, monkeypatch):
    from copy import deepcopy

    entered, finish = Event(), Event()
    definition = service._definition_by_key("codex")
    observed = []

    class Reader:
        closed = False

        def read(self):
            return datetime.now(UTC).isoformat(), usage_snapshot(
                "fixture.last-observed", "invocation", 80, 20, cache_read=30
            )

        def close(self):
            self.closed = True

    reader = Reader()

    def run(*args, **kwargs):
        entered.set()
        assert finish.wait(3)
        return AgentRunResult(-15, thread_id="retained-native")

    def changed(call, active):
        if active:
            call.reader = reader
        else:
            observed.append(deepcopy(call.usage))

    monkeypatch.setattr(service, "_definition_by_key", lambda _: replace(definition, run_agent=run))
    monkeypatch.setattr(service, "agent_selection_error", lambda *a, **kw: None)
    scope = service.AgentExecutionScope(42, "usage", "owned", "worker", 1, "delivery")
    with ThreadPoolExecutor() as worker:
        future = worker.submit(
            service.run_agent,
            "codex",
            tmp_path,
            "role",
            "",
            {},
            execution_scope=scope,
            on_execution_change=changed,
        )
        assert entered.wait(3)
        calls = service.cancel_task_invocations(42, "usage")
        assert len(calls) == 1
        finish.set()
        future.result(timeout=3)
    assert observed[0]["measured"]["total"] == 100
    assert observed[0]["measured"]["cache_read"] == 30
    assert reader.closed and calls[0].retired.is_set()
    assert not service.usage_executions()
