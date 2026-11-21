"""Project queue boundaries through real handoffs and scheduler callbacks."""

# Imported pytest fixtures are intentionally shadowed by fixture arguments.
# ruff: noqa: F811

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier, Event, Lock

import pytest
from apscheduler.events import EVENT_JOB_ERROR, EVENT_SCHEDULER_SHUTDOWN
from sqlmodel import Session

from stackos import task_delivery
from stackos.config import Settings
from stackos.jobs.scheduler import build_scheduler
from stackos.repositories.base import ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.tracker import TrackerRepository
from stackos.task_capacity import save_limits
from tests.integration.test_repositories.test_task_capacity import save
from tests.integration.test_repositories.test_task_delivery import (
    QueuedScheduler,
    delivery_engine,  # noqa: F401
    git_project,  # noqa: F401
    native_source_fixtures,  # noqa: F401
    prepared_task,
    submit,
    workflow_task,
)


@pytest.mark.parametrize(
    ("status", "lane"),
    [
        ("not-started", "backlog"),
        ("in-progress", "implementation"),
        ("deferred", "done"),
        ("complete", "done"),
        ("aborted", "done"),
        ("failed", "done"),
        ("skipped", "done"),
    ],
)
def test_fresh_handoff_rejects_ineligible_placement_before_metadata(
    session, project_id, tmp_path, status, lane
):
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="held", title="Held", status=status, lane_key=lane)
    before = repo.get(project_id=project_id, task_key="held").tasks[0]
    scheduler = QueuedScheduler()
    with pytest.raises(ValidationError, match=r"Todo|open"):
        submit(session, project_id, tmp_path, scheduler, task_key="held")
    after = repo.get(project_id=project_id, task_key="held").tasks[0]
    assert after.model_dump() == before.model_dump()
    assert scheduler.jobs == []


def test_placement_alone_cannot_launch_and_explicit_resume_retains_invocations(
    session, project_id, tmp_path
):
    prepared_task(session, project_id)
    repo = TrackerRepository(session)
    row = repo.get(project_id=project_id, task_key="deliver").tasks[0]
    for column in ("backlog", "todo"):
        row = repo.update_task_board(
            project_id=project_id,
            task_key=row.key,
            expected_status=row.status,
            expected_updated_at=row.updated_at,
            idempotency_key=f"place-{column}",
            board_column=column,
        ).data.task
    scheduler = QueuedScheduler()

    def factory():
        return Session(session.get_bind())

    task_delivery.drain_delivery(session_factory=factory, scheduler=scheduler)
    assert scheduler.jobs == [] and "delivery" not in (row.metadata_json or {})
    with pytest.raises(ValidationError):
        submit(session, project_id, tmp_path, scheduler, delivery_agent=None, review_agent=None)
    assert scheduler.jobs == []
    first = submit(session, project_id, tmp_path, scheduler)
    history = {
        "automatic_delivery": {
            "repair_count": 2,
            "attempt": 3,
            "sessions": {"delivery": {"thread_id": "retained-native-session"}},
            "delivery": {
                "execution": {
                    "agent": "project-builder",
                    "host_key": "codex",
                    "model": "fixture",
                    "started_at": "2026-11-12T00:00:00+00:00",
                    "ticket_key": "first",
                    "invocation_id": "recorded-prior-call",
                }
            },
        }
    }
    history["automatic_delivery"]["delivery"]["launch"] = deepcopy(
        history["automatic_delivery"]["delivery"]["execution"]
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="first",
        patch_json={"completion_evidence_json": history},
    )
    task_delivery._update_delivery(repo, project_id, "deliver", status="stopped")
    scheduler.run()  # Retire the old fenced callback, without launching any native work.
    task_delivery.drain_delivery(session_factory=factory, scheduler=scheduler)
    assert scheduler.jobs == []
    assert repo.get(project_id=project_id, task_key="deliver").tasks[0].board_column == "stopped"
    second = submit(session, project_id, tmp_path, scheduler)
    assert second["job_ref"] != first["job_ref"]
    assert (
        repo.get(project_id=project_id, task_key="deliver").tasks[0].board_column == "in-progress"
    )
    assert (
        repo.brief(project_id=project_id, ticket_key="first").ticket.completion_evidence_json
        == history
    )


