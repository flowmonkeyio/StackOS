"""Connection loss is observation; only an owner can reconcile uncertain work."""

import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest
from sqlmodel import Session

from stackos.host_mcp.result import AgentRunResult
from stackos.local_runtime.exchange import InvocationExchange
from stackos.local_runtime.worker import OutboundWorker
from stackos.repositories.base import ConflictError
from stackos.repositories.tracker import TrackerRepository
from tests.integration.test_routes.test_connected_delivery import (
    _run_connected_scenario,
    poll,
    prepare,
    start,
)
from tests.integration.test_routes.test_local_runtime_exchange import (
    call,
    local_store,
    offer,
    terminal,
)

pytest_plugins = ["tests.integration.test_routes.test_local_runtime_exchange"]


def test_loss_shows_last_known_work_and_accepts_late_result_once(connected, monkeypatch):
    client, _, identity = connected
    exchange, connection, invocation, _, handoff = offer(connected)
    now = [100.0]
    monkeypatch.setattr("stackos.local_runtime.exchange.time.monotonic", lambda: now[0])
    assert call(client, "poll", **identity).json()["state"] == "launch"
    now[0] += 31
    observed = call(client, "status", **identity).json()
    assert observed["connection_state"] == "disconnected"
    assert observed["last_known_state"] == "dispatched"
    assert observed["invocation_id"] == invocation.invocation_id
    assert observed["last_seen_at"]
    assert not invocation.cancelled.is_set()
    assert not invocation.process_control.cancelled.is_set()
    assert not handoff.ready.is_set()
    with pytest.raises(ConflictError, match="disconnected"):
        exchange.require_available(connection)
    assert "launch" not in call(client, "poll", **identity).json()
    assert call(client, "status", **identity).json()["needs_attention"]
    # The original invocation's presence cannot erase another consumer's loss.
    call(client, "poll", **identity, invocation_id=invocation.invocation_id)
    assert call(client, "status", **identity).json()["needs_attention"]
    report = terminal(invocation)
    assert call(client, "report", **identity, report=report).json()["replayed"] is False
    assert call(client, "report", **identity, report=report).json()["replayed"] is True
    assert handoff.outcome().ok
    observed = call(client, "status", **identity).json()
    assert observed["last_known_state"] == "completed" and not observed["needs_attention"]


def test_fresh_consumer_exposes_controller_invocation_uncertainty(connected, tmp_path, monkeypatch):
    store = local_store(connected, tmp_path)
    client, _, identity, computer, args, jobs = prepare(connected, monkeypatch)
    poll(client, identity, computer)
    assert start(client, args).status_code == 200
    launches = []
    worker = OutboundWorker(client, store, lambda *args: launches.append(args))
    fn, job = jobs[0]
    with ThreadPoolExecutor() as pool:
        future = pool.submit(fn, **job["kwargs"])
        deadline = time.monotonic() + 10
        while True:
            received = poll(client, identity, computer).json()
            if received["state"] == "launch":
                break
            assert not future.done() and time.monotonic() < deadline
            time.sleep(0.01)
        try:
            with pytest.raises(ValueError, match="uncertain"):
                worker.exchange_once()
            for observed in (
                call(client, "status", **identity).json(),
                call(client, "list", project_id=identity["project_id"]).json()["items"][0],
            ):
                assert observed["connection_state"] == "needs_attention"
                assert observed["needed_action"]
                assert observed["unresolved_tasks"][0]["invocation_id"] == received["invocation_id"]
            assert not launches and start(client, args).status_code == 409
        finally:
            # Only the matching synthetic report retires this waiting controller.
            finished = client.post(
                "/api/v1/operations/localRuntime.report/call",
                json={
                    "arguments": identity
                    | {
                        "report": {
                            "invocation_id": received["invocation_id"],
                            "attempt": received["scope"]["attempt"],
                            "state": "unknown",
                        }
                    }
                },
                headers={"Authorization": computer},
            )
            assert finished.status_code == 200
            future.result(timeout=10)


