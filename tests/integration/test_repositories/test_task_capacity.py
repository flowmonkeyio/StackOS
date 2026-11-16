"""Real admission and persistence with inert native calls and isolated databases."""

# Imported pytest fixtures are intentionally shadowed by fixture arguments.
# ruff: noqa: F811

import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from threading import Barrier, Event, Lock
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from stackos import task_delivery
from stackos.config import Settings
from stackos.db.models import Project, TrackerTask
from stackos.host_mcp import service
from stackos.host_mcp.result import AgentRunResult
from stackos.jobs.scheduler import build_scheduler
from stackos.mcp.context import MCPContext
from stackos.operations.tracker.capacity import SetDeliveryCapacityInput, set_capacity
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.tracker import TrackerRepository
from stackos.task_capacity import configured_limit, save_limits
from tests.integration.test_repositories.test_task_delivery import (
    QueuedScheduler,
    delivery_engine,  # noqa: F401
    git_project,  # noqa: F401
    native_source_fixtures,  # noqa: F401
    prepared_task,
    submit,
    workflow_task,
)


def capacity(session, scheduler):
    session.expire_all()
    return task_delivery.delivery_capacity(session, scheduler)["projects"][0]


@pytest.fixture
def capacity_session(delivery_engine):
    with Session(delivery_engine) as session:
        yield session


def save(session, scheduler, project_id, value):
    return (
        asyncio.run(
            set_capacity(
                SetDeliveryCapacityInput(
                    updates=[{"project_id": project_id, "max_concurrent_tasks": value}]
                ),
                MCPContext(
                    session=session,
                    request_id="capacity-fixture",
                    extras={
                        "surface": "rest",
                        "trusted_local_admin": True,
                        "scheduler": scheduler,
                        "session_factory": lambda: Session(session.get_bind()),
                    },
                ),
                None,
            )
        )
        .data.projects[0]
        .model_dump()
    )


def task_rows(session):
    session.expire_all()
    return {
        (task.project_id, task.key): deepcopy(task.metadata_json)
        for task in session.exec(select(TrackerTask)).all()
    }


def test_capacity_raise_lower_drain_cross_project_and_preserve_evidence(
    capacity_session, project_id, tmp_path, monkeypatch
):
    session = capacity_session
    monkeypatch.setattr(task_delivery, "_select_work", lambda *args: (None, None, False))
    other = (
        ProjectRepository(session).create(slug="other", name="Other", domain="other.test").data.id
    )
    scheduler = QueuedScheduler()
    receipts = []
    for index in range(7):
        project = project_id
        key = f"work-{index}"
        root = tmp_path / key
        root.mkdir()
        repo = TrackerRepository(session)
        repo.create_task(project_id=project, key=key, title=key)
        repo.create_ticket(
            project_id=project,
            task_key=key,
            key=key,
            title=key,
        )
        repo.update_ticket(
            project_id=project,
            ticket_key=key,
            patch_json={
                "completion_evidence_json": {
                    "automatic_delivery": {
                        "repair_count": 2,
                        "sessions": {"delivery": {"thread_id": "retained"}},
                        "candidate": {"commit_sha": "a" * 40},
                    }
                }
            },
        )
        receipts.append((project, key, submit(session, project, root, scheduler, task_key=key)))
    before = task_rows(session)
    assert capacity(session, scheduler) == {
        "project_id": project_id,
        "project_name": session.get(Project, project_id).name,
        "project_slug": session.get(Project, project_id).slug,
        "configured_limit": 3,
        "running_count": 3,
        "queued_count": 4,
    }
    assert len(scheduler.jobs) == 3
    assert save(session, scheduler, project_id, 5)["running_count"] == 5
    assert len(scheduler.jobs) == 5
    assert save(session, scheduler, project_id, 1)["running_count"] == 5
    assert len(scheduler.jobs) == 5
    # Actual run_delivery finally releases slots; empty-work fixtures never call a host.
    for _ in range(4):
        scheduler.run()
    assert capacity(session, scheduler)["running_count"] == 1
    assert len(scheduler.jobs) == 1
    scheduler.run()
    assert capacity(session, scheduler)["running_count"] == 1
    assert len(scheduler.jobs) == 1  # Next waiting task released through existing queue.
    assert save(session, scheduler, project_id, 4)["running_count"] == 2
    assert configured_limit(session, other) == 3
    for project, key, _ in receipts:
        ticket = TrackerRepository(session).brief(project_id=project, ticket_key=key).ticket
        # Capacity writes did not touch ticket sessions, candidate or repair allowance.
        assert ticket.completion_evidence_json["automatic_delivery"]["repair_count"] == 2
        assert ticket.completion_evidence_json["automatic_delivery"]["sessions"]["delivery"] == {
            "thread_id": "retained"
        }
    after = task_rows(session)
    for identity, metadata in before.items():
        assert after[identity]["delivery"]["handoff_id"] == metadata["delivery"]["handoff_id"]
        assert after[identity]["delivery"]["agents"] == metadata["delivery"]["agents"]


