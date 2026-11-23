"""Account switching retires only owned native workers and resumes their threads."""

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace

import pytest

from stackos.host_mcp import service
from stackos.host_mcp.accounts import DesiredAccount, HostAccountError
from stackos.host_mcp.result import AgentRunResult, usage_snapshot


@pytest.fixture
def host_key(request):
    return getattr(request, "param", "codex")


class Accounts:
    def __init__(self, activation=None):
        self.desired = DesiredAccount(
            account_ref="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", revision=1
        )
        self.activation = activation
        self.writes = 0

    def resolve(self, host):
        return SimpleNamespace(desired=self.desired)

    def inventory(self):
        return SimpleNamespace(
            hosts={
                host: SimpleNamespace(
                    accounts=[
                        SimpleNamespace(account_ref="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                        SimpleNamespace(account_ref="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                    ]
                )
                for host in ("codex", "claude-code", "grok")
            }
        )

    def select(self, host, ref, revision):
        self.writes += 1
        if self.activation:
            self.activation()
        self.desired = DesiredAccount(account_ref=ref, revision=revision + 1)
        return self.desired


@pytest.fixture
def worker(tmp_path, monkeypatch, host_key):
    ready = Event()
    calls, transitions, processes = [], [], []
    accounts = Accounts()
    allowed = Event()
    allowed.set()
    completion_wins = False

    def native(directory, instructions, prompt, schema, *, process_control, on_session, **kwargs):
        calls.append((accounts.desired.account_ref, prompt, deepcopy(kwargs)))
        thread = kwargs.get("resume_thread_id") or (
            "native-new-thread" if prompt == "Queued assignment" else "native-account-thread"
        )
        on_session(thread)
        if len(calls) == 1:
            process = process_control.spawn(
                [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(30)"],
                stdout=subprocess.PIPE,
                text=True,
            )
            processes.append(process)
            assert process.stdout.readline().strip() == "ready"
            process.stdout.close()
            ready.set()
            process.wait(timeout=10)
            return AgentRunResult(
                0 if completion_wins else process.returncode,
                thread_id="native-account-thread",
                result={"done": True} if completion_wins else None,
                usage=usage_snapshot(host_key, "session", 100, 20),
            )
        return AgentRunResult(
            0,
            thread_id=thread,
            result={"done": True},
            usage=usage_snapshot(host_key, "session", 130, 25),
        )

    definition = service._definition_by_key(host_key)
    monkeypatch.setattr(
        service, "_definition_by_key", lambda _: replace(definition, run_agent=native)
    )
    monkeypatch.setattr(service, "agent_selection_error", lambda *a, **kw: None)

    def observe(call, active):
        transitions.append((active, call.projection(), deepcopy(call.usage)))

    def run():
        return service.run_agent(
            host_key,
            tmp_path,
            "Original instructions",
            "Original assignment",
            {},
            model="selected-model",
            model_reasoning_effort="high",
            provider="frozen-provider",
            profile="frozen-profile",
            sandbox="read-only",
            execution_scope=service.AgentExecutionScope(
                42, "task", "ticket", "reviewer", 3, "review"
            ),
            on_execution_change=observe,
            launch_allowed=allowed.is_set,
        )

    def finish_normally():
        nonlocal completion_wins
        completion_wins = True

    yield SimpleNamespace(
        ready=ready,
        calls=calls,
        transitions=transitions,
        accounts=accounts,
        allowed=allowed,
        run=run,
        processes=processes,
        finish_normally=finish_normally,
        host_key=host_key,
    )
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
    assert not service._account_switches
    service.shutdown_usage()


@pytest.mark.parametrize("host_key", ["codex", "claude-code", "grok"], indirect=True)
def test_switch_resumes_exact_thread_with_proceed_and_separate_usage(worker):
    external = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    other_call = service.ActiveInvocation(
        "unrelated-host",
        service.AgentExecutionScope(99, "other-task", "other-ticket", "worker", 1, "delivery"),
        "grok" if worker.host_key != "grok" else "codex",
        None,
        "now",
        thread_id="other-session",
    )
    other_process = other_call.process_control.spawn(
        [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    service._register(other_call)
    try:
        with ThreadPoolExecutor() as pool:
            future = pool.submit(worker.run)
            assert worker.ready.wait(3)
            worker.accounts.activation = lambda: (
                worker.processes[0].poll() is not None
                or pytest.fail("credentials changed before exit")
            )
            selected = service.select_account(
                worker.accounts, worker.host_key, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", 1
            )
            assert selected.revision == 2
            assert future.result(timeout=3).ok
        assert external.poll() is None
        assert other_process.poll() is None
        assert not other_call.process_control.cancelled.is_set()
        assert other_call.account_switch is None
        assert [row[:2] for row in worker.calls] == [
            ("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "Original assignment"),
            ("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "proceed"),
        ]
        assert worker.calls[1][2]["resume_thread_id"] == "native-account-thread"
        assert worker.calls[0][2].get("native_settings") == worker.calls[1][2].get(
            "native_settings"
        )
        if worker.host_key != "codex":
            assert worker.calls[0][2]["model_reasoning_effort"] == "high"
            assert worker.calls[1][2]["model_reasoning_effort"] == "high"
        for key in ("model", "provider", "profile", "sandbox"):
            assert worker.calls[0][2][key] == worker.calls[1][2][key]
        assert [row[0] for row in worker.transitions] == [True, False, True, False]
        first, second = worker.transitions[1], worker.transitions[3]
        assert second[1]["restart_of"] == first[1]["invocation_id"]
        assert first[1]["invocation_id"] != second[1]["invocation_id"]
        assert first[2]["measured"]["total"] == 120
        assert second[2]["measured"]["total"] == 35
    finally:
        external.terminate()
        external.wait(timeout=3)
        other_call.process_control.stop()
        service._unregister(other_call)


@pytest.mark.parametrize(
    "code", ["activation_failed", "account_store_unavailable", "selection_restore_failed"]
)
def test_failed_switch_resumes_only_when_original_credentials_are_restored(worker, code):
    def fail():
        raise HostAccountError(code)

    with ThreadPoolExecutor() as pool:
        future = pool.submit(worker.run)
        assert worker.ready.wait(3)
        worker.accounts.activation = fail
        with pytest.raises(HostAccountError, match=code):
            service.select_account(
                worker.accounts, worker.host_key, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", 1
            )
        result = future.result(timeout=3)
    assert len(worker.calls) == (1 if code == "selection_restore_failed" else 2)
    if code != "selection_restore_failed":
        assert result.ok and worker.calls[-1][0] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_successful_result_racing_switch_is_not_replayed(worker):
    worker.finish_normally()
    with ThreadPoolExecutor() as pool:
        future = pool.submit(worker.run)
        assert worker.ready.wait(3)
        service.select_account(
            worker.accounts, worker.host_key, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", 1
        )
        assert future.result(timeout=3).ok
    assert len(worker.calls) == 1


@pytest.mark.parametrize("host_key", ["codex", "claude-code", "grok"], indirect=True)
def test_switch_fences_new_host_launch_but_not_other_hosts(worker, tmp_path, monkeypatch):
    entered, release, queued = Event(), Event(), Event()
    started = []
    lookup = service._definition_by_key
    other = replace(
        lookup(worker.host_key), run_agent=lambda *a, **kw: AgentRunResult(0, result={})
    )
    monkeypatch.setattr(
        service,
        "_definition_by_key",
        lambda host: lookup(host) if host == worker.host_key else other,
    )

    def activation():
        entered.set()
        assert release.wait(5)

    def launch_new(host):
        queued.set()
        return service.run_agent(
            host,
            tmp_path,
            "instructions",
            "Queued assignment",
            {},
            execution_scope=service.AgentExecutionScope(
                99, "new", "new-ticket", "worker", 1, "delivery"
            ),
            on_execution_change=lambda call, active: (
                started.append(call.started_at) if active and host == worker.host_key else None
            ),
        )

    worker.accounts.activation = activation
    with ThreadPoolExecutor(max_workers=4) as pool:
        existing = pool.submit(worker.run)
        assert worker.ready.wait(3)
        switching = pool.submit(
            service.select_account,
            worker.accounts,
            worker.host_key,
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            1,
        )
        try:
            assert entered.wait(3)
            pending = pool.submit(launch_new, worker.host_key)
            assert queued.wait(3)
            assert not pending.done() and len(worker.calls) == 1
            other_host = "codex" if worker.host_key != "codex" else "grok"
            assert pool.submit(launch_new, other_host).result(timeout=2).ok
            with pytest.raises(HostAccountError, match="account_switch_busy"):
                service.select_account(
                    worker.accounts, worker.host_key, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", 1
                )
            released_at = datetime.now(UTC).isoformat()
        finally:
            release.set()
        switching.result(timeout=3)
        assert pending.result(timeout=3).ok and existing.result(timeout=3).ok
    assert len(started) == 1 and started[0] >= released_at


def test_abort_during_activation_prevents_restart(worker):
    with ThreadPoolExecutor() as pool:
        future = pool.submit(worker.run)
        assert worker.ready.wait(3)
        worker.accounts.activation = worker.allowed.clear
        service.select_account(
            worker.accounts, worker.host_key, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", 1
        )
        assert not future.result(timeout=3).ok
    assert len(worker.calls) == 1


def test_stale_selection_does_not_interrupt(worker):
    with ThreadPoolExecutor() as pool:
        future = pool.submit(worker.run)
        assert worker.ready.wait(3)
        with pytest.raises(HostAccountError, match="stale_selection"):
            service.select_account(
                worker.accounts, worker.host_key, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", 0
            )
        assert worker.processes[0].poll() is None and worker.accounts.writes == 0
        service.select_account(
            worker.accounts, worker.host_key, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", 1
        )
        assert future.result(timeout=3).ok


@pytest.mark.parametrize("is_request", [False, True])
def test_unresumable_worker_rejects_before_credentials_or_process_change(is_request, host_key):
    scope = (
        service.AgentExecutionScope(43, agent="setup", request_id=1)
        if is_request
        else service.AgentExecutionScope(42, "task", "ticket", "worker", 1, "delivery")
    )
    call = service.ActiveInvocation("unresumable", scope, host_key, None, "now")
    if is_request:
        call.thread_id = "setup-thread"
    service._register(call)
    accounts = Accounts()
    try:
        with pytest.raises(HostAccountError, match="account_switch_unresumable_worker"):
            service.select_account(accounts, host_key, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", 1)
        assert accounts.writes == 0 and not call.process_control.cancelled.is_set()
    finally:
        service._unregister(call)
        service.shutdown_usage()


def test_unconfirmed_retirement_prevents_credential_write(monkeypatch, host_key):
    call = service.ActiveInvocation(
        "uncertain",
        service.AgentExecutionScope(43, "task", "ticket", "worker", 1, "delivery"),
        host_key,
        None,
        "now",
        thread_id="retained-thread",
    )
    service._register(call)
    accounts = Accounts()
    monkeypatch.setattr(service, "retire_invocation", lambda _call: "unconfirmed")
    try:
        with pytest.raises(HostAccountError, match="account_switch_retirement_unconfirmed"):
            service.select_account(accounts, host_key, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", 1)
        assert accounts.writes == 0 and accounts.desired.revision == 1
        assert not call.retired.is_set() and not service._account_switches
    finally:
        service._unregister(call)
        service.shutdown_usage()