def test_workflow_first_handoff_keeps_execution_projection(session, project_id, tmp_path):
    plan = workflow_task(session, project_id)
    repo = TrackerRepository(session)
    key = f"workflow-{plan}"
    assert repo.get(project_id=project_id, task_key=key).tasks[0].board_column == "in-progress"
    scheduler = QueuedScheduler()
    submit(
        session, project_id, tmp_path, scheduler, task_key=key, run_plan_id=plan, step_id="deliver"
    )
    # A running step is preparation authority, not an actual native invocation.
    row = repo.get(project_id=project_id, task_key=key).tasks[0]
    assert row.board_column == "todo" and row.status == "in-progress"
    assert len(scheduler.jobs) == 1


def test_placement_is_rechecked_after_preflight(session, project_id, tmp_path, monkeypatch):
    prepared_task(session, project_id)
    resolve = task_delivery.resolve_delivery_settings

    def move_before_queue(*args, **kwargs):
        selected = resolve(*args, **kwargs)
        TrackerRepository(session).update_task(
            project_id=project_id, task_key="deliver", patch_json={"lane_key": "backlog"}
        )
        return selected

    monkeypatch.setattr(task_delivery, "resolve_delivery_settings", move_before_queue)
    scheduler = QueuedScheduler()
    with pytest.raises(ValidationError, match="Todo"):
        submit(session, project_id, tmp_path, scheduler)
    row = TrackerRepository(session).get(project_id=project_id, task_key="deliver").tasks[0]
    assert row.board_column == "backlog" and "delivery" not in row.metadata_json
    assert scheduler.jobs == []


def test_concurrent_drains_skip_saturated_project_and_reserve_in_task_id_order(
    delivery_engine, project_id, tmp_path, monkeypatch
):
    scheduler = QueuedScheduler()
    original = task_delivery.drain_delivery
    with monkeypatch.context() as queued:
        queued.setattr(task_delivery, "drain_delivery", lambda **kwargs: None)
        with Session(delivery_engine) as session:
            other = (
                ProjectRepository(session)
                .create(slug="other", name="Other", domain="other.test")
                .data.id
            )
            save_limits(session, {project_id: 2, other: 2})
            for project, keys in [(project_id, range(5)), (other, range(5, 7))]:
                for index in keys:
                    key = f"task-{index}"
                    TrackerRepository(session).create_task(project_id=project, key=key, title=key)
                    root = tmp_path / key
                    root.mkdir()
                    submit(session, project, root, scheduler, task_key=key, host_key="antigravity")
    ready = Barrier(4)

    def drain(_):
        ready.wait(5)
        original(session_factory=lambda: Session(delivery_engine), scheduler=scheduler)

    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(drain, range(4)))
    keys = [options["kwargs"]["task_key"] for _, options in scheduler.jobs]
    assert sorted(keys) == ["task-0", "task-1", "task-5", "task-6"]
    assert len({options["id"] for _, options in scheduler.jobs}) == 4
    with Session(delivery_engine) as session:
        rows = task_delivery.delivery_capacity(session, scheduler)["projects"]
        assert [(r["running_count"], r["queued_count"]) for r in rows] == [(2, 3), (2, 0)]