def test_concurrent_admission_is_bounded_across_projects(delivery_engine, project_id, tmp_path):
    scheduler = QueuedScheduler()
    with Session(delivery_engine) as session:
        other = (
            ProjectRepository(session)
            .create(slug="other", name="Other", domain="other.test")
            .data.id
        )
        save_limits(session, {project_id: 4})
        for index in range(10):
            project = project_id if index % 2 else other
            repo = TrackerRepository(session)
            repo.create_task(project_id=project, key=f"work-{index}", title="Work")
            (tmp_path / str(index)).mkdir()
    ready = Barrier(10)

    def start(index):
        ready.wait(10)
        with Session(delivery_engine) as session:
            return submit(
                session,
                project_id if index % 2 else other,
                tmp_path / str(index),
                scheduler,
                task_key=f"work-{index}",
                host_key="gemini-cli",
                delivery_agent={"name": "inert", "instructions": "Fixture"},
                review_agent={"name": "reviewer", "instructions": "Fixture"},
            )

    with ThreadPoolExecutor(max_workers=10) as workers:
        assert len(list(workers.map(start, range(10)))) == 10
    with Session(delivery_engine) as session:
        assert capacity(session, scheduler)["running_count"] == 4
        assert capacity(session, scheduler)["queued_count"] == 1
        other_row = task_delivery.delivery_capacity(session, scheduler, other)["projects"][0]
        assert (other_row["running_count"], other_row["queued_count"]) == (3, 2)
    assert len(scheduler.jobs) == 7


def test_limit_reload_separate_database_and_failed_commit(
    delivery_engine, session, project_id, monkeypatch
):
    from sqlalchemy.exc import OperationalError

    with Session(delivery_engine) as first:
        assert configured_limit(first, project_id) == 3
        save_limits(first, {project_id: 9})
    delivery_engine.dispose()
    with Session(delivery_engine) as reloaded:
        assert configured_limit(reloaded, project_id) == 9
        original_commit = reloaded.commit

        def fail():
            raise OperationalError("fixture", {}, Exception("disk unavailable"))

        monkeypatch.setattr(reloaded, "commit", fail)
        with pytest.raises(OperationalError):
            save_limits(reloaded, {project_id: 12})
        from stackos.repositories.base import ValidationError

        with pytest.raises(ValidationError, match="previous limits remain effective"):
            save(reloaded, QueuedScheduler(), project_id, 12)
        monkeypatch.setattr(reloaded, "commit", original_commit)
        assert configured_limit(reloaded, project_id) == 9
    assert configured_limit(session, project_id) == 3


@pytest.mark.parametrize("value", [0, 33, -1, True, 2.5, "6", None])
def test_invalid_capacity_never_mutates(session, project_id, value):
    with pytest.raises(ValueError):
        SetDeliveryCapacityInput(
            updates=[{"project_id": project_id, "max_concurrent_tasks": value}]
        )
    with pytest.raises(Exception, match="integer"):
        save_limits(session, {project_id: value})
    assert configured_limit(session, project_id) == 3


