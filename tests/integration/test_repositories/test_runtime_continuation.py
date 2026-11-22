"""Ordinary startup/wake uses persisted delivery authority, never failure retry."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from stackos import task_delivery
from stackos.host_mcp import service
from stackos.host_mcp.result import AgentRunResult
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import TrackerRepository
from tests.integration.test_repositories.test_task_delivery import (
    QueuedScheduler,
    generic_result,
    git,
    native_source_fixtures,  # noqa: F401
    prepared_task,
    submit,
    use_codex_native,
)
from tests.integration.test_repositories.test_task_delivery import (
    delivery_engine as delivery_engine,
)


def state(session, project_id):
    session.expire_all()
    return (
        TrackerRepository(session)
        .get(project_id=project_id, task_key="deliver")
        .tasks[0]
        .metadata_json["delivery"]
    )


def startup(session, scheduler):
    task_delivery.continue_delivery(
        session_factory=lambda: Session(session.get_bind()), scheduler=scheduler
    )


def test_new_generation_rejoins_authorized_queue_once_and_fences_old_callback(
    session, project_id, tmp_path
):
    prepared_task(session, project_id)
    old = QueuedScheduler()
    submit(session, project_id, tmp_path, old)
    callback, options = old.jobs[0]
    before = deepcopy(state(session, project_id))
    new = QueuedScheduler()
    startup(session, new)
    after = state(session, project_id)
    assert len(new.jobs) == 1
    assert after["admission_id"] != before["admission_id"]
    assert after["handoff_id"] != before["handoff_id"]
    assert after["agents"] == before["agents"]
    callback(**options["kwargs"])
    startup(session, new)
    assert len(new.jobs) == 1
    assert state(session, project_id) == after


def test_missed_authorized_launch_requeues_once_without_accepting_late_callback(
    session, project_id, tmp_path
):
    prepared_task(session, project_id)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    callback, options = scheduler.jobs.pop(0)
    event = SimpleNamespace(job_id=options["id"])
    task_delivery.missed_delivery_job(
        event, session_factory=lambda: Session(session.get_bind()), scheduler=scheduler
    )
    assert len(scheduler.jobs) == 1
    assert state(session, project_id)["status"] == "queued"
    callback(**options["kwargs"])
    task_delivery.missed_delivery_job(
        event, session_factory=lambda: Session(session.get_bind()), scheduler=scheduler
    )
    assert len(scheduler.jobs) == 1


@pytest.mark.parametrize("excluded", ["legacy", "stopped", "failed", "complete"])
def test_startup_never_rewrites_historical_or_terminal_admission(
    session, project_id, tmp_path, excluded
):
    prepared_task(session, project_id)
    old = QueuedScheduler()
    submit(session, project_id, tmp_path, old)
    repo = TrackerRepository(session)
    task = repo.get(project_id=project_id, task_key="deliver").tasks[0]
    metadata = deepcopy(task.metadata_json)
    if excluded == "legacy":
        metadata["delivery"].pop("continuation_intent")
    else:
        metadata["delivery"]["status"] = "complete" if excluded == "complete" else "stopped"
        metadata["delivery"]["reason"] = excluded
    repo.update_task(
        project_id=project_id, task_key="deliver", patch_json={"metadata_json": metadata}
    )
    before = state(session, project_id)
    startup(session, QueuedScheduler())
    assert state(session, project_id) == before


def test_scheduler_start_event_uses_the_same_queue_owner(
    delivery_engine, project_id, tmp_path, monkeypatch
):
    import asyncio

    from stackos.config import Settings
    from stackos.jobs.scheduler import build_scheduler

    old, invoked = QueuedScheduler(), Event()
    calls = []
    with Session(delivery_engine) as session:
        prepared_task(session, project_id)
        submit(session, project_id, tmp_path, old)

    def callback(**kwargs):
        calls.append(kwargs["handoff_id"])
        invoked.set()

    monkeypatch.setattr(task_delivery, "run_delivery", callback)

    async def run():
        scheduler = build_scheduler(Settings(), delivery_engine)
        scheduler.start()
        try:
            assert await asyncio.to_thread(invoked.wait, 5)
            with Session(delivery_engine) as session:
                assert calls == [state(session, project_id)["handoff_id"]]
                startup(session, scheduler)
                assert len(calls) == 1
        finally:
            scheduler.shutdown(wait=True)
            await asyncio.sleep(0)

    asyncio.run(run())


@pytest.mark.parametrize("mirror", [False, True])
def test_loss_after_acceptance_advances_without_replaying_completed_producer(
    session, project_id, tmp_path, monkeypatch, mirror
):
    class ControllerLost(BaseException):
        pass

    repo = TrackerRepository(session)
    scope = {}
    if mirror:
        plans = RunPlanRepository(session)
        plan = plans.create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "runtime-mirrors",
                "title": "Fixture",
                "steps": [
                    {"id": "first", "title": "First"},
                    {"id": "next", "title": "Next", "depends_on": ["first"]},
                ],
            },
        ).data
        started = plans.start(plan.id, project_id=project_id)
        plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="first")
        task_key, target = f"workflow-{plan.id}", f"workflow-{plan.id}-first"
        scope = {"run_plan_id": plan.id, "step_id": "first"}
    else:
        prepared_task(session, project_id)
        task_key, target = "deliver", "first"
    calls = []

    def native(_dir, _instructions, prompt, _schema, *, on_session, **_kwargs):
        packet = json.loads(prompt)
        key, phase = packet["brief"]["ticket"]["key"], packet["phase"]
        calls.append((key, phase))
        thread = f"{key}-{phase}"
        on_session(thread)
        return AgentRunResult(0, thread_id=thread, result=generic_result())

    use_codex_native(monkeypatch, native)
    save = task_delivery._save_evidence
    interrupted = False

    def lose_after_acceptance(*args, accepted=False, **kwargs):
        nonlocal interrupted
        save(*args, accepted=accepted, **kwargs)
        if accepted and args[2] == target and not interrupted:
            interrupted = True
            raise ControllerLost()

    monkeypatch.setattr(task_delivery, "_save_evidence", lose_after_acceptance)
    old, new = QueuedScheduler(), QueuedScheduler()
    submit(session, project_id, tmp_path, old, task_key=task_key, **scope)
    with pytest.raises(ControllerLost):
        old.run()
    monkeypatch.setattr(task_delivery, "_save_evidence", save)
    startup(session, new)
    assert len(new.jobs) == 1
    new.run()
    session.expire_all()
    assert (
        repo.get(project_id=project_id, task_key=task_key)
        .tasks[0]
        .metadata_json["delivery"]["status"]
        == "complete"
    )
    assert calls.count((target, "delivery")) == 1
    if mirror:
        assert plans.get(plan.id).status == "completed"
    else:
        assert calls.count((target, "review")) == 1


@pytest.mark.parametrize(
    ("phase", "control"),
    [
        (phase, control)
        for phase in ("delivery", "review")
        for control in (
            "retired",
            "surviving",
            "missing-session",
            "missing-owner",
            "authority",
            "settings",
            "stop",
            "failed",
            "exhausted",
        )
    ]
    + [("delivery", "dirty"), ("review", "managed")],
)
def test_retired_phase_resumes_exact_session_while_unsafe_owners_hold(
    delivery_engine, project_id, tmp_path, monkeypatch, phase, control
):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.test")
    git(root, "commit", "--allow-empty", "-m", "baseline")
    old, new = QueuedScheduler(), QueuedScheduler()
    ready, release = Event(), Event()
    calls, processes = [], []

    def native(_dir, _instructions, prompt, _schema, *, process_control, on_session, **kwargs):
        current_phase = json.loads(prompt)["phase"]
        calls.append((current_phase, kwargs.get("resume_thread_id")))
        thread = f"retained-{current_phase}"
        if current_phase == phase and kwargs.get("resume_thread_id") is None:
            script = "import time; print('ready', flush=True); time.sleep(30)"
            if control == "dirty":
                script = (
                    "from pathlib import Path; Path('owned.txt').write_text('owned work'); "
                    + script
                )
            child = process_control.spawn(
                [sys.executable, "-c", script],
                cwd=root,
                stdout=subprocess.PIPE,
                text=True,
            )
            processes.append(child)
            assert child.stdout.readline().strip() == "ready"
            child.stdout.close()
            on_session(thread)
            ready.set()
            assert release.wait(15)
            child.wait(timeout=5)
            return AgentRunResult(child.returncode, thread_id=thread, error="Interrupted fixture")
        else:
            on_session(thread)
        if current_phase == "delivery":
            if control == "dirty":
                git(root, "add", "owned.txt")
            git(root, "commit", "--allow-empty", "-m", "delivered candidate")
        return AgentRunResult(
            0,
            thread_id=thread,
            result=generic_result(outputs={"commit_sha": git(root, "rev-parse", "HEAD")}),
        )

    use_codex_native(monkeypatch, native)
    with Session(delivery_engine) as session:
        prepared_task(session, project_id)
        repo = TrackerRepository(session)
        accepted = {"accepted": {"candidate": "unchanged", "independent_review": True}}
        submit(session, project_id, root, old)
        repo.update_ticket(
            project_id=project_id,
            ticket_key="first",
            patch_json={"status": "complete", "completion_evidence_json": accepted},
        )
        repo.update_ticket(
            project_id=project_id,
            ticket_key="second",
            patch_json={"completion_evidence_json": {"automatic_delivery": {"repair_count": 2}}},
        )
        managed = None
        if control == "managed":
            from tests.integration.test_repositories.test_managed_upgrade import prepare
            from tests.integration.test_repositories.test_upgrade_continuation import finish

            prepare(session, old)
            middle = QueuedScheduler()
            managed = finish(session, middle)
            old = middle
        try:
            with ThreadPoolExecutor(1) as workers:
                work = workers.submit(old.run)
                try:
                    assert ready.wait(5), state(session, project_id)
                    session.expire_all()
                    original = deepcopy(
                        repo.brief(
                            project_id=project_id, ticket_key="second"
                        ).ticket.completion_evidence_json["automatic_delivery"]
                    )
                    execution = original[phase]["execution"]
                    assert execution["process_identity"]["pid"] == processes[0].pid
                    assert execution["process_identity"]["started"]
                    if control != "surviving":
                        processes[0].terminate()
                        processes[0].wait(timeout=3)
                    if control == "missing-session":
                        changed = deepcopy(original)
                        changed["sessions"].pop(phase)
                        # Exercise the session check, beyond the evidence-integrity fence.
                        task_delivery._save_evidence(repo, project_id, "second", changed)
                    elif control == "authority":
                        repo.update_ticket(
                            project_id=project_id,
                            ticket_key="second",
                            patch_json={"context_json": {"contract_revision": 2}},
                        )
                    elif control == "settings":
                        agents = deepcopy(state(session, project_id)["agents"])
                        agents["delivery_agent"]["instructions"] = "Changed frozen instructions"
                        task_delivery._update_delivery(repo, project_id, "deliver", agents=agents)
                    elif control == "missing-owner":
                        changed = deepcopy(original)
                        changed[phase]["execution"]["process_identity"] = None
                        task_delivery._save_evidence(repo, project_id, "second", changed)
                    elif control == "stop":
                        task_delivery._update_delivery(
                            repo, project_id, "deliver", status="stopped", reason="Operator Stop"
                        )
                    elif control in {"failed", "exhausted"}:
                        changed = deepcopy(original)
                        if control == "failed":
                            changed[phase]["error"] = "Native approval denied"
                        else:
                            changed["repair_count"] = 3
                        task_delivery._save_evidence(repo, project_id, "second", changed)
                    # A fresh daemon has no prior in-memory invocation registry.
                    # Keep the old callback to exercise late-result fencing below.
                    with service._active_invocations_lock:
                        service._active_invocations.pop(execution["invocation_id"])
                    startup(session, new)
                    startup(session, new)
                    if control in {"retired", "dirty", "managed"}:
                        assert len(new.jobs) == 1, state(session, project_id)
                        new.run()
                        current = repo.brief(project_id=project_id, ticket_key="second").ticket
                        assert current.status == "complete", state(session, project_id).get(
                            "reason"
                        )
                        result = current.completion_evidence_json["automatic_delivery"]
                        assert result["attempt"] == original["attempt"]
                        assert result["repair_count"] == 2
                        assert result["sessions"] == original["sessions"] or phase == "delivery"
                        assert (phase, f"retained-{phase}") in calls
                        if phase == "review":
                            assert [p for p, _ in calls] == ["delivery", "review", "review"]
                            assert result["delivery"] == original["delivery"]
                        if managed:
                            from stackos.repositories.managed_upgrades import (
                                ManagedUpgradeRepository,
                            )

                            upgraded = ManagedUpgradeRepository(session).status(
                                managed["upgrade_id"],
                                daemon_generation=task_delivery._admission_id(new),
                            )
                            assert upgraded["checkpoint_refs"] == managed["checkpoint_refs"]
                            assert upgraded["checkpoints"][0]["continuation_state"] == "settled"
                            assert not upgraded["recovery_required"]
                            finish(session, new, revision=managed["revision"])
                            assert not new.jobs
                    else:
                        assert not new.jobs
                        assert state(session, project_id)["status"] == "stopped"
                        assert state(session, project_id)["reason"]
                        if control in {"surviving", "missing-owner"}:
                            assert (
                                task_delivery.delivery_capacity(session, new)["projects"][0][
                                    "running_count"
                                ]
                                == 1
                            )
                            later = QueuedScheduler()
                            startup(session, later)
                            assert not later.jobs
                            assert (
                                task_delivery.delivery_capacity(session, later)["projects"][0][
                                    "running_count"
                                ]
                                == 1
                            )
                            with pytest.raises(Exception, match="still retiring"):
                                task_delivery._retain_reservation(
                                    state(session, project_id), task_delivery._admission_id(later)
                                )
                            if control == "surviving":
                                processes[0].terminate()
                                processes[0].wait(timeout=3)
                                # Only an explicit later handoff can reconcile
                                # this exact, now-retired owner; no auto retry.
                                task_delivery._retain_reservation(
                                    state(session, project_id), task_delivery._admission_id(later)
                                )
                                assert not later.jobs
                    before_late = deepcopy(
                        repo.brief(
                            project_id=project_id, ticket_key="second"
                        ).ticket.completion_evidence_json
                    )
                finally:
                    for child in processes:
                        if child.poll() is None:
                            child.terminate()
                            child.wait(timeout=3)
                    release.set()
                work.result(timeout=5)
            session.expire_all()
            assert (
                repo.brief(
                    project_id=project_id, ticket_key="first"
                ).ticket.completion_evidence_json
                == accepted
            )
            if control != "stop":
                assert (
                    repo.brief(
                        project_id=project_id, ticket_key="second"
                    ).ticket.completion_evidence_json
                    == before_late
                )
        finally:
            release.set()
