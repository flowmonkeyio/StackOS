"""Managed upgrade uses real delivery persistence and exact native ownership."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event

import pytest
from sqlmodel import Session

from stackos import task_delivery
from stackos.repositories.base import ConflictError
from stackos.repositories.tracker import TrackerRepository
from tests.integration.test_repositories.test_task_delivery import (
    QueuedScheduler,
    generic_result,
    git,
    git_project,  # noqa: F401 -- real disposable Git baseline
    native_source_fixtures,  # noqa: F401 -- native source admission
    prepared_task,
    submit,
    use_codex_native,
)
from tests.integration.test_repositories.test_task_delivery import (
    delivery_engine as delivery_engine,
)

IDENTITY = dict(
    upgrade_id="fixture-upgrade", source_identity="source-build", target_identity="target-build"
)


def prepare(session, scheduler, **kwargs):
    return task_delivery.prepare_managed_upgrade(
        session_factory=lambda: Session(session.get_bind()),
        scheduler=scheduler,
        expected_revision=kwargs.pop("expected_revision", 0),
        **(IDENTITY | kwargs),
    )


def test_prepare_checkpoints_queue_without_changing_lifecycle(session, project_id, tmp_path):
    prepared_task(session, project_id)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    stale_callback, options = scheduler.jobs[0]
    repo = TrackerRepository(session)
    before = repo.get(project_id=project_id, task_key="deliver")
    result = prepare(session, scheduler)
    assert result["status"] == "quiesced"
    assert len(result["checkpoint_refs"]) == 1
    session.expire_all()
    after = repo.get(project_id=project_id, task_key="deliver")
    assert after.tasks[0].status == before.tasks[0].status
    assert [(t.key, t.status, t.completion_evidence_json) for t in after.tickets] == [
        (t.key, t.status, t.completion_evidence_json) for t in before.tickets
    ]
    state = after.tasks[0].metadata_json["delivery"]
    checkpoint = state["upgrade_checkpoint"]
    assert state["status"] == "queued" and not state["slot_reserved"]
    assert checkpoint["handoff_id"] == before.tasks[0].metadata_json["delivery"]["handoff_id"]
    assert checkpoint["status"] == "quiesced" and checkpoint["phase"] is None
    assert scheduler.jobs == []
    stale_callback(**options["kwargs"])
    assert prepare(session, scheduler) == result
    with Session(session.get_bind()) as reopened:
        from stackos.repositories.managed_upgrades import ManagedUpgradeRepository

        assert ManagedUpgradeRepository(reopened).status(IDENTITY["upgrade_id"]) == result
    with pytest.raises(ConflictError, match="fenced"):
        (tmp_path / "next").mkdir()
        submit(session, project_id, tmp_path / "next", scheduler, task_key="unrelated")


def test_unknown_running_owner_holds_fence_and_retains_evidence(session, project_id, tmp_path):
    prepared_task(session, project_id)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    repo = TrackerRepository(session)
    task_delivery._update_delivery(
        repo, project_id, "deliver", status="running", ticket_key="first"
    )
    retained = {
        "automatic_delivery": {
            "attempt": 2,
            "repair_count": 1,
            "delivery": {"launch": {"agent": "fixture"}},
        }
    }
    repo.update_ticket(
        project_id=project_id,
        ticket_key="first",
        patch_json={"status": "in-progress", "completion_evidence_json": retained},
    )
    before = deepcopy(repo.brief(project_id=project_id, ticket_key="first").ticket)
    with pytest.raises(ConflictError) as held:
        prepare(session, scheduler)
    assert held.value.data["status"] == "held"
    assert "unknown_native_owner" in str(held.value.data)
    session.expire_all()
    after = repo.brief(project_id=project_id, ticket_key="first").ticket
    assert (
        after.status == before.status
        and after.completion_evidence_json == before.completion_evidence_json
    )
    assert (
        repo.get(project_id=project_id, task_key="deliver")
        .tasks[0]
        .metadata_json["delivery"]["status"]
        == "running"
    )


@pytest.mark.parametrize(
    "phase,late",
    [
        ("delivery", "result"),
        ("review", "result"),
        ("mirror", "result"),
        ("delivery", "exception"),
        ("review", "exception"),
        ("mirror", "exception"),
        ("delivery", "unconfirmed"),
    ],
)
def test_real_process_checkpoint_and_late_callback_preserve_original_phase(
    delivery_engine, project_id, tmp_path, monkeypatch, phase, late
):
    from stackos.host_mcp import service
    from stackos.host_mcp.result import AgentRunResult
    from stackos.repositories.run_plans import RunPlanRepository

    root = tmp_path / "source"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.test")
    git(root, "commit", "--allow-empty", "-m", "baseline")
    head = git(root, "rev-parse", "HEAD")
    scheduler, ready = QueuedScheduler(), Event()
    native, launches = [], []
    if late == "unconfirmed":
        from stackos.host_mcp.agent_execution import ManagedProcessControl

        stop = ManagedProcessControl.stop

        def unconfirmed(control, *args, **kwargs):
            stop(control, *args, **kwargs)
            return "unconfirmed"  # A missing confirmation must remain a hold, even after exit.

        monkeypatch.setattr(ManagedProcessControl, "stop", unconfirmed)

    def execute(
        _directory, _instructions, prompt, _schema, *, on_session, process_control, **_kwargs
    ):
        current = json.loads(prompt)["phase"]
        launches.append(current)
        thread = f"original-{current}"
        on_session(thread)
        if phase == "review" and current == "delivery":
            return AgentRunResult(
                0, thread_id=thread, result=generic_result(outputs={"commit_sha": head})
            )
        script = (
            "from pathlib import Path; import time; "
            "Path('credentials.txt').write_text('owned fixture contents'); "
            "print('ready', flush=True); time.sleep(30)"
        )
        process = process_control.spawn(
            [sys.executable, "-c", script], cwd=root, stdout=subprocess.PIPE, text=True
        )
        native.append(process)
        assert process.stdout.readline().strip() == "ready"
        process.stdout.close()
        ready.set()
        process.wait(timeout=10)
        if late == "exception":
            raise RuntimeError("controlled late native exception")
        # Simulate a buffered successful payload crossing retirement. It must
        # remain observation, never acceptance of the fenced phase.
        return AgentRunResult(
            0, thread_id=thread, result=generic_result(outputs={"commit_sha": head})
        )

    use_codex_native(monkeypatch, execute)
    with Session(delivery_engine) as session:
        repo = TrackerRepository(session)
        scope = {}
        task_key, ticket_key = "deliver", "first"
        if phase == "mirror":
            plans = RunPlanRepository(session)
            plan = plans.create(
                project_id=project_id,
                run_plan_json={
                    "schema_version": "stackos.run-plan.v1",
                    "key": "upgrade-mirror",
                    "title": "Fixture",
                    "steps": [{"id": "deliver", "title": "Deliver"}],
                },
            ).data
            started = plans.start(plan.id, project_id=project_id)
            plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="deliver")
            scope = {"run_plan_id": plan.id, "step_id": "deliver"}
            task_key, ticket_key = f"workflow-{plan.id}", f"workflow-{plan.id}-deliver"
        else:
            prepared_task(session, project_id)
        submit(session, project_id, root, scheduler, task_key=task_key, **scope)
        try:
            with ThreadPoolExecutor() as workers:
                running = workers.submit(scheduler.run)
                assert ready.wait(5)
                session.expire_all()
                before = repo.brief(project_id=project_id, ticket_key=ticket_key).ticket
                evidence = deepcopy(before.completion_evidence_json["automatic_delivery"])
                if late == "unconfirmed":
                    with pytest.raises(ConflictError) as held:
                        prepare(session, scheduler)
                    result = held.value.data
                else:
                    result = prepare(session, scheduler)
                running.result(timeout=3)
            assert result["status"] == ("held" if late == "unconfirmed" else "quiesced")
            session.expire_all()
            task = repo.get(project_id=project_id, task_key=task_key).tasks[0]
            checkpoint = task.metadata_json["delivery"]["upgrade_checkpoint"]
            after = repo.brief(project_id=project_id, ticket_key=ticket_key).ticket
            retained = after.completion_evidence_json["automatic_delivery"]
            original_phase = "delivery" if phase == "mirror" else phase
            assert checkpoint["phase"] == original_phase and checkpoint["attempt"] == 1
            if late == "unconfirmed":
                assert checkpoint["issues"] == ["native_retirement_unconfirmed"]
            else:
                assert checkpoint["issues"] == [] and checkpoint["status"] == "quiesced"
                assert checkpoint["worktree_snapshot"]["untracked"][0]["sha256"]
            assert "owned fixture contents" not in json.dumps(checkpoint)
            assert retained["sessions"] == evidence["sessions"]
            assert retained.get("repair_count") == evidence.get("repair_count")
            assert retained["candidate"] == evidence["candidate"]
            assert retained[original_phase].get("validated_outcome") is None
            assert not retained[original_phase].get("cancelled")
            assert after.status == before.status == "in-progress"
            assert task.metadata_json["delivery"]["status"] == "running"
            assert not task.metadata_json["delivery"]["slot_reserved"]
            assert native[0].poll() is not None and not service.usage_executions(project_id)
            assert launches == (["delivery", "review"] if phase == "review" else ["delivery"])
            if phase == "review":
                assert retained["delivery"] == evidence["delivery"]
            if phase == "mirror":
                assert plans.get_step(plan.id, "deliver").status == "running"
            if late != "unconfirmed":
                assert prepare(session, scheduler) == result
        finally:
            for process in native:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=3)


def test_atomic_cross_project_capture_and_exclusions(session, project_id, tmp_path, monkeypatch):
    from stackos.db.models import ManagedUpgrade
    from stackos.repositories.projects import ProjectRepository

    other = (
        ProjectRepository(session).create(slug="other", name="Other", domain="fixture.test").data.id
    )
    scheduler = QueuedScheduler()
    for identity, name in ((project_id, "one"), (other, "two")):
        prepared_task(session, identity)
        root = tmp_path / name
        root.mkdir()
        submit(session, identity, root, scheduler)
    repo = TrackerRepository(session)
    # Pre-existing manual/stopped/failed work is retained and never captured.
    for name, status in (("manual", None), ("stopped", "stopped"), ("failed", "stopped")):
        repo.create_task(project_id=project_id, key=name, title=name)
        if status:
            task_delivery._update_delivery(repo, project_id, name, status=status, handoff_id=name)
    checkpoint = task_delivery._checkpoint_task
    count = 0

    def fail_second(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("controlled preparation write failure")
        return checkpoint(*args)

    monkeypatch.setattr(task_delivery, "_checkpoint_task", fail_second)
    with pytest.raises(RuntimeError, match="write failure"):
        prepare(session, scheduler)
    with Session(session.get_bind()) as fresh:
        assert fresh.get(ManagedUpgrade, IDENTITY["upgrade_id"]) is None
        for identity in (project_id, other):
            task = TrackerRepository(fresh).get(project_id=identity, task_key="deliver").tasks[0]
            assert "upgrade_checkpoint" not in task.metadata_json["delivery"]
    assert len(scheduler.jobs) == 2
    monkeypatch.setattr(task_delivery, "_checkpoint_task", checkpoint)
    result = prepare(session, scheduler)
    assert {ref["project_id"] for ref in result["checkpoint_refs"]} == {project_id, other}
    assert scheduler.jobs == []


def test_registration_race_holds_until_exact_callback_acknowledges_retirement(
    delivery_engine, project_id, tmp_path
):
    from stackos.repositories.managed_upgrades import ManagedUpgradeRepository

    entered, release = Event(), Event()

    class Delayed(QueuedScheduler):
        def add_job(self, function, **options):
            entered.set()
            assert release.wait(10)
            super().add_job(function, **options)

    scheduler = Delayed()
    with Session(delivery_engine) as session:
        prepared_task(session, project_id)

    def admit():
        with Session(delivery_engine) as session:
            return submit(session, project_id, tmp_path, scheduler)

    with ThreadPoolExecutor() as workers:
        admission = workers.submit(admit)
        assert entered.wait(5)
        try:
            with Session(delivery_engine) as session:
                with pytest.raises(ConflictError) as held:
                    prepare(session, scheduler)
                assert "callback_retirement_unconfirmed" in str(held.value.data)
                revision = held.value.data["revision"]
                with pytest.raises(ConflictError, match="revision"):
                    prepare(session, scheduler, expected_revision=revision - 1)
        finally:
            release.set()
        admission.result(timeout=3)
    assert scheduler.jobs == []
    with Session(delivery_engine) as session:
        result = prepare(session, scheduler, expected_revision=revision)
        assert result["status"] == "quiesced"
        assert ManagedUpgradeRepository(session).active().revision == revision + 1


def test_terminal_result_winner_is_never_checkpointed(session, project_id, tmp_path, monkeypatch):
    from stackos.host_mcp.result import AgentRunResult

    prepared_task(session, project_id)
    scheduler = QueuedScheduler()
    head = git(tmp_path, "rev-parse", "HEAD")

    def complete(*_args, on_session, **_kwargs):
        thread = f"native-{len(calls)}"
        calls.append(thread)
        on_session(thread)
        return AgentRunResult(
            0, thread_id=thread, result=generic_result(outputs={"commit_sha": head})
        )

    calls = []
    use_codex_native(monkeypatch, complete)
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    before = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert all(ticket.status == "complete" for ticket in before.tickets)
    result = prepare(session, scheduler)
    assert result["status"] == "quiesced" and result["checkpoint_refs"] == []
    after = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert after.model_dump() == before.model_dump()


def test_fence_denies_new_setup_and_continuation_before_any_mutation(session, project_id):
    from sqlmodel import select

    from stackos.db.models import AgentRequest
    from stackos.project_setup_execution import continue_managed, start_managed

    scheduler = QueuedScheduler()
    prepare(session, scheduler)
    common = dict(
        project_id=project_id,
        session_factory=lambda: Session(session.get_bind()),
        scheduler=scheduler,
    )
    for operation in (
        lambda: start_managed(
            **common, mode="setup", workflow_keys=["engineering.tracked-delivery"]
        ),
        lambda: continue_managed(request_id=999, expected_revision=1, **common),
    ):
        with pytest.raises(ConflictError, match="fenced"):
            operation()
    assert session.exec(select(AgentRequest)).all() == []


def test_preparation_prevents_first_spawn_without_inventing_native_session(
    delivery_engine, project_id, tmp_path, monkeypatch
):
    ready = Event()
    scheduler = QueuedScheduler()

    def execute(*_args, process_control, **_kwargs):
        ready.set()
        assert process_control.cancelled.wait(5)
        process_control.spawn([sys.executable, "-c", "raise SystemExit('must never launch')"])

    use_codex_native(monkeypatch, execute)
    with Session(delivery_engine) as session:
        prepared_task(session, project_id)
        submit(session, project_id, tmp_path, scheduler)
        with ThreadPoolExecutor() as workers:
            running = workers.submit(scheduler.run)
            assert ready.wait(5)
            assert prepare(session, scheduler)["status"] == "quiesced"
            running.result(timeout=3)
        session.expire_all()
        repo = TrackerRepository(session)
        task = repo.get(project_id=project_id, task_key="deliver").tasks[0]
        checkpoint = task.metadata_json["delivery"]["upgrade_checkpoint"]
        assert checkpoint["native_never_spawned"] is True
        assert checkpoint["phase"] == "delivery" and checkpoint["attempt"] == 1
        receipt = repo.brief(project_id=project_id, ticket_key="first").ticket
        assert not receipt.completion_evidence_json["automatic_delivery"].get("sessions")
        assert receipt.status == "in-progress"