def test_cleanup_queue_and_missed_launch_release(session, project_id, tmp_path):
    scheduler = QueuedScheduler()
    save_limits(session, {project_id: 1})
    prepared_task(session, project_id)
    first = submit(session, project_id, tmp_path, scheduler)
    root = tmp_path / "other"
    root.mkdir()
    submit(session, project_id, root, scheduler, task_key="unrelated")
    project = session.get(Project, project_id)
    project.cleanup_json = {"status": "incomplete"}
    session.add(project)
    session.commit()
    # APScheduler's missed callback never entered run_delivery.
    task_delivery.missed_delivery_job(
        SimpleNamespace(job_id=first["job_ref"]),
        session_factory=lambda: Session(session.get_bind()),
        scheduler=scheduler,
    )
    assert capacity(session, scheduler)["running_count"] == 0
    assert capacity(session, scheduler)["queued_count"] == 0
    assert len(scheduler.jobs) == 1  # Only the inert missed-job stub; no cleanup project launch.
    states = task_rows(session)
    assert states[(project_id, "unrelated")]["delivery"]["reason"] == "Project unavailable."


def test_saturated_backup_pool_does_not_consume_task_slots(
    delivery_engine, project_id, tmp_path, monkeypatch
):
    """Real APScheduler executors; controlled callbacks replace paid native launches."""
    backup_ready = [Event() for _ in range(3)]
    backup_release, task_release, six_tasks, all_tasks = Event(), Event(), Event(), Event()
    lock = Lock()
    starts = []

    def backup(index):
        backup_ready[index].set()
        assert backup_release.wait(10)

    def controlled_task(**kwargs):
        with lock:
            starts.append(kwargs["task_key"])
            if len(starts) >= 6:
                six_tasks.set()
        try:
            assert task_release.wait(10)
            with Session(delivery_engine) as session:
                task_delivery._update_delivery(
                    TrackerRepository(session),
                    kwargs["project_id"],
                    kwargs["task_key"],
                    status="complete",
                )
        finally:
            task_delivery.release_delivery(**kwargs)
            with lock:
                if len(starts) == 7:
                    all_tasks.set()

    monkeypatch.setattr(task_delivery, "run_delivery", controlled_task)
    with Session(delivery_engine) as session:
        save_limits(session, {project_id: 6})
        for index in range(7):
            TrackerRepository(session).create_task(
                project_id=project_id, key=f"pool-{index}", title="Pool fixture"
            )
            (tmp_path / f"pool-{index}").mkdir()

    async def scenario():
        scheduler = build_scheduler(
            Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state"), delivery_engine
        )
        scheduler.start()
        try:
            for index in range(3):
                scheduler.add_job(
                    backup, args=[index], trigger="date", jobstore="memory", executor="long"
                )
            for ready in backup_ready:
                assert await asyncio.to_thread(ready.wait, 5)

            def start(index):
                with Session(delivery_engine) as session:
                    submit(
                        session,
                        project_id,
                        tmp_path / f"pool-{index}",
                        scheduler,
                        task_key=f"pool-{index}",
                        host_key="gemini-cli",
                        delivery_agent={"name": "inert", "instructions": "Fixture"},
                        review_agent={"name": "reviewer", "instructions": "Fixture"},
                    )

            for index in range(7):
                await asyncio.to_thread(start, index)
            assert await asyncio.to_thread(six_tasks.wait, 5)
            with Session(delivery_engine) as session:
                state = capacity(session, scheduler)
                assert state["running_count"] == 6 and state["queued_count"] == 1
            # All three backup threads are still occupied while six task callbacks run.
            assert not backup_release.is_set()
            task_release.set()
            assert await asyncio.to_thread(all_tasks.wait, 5)
            assert len(starts) == 7
        finally:
            backup_release.set()
            task_release.set()
            from apscheduler.events import EVENT_SCHEDULER_SHUTDOWN

            stopped = asyncio.Event()
            scheduler.add_listener(lambda _: stopped.set(), EVENT_SCHEDULER_SHUTDOWN)
            scheduler.shutdown(wait=True)
            await asyncio.wait_for(stopped.wait(), 5)

    asyncio.run(scenario())


