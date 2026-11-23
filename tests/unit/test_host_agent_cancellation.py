"""Real invocation-owned POSIX workers and deterministic native launch barriers."""

import json
import os
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace

import pytest

from stackos.host_mcp import agent_execution, service
from stackos.host_mcp.adapters import antigravity_execution, claude_code, codex, grok, hermes
from stackos.host_mcp.agent_execution import InvocationCancelled, ManagedProcessControl
from stackos.host_mcp.result import AgentRunResult, usage_snapshot


@pytest.fixture(autouse=True)
def clean_registry():
    yield
    service.shutdown_usage()


@pytest.fixture
def process_stop_boundary(monkeypatch):
    """Script syscall outcomes and elapsed time without signaling real PIDs."""
    control = ManagedProcessControl()
    process = SimpleNamespace(pid=12345, returncode=None)
    process.poll = lambda: process.returncode
    control._process = process
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(agent_execution, "monotonic", lambda: clock.now)
    monkeypatch.setattr(
        agent_execution, "sleep", lambda delay: setattr(clock, "now", clock.now + delay)
    )
    return control, process, clock


def test_recovered_identity_holds_surviving_child_without_signalling_it():
    control = ManagedProcessControl()
    child = control.spawn(
        [
            sys.executable,
            "-c",
            "import subprocess,sys,time; "
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'], "
            "stdout=subprocess.DEVNULL); print(p.pid, flush=True); time.sleep(.3)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert control.identity is not None
        descendant = int(child.stdout.readline())
        child.stdout.close()
        child.wait(timeout=3)
        assert ManagedProcessControl.observe_identity(control.identity) == "surviving_native_group"
        os.kill(descendant, 0)
    finally:
        control.stop()


def test_spawn_receipt_failure_retires_its_actual_process():
    def fail():
        raise RuntimeError("receipt commit failed")

    control = ManagedProcessControl(on_spawn=fail)
    with pytest.raises(RuntimeError, match="receipt commit failed"):
        control.spawn([sys.executable, "-c", "import time;time.sleep(30)"])
    assert control.retirement_status() == "stopped"
    assert ManagedProcessControl.observe_identity(control.identity) == "stopped"


def test_recovered_identity_never_trusts_reused_pid_or_unknown_probe(monkeypatch):
    identity = {"schema_version": 1, "pid": 12345, "pgid": 12345, "started": "original"}
    probes = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: probes.append((pid, sig)))
    monkeypatch.setattr(
        ManagedProcessControl,
        "_identity",
        staticmethod(lambda _pid: {**identity, "started": "new"}),
    )
    assert ManagedProcessControl.observe_identity(identity) == "changed_native_owner"
    assert probes == [(12345, 0)]
    assert ManagedProcessControl.observe_identity(None) == "unknown_native_owner"


@pytest.mark.parametrize("phase", ["delivery", "review"])
@pytest.mark.parametrize("resumed", [False, True])
def test_hermes_managed_gateway_route_rejects_before_submission(
    tmp_path, monkeypatch, phase, resumed
):
    def forbidden(*args, **kwargs):
        pytest.fail("Managed Hermes must reject before native discovery or submission")

    monkeypatch.setattr(hermes, "resolve_hermes_bin", forbidden)
    options = {
        "model": "selected/model",
        "provider": "openrouter",
        "sandbox": "normal" if phase == "review" else "workspace-write",
        "resume_thread_id": "exact-owned-session" if resumed else None,
    }
    result = service.run_agent(
        "hermes",
        tmp_path,
        "role",
        "work",
        {},
        execution_scope=service.AgentExecutionScope(42, "A", "t", "worker", 1, phase),
        **options,
    )
    assert not result.ok and "gateway" in result.error and "managed" in result.error
    assert not service.usage_executions()
    result = hermes.run_agent(
        tmp_path, "role", "work", {}, process_control=ManagedProcessControl(), **options
    )
    assert not result.ok and "gateway" in result.error


def test_hermes_retained_viewer_exit_cannot_confirm_gateway_retirement(tmp_path):
    # v0.23.3 gateway_client: closing a viewer never stops its authority.
    # https://github.com/NousResearch/hermes-agent/blob/v0.23.3/hermes_cli/gateway_client.py
    # This owned two-process protocol fixture deliberately keeps gateway work
    # independent of the CLI group; no native Hermes/provider is installed.
    marker = tmp_path / "accepted-work"
    gateway = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; "
            "print('accepted',flush=True); sys.stdin.readline(); "
            "Path(sys.argv[1]).write_text('completed after viewer exit')",
            str(marker),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    control = ManagedProcessControl()
    call = service.ActiveInvocation(
        "legacy-hermes",
        service.AgentExecutionScope(42, "A", "t", "worker"),
        "hermes",
        "selected/model",
        datetime.now(UTC).isoformat(),
        process_control=control,
    )
    try:
        assert gateway.stdout.readline().strip() == "accepted"
        viewer = control.spawn([sys.executable, "-c", "import time;time.sleep(30)"])
        service._register(call)
        call.cleanup_complete = True
        assert service.retire_invocation(call) == "unconfirmed"
        assert viewer.poll() is not None
        assert control.retirement_status() == "stopped"
        assert not call.retired.is_set() and call.invocation_id in service._active_invocations
        assert gateway.poll() is None  # Never kill the shared authority/foreign sessions.
        gateway.communicate("release\n", timeout=3)
        assert gateway.returncode == 0 and marker.exists()
    finally:
        control.stop()
        if gateway.poll() is None:
            gateway.terminate()
        gateway.communicate(timeout=3)
        service._active_invocations.pop(call.invocation_id, None)


@pytest.mark.parametrize("retiring_signal", [signal.SIGTERM, signal.SIGKILL])
def test_stop_confirms_group_after_transient_probe_denial(
    process_stop_boundary, monkeypatch, retiring_signal
):
    control, process, clock = process_stop_boundary
    signals = []
    probes_after_signal = []

    def killpg(group, sig):
        assert group == process.pid
        if sig:
            signals.append(sig)
            if sig == retiring_signal:
                process.returncode = -sig
        elif retiring_signal in signals:
            probes_after_signal.append(sig)
            if len(probes_after_signal) == 1:
                raise PermissionError(1, "group still retiring")
            raise ProcessLookupError(3, "whole group gone")

    monkeypatch.setattr(os, "killpg", killpg)
    assert control.stop(timeout=0.1) == "stopped", control.stop_error
    assert probes_after_signal == [0, 0]
    assert signals == (
        [signal.SIGTERM] if retiring_signal == signal.SIGTERM else [signal.SIGTERM, signal.SIGKILL]
    )
    assert control.cancelled.is_set() and control.stop_error is None
    assert clock.now <= 0.22


def test_stop_persistent_probe_denial_never_confirms_from_root_exit(
    process_stop_boundary, monkeypatch
):
    control, process, clock = process_stop_boundary
    process.returncode = 0
    signals = []
    denied = True

    def killpg(group, sig):
        assert group == process.pid
        if sig:
            signals.append(sig)
        elif denied:
            raise PermissionError(1, "confirmation denied")
        else:
            raise ProcessLookupError(3, "whole group gone")

    monkeypatch.setattr(os, "killpg", killpg)
    assert control.retirement_status() == "unconfirmed"
    assert not control.cancelled.is_set()  # Observation cannot manufacture cancellation.
    assert control.stop(timeout=0.1) == "unconfirmed"
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert 0.2 <= clock.now <= 0.24
    assert "PermissionError (errno 1)" in control.stop_error
    assert "deadline" in control.stop_error
    # A later explicit stop can confirm exit, without retaining a stale failure.
    denied = False
    assert control.stop(timeout=0.1) == "stopped"
    assert control.stop_error is None
    assert control.retirement_status() == "stopped"


@pytest.mark.parametrize("survives_kill", [False, True])
def test_stop_root_exit_does_not_confirm_surviving_descendants(
    process_stop_boundary, monkeypatch, survives_kill
):
    control, process, clock = process_stop_boundary
    process.returncode = 0
    signals = []

    def killpg(group, sig):
        assert group == process.pid
        if sig:
            signals.append(sig)
        elif signal.SIGKILL in signals and not survives_kill:
            raise ProcessLookupError(3, "whole group gone")

    monkeypatch.setattr(os, "killpg", killpg)
    assert control.stop(timeout=0.1) == ("unconfirmed" if survives_kill else "stopped")
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert clock.now <= 0.24
    if survives_kill:
        assert "deadline" in control.stop_error


@pytest.mark.parametrize(
    "failure", [PermissionError(1, "signal denied"), OSError(5, "probe failed")]
)
def test_stop_signal_denial_or_unavailable_confirmation_stays_unconfirmed(
    process_stop_boundary, monkeypatch, failure
):
    control, process, _ = process_stop_boundary
    signals = []

    def killpg(group, sig):
        assert group == process.pid
        if sig:
            signals.append(sig)
        if sig or not isinstance(failure, PermissionError):
            raise failure

    monkeypatch.setattr(os, "killpg", killpg)
    assert control.stop(timeout=0.1) == "unconfirmed"
    assert f"{type(failure).__name__} (errno {failure.errno})" in control.stop_error
    assert signals == ([signal.SIGTERM] if isinstance(failure, PermissionError) else [])


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
        "import os,subprocess,sys,signal,time\n"
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}],stdout=subprocess.PIPE)\n"
        "child.stdout.readline()\nchild.stdout.close()\n"
        "def stop(*args):\n"
        " child.wait(timeout=2)\n"
        + (" signal.signal(signal.SIGTERM,signal.SIG_IGN)\n" if force else " sys.exit(0)\n")
        + "signal.signal(signal.SIGTERM,stop)\n"
        # Publish readiness after closing the writer. A signal during a temporary
        # TextIOWrapper's destructor can swallow SystemExit and force a false KILL.
        + f"with open({str(tmp_path / 'ready.pending')!r},'w') as marker:\n"
        + " marker.write(str(child.pid))\n"
        + f"os.replace({str(tmp_path / 'ready.pending')!r},{str(tmp_path / 'ready')!r})\n"
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
            assert control.retirement_status() == "unconfirmed"
            assert not service.cancel_task_invocations(41, "owned-A")
            assert control.stop() == "stopped", control.stop_error
            result = future.result(timeout=3)
            assert result.exit_code == (-9 if force else 0)
            assert calls[0].retired.is_set()
            assert control.retirement_status() == "stopped"
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