@pytest.mark.parametrize("mode", ["wrong-sha", "same-session"])
def test_known_terminal_failure_releases_only_transport_and_keeps_failed_evidence(
    connected, monkeypatch, mode
):
    client, identity, _, args, jobs = _run_connected_scenario(connected, monkeypatch, mode)
    observed = call(client, "status", **identity).json()
    assert observed["invocation_id"] is None
    assert not observed["needs_attention"] and not observed["unresolved_tasks"]
    with Session(client.app.state.engine) as session:
        repo = TrackerRepository(session)
        before = repo.get(project_id=identity["project_id"], task_key="connected")
        assert before.tickets[0].status == "failed"
        state = before.tasks[0].metadata_json["delivery"]
        assert state["status"] == "stopped" and not state.get("uncertain_invocation")
        evidence = before.tickets[0].completion_evidence_json
        repo.create_task(project_id=identity["project_id"], key="next", title="Next")
        repo.create_ticket(
            project_id=identity["project_id"], task_key="next", key="next-build", title="Next"
        )
    assert start(client, args | {"task_key": "next"}).status_code == 200
    assert len(jobs) == 2
    with Session(client.app.state.engine) as session:
        after = TrackerRepository(session).get(
            project_id=identity["project_id"], task_key="connected"
        )
        assert after.tickets[0].status == "failed"
        assert after.tickets[0].completion_evidence_json == evidence


def test_presence_loss_leaves_accepted_process_alive_and_report_retry_is_same_work(
    connected, tmp_path
):
    client, _, identity = connected
    exchange, connection, invocation, _, handoff = offer(connected)
    store = local_store(connected, tmp_path)
    running, finish = Event(), Event()
    processes = []

    def runner(*args):
        process = subprocess.Popen(
            [sys.executable, "-E", "-c", "import sys; sys.stdin.read()"],
            stdin=subprocess.PIPE,
        )
        processes.append(process)
        running.set()
        try:
            assert finish.wait(10)
            assert process.poll() is None
        finally:
            process.communicate(timeout=5)
        return AgentRunResult(0, "exact-native-session", {"status": "success"})

    class LoseReport:
        lost = False

        def post(self, url, **kwargs):
            if ".report/" in url and not self.lost:
                self.lost = True
                raise httpx.ReadError("disconnected before known result arrived")
            return client.post(url, **kwargs)

    worker = OutboundWorker(LoseReport(), store, runner)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(worker.exchange_once)
        try:
            assert running.wait(5)
            # Age only this connection's observation, never the native process.
            with exchange._lock:
                identity_tuple, seen = exchange._seen[connection.id]
                exchange._seen[connection.id] = (identity_tuple, seen - 31)
            assert call(client, "status", **identity).json()["connection_state"] == "disconnected"
            assert processes[0].poll() is None
            assert not handoff.ready.is_set()
            assert not invocation.process_control.cancelled.is_set()
        finally:
            finish.set()
        with pytest.raises(httpx.ReadError):
            future.result(timeout=10)
    assert worker.exchange_once()["state"] == "completed"
    assert worker.exchange_once()["state"] == "completed"
    assert len(processes) == 1 and handoff.outcome().ok


def test_presence_poll_names_only_the_retained_invocation(connected, tmp_path):
    client, _, identity = connected
    _, _, invocation, _, _ = offer(connected)
    store = local_store(connected, tmp_path)
    assert call(client, "poll", **identity).json()["state"] == "launch"
    worker = OutboundWorker(client, store, lambda *args: None)

    class OnePoll:
        def __init__(self):
            self.calls = 0

        def wait(self, seconds):
            self.calls += 1
            return self.calls > 1

    worker._presence(store.load(), identity, invocation.invocation_id, OnePoll())
    response = call(client, "poll", **identity, invocation_id=invocation.invocation_id).json()
    assert response["state"] == "dispatched" and not response["needs_attention"]
    assert "launch" not in response
    assert call(client, "poll", **identity).json()["needs_attention"]


def test_duplicate_report_refreshes_presence_without_second_acceptance(connected, monkeypatch):
    client, _, identity = connected
    _, _, invocation, _, _ = offer(connected)
    now = [100.0]
    monkeypatch.setattr("stackos.local_runtime.exchange.time.monotonic", lambda: now[0])
    call(client, "poll", **identity)
    report = terminal(invocation)
    assert not call(client, "report", **identity, report=report).json()["replayed"]
    now[0] += 31
    assert call(client, "status", **identity).json()["connection_state"] == "disconnected"
    assert call(client, "report", **identity, report=report).json()["replayed"]
    assert call(client, "status", **identity).json()["connection_state"] == "connected"


