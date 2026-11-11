"""Real admission and persistence with inert native calls and isolated databases."""

# Imported pytest fixtures are intentionally shadowed by fixture arguments.
# ruff: noqa: F811

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier, Event, Lock
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from stackos import task_delivery
from stackos.config import Settings
from stackos.db.models import Project, TrackerTask
from stackos.jobs.scheduler import build_scheduler
from stackos.mcp.context import MCPContext
from stackos.operations.tracker.capacity import SetDeliveryCapacityInput, set_capacity
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.tracker import TrackerRepository
from stackos.task_capacity import configured_limit, save_limit
from tests.integration.test_repositories.test_task_delivery import (
    QueuedScheduler,
    delivery_engine,  # noqa: F401
    git_project,  # noqa: F401
    native_source_fixtures,  # noqa: F401
    prepared_task,
    submit,
)


def capacity(session, scheduler):
    session.expire_all()
    return task_delivery.delivery_capacity(session, scheduler)


@pytest.fixture
def capacity_session(delivery_engine):
    with Session(delivery_engine) as session:
        yield session


def save(session, scheduler, value):
    return asyncio.run(
        set_capacity(
            SetDeliveryCapacityInput(concurrent_tasks=value),
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
    ).data.model_dump()


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
        project = project_id if index % 2 == 0 else other
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
        "scope": "local-user-daemon",
        "configured_limit": 3,
        "running_count": 3,
        "queued_count": 4,
        "supported_min": 1,
        "supported_max": 32,
    }
    assert len(scheduler.jobs) == 3
    assert save(session, scheduler, 5)["running_count"] == 5
    assert len(scheduler.jobs) == 5
    assert save(session, scheduler, 1)["running_count"] == 5
    assert len(scheduler.jobs) == 5
    # Actual run_delivery finally releases slots; empty-work fixtures never call a host.
    for _ in range(4):
        scheduler.run()
    assert capacity(session, scheduler)["running_count"] == 1
    assert len(scheduler.jobs) == 1
    scheduler.run()
    assert capacity(session, scheduler)["running_count"] == 1
    assert len(scheduler.jobs) == 1  # Next waiting task released through existing queue.
    assert save(session, scheduler, 4)["running_count"] == 2
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
        save_limit(session, 4)
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
        assert capacity(session, scheduler)["queued_count"] == 6
    assert len(scheduler.jobs) == 4


def test_limit_reload_separate_database_and_failed_commit(delivery_engine, session, monkeypatch):
    from sqlalchemy.exc import OperationalError

    with Session(delivery_engine) as first:
        assert configured_limit(first) == 3
        save_limit(first, 9)
    delivery_engine.dispose()
    with Session(delivery_engine) as reloaded:
        assert configured_limit(reloaded) == 9
        original_commit = reloaded.commit

        def fail():
            raise OperationalError("fixture", {}, Exception("disk unavailable"))

        monkeypatch.setattr(reloaded, "commit", fail)
        with pytest.raises(OperationalError):
            save_limit(reloaded, 12)
        from stackos.repositories.base import ValidationError

        with pytest.raises(ValidationError, match="previous limit remains effective"):
            save(reloaded, QueuedScheduler(), 12)
        monkeypatch.setattr(reloaded, "commit", original_commit)
        assert configured_limit(reloaded) == 9
    assert configured_limit(session) == 3


@pytest.mark.parametrize("value", [0, 33, -1, True, 2.5, "6", None])
def test_invalid_capacity_never_mutates(session, value):
    with pytest.raises(ValueError):
        SetDeliveryCapacityInput(concurrent_tasks=value)
    with pytest.raises(Exception, match="integer"):
        save_limit(session, value)
    assert configured_limit(session) == 3


def test_cleanup_queue_and_missed_launch_release(session, project_id, tmp_path):
    scheduler = QueuedScheduler()
    save_limit(session, 1)
    prepared_task(session, project_id)
    first = submit(session, project_id, tmp_path, scheduler)
    other = (
        ProjectRepository(session).create(slug="other", name="Other", domain="other.test").data.id
    )
    prepared_task(session, other)
    root = tmp_path / "other"
    root.mkdir()
    submit(session, other, root, scheduler)
    project = session.get(Project, other)
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
    assert states[(other, "deliver")]["delivery"]["reason"] == "Project unavailable."


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
        save_limit(session, 6)
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
    save_limit(session, 1)
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
    save_limit(session, 1)
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
