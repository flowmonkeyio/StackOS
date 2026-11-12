"""Request-owned native execution uses the real registry and repository boundary."""

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event, Thread, current_thread
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from stackos.config import Settings
from stackos.db.connection import make_engine
from stackos.db.models import AgentRequest, AgentRequestStatus
from stackos.host_mcp import service
from stackos.host_mcp.result import AgentRunResult, usage_snapshot
from stackos.project_cleanup import ProjectCleanupService
from stackos.repositories import agent_requests
from stackos.repositories.agent_requests import AgentRequestExecution, AgentRequestRepository
from stackos.repositories.base import ConflictError, NotFoundError
from tests.helpers.sqlite_schema import capture_database_snapshot, restore_database_snapshot


@pytest.fixture(autouse=True)
def native_lifetime(monkeypatch):
    service.shutdown_usage()
    # The real collector is tested separately; ticks here use a virtual clock.
    monkeypatch.setattr(service, "_collector", SimpleNamespace())
    yield
    service.shutdown_usage()
    monkeypatch.setattr(service, "_collector", None)


@pytest.fixture
def threaded_session(session, project_id, tmp_path):
    # Separate connections on native/collector threads require a file-backed DB.
    engine = make_engine(tmp_path / "request-execution.sqlite")
    restore_database_snapshot(engine, capture_database_snapshot(session.get_bind()))
    try:
        with Session(engine) as other:
            yield other
    finally:
        engine.dispose()


def owner(session, project_id, key="scheduled"):
    repo = AgentRequestRepository(session)
    request = repo.create(project_id=project_id, request_key=key, title="Benign goal").data
    claim = repo.claim(project_id=project_id, request_id=request.id, claimed_by="controller").data
    execution = AgentRequestExecution(
        project_id=project_id,
        request_id=request.id,
        claim_token=claim.claim_token,
        session_factory=lambda: Session(session.get_bind()),
        snapshot={"goal": "Benign goal", "project_dir": "/fixture", "workflow": None},
    )
    return repo, request, claim, execution


def launch(monkeypatch, tmp_path, execution, runner, observer=None):
    monkeypatch.setattr(service, "agent_selection_error", lambda *a, **kw: None)
    monkeypatch.setattr(service, "_definition_by_key", lambda _: SimpleNamespace(run_agent=runner))
    return service.run_agent(
        "codex",
        tmp_path,
        "Load project guidance",
        "Benign goal",
        {},
        execution_scope=service.AgentExecutionScope(
            execution.project_id, agent="main", request_id=execution.request_id
        ),
        request_execution=execution,
        on_execution_change=observer,
    )