def test_registration_error_after_callback_start_retains_slot(
    capacity_session, project_id, tmp_path
):
    session = capacity_session
    save_limits(session, {project_id: 1})
    prepared_task(session, project_id)

    class StartedThenError(QueuedScheduler):
        failed = False

        def add_job(self, function, **options):
            if not self.failed:
                self.failed = True
                task_delivery._update_delivery(
                    TrackerRepository(session), project_id, "deliver", status="running"
                )
                raise RuntimeError("Registration failed after callback started")
            return super().add_job(function, **options)

    scheduler = StartedThenError()
    with pytest.raises(RuntimeError, match="after callback started"):
        submit(session, project_id, tmp_path, scheduler)
    first = task_rows(session)[(project_id, "deliver")]["delivery"]
    assert first["status"] == "stopped" and first["slot_reserved"] is True
    second_root = tmp_path / "second"
    second_root.mkdir()
    submit(session, project_id, second_root, scheduler, task_key="unrelated")
    assert len(scheduler.jobs) == 0
    assert capacity(session, scheduler)["running_count"] == 1
    task_delivery.release_delivery(
        session_factory=lambda: Session(session.get_bind()),
        scheduler=scheduler,
        project_id=project_id,
        task_key="deliver",
        handoff_id=first["handoff_id"],
    )
    assert len(scheduler.jobs) == 1
    assert capacity(session, scheduler)["running_count"] == 1


def test_retiring_callback_does_not_drain_during_scheduler_shutdown(
    capacity_session, project_id, tmp_path
):
    from apscheduler.schedulers.base import STATE_STOPPED

    session = capacity_session
    save_limits(session, {project_id: 1})
    prepared_task(session, project_id)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    second_root = tmp_path / "second"
    second_root.mkdir()
    submit(session, project_id, second_root, scheduler, task_key="unrelated")
    first_state = task_rows(session)[(project_id, "deliver")]["delivery"]
    assert len(scheduler.jobs) == 1
    scheduler.state = STATE_STOPPED
    scheduler.task_delivery_started = True
    task_delivery.release_delivery(
        session_factory=lambda: Session(session.get_bind()),
        scheduler=scheduler,
        project_id=project_id,
        task_key="deliver",
        handoff_id=first_state["handoff_id"],
    )
    assert len(scheduler.jobs) == 1  # No add_job while shutdown waits on this callback.
    assert capacity(session, scheduler)["running_count"] == 0
    assert capacity(session, scheduler)["queued_count"] == 2


@pytest.mark.parametrize("registration_failure", [False, True])
def test_abort_stops_owned_process_before_admitting_waiting_work(
    delivery_engine, project_id, tmp_path, monkeypatch, registration_failure
):
    ready = Event()
    native, observations = [], []
    definition = service._definition_by_key("codex")

    def execute(*args, process_control, **kwargs):
        process = process_control.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
        native.append(process)
        ready.set()
        process.wait(timeout=10)
        return AgentRunResult(process.returncode, thread_id="inert-capacity-fixture")

    monkeypatch.setattr(
        service, "_definition_by_key", lambda _: replace(definition, run_agent=execute)
    )

    class ObservedScheduler(QueuedScheduler):
        def add_job(self, function, **options):
            if options["kwargs"]["task_key"] == "waiting":
                observations.append(native[0].poll() is None)
                if registration_failure:
                    raise RuntimeError("Waiting-job fixture registration failure")
            super().add_job(function, **options)

    scheduler = ObservedScheduler()
    try:
        with Session(delivery_engine) as session:
            save_limits(session, {project_id: 1})
            plan_id = workflow_task(session, project_id)
            submit(
                session,
                project_id,
                tmp_path,
                scheduler,
                task_key=f"workflow-{plan_id}",
                run_plan_id=plan_id,
                step_id="deliver",
            )
            TrackerRepository(session).create_task(
                project_id=project_id, key="waiting", title="Wait"
            )
            waiting = tmp_path / "waiting"
            waiting.mkdir()
            submit(session, project_id, waiting, scheduler, task_key="waiting")
            with ThreadPoolExecutor() as workers:
                running = workers.submit(scheduler.run)
                try:
                    assert ready.wait(5)
                    _, cancellation = task_delivery.abort_run_plan(
                        session, run_plan_id=plan_id, project_id=project_id, scheduler=scheduler
                    )
                    assert cancellation["status"] == "managed_stopped"
                    assert native[0].poll() is not None
                finally:
                    for process in native:
                        if process.poll() is None:
                            process.kill()
                    running.result(timeout=5)
            assert observations == [False]
            if registration_failure:
                state = task_rows(session)[(project_id, "waiting")]["delivery"]
                assert state["status"] == "stopped"
                assert state["reason"] == "Waiting-job fixture registration failure"
    finally:
        for process in native:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
        service.shutdown_usage()