def test_independent_projects_execute_beyond_old_pool_and_lower_without_preemption(
    delivery_engine, project_id, tmp_path, monkeypatch
):
    """Use actual APScheduler pools and persisted limits, with inert held callbacks."""
    first_keys = [f"first-{index}" for index in range(18)]
    other_keys = [f"other-{index}" for index in range(16)]
    release = {key: Event() for key in first_keys + other_keys}
    started = {key: Event() for key in release}
    retired = {key: Event() for key in release}
    errors, starts = [], []
    lock = Lock()

    def controlled(**kwargs):
        project, key = kwargs["project_id"], kwargs["task_key"]
        try:
            with Session(delivery_engine) as session:
                task_delivery._update_delivery(
                    TrackerRepository(session), project, key, status="running"
                )
            with lock:
                starts.append(key)
            started[key].set()
            assert release[key].wait(30), key
            with Session(delivery_engine) as session:
                task_delivery._update_delivery(
                    TrackerRepository(session), project, key, status="complete"
                )
        finally:
            task_delivery.release_delivery(**kwargs)
            retired[key].set()

    monkeypatch.setattr(task_delivery, "run_delivery", controlled)
    with Session(delivery_engine) as session:
        other = (
            ProjectRepository(session)
            .create(slug="other", name="Other", domain="other.test")
            .data.id
        )
        save_limits(session, {project_id: 17, other: 16})
        for project, keys in [(project_id, first_keys), (other, other_keys)]:
            for key in keys:
                TrackerRepository(session).create_task(project_id=project, key=key, title=key)
                (tmp_path / key).mkdir()

    async def scenario():
        scheduler = build_scheduler(Settings(), delivery_engine)
        scheduler.add_listener(lambda event: errors.append(event.exception), EVENT_JOB_ERROR)
        scheduler.start()

        def start(project, key):
            with Session(delivery_engine) as session:
                submit(
                    session,
                    project,
                    tmp_path / key,
                    scheduler,
                    task_key=key,
                    host_key="antigravity",
                    delivery_agent={"name": "inert", "instructions": "Fixture"},
                    review_agent={"name": "reviewer", "instructions": "Fixture"},
                )

        def counts():
            with Session(delivery_engine) as session:
                return {
                    row["project_id"]: (row["running_count"], row["queued_count"])
                    for row in task_delivery.delivery_capacity(session, scheduler)["projects"]
                }

        try:
            for project, keys in [(project_id, first_keys), (other, other_keys)]:
                for key in keys:
                    await asyncio.to_thread(start, project, key)
            for key in first_keys[:17] + other_keys:
                assert await asyncio.to_thread(started[key].wait, 5), key
            assert len(starts) == 33
            assert not started[first_keys[-1]].is_set()
            assert counts() == {project_id: (17, 1), other: (16, 0)}

            def lower():
                with Session(delivery_engine) as session:
                    return save(session, scheduler, project_id, 1)

            with Session(delivery_engine) as session:
                repo = TrackerRepository(session)
                before = {
                    key: deepcopy(
                        repo.get(project_id=project_id, task_key=key).tasks[0].metadata_json
                    )
                    for key in first_keys
                }
                executors = dict(scheduler._executors)
                assert (await asyncio.to_thread(lower))["running_count"] == 17
                assert scheduler._executors == executors
                for key in first_keys:
                    assert (
                        repo.get(project_id=project_id, task_key=key).tasks[0].metadata_json
                        == before[key]
                    )
            assert counts() == {project_id: (17, 1), other: (16, 0)}
            for key in first_keys[:16]:
                release[key].set()
            for key in first_keys[:16]:
                assert await asyncio.to_thread(retired[key].wait, 5)
            assert counts() == {project_id: (1, 1), other: (16, 0)}
            assert not started[first_keys[-1]].is_set()
            release[first_keys[16]].set()
            assert await asyncio.to_thread(started[first_keys[-1]].wait, 5)
            assert counts() == {project_id: (1, 0), other: (16, 0)}
        finally:
            for event in release.values():
                event.set()
            stopped = asyncio.Event()
            scheduler.add_listener(lambda _: stopped.set(), EVENT_SCHEDULER_SHUTDOWN)
            scheduler.shutdown(wait=True)
            await asyncio.wait_for(stopped.wait(), 5)
        assert errors == []

    asyncio.run(scenario())