def test_request_identity_lease_usage_and_terminal_handoff(
    session, project_id, monkeypatch, tmp_path
):
    repo, request, claim, execution = owner(session, project_id)
    clock = datetime.now(UTC).replace(tzinfo=None)
    monkeypatch.setattr(agent_requests, "_utcnow", lambda: clock)
    retained = []
    handed_off = []
    before_persistence = []
    persist = AgentRequestRepository._finish_execution

    def inspect_handoff(repository, owner, call, result):
        before_persistence.append(
            repo.execution_evidence(project_id=project_id, request_id=request.id)
        )
        persist(repository, owner, call, result)

    monkeypatch.setattr(AgentRequestRepository, "_finish_execution", inspect_handoff)

    def observe(call, active):
        if not active:
            stored = repo.get(project_id=project_id, request_id=request.id)
            handed_off.append((stored.metadata_json["native_execution"]["usage"], call.usage))
            with pytest.raises(ConflictError):
                execution.finish(call, AgentRunResult(0, thread_id="late", result={}))

    def runner(*args, on_session, **kwargs):
        nonlocal clock
        on_session("native-request-thread")
        assert (
            repo.get(project_id=project_id, request_id=request.id).metadata_json[
                "native_execution"
            ]["execution"]["thread_id"]
            == "native-request-thread"
        )
        call = next(iter(service._active_invocations.values()))
        retained.append(call)
        assert service.active_execution(project_id, "unrelated-task") is None
        live = service.active_request_execution(project_id, request.id)
        assert live["request_id"] == request.id and live["thread_id"] == "native-request-thread"
        assert live["ticket_key"] is None
        assert service.active_request_execution(project_id + 1, request.id) is None
        assert service.provisional_usage(project_id) == {}
        clock += timedelta(seconds=601)
        service._sample_invocations()
        cleanup = ProjectCleanupService(
            session, Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
        )
        assert "native_invocation" in cleanup._blockers(project_id)
        assert "native_invocation" not in cleanup._blockers(project_id + 1)
        with Session(session.get_bind()) as other:
            competing = AgentRequestRepository(other)
            with pytest.raises(ConflictError):
                competing.claim(project_id=project_id, request_id=request.id, claimed_by="thief")
            assert competing.list_claimable(project_id=project_id).items == []
            assert (
                competing.get(project_id=project_id, request_id=request.id).claim_expires_at > clock
            )
        return AgentRunResult(
            0,
            thread_id="native-request-thread",
            result={"status": "success", "usage": 999999},
            usage=usage_snapshot("fixture", "invocation", 40, 3),
        )

    result = launch(monkeypatch, tmp_path, execution, runner, observe)
    assert result.ok
    assert service.active_request_execution(project_id, request.id) is None
    assert not service.usage_executions(project_id)
    stored = repo.get(project_id=project_id, request_id=request.id)
    evidence = stored.metadata_json["native_execution"]
    assert evidence["snapshot"]["context"]["goal"] == "Benign goal"
    assert evidence["snapshot"]["project_dir"] == str(tmp_path.resolve())
    assert evidence["snapshot"]["instructions"] == "Load project guidance"
    assert evidence["execution"]["thread_id"] == "native-request-thread"
    assert evidence["usage"]["measured"]["total"] == 43
    assert handed_off == [(evidence["usage"], evidence["usage"])]
    assert before_persistence[0]["active_execution"] is None
    assert before_persistence[0]["native_execution"]["usage"]["measured"]["total"] == 43
    assert repo.execution_evidence(project_id=project_id, request_id=request.id) == {
        "native_execution": evidence,
        "active_execution": None,
    }
    stale = {**evidence["execution"], "finished": False, "usage": {"status": "unavailable"}}
    monkeypatch.setattr(agent_requests, "active_request_execution", lambda *a, **kw: stale.copy())
    assert repo.execution_evidence(project_id=project_id, request_id=request.id) == {
        "native_execution": evidence,
        "active_execution": None,
    }
    assert evidence["result"]["usage"] == 999999
    assert evidence["finished_at"] and stored.run_plan_id is None
    assert retained[0].retired.is_set() and retained[0].usage is None
    repo.complete(project_id=project_id, request_id=request.id, claim_token=claim.claim_token)
    with pytest.raises(ConflictError):
        repo.complete(project_id=project_id, request_id=request.id, claim_token=claim.claim_token)


@pytest.mark.parametrize(
    "attribution",
    [
        {"request_id": 1, "task_key": "task", "ticket_key": "ticket"},
        {"request_id": 0},
        {"request_id": True},
        {"task_key": "task"},
        {},
    ],
)
def test_attribution_is_exclusive(attribution):
    with pytest.raises(ValueError):
        service.AgentExecutionScope(1, agent="main", **attribution)


@pytest.mark.parametrize("failure", ["token", "request", "project"])
def test_rejected_owner_never_launches(session, project_id, monkeypatch, tmp_path, failure):
    repo, request, _claim, execution = owner(session, project_id)
    bad = replace(
        execution,
        **{
            "token": {"claim_token": "wrong"},
            "request": {"request_id": request.id + 1},
            "project": {"project_id": project_id + 1},
        }[failure],
    )
    entered = []

    def runner(*args, **kwargs):
        entered.append(True)

    with pytest.raises((ConflictError, NotFoundError)):
        launch(monkeypatch, tmp_path, bad, runner)
    assert not entered and not service.usage_executions()
    assert "native_execution" not in (
        repo.get(project_id=project_id, request_id=request.id).metadata_json or {}
    )


def test_request_requires_exact_owner_and_terminal_identity(
    session, project_id, monkeypatch, tmp_path
):
    repo, request, claim, execution = owner(session, project_id)
    scope = service.AgentExecutionScope(project_id, agent="main", request_id=request.id)
    assert not service.run_agent("codex", tmp_path, "rules", "goal", {}, execution_scope=scope).ok
    assert not service.run_agent(
        "codex", tmp_path, "rules", "goal", {}, request_execution=execution
    ).ok

    def runner(*args, on_session, **kwargs):
        on_session("observed-thread")
        return AgentRunResult(
            0,
            thread_id="wrong-thread",
            result={"status": "success"},
            usage=usage_snapshot("fixture", "invocation", 123, 4),
        )

    result = launch(monkeypatch, tmp_path, execution, runner)
    assert not result.ok and "identity mismatch" in result.error
    evidence = repo.get(project_id=project_id, request_id=request.id).metadata_json[
        "native_execution"
    ]
    assert evidence["execution"]["thread_id"] == "observed-thread"
    assert evidence["usage"] is None
    with pytest.raises(ConflictError):
        repo.complete(project_id=project_id, request_id=request.id, claim_token=claim.claim_token)