@pytest.mark.parametrize("already_cancelled", [False, True])
def test_cleanup_without_cancellation_still_fences_launch(monkeypatch, already_cancelled):
    control = ManagedProcessControl()
    if already_cancelled:
        control.cancelled.set()
    assert control.stop(cancel=False) == "prevented"
    assert control.cancelled.is_set() is already_cancelled
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("closed spawn"))
    with pytest.raises(InvocationCancelled):
        control.spawn(["never-launch"])


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


@pytest.mark.parametrize("adapter", [claude_code, grok, antigravity_execution])
def test_all_shared_adapters_forward_same_private_control(adapter, tmp_path, monkeypatch):
    control = ManagedProcessControl()
    if adapter is claude_code:
        monkeypatch.setattr(adapter.claude_mcp, "resolve_claude_bin", lambda: "/fake/native")
    elif adapter is grok:
        monkeypatch.setattr(adapter, "resolve_grok_bin", lambda *_: "/fake/native")
    elif adapter is antigravity_execution:
        monkeypatch.setattr(adapter, "resolve_antigravity_bin", lambda: "/fake/native")
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
        provider=None if adapter is antigravity_execution else "selected",
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
            return [
                (
                    datetime.now(UTC).isoformat(),
                    usage_snapshot("fixture.last-observed", "invocation", 80, 20, cache_read=30),
                )
            ]

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
