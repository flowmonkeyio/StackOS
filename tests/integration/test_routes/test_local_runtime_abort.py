"""Owned stop relay over the existing authenticated invocation exchange."""

import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest
from sqlmodel import Session

from stackos.host_mcp import service as hosts
from stackos.host_mcp.agent_execution import ManagedProcessControl
from stackos.host_mcp.result import AgentRunResult, usage_snapshot
from stackos.local_runtime.exchange import InvocationReport
from stackos.local_runtime.native import NativeAgentRunner
from stackos.local_runtime.worker import OutboundWorker
from stackos.repositories.base import ConflictError
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import TrackerRepository
from tests.integration.test_routes.test_local_runtime_exchange import (
    call,
    local_store,
    offer,
    terminal,
)

pytest_plugins = [
    "tests.integration.test_routes.test_local_runtime_exchange",
    "tests.integration.test_routes.test_project_privacy",
]


def test_dispatched_remote_work_never_claims_prevented_without_local_confirmation(connected):
    exchange, connection, invocation, _, _ = offer(connected)
    exchange.poll(connection, None)
    invocation.cancelled.set()
    assert invocation.process_control.stop(timeout=0.01) == "unconfirmed"
    assert exchange.snapshot(connection)["needs_attention"]
    assert exchange.poll(connection, None)["state"] == "unknown"
    assert exchange.poll(connection, "another-invocation")["state"] == "unknown"
    stop = exchange.poll(connection, invocation.invocation_id)
    assert stop["state"] == "stop"
    assert stop["attempt"] == invocation.scope.attempt
    assert "launch" not in stop


def test_pending_remote_launch_is_prevented_without_dispatch(connected):
    exchange, connection, invocation, _, _ = offer(connected)
    invocation.cancelled.set()
    assert invocation.process_control.stop(timeout=0.01) == "prevented"
    assert exchange.poll(connection, None)["state"] == "unknown"
    with pytest.raises(ConflictError):
        exchange.report(connection, InvocationReport.model_validate(terminal(invocation)))


def test_stop_ack_requires_exact_attempt_and_cannot_accept_late_success(connected):
    exchange, connection, invocation, _, _ = offer(connected)
    exchange.poll(connection, None)
    invocation.cancelled.set()
    assert invocation.process_control.stop(timeout=0.01) == "unconfirmed"
    invocation.finished = True
    invocation.retired.set()
    report = {
        "invocation_id": invocation.invocation_id,
        "attempt": invocation.scope.attempt,
        "state": "stopped",
        "stop_outcome": "stopped",
    }
    with pytest.raises(ConflictError):
        exchange.report(connection, InvocationReport.model_validate(report | {"attempt": 2}))
    assert (
        exchange.report(connection, InvocationReport.model_validate(report))["state"] == "stopped"
    )
    assert invocation.process_control.stop(timeout=0.01) == "stopped"
    with pytest.raises(ConflictError):
        exchange.report(connection, InvocationReport.model_validate(terminal(invocation)))
    assert exchange.report(connection, InvocationReport.model_validate(report))["replayed"]