@pytest.mark.parametrize("stop", ["cancel", "shutdown"])
def test_managed_request_process_stop_and_final_persistence(
    threaded_session, project_id, monkeypatch, tmp_path, stop
):
    session = threaded_session
    repo, request, claim, execution = owner(session, project_id)
    ready = Event()
    controls = []
    external = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])

    def runner(*args, process_control, on_session, **kwargs):
        on_session("owned-request-thread")
        process = process_control.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
        controls.append(process_control)
        ready.set()
        process.wait(timeout=10)
        return AgentRunResult(
            process.returncode,
            thread_id="owned-request-thread",
            result={"status": "success"},
            usage=usage_snapshot("fixture", "invocation", 7, 2),
        )

    try:
        with ThreadPoolExecutor() as workers:
            pending = workers.submit(launch, monkeypatch, tmp_path, execution, runner)
            assert ready.wait(3)
            if stop == "shutdown":
                service.shutdown_usage()
            else:
                calls = service.cancel_request_invocations(project_id, request.id)
                assert len(calls) == 1 and calls[0].process_control.stop() == "stopped"
            assert not pending.result(timeout=5).ok
        assert controls[0].cancelled.is_set() and external.poll() is None
        assert service.active_request_execution(project_id, request.id) is None
        evidence = repo.get(project_id=project_id, request_id=request.id).metadata_json[
            "native_execution"
        ]
        assert evidence["cancelled"] and evidence["usage"]["measured"]["total"] == 9
        with pytest.raises(ConflictError):
            repo.complete(
                project_id=project_id, request_id=request.id, claim_token=claim.claim_token
            )
    finally:
        for control in controls:
            control.stop()
        external.terminate()
        external.wait(timeout=5)


def test_cancellation_fences_renewal_late_success_and_wrong_scope(
    session, project_id, monkeypatch, tmp_path
):
    repo, request, claim, execution = owner(session, project_id)
    retained = []

    def runner(*args, on_session, **kwargs):
        on_session("native-request-thread")
        assert service.cancel_request_invocations(project_id + 1, request.id) == []
        assert service.cancel_request_invocations(project_id, request.id + 1) == []
        calls = service.cancel_request_invocations(project_id, request.id)
        retained.extend(calls)
        assert len(calls) == 1
        before = repo.get(project_id=project_id, request_id=request.id).claim_expires_at
        service._sample_invocations()
        assert repo.get(project_id=project_id, request_id=request.id).claim_expires_at == before
        service._attach_session(calls[0], "late-thread", tmp_path, None)
        assert calls[0].session_id == "native-request-thread"
        return AgentRunResult(
            0,
            thread_id="native-request-thread",
            result={"status": "success"},
            usage=usage_snapshot("fixture", "invocation", 10, 2),
        )

    assert not launch(monkeypatch, tmp_path, execution, runner).ok
    evidence = repo.get(project_id=project_id, request_id=request.id).metadata_json[
        "native_execution"
    ]
    assert evidence["cancelled"] and evidence["usage"]["measured"]["total"] == 12
    with pytest.raises(ConflictError):
        repo.complete(project_id=project_id, request_id=request.id, claim_token=claim.claim_token)
    repo.complete(
        project_id=project_id,
        request_id=request.id,
        claim_token=claim.claim_token,
        status=AgentRequestStatus.FAILED,
    )
    with pytest.raises(ConflictError):
        execution.renew(retained[0])
    with pytest.raises(NotFoundError):
        repo.get(project_id=project_id + 1, request_id=request.id)