def test_unconfirmed_stop_retains_slot_until_callback_retirement(
    delivery_engine, project_id, tmp_path, monkeypatch
):
    ready = Event()
    native, controls = [], []
    definition = service._definition_by_key("codex")

    def execute(*args, process_control, **kwargs):
        process = process_control.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
        native.append(process)
        controls.append(process_control)
        ready.set()
        process.wait(timeout=10)
        return AgentRunResult(process.returncode, thread_id="unconfirmed-capacity-fixture")

    monkeypatch.setattr(
        service, "_definition_by_key", lambda _: replace(definition, run_agent=execute)
    )
    scheduler = QueuedScheduler()
    try:
        with Session(delivery_engine) as session:
            save_limits(session, {project_id: 1})
            plan_id = workflow_task(session, project_id)
            task_key = f"workflow-{plan_id}"
            submit(
                session,
                project_id,
                tmp_path,
                scheduler,
                task_key=task_key,
                run_plan_id=plan_id,
                step_id="deliver",
            )
            TrackerRepository(session).create_task(
                project_id=project_id, key="waiting", title="Wait"
            )
            waiting = tmp_path / "waiting"
            waiting.mkdir()
            submit(session, project_id, waiting, scheduler, task_key="waiting")
            with ThreadPoolExecutor() as workers:
                running = workers.submit(scheduler.run)
                try:
                    assert ready.wait(5)
                    monkeypatch.setattr(controls[0], "stop", lambda: "unconfirmed")
                    with pytest.raises(ConflictError) as error:
                        task_delivery.abort_run_plan(
                            session, run_plan_id=plan_id, project_id=project_id, scheduler=scheduler
                        )
                    assert error.value.data["cancellation"]["status"] == "stop_unconfirmed"
                    assert native[0].poll() is None
                    assert capacity(session, scheduler)["running_count"] == 1
                    assert task_rows(session)[(project_id, task_key)]["delivery"]["slot_reserved"]
                    assert scheduler.jobs == []
                finally:
                    for process in native:
                        if process.poll() is None:
                            process.kill()
                    running.result(timeout=5)
            assert len(scheduler.jobs) == 1  # Retirement, rather than abort, frees the slot.
    finally:
        for process in native:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
        service.shutdown_usage()


@pytest.mark.parametrize("same_runtime", [True, False])
def test_rehandoff_retains_owned_reservation_and_exact_handoff_release(
    session, project_id, tmp_path, same_runtime
):
    save_limits(session, {project_id: 1})
    prepared_task(session, project_id)
    repo = TrackerRepository(session)
    evidence = {
        "automatic_delivery": {
            "repair_count": 2,
            "sessions": {"delivery": {"thread_id": "retained"}},
            "candidate": {"commit_sha": "a" * 40},
        }
    }
    repo.update_ticket(
        project_id=project_id,
        ticket_key="first",
        patch_json={
            "completion_evidence_json": evidence,
        },
    )

    class StartedThenError(QueuedScheduler):
        failed = False

        def add_job(self, function, **options):
            if not self.failed:
                self.failed = True
                task_delivery._update_delivery(repo, project_id, "deliver", status="running")
                raise RuntimeError("Registration failed after callback started")
            super().add_job(function, **options)

    original = StartedThenError()
    with pytest.raises(RuntimeError, match="after callback started"):
        submit(session, project_id, tmp_path, original)
    prior = task_rows(session)[(project_id, "deliver")]
    scheduler = original if same_runtime else QueuedScheduler()
    if same_runtime:
        with pytest.raises(ValidationError, match="still retiring"):
            submit(session, project_id, tmp_path, scheduler)
        assert task_rows(session)[(project_id, "deliver")] == prior
        assert scheduler.jobs == []
        task_delivery.release_delivery(
            session_factory=lambda: Session(session.get_bind()),
            scheduler=scheduler,
            project_id=project_id,
            task_key="deliver",
            handoff_id=prior["delivery"]["handoff_id"],
        )
    submit(session, project_id, tmp_path, scheduler)
    replacement = task_rows(session)[(project_id, "deliver")]
    assert replacement["delivery"]["handoff_id"] != prior["delivery"]["handoff_id"]
    assert capacity(session, scheduler)["running_count"] == 1
    task_delivery.release_delivery(
        session_factory=lambda: Session(session.get_bind()),
        scheduler=original,
        project_id=project_id,
        task_key="deliver",
        handoff_id=prior["delivery"]["handoff_id"],
    )
    assert task_rows(session)[(project_id, "deliver")] == replacement
    assert (
        repo.brief(project_id=project_id, ticket_key="first").ticket.completion_evidence_json
        == evidence
    )