@pytest.mark.parametrize("stop_before_registration", [False, True])
def test_actual_local_owned_process_or_launch_stops_and_external_work_survives(
    connected, tmp_path, monkeypatch, stop_before_registration
):
    """Real worker/host/process control; substitute native CLI and source discovery.

    The exchange's server invocation is separate from the real local host registry,
    as it is in deployment. Supabase HTTP is the shared synthetic fixture.
    """
    exchange, connection, invocation, _, handoff = offer(connected)
    store = local_store(connected, tmp_path)
    discovered, proceed, running = Event(), Event(), Event()
    processes = []

    def discovery(*args, **kwargs):
        discovered.set()
        assert proceed.wait(10)

    def native(*args, process_control, **kwargs):
        child = process_control.spawn(
            [sys.executable, "-E", "-c", "import sys; sys.stdin.read()"],
            stdin=subprocess.PIPE,
        )
        processes.append(child)
        running.set()
        child.wait(timeout=10)
        child.stdin.close()
        return AgentRunResult(
            child.returncode,
            "owned-session",
            error="Owned process stopped",
            usage={"input_tokens": 11, "output_tokens": 1},
        )

    definition = hosts._definition_by_key("codex")
    monkeypatch.setattr(
        hosts, "_definition_by_key", lambda key: replace(definition, run_agent=native)
    )
    monkeypatch.setattr(hosts, "agent_selection_error", lambda *a, **kw: None)
    monkeypatch.setattr("stackos.local_runtime.native.check_codex_source", discovery)
    external = subprocess.Popen(
        [sys.executable, "-E", "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    sibling = ManagedProcessControl()
    sibling_child = sibling.spawn(
        [sys.executable, "-E", "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    runner = NativeAgentRunner(store)
    worker = OutboundWorker(connected[0], store, runner)
    try:
        with ThreadPoolExecutor() as pool:
            work = pool.submit(worker.exchange_once)
            assert discovered.wait(5)
            if not stop_before_registration:
                proceed.set()
                assert running.wait(5)
            stopping = pool.submit(invocation.process_control.stop)
            if stop_before_registration:
                deadline = time.monotonic() + 5
                while not runner._cancelled.is_set():
                    assert time.monotonic() < deadline
                    time.sleep(0.01)
                proceed.set()
            assert stopping.result(timeout=10) == (
                "prevented" if stop_before_registration else "stopped"
            )
            assert work.result(timeout=10)["state"] == "stopped"
        assert len(processes) == (0 if stop_before_registration else 1)
        assert all(process.poll() is not None for process in processes)
        if not stop_before_registration:
            assert handoff.report.outcome.thread_id == "owned-session"
            assert handoff.report.outcome.usage == {"input_tokens": 11, "output_tokens": 1}
        assert external.poll() is None and sibling_child.poll() is None
        assert not sibling.cancelled.is_set()
        assert exchange.snapshot(connection)["needs_attention"]
    finally:
        proceed.set()
        runner.stop(invocation.invocation_id, invocation.scope.attempt)
        external.communicate(timeout=5)
        sibling_child.communicate(timeout=5)
        hosts.shutdown_usage()


def test_unconfirmed_local_stop_keeps_unknown_state_and_cannot_release(connected):
    exchange, connection, invocation, _, _ = offer(connected)
    exchange.poll(connection, None)
    report = {
        "invocation_id": invocation.invocation_id,
        "attempt": invocation.scope.attempt,
        "state": "unknown",
        "stop_outcome": "unconfirmed",
    }
    with pytest.raises(ConflictError):
        exchange.report(connection, InvocationReport.model_validate(report))
    invocation.cancelled.set()
    assert (
        exchange.report(connection, InvocationReport.model_validate(report))["state"] == "unknown"
    )
    assert invocation.process_control.stop(timeout=0.01) == "unconfirmed"
    invocation.finished = True
    with pytest.raises(ConflictError):
        exchange.release(connection, invocation)
    assert exchange.snapshot(connection)["needs_attention"]


def workflow(connected, monkeypatch):
    from tests.integration.test_repositories.test_task_delivery import workflow_task
    from tests.integration.test_routes.test_connected_delivery import poll, prepare, start

    client, _, identity, computer, args, jobs = prepare(connected, monkeypatch)
    with Session(client.app.state.engine) as session:
        plan_id = workflow_task(session, identity["project_id"])
    args |= {"task_key": f"workflow-{plan_id}", "run_plan_id": plan_id, "step_id": "deliver"}
    assert poll(client, identity, computer).json()["state"] == "idle"
    response = start(client, args)
    assert response.status_code == 200, response.text
    return client, identity, computer, args, jobs


def test_offline_abort_stays_pending_after_controller_retires_and_late_result_cannot_revive(
    connected, monkeypatch
):
    from tests.integration.test_routes.test_connected_delivery import poll, start

    client, identity, computer, args, jobs = workflow(connected, monkeypatch)
    fn, job = jobs[0]
    with ThreadPoolExecutor() as pool:
        work = pool.submit(fn, **job["kwargs"])
        try:
            deadline = time.monotonic() + 5
            while True:
                command = poll(client, identity, computer).json()
                if command["state"] == "launch":
                    break
                assert time.monotonic() < deadline and not work.done()
                time.sleep(0.01)
            abort_args = {"project_id": identity["project_id"], "run_plan_id": args["run_plan_id"]}
            url = "/api/v1/operations/runPlan.abort/call"
            aborted = client.post(url, json={"arguments": abort_args})
            assert aborted.status_code == 409, aborted.text
            work.result(timeout=10)
        finally:
            if not work.done():
                exchange = client.app.state.operation_services["local_invocations"]
                handoff = exchange._calls.get(identity["connection_id"])
                if handoff:
                    handoff.call.cancelled.set()
                    handoff.ready.set()
    # Retired observation is not proof that disconnected native work stopped.
    again = client.post(url, json={"arguments": abort_args})
    assert again.status_code == 409, again.text
    late = client.post(
        "/api/v1/operations/localRuntime.report/call",
        headers={"Authorization": computer},
        json={
            "arguments": identity
            | {
                "report": {
                    "invocation_id": command["invocation_id"],
                    "attempt": command["scope"]["attempt"],
                    "state": "completed",
                    "outcome": {
                        "exit_code": 0,
                        "thread_id": "late-session",
                        "result": {"status": "success"},
                    },
                }
            }
        },
    )
    assert late.status_code == 409
    with Session(client.app.state.engine) as session:
        snapshot = TrackerRepository(session).get(
            project_id=identity["project_id"], task_key=args["task_key"]
        )
        assert snapshot.tasks[0].status == "aborted"
        state = snapshot.tasks[0].metadata_json["delivery"]
        assert state["needs_attention"] and state["cancellation"]["status"] == "stop_unconfirmed"
        assert state["uncertain_invocation"] == command["invocation_id"]
        assert (
            next(ticket for ticket in snapshot.tickets if ticket.key == "selected").status
            == "aborted"
        )
    with Session(client.app.state.engine) as session:
        repo = TrackerRepository(session)
        repo.create_task(project_id=identity["project_id"], key="after-abort", title="Later work")
        repo.create_ticket(
            project_id=identity["project_id"],
            task_key="after-abort",
            key="later-build",
            title="Later build",
        )
    new_args = {k: v for k, v in args.items() if k not in {"run_plan_id", "step_id"}}
    assert start(client, new_args | {"task_key": "after-abort"}).status_code == 409
    assert len(jobs) == 1
    reconciled = client.post(
        "/api/v1/operations/localRuntime.reconcile/call",
        json={
            "arguments": identity
            | {
                "task_key": args["task_key"],
                "handoff_id": state["handoff_id"],
                "invocation_id": command["invocation_id"],
                "confirmed_stopped": True,
                "outcome_note": "Fixture launch never executed; no native process exists.",
            }
        },
    )
    assert reconciled.status_code == 200, reconciled.text
    assert start(client, new_args | {"task_key": "after-abort"}).status_code == 200
    assert len(jobs) == 2


def test_abort_resolves_persisted_owner_and_refuses_computer_credentials(private_api):
    from tests.integration.test_routes.test_project_privacy import create_project

    client, selected, users, login = private_api
    plans = []
    for user in ("u1", "u2"):
        login(user)
        project_id = create_project(client, user)
        with Session(client.app.state.engine) as session:
            repo = RunPlanRepository(session)
            plan = repo.create(
                project_id=project_id,
                run_plan_json={
                    "schema_version": "stackos.run-plan.v1",
                    "key": user,
                    "title": user,
                    "steps": [{"id": "work", "title": "Work"}],
                },
            ).data
            started = repo.start(plan.id, project_id=project_id)
            plans.append((project_id, plan.id, started.run_id))
    owned_project, owned_plan, owned_run = plans[0]
    foreign_project, foreign_plan, foreign_run = plans[1]
    login("u1")
    url = "/api/v1/operations/runPlan.abort/call"
    for arguments in (
        {"run_plan_id": foreign_plan},
        {"project_id": foreign_project, "run_plan_id": foreign_plan},
        {"project_id": owned_project, "run_plan_id": foreign_plan},
    ):
        assert client.post(url, json={"arguments": arguments}).status_code == 404
    with Session(client.app.state.engine) as session:
        assert RunPlanRepository(session).get(foreign_plan).status == "started"
        from stackos.auth import UserPrincipal
        from stackos.repositories.base import NotFoundError
        from stackos.repositories.project_access import authorize_operation

        session.info["user_principal"] = UserPrincipal(users["u1"], selected.environment_id)
        assert authorize_operation(session, "run.abort", {"run_id": owned_run}) == owned_project
        with pytest.raises(NotFoundError):
            authorize_operation(session, "run.abort", {"run_id": foreign_run})
    credential = client.post(
        "/api/v1/operations/localRuntime.authorize/call",
        json={"arguments": {"project_id": owned_project}},
    ).json()["credential"]
    assert (
        client.post(
            url,
            json={"arguments": {"run_plan_id": owned_plan}},
            headers={"Authorization": f"Bearer {credential}"},
        ).status_code
        == 403
    )
    assert client.post(url, json={"arguments": {"run_plan_id": owned_plan}}).status_code == 200


@pytest.mark.parametrize("stop_outcome", ["stopped", "unconfirmed"])
def test_stop_report_validates_native_identity_before_retaining_usage(connected, stop_outcome):
    exchange, connection, invocation, _, handoff = offer(
        connected, resume_thread_id="expected-native-session"
    )
    exchange.poll(connection, None)
    invocation.cancelled.set()
    report = InvocationReport(
        invocation_id=invocation.invocation_id,
        attempt=invocation.scope.attempt,
        state="unknown" if stop_outcome == "unconfirmed" else "stopped",
        stop_outcome=stop_outcome,
        outcome={
            "exit_code": -15,
            "thread_id": "foreign-native-session",
            "usage": {"input_tokens": 999},
        },
    )
    with pytest.raises(ConflictError, match="session identity mismatch"):
        exchange.report(connection, report)
    assert handoff.report is None and not handoff.stop_confirmed.is_set()
    assert not handoff.ready.is_set()
    matching = report.model_copy(
        update={
            "outcome": report.outcome.model_copy(update={"thread_id": "expected-native-session"})
        }
    )
    assert exchange.report(connection, matching)["state"] == report.state
    assert exchange.report(connection, matching)["replayed"]


@pytest.mark.parametrize("no_outcome", [False, True])
def test_launch_prevention_without_native_session_remains_valid(connected, no_outcome):
    exchange, connection, invocation, _, _ = offer(
        connected, resume_thread_id="expected-native-session"
    )
    exchange.poll(connection, None)
    invocation.cancelled.set()
    report = InvocationReport(
        invocation_id=invocation.invocation_id,
        attempt=invocation.scope.attempt,
        state="stopped",
        stop_outcome="prevented",
        outcome=None
        if no_outcome
        else {"exit_code": None, "error": "Prevented before registration"},
    )
    assert exchange.report(connection, report)["state"] == "stopped"
    assert exchange.report(connection, report)["replayed"]


def test_unresolved_operation_poll_fences_launch_but_relays_exact_stop(connected):
    client, _, identity = connected
    exchange, connection, invocation, _, handoff = offer(connected)
    with Session(client.app.state.engine) as session:
        TrackerRepository(session).create_task(
            project_id=identity["project_id"],
            key=invocation.scope.task_key,
            title="Unresolved",
            metadata_json={
                "delivery": {
                    "connection_id": connection.id,
                    "status": "stopped",
                    "needs_attention": True,
                    "uncertain_invocation": invocation.invocation_id,
                }
            },
        )
    for identifier in (None, invocation.invocation_id, "stale-invocation"):
        response = call(client, "poll", **identity, invocation_id=identifier)
        assert response.status_code == 200, response.text
        assert response.json() == {"state": "unknown", "needs_attention": True}
        assert handoff.state == "pending"  # A refused poll must not consume the launch.
    # Prepare an already-dispatched owned invocation, as the controller/worker
    # test below does through actual REST. Uncertainty still fences every launch.
    exchange.poll(connection, None)
    invocation.cancelled.set()
    stop = call(client, "poll", **identity, invocation_id=invocation.invocation_id).json()
    assert stop["state"] == "stop" and stop["attempt"] == invocation.scope.attempt
    for identifier in (None, "stale-invocation"):
        assert call(client, "poll", **identity, invocation_id=identifier).json() == {
            "state": "unknown",
            "needs_attention": True,
        }


def test_controller_rest_worker_confirms_owned_stop(connected, tmp_path, monkeypatch):
    """Exercise actual controller and HTTP operations with an owned POSIX child.

    Substitute the native runner to keep server and local registries separate in
    this single-process fixture. NativeAgentRunner has its own real-host tests.
    """
    store = local_store(connected, tmp_path)
    client, identity, computer, args, jobs = workflow(connected, monkeypatch)
    running = Event()
    stops = []

    class LocalProcess:
        def __init__(self):
            self.control = ManagedProcessControl()
            self.child = None
            self.command = None

        def prepare(self, command):
            self.command = command

        def stop(self, invocation_id, attempt):
            stops.append((invocation_id, attempt))
            assert (invocation_id, attempt) == (
                self.command.invocation_id,
                self.command.scope.attempt,
            )
            return self.control.stop()

        def __call__(self, command, root, binding):
            self.child = self.control.spawn(
                [sys.executable, "-E", "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
            )
            running.set()
            self.child.wait(timeout=12)
            self.child.stdin.close()
            return AgentRunResult(
                self.child.returncode,
                "owned-session",
                error="Owned stop",
                usage=usage_snapshot("owned-session", "invocation", 13, 2),
            )

    runner = LocalProcess()
    worker = OutboundWorker(client, store, runner)
    external = subprocess.Popen(
        [sys.executable, "-E", "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    sibling = ManagedProcessControl()
    sibling_child = sibling.spawn(
        [sys.executable, "-E", "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )

    def consume():
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            response = worker.exchange_once()
            if response["state"] != "idle":
                return response
            time.sleep(0.01)
        raise AssertionError("No controller launch")

    fn, job = jobs[0]
    try:
        with ThreadPoolExecutor() as pool:
            controller = pool.submit(fn, **job["kwargs"])
            consumer = pool.submit(consume)
            try:
                assert running.wait(8)
                aborted = client.post(
                    "/api/v1/operations/runPlan.abort/call",
                    json={
                        "arguments": {
                            "project_id": identity["project_id"],
                            "run_plan_id": args["run_plan_id"],
                        }
                    },
                )
                assert aborted.status_code == 200, aborted.text
                assert aborted.json()["data"]["cancellation"]["status"] == "managed_stopped"
                assert consumer.result(timeout=10)["state"] == "stopped"
                controller.result(timeout=10)
            finally:
                runner.control.stop()
                handoff = client.app.state.operation_services["local_invocations"]._calls.get(
                    identity["connection_id"]
                )
                if handoff:
                    handoff.call.cancelled.set()
                    handoff.ready.set()
        assert stops == [(runner.command.invocation_id, runner.command.scope.attempt)]
        assert runner.child.poll() is not None
        assert external.poll() is None and sibling_child.poll() is None
        assert not sibling.cancelled.is_set() and len(jobs) == 1
        late = client.post(
            "/api/v1/operations/localRuntime.report/call",
            headers={"Authorization": computer},
            json={
                "arguments": identity
                | {
                    "report": {
                        "invocation_id": runner.command.invocation_id,
                        "attempt": runner.command.scope.attempt,
                        "state": "completed",
                        "outcome": {
                            "exit_code": 0,
                            "thread_id": "owned-session",
                            "result": {"status": "success"},
                        },
                    }
                }
            },
        )
        assert late.status_code == 409
        with Session(client.app.state.engine) as session:
            snap = TrackerRepository(session).get(
                project_id=identity["project_id"], task_key=args["task_key"]
            )
            state = snap.tasks[0].metadata_json["delivery"]
            assert snap.tasks[0].status == "aborted"
            assert state["cancellation"]["status"] == "managed_stopped"
            assert not state["needs_attention"] and state["uncertain_invocation"] is None
            evidence = next(
                t for t in snap.tickets if t.key == "selected"
            ).completion_evidence_json["automatic_delivery"]
            assert evidence["delivery"]["cancelled"]
            assert evidence["delivery"]["thread_id"] == "owned-session"
            assert evidence["delivery"]["usage"]["measured"]["input"] == 13
            assert "review" not in evidence
    finally:
        runner.control.stop()
        external.communicate(timeout=5)
        sibling_child.communicate(timeout=5)