def test_crash_remains_unresolved_and_cannot_be_reclaimed(
    session, project_id, monkeypatch, tmp_path
):
    repo, request, claim, execution = owner(session, project_id)

    def runner(*args, **kwargs):
        raise RuntimeError("synthetic native crash")

    with pytest.raises(RuntimeError, match="synthetic native crash"):
        launch(monkeypatch, tmp_path, execution, runner)
    row = session.get(AgentRequest, request.id)
    session.refresh(row)
    row.claim_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    session.add(row)
    session.commit()
    assert (
        repo.get(project_id=project_id, request_id=request.id).status == AgentRequestStatus.CLAIMED
    )
    assert repo.list_claimable(project_id=project_id).items == []
    with pytest.raises(ConflictError):
        repo.claim(project_id=project_id, request_id=request.id, claimed_by="retry")
    with pytest.raises(ConflictError):
        repo.prepare_run_plan(
            project_id=project_id,
            request_id=request.id,
            claimed_by="other-runner",
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "stolen",
                "title": "Wrong owner",
                "steps": [{"id": "work", "title": "Work"}],
            },
        )
    with pytest.raises(ConflictError):
        repo.complete(project_id=project_id, request_id=request.id, claim_token=claim.claim_token)


@pytest.mark.parametrize("renewal_rejected", [False, True])
def test_collector_and_completion_preserve_native_outcome(
    threaded_session, project_id, monkeypatch, tmp_path, renewal_rejected
):
    repo, request, claim, execution = owner(threaded_session, project_id)
    inspected = Event()
    finish_boundary = Event()
    may_renew = Event()
    launch_lock = service.managed_launch_lock
    owns = service.owns_request_invocation
    finish = service._finish
    renew = AgentRequestRepository._renew_execution
    collectors = []

    class CompletionBoundary:
        def __enter__(self):
            if current_thread().name.startswith("request-native") and inspected.is_set():
                # Let the collector proceed when completion reaches its lock,
                # without requiring completion to run through a held launch lock.
                finish_boundary.set()
            return launch_lock.__enter__()

        def __exit__(self, *args):
            return launch_lock.__exit__(*args)

    def pause_after_live_check(call, *, finished=False):
        owned = owns(call, finished=finished)
        if current_thread().name == "request-collector" and owned:
            inspected.set()
            assert may_renew.wait(5)
        return owned

    def checked_renew(repository, owner, call):
        if current_thread().name == "request-collector" and renewal_rejected:
            raise ConflictError("synthetic live ownership rejection")
        renew(repository, owner, call)

    def finish_during_collection(call, result, resume_thread_id=None, *, finished_at=None):
        collector = Thread(target=service._sample_invocations, name="request-collector")
        collectors.append(collector)
        collector.start()
        assert inspected.wait(5)
        try:
            finish(call, result, resume_thread_id, finished_at=finished_at)
        finally:
            # On the rejected implementation completion has already marked the
            # call finished without waiting for the paused renewal.
            finish_boundary.set()

    monkeypatch.setattr(service, "managed_launch_lock", CompletionBoundary())
    monkeypatch.setattr(service, "owns_request_invocation", pause_after_live_check)
    monkeypatch.setattr(service, "_finish", finish_during_collection)
    monkeypatch.setattr(AgentRequestRepository, "_renew_execution", checked_renew)

    def runner(*args, on_session, **kwargs):
        on_session("collector-race-thread")
        return AgentRunResult(
            0,
            thread_id="collector-race-thread",
            result={"status": "success"},
            usage=usage_snapshot("fixture", "invocation", 40, 3),
        )

    try:
        with ThreadPoolExecutor(thread_name_prefix="request-native") as workers:
            pending = workers.submit(launch, monkeypatch, tmp_path, execution, runner)
            try:
                assert finish_boundary.wait(5)
            finally:
                may_renew.set()
            result = pending.result(timeout=5)
    finally:
        may_renew.set()
        for collector in collectors:
            collector.join(5)
            assert not collector.is_alive()

    evidence = repo.get(project_id=project_id, request_id=request.id).metadata_json[
        "native_execution"
    ]
    assert result.ok is not renewal_rejected
    assert evidence["exit_code"] == 0
    assert evidence["cancelled"] is renewal_rejected
    assert evidence["usage"]["measured"]["total"] == 43
    if renewal_rejected:
        with pytest.raises(ConflictError, match="no successful terminal evidence"):
            repo.complete(
                project_id=project_id, request_id=request.id, claim_token=claim.claim_token
            )
    repo.complete(
        project_id=project_id,
        request_id=request.id,
        claim_token=claim.claim_token,
        status=AgentRequestStatus.FAILED if renewal_rejected else AgentRequestStatus.RESOLVED,
    )
    assert service.active_request_execution(project_id, request.id) is None