def test_restart_uncertainty_is_visible_and_requires_owner_confirmation(connected, monkeypatch):
    client, identity, computer, args, jobs = _run_connected_scenario(
        connected, monkeypatch, "unknown"
    )
    observed = client.post(
        "/api/v1/operations/localRuntime.status/call", json={"arguments": identity}
    ).json()
    assert observed["connection_state"] == "needs_attention"
    pending = observed["unresolved_tasks"][0]
    assert pending["task_key"] == "connected"
    assert poll(client, identity, computer).json()["needs_attention"] is True
    confirm = (
        identity
        | pending
        | {"confirmed_stopped": True, "outcome_note": "Checked native terminal outcome locally."}
    )
    url = "/api/v1/operations/localRuntime.reconcile/call"
    # The scoped computer cannot clear its own admission fence.
    assert (
        client.post(
            url, json={"arguments": confirm}, headers={"Authorization": computer}
        ).status_code
        == 403
    )
    assert (
        client.post(url, json={"arguments": confirm | {"invocation_id": "stale"}}).status_code
        == 409
    )
    assert (
        client.post(url, json={"arguments": confirm | {"confirmed_stopped": False}}).status_code
        == 422
    )
    result = client.post(url, json={"arguments": confirm})
    assert result.status_code == 200, result.text
    assert result.json()["reconciled"] is True
    assert poll(client, identity, computer).json()["state"] == "idle"
    with Session(client.app.state.engine) as session:
        repo = TrackerRepository(session)
        snapshot = repo.get(project_id=identity["project_id"], task_key="connected")
        assert snapshot.tickets[0].status == "failed"  # Confirmation is not acceptance.
        assert snapshot.tickets[0].completion_evidence_json["automatic_delivery"]["attempt"] == 1
        state = snapshot.tasks[0].metadata_json["delivery"]
        assert state["reconciliation"]["invocation_id"] == pending["invocation_id"]
        assert not state.get("uncertain_invocation")
        repo.create_task(project_id=identity["project_id"], key="next", title="Next")
        repo.create_ticket(
            project_id=identity["project_id"], task_key="next", key="next-build", title="Next"
        )
    assert start(client, args | {"task_key": "next"}).status_code == 200
    assert len(jobs) == 2


def test_restart_marks_connected_admission_only_and_preserves_history(connected, monkeypatch):
    from stackos.task_delivery import mark_interrupted_connected_deliveries

    client, _, identity, computer, args, _ = prepare(connected, monkeypatch)
    poll(client, identity, computer)
    assert start(client, args).status_code == 200
    with Session(client.app.state.engine) as session:
        repo = TrackerRepository(session)
        before = repo.get(project_id=identity["project_id"], task_key="connected")
        assert mark_interrupted_connected_deliveries(session) == 1
        assert mark_interrupted_connected_deliveries(session) == 0
        after = repo.get(project_id=identity["project_id"], task_key="connected")
        assert after.tickets == before.tickets
        state = after.tasks[0].metadata_json["delivery"]
        assert state["status"] == "stopped" and state["needs_attention"]
        assert state["handoff_id"] == before.tasks[0].metadata_json["delivery"]["handoff_id"]
    client.app.state.operation_services["local_invocations"] = InvocationExchange()
    assert poll(client, identity, computer).json()["needs_attention"]
    assert start(client, args).status_code == 409


def test_reconciliation_cannot_retire_a_live_invocation(connected):
    _, connection, invocation, _, _ = offer(connected)
    exchange = connected[0].app.state.operation_services["local_invocations"]
    with pytest.raises(ConflictError, match="still owned"):
        exchange.reconcile(connection, invocation.invocation_id)
    assert exchange.poll(connection, None)["state"] == "launch"
    assert not invocation.cancelled.is_set()


def test_reconciliation_contract_has_no_runtime_mcp_or_cli_escalation():
    from stackos.operations.local_runtime import RUNTIME_OPERATIONS
    from stackos.operations.registry import build_operation_registry

    spec = build_operation_registry().get("localRuntime.reconcile")
    assert spec.surfaces.rest.enabled and spec.surfaces.rest.browser_safe
    assert not spec.surfaces.mcp.enabled and not spec.surfaces.cli.enabled
    assert spec.response_policy.allowed_modes == ("raw",)
    assert "localRuntime.reconcile" not in RUNTIME_OPERATIONS
    spec.input_model.model_validate(spec.examples[0].arguments)
