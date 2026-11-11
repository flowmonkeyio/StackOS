from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from sqlmodel import Session, create_engine, select

from stackos.context.repository import ContextRepository
from stackos.db.models import IdempotencyKey, ProjectEvent, TrackerTask
from stackos.repositories.base import ConflictError, NotFoundError, ValidationError
from stackos.repositories.tracker import TrackerRepository
from tests.helpers.sqlite_schema import capture_database_snapshot, restore_database_snapshot


def _seed(session, project_id):
    repo = TrackerRepository(session)
    for key in ("old", "hidden", "new", "last", "other-column"):
        repo.create_task(project_id=project_id, key=key, title=key)
    repo.create_ticket(
        project_id=project_id,
        task_key="new",
        key="keep-ticket-priority",
        title="Keep ticket",
        priority_key="p1",
    )
    rows = session.exec(select(TrackerTask)).all()
    for index, row in enumerate(rows):
        row.order_index = 7 if index < 3 else 20
        row.created_at = datetime(2026, 1, 1) + timedelta(days=index)
        if row.key == "other-column":
            row.status = "in-progress"
        session.add(row)
    session.commit()
    return repo, {task.key: task for task in repo.get(project_id=project_id).tasks}


def _save(repo, project_id, task, intent="board-1", **change):
    return repo.update_task_board(
        project_id=project_id,
        task_key=task.key,
        expected_status=task.status,
        expected_updated_at=task.updated_at,
        idempotency_key=intent,
        **change,
    ).data


def _order(session, status="not-started"):
    return [
        row.key
        for row in session.exec(
            select(TrackerTask)
            .where(TrackerTask.status == status)
            .order_by(TrackerTask.order_index, TrackerTask.created_at.desc(), TrackerTask.id.desc())
        ).all()
    ]


def test_filtered_move_preserves_hidden_order_and_uses_newest_ties(session, project_id):
    repo, tasks = _seed(session, project_id)
    tickets = repo.get(project_id=project_id).tickets
    before = {key: task.model_dump() for key, task in tasks.items()}
    assert _order(session) == ["new", "hidden", "old", "last"]
    result = _save(repo, project_id, tasks["last"], anchor_task_key="new", placement="before")
    assert _order(session) == ["last", "new", "hidden", "old"]
    assert result.changed is True
    assert result.task.order_index == 0
    for task in repo.get(project_id=project_id).tasks:
        expected = before[task.key]
        for field in ("order_index", "updated_at"):
            expected.pop(field)
        assert task.model_dump(exclude={"order_index", "updated_at"}) == expected
    assert tasks["other-column"].order_index == 20
    assert repo.get(project_id=project_id).tickets == tickets
    events = ContextRepository(session).tracker_status_timeline(project_id=project_id).items
    assert len(events) == 4
    assert {event.event_type for event in events} == {"tracker.task.changed"}
    assert {event.metadata_json["task_key"] for event in events} == {"old", "hidden", "new", "last"}
    assert ContextRepository(session).latest_tracker_status_event_id(
        project_id=project_id, task_key="hidden"
    ) == next(event.id for event in events if event.metadata_json["task_key"] == "hidden")


def test_priority_is_narrow_retry_bound_and_noops_do_not_emit(session, project_id):
    repo, tasks = _seed(session, project_id)
    tickets = repo.get(project_id=project_id).tickets
    task = tasks["new"]
    before = task.model_dump(exclude={"priority_key", "updated_at"})
    result = _save(repo, project_id, task, priority_key="p0")
    assert result.task.priority_key == "p0"
    assert result.task.model_dump(exclude={"priority_key", "updated_at"}) == before
    replay = _save(repo, project_id, task, priority_key="p0")
    assert replay.replayed and replay.rev == result.rev
    assert len(session.exec(select(ProjectEvent)).all()) == 1
    noop = _save(repo, project_id, result.task, "noop", priority_key="p0")
    assert not noop.changed and noop.rev == result.rev
    assert len(session.exec(select(ProjectEvent)).all()) == 1
    with pytest.raises(ConflictError):
        _save(repo, project_id, task, priority_key="p1")
    with pytest.raises(ConflictError):
        _save(repo, project_id, task, "stale", priority_key="p1")
    with pytest.raises(ValidationError):
        _save(repo, project_id, result.task, "bad", priority_key="urgent")
    assert len(session.exec(select(IdempotencyKey)).all()) == 2
    assert repo.get(project_id=project_id).tickets == tickets