def test_retiring_reservation_still_owns_its_working_directory(session, project_id, tmp_path):
    save_limits(session, {project_id: 2})
    prepared_task(session, project_id)
    repo = TrackerRepository(session)

    class StartedThenError(QueuedScheduler):
        failed = False

        def add_job(self, function, **options):
            if not self.failed:
                self.failed = True
                task_delivery._update_delivery(repo, project_id, "deliver", status="running")
                raise RuntimeError("Registration failed after callback started")
            super().add_job(function, **options)

    scheduler = StartedThenError()
    with pytest.raises(RuntimeError, match="after callback started"):
        submit(session, project_id, tmp_path, scheduler)
    with pytest.raises(ValidationError, match="directory is already owned"):
        submit(session, project_id, tmp_path, scheduler, task_key="unrelated")
    assert scheduler.jobs == []
    assert capacity(session, scheduler)["running_count"] == 1


@pytest.mark.parametrize("legacy_limit", [None, 3, 9])
def test_project_capacity_migration_preserves_legacy_and_new_project_default(
    tmp_path, legacy_limit
):
    import sqlite3

    from alembic import command

    from stackos.db.connection import make_engine
    from stackos.db.migrate import alembic_config, upgrade_to_head

    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    settings.ensure_dirs()
    config = alembic_config(settings)
    command.upgrade(config, "0048_product_receipts_merge")
    with sqlite3.connect(settings.db_path) as connection:
        for identity in (2, 7):
            connection.execute(
                "INSERT INTO projects (id,slug,name,domain,locale,is_active,created_at,updated_at) "
                "VALUES (?,?,?,'fixture.test','en-US',?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                (identity, f"p{identity}", f"Project {identity}", identity == 2),
            )
        if legacy_limit is not None:
            connection.execute("INSERT INTO task_delivery_capacity VALUES (1, ?)", (legacy_limit,))
    upgrade_to_head(settings)
    expected = legacy_limit if legacy_limit is not None else 3
    with sqlite3.connect(settings.db_path) as connection:
        assert connection.execute(
            "SELECT * FROM task_delivery_capacity ORDER BY project_id"
        ).fetchall() == [(2, expected), (7, expected)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    # Simulate completed schema with a lost revision stamp: saved edits survive replay.
    engine = make_engine(settings.db_path)
    with Session(engine) as first:
        save_limits(first, {2: 5, 7: 1})
    engine.dispose()
    with pytest.raises(RuntimeError, match="without losing preferences"):
        command.downgrade(config, "0048_product_receipts_merge")
    command.stamp(config, "0041_ui804_consolidation")
    upgrade_to_head(settings)
    with Session(engine) as reloaded:
        assert (configured_limit(reloaded, 2), configured_limit(reloaded, 7)) == (5, 1)
        added = (
            ProjectRepository(reloaded).create(slug="new", name="New", domain="new.test").data.id
        )
        assert configured_limit(reloaded, added) == 3
        # Permanent project deletion cascades only its own capacity; a new ID
        # never inherits a deleted project's preference.
        reloaded.delete(reloaded.get(Project, 2))
        reloaded.commit()
        assert configured_limit(reloaded, 7) == 1
        another = (
            ProjectRepository(reloaded)
            .create(slug="replacement", name="Replacement", domain="new.test")
            .data.id
        )
        assert another != 2 and configured_limit(reloaded, another) == 3
    with sqlite3.connect(settings.db_path) as connection:
        assert connection.execute("SELECT * FROM task_delivery_capacity").fetchall() == [(7, 1)]
    engine.dispose()


def test_all_project_counts_are_consolidated_scalar_reads(
    session, project_id, tmp_path, monkeypatch
):
    from sqlalchemy import event

    from stackos.db.models import AgentRequest, TaskDeliveryCapacity
    from stackos.repositories.project_setup import ProjectSetupRepository, SetupAttempt
    from tests.integration.test_repositories.test_managed_setup import stored_setup

    other = (
        ProjectRepository(session).create(slug="other", name="Other", domain="other.test").data.id
    )
    scheduler = QueuedScheduler()
    identity = task_delivery._admission_id(scheduler)
    save_limits(session, {project_id: 1, other: 8})
    for owner, states in (
        (project_id, [("running", True), ("queued", False)]),
        (other, [("stopped", True), ("queued", False)]),
    ):
        for index, (status, reserved) in enumerate(states):
            TrackerRepository(session).create_task(
                project_id=owner,
                key=f"{owner}-{index}",
                title="Fixture",
                metadata_json={
                    "delivery": {
                        "admission_id": identity,
                        "status": status,
                        "slot_reserved": reserved,
                    }
                },
            )
        for index in range(20):
            stored_setup(
                session,
                owner,
                f"history-{owner}-{index}",
                {
                    "schema_version": 999,
                    "entry_kind": "managed_ui",
                    "status": "success",
                    "managed": {"slot_reserved": False},
                },
            )
        for status, reserved, admission in (
            ("queued", False, identity),
            ("cancelled", True, "prior-daemon"),
            ("queued", False, "prior-daemon"),
        ):
            stored_setup(
                session,
                owner,
                f"setup-{owner}-{status}-{admission}",
                {
                    "schema_version": 999,
                    "entry_kind": "managed_ui",
                    "status": status,
                    "managed": {
                        "slot_reserved": reserved,
                        "admission_id": admission,
                        "settings": {"project_dir": str(tmp_path / str(owner))},
                    },
                },
            )
    session.expire_all()
    statements, hydrated, calls = [], [], []
    managed_admissions = ProjectSetupRepository.managed_admissions

    def admissions(repo, **filters):
        calls.append(filters)
        return managed_admissions(repo, **filters)

    def queried(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    def loaded(_session, instance):
        if isinstance(instance, (AgentRequest, TrackerTask)):
            hydrated.append(instance)

    def full_payload_forbidden(*_args, **_kwargs):
        pytest.fail("Capacity must not validate full setup history")

    monkeypatch.setattr(ProjectSetupRepository, "managed_admissions", admissions)
    monkeypatch.setattr(SetupAttempt, "model_validate", full_payload_forbidden)
    event.listen(session.get_bind(), "before_cursor_execute", queried)
    event.listen(session, "loaded_as_persistent", loaded)
    try:
        all_rows = task_delivery.delivery_capacity(session, scheduler)["projects"]
        assert len(statements) == 3 and calls == [{"project_id": None}]
        assert hydrated == []
        for statement in statements:
            assert statement.lstrip().startswith("SELECT")
        assert all((row["running_count"], row["queued_count"]) == (2, 2) for row in all_rows)
        assert [row["configured_limit"] for row in all_rows] == [1, 8]
        for row in all_rows:
            filtered = task_delivery.delivery_capacity(session, scheduler, row["project_id"])
            assert filtered["projects"] == [row]
        assert len(statements) == 9  # Three queries per read, independent of project count.
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", queried)
        event.remove(session, "loaded_as_persistent", loaded)
    assert len(session.exec(select(TaskDeliveryCapacity)).all()) == 2