def test_reorder_validates_status_anchor_and_noop_before_normalizing(session, project_id):
    repo, tasks = _seed(session, project_id)
    result = _save(repo, project_id, tasks["new"], anchor_task_key="hidden", placement="before")
    assert not result.changed
    assert _order(session) == ["new", "hidden", "old", "last"]
    assert session.exec(select(ProjectEvent)).all() == []
    for anchor in ("other-column", "missing"):
        with pytest.raises((ConflictError, NotFoundError)):
            _save(
                repo, project_id, tasks["last"], anchor, anchor_task_key=anchor, placement="after"
            )
    changed = repo.update_task(
        project_id=project_id, task_key="last", patch_json={"status": "in-progress"}
    ).data.task
    with pytest.raises(ConflictError):
        _save(
            repo,
            project_id,
            tasks["last"],
            "stale-status",
            anchor_task_key="new",
            placement="after",
        )
    assert repo.get(project_id=project_id, task_key="last").tasks[0] == changed
    with pytest.raises(NotFoundError):
        _save(repo, project_id + 100, tasks["last"], "wrong-project", priority_key="p0")


def test_failed_board_save_rolls_back_ranks_events_revision_and_receipt(
    session, project_id, monkeypatch
):
    repo, tasks = _seed(session, project_id)
    before = repo.get(project_id=project_id)

    def fail(*args, **kwargs):
        raise RuntimeError("failed revision")

    monkeypatch.setattr(repo, "_record_revision", fail)
    with pytest.raises(RuntimeError, match="failed revision"):
        _save(repo, project_id, tasks["last"], anchor_task_key="new", placement="before")
    assert repo.get(project_id=project_id) == before
    assert session.exec(select(ProjectEvent)).all() == []
    assert session.exec(select(IdempotencyKey)).all() == []


def test_equal_creation_ties_use_id_and_invalidate_moved_task_when_rank_stays_same(
    session, project_id
):
    repo, _ = _seed(session, project_id)
    for row in session.exec(select(TrackerTask).where(TrackerTask.status == "not-started")).all():
        row.created_at = datetime(2026, 1, 1)
        row.order_index = 0
        session.add(row)
    session.commit()
    tasks = {task.key: task for task in repo.get(project_id=project_id).tasks}
    assert _order(session) == ["last", "new", "hidden", "old"]
    result = _save(repo, project_id, tasks["old"], anchor_task_key="last", placement="before")
    assert _order(session) == ["old", "last", "new", "hidden"]
    assert result.task.updated_at > tasks["old"].updated_at
    assert result.task.order_index == tasks["old"].order_index == 0
    assert "old" in result.affected_task_keys
    assert ContextRepository(session).tracker_status_timeline(
        project_id=project_id, task_key="old"
    ).items[0].metadata_json["changed_fields"] == ["order"]


def test_existing_agent_priority_writer_emits_genuine_task_change(session, project_id):
    repo, tasks = _seed(session, project_id)
    repo.update_task(
        project_id=project_id, task_key=tasks["new"].key, patch_json={"priority_key": "p3"}
    )
    assert [event.event_type for event in session.exec(select(ProjectEvent)).all()] == [
        "tracker.task.changed"
    ]


def test_concurrent_reorders_serialize_and_stale_writer_cannot_overwrite(
    session, project_id, tmp_path
):
    _, tasks = _seed(session, project_id)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'board.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    restore_database_snapshot(engine, capture_database_snapshot(session.get_bind()))
    barrier = Barrier(2)

    def submit(anchor):
        with Session(engine) as writer:
            barrier.wait(timeout=5)
            try:
                return _save(
                    TrackerRepository(writer),
                    project_id,
                    tasks["last"],
                    anchor,
                    anchor_task_key=anchor,
                    placement="before",
                )
            except ConflictError:
                return None

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, ["new", "old"]))
        assert sum(result is not None for result in results) == 1
        with Session(engine) as reader:
            assert len(reader.exec(select(IdempotencyKey)).all()) == 1
            assert _order(reader) in (
                ["last", "new", "hidden", "old"],
                ["new", "hidden", "last", "old"],
            )
    finally:
        engine.dispose()
