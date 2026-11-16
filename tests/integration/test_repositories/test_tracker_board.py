from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import event
from sqlmodel import Session, create_engine, select

from stackos.context.repository import ContextRepository
from stackos.db.models import IdempotencyKey, ProjectEvent, TrackerRevision, TrackerTask
from stackos.repositories.base import ConflictError, NotFoundError, ValidationError
from stackos.repositories.projects import ProjectRepository
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
    assert len(events) == 1
    assert events[0].event_type == "tracker.tasks.changed"
    assert set(events[0].metadata_json["affected_task_keys"]) == {"old", "hidden", "new", "last"}
    assert (
        ContextRepository(session).latest_tracker_status_event_id(
            project_id=project_id, task_key="hidden"
        )
        == events[0].id
    )


def test_large_reorder_has_one_commit_revision_event_and_exact_scoped_membership(
    session, project_id
):
    repo = TrackerRepository(session)
    for index in range(62):
        repo.create_task(project_id=project_id, key=f"row-{index}", title=str(index))
    repo.create_task(project_id=project_id, key="unrelated", title="Unrelated")
    for row in session.exec(select(TrackerTask)).all():
        if row.key == "unrelated":
            row.status = "in-progress"
        else:
            row.order_index = int(row.key.removeprefix("row-"))
        session.add(row)
    session.commit()
    foreign_id = (
        ProjectRepository(session)
        .create(slug="foreign", name="Foreign", domain="example.org", locale="en-US")
        .data.id
    )
    repo.create_task(project_id=foreign_id, key="row-0", title="Same key, foreign project")
    before = repo.get(project_id=project_id)
    tasks = {task.key: task for task in before.tasks}
    commits = []

    def committed(_session):
        commits.append(True)

    event.listen(session, "after_commit", committed)
    try:
        result = _save(
            repo, project_id, tasks["row-61"], anchor_task_key="row-0", placement="before"
        )
    finally:
        event.remove(session, "after_commit", committed)
    assert commits == [True]
    assert result.rev == before.tracker.rev + 1
    expected = ["row-61", *[f"row-{i}" for i in range(61)]]
    # Filter the foreign task from the independently expected local column.
    local = session.exec(
        select(TrackerTask)
        .where(TrackerTask.project_id == project_id, TrackerTask.status == "not-started")
        .order_by(TrackerTask.order_index)
    ).all()
    assert [(row.key, row.order_index) for row in local] == list(
        zip(expected, range(62), strict=True)
    )
    revisions = session.exec(
        select(TrackerRevision).where(
            TrackerRevision.project_id == project_id, TrackerRevision.rev > before.tracker.rev
        )
    ).all()
    assert len(revisions) == 1
    assert revisions[0].patch_json["anchor_task_key"] == "row-0"
    context = ContextRepository(session)
    page = context.tracker_status_timeline(project_id=project_id, limit=50)
    assert len(page.items) == page.total_estimate == 1
    assert page.next_cursor is None
    batch = page.items[0]
    assert batch.event_type == "tracker.tasks.changed"
    assert batch.metadata_json["rev"] == result.rev
    assert batch.metadata_json["affected_task_keys"] == expected
    assert batch.metadata_json["changed_fields"] == ["order_index"]
    assert len(batch.metadata_json["affected_tasks"]) == 62
    for key in ("row-0", "row-61", "row-30"):
        scoped = context.tracker_status_timeline(project_id=project_id, task_key=key, limit=1)
        assert [item.id for item in scoped.items] == [batch.id]
        assert scoped.next_cursor is None and scoped.total_estimate == 1
        assert (
            context.latest_tracker_status_event_id(project_id=project_id, task_key=key) == batch.id
        )
        activity = repo.task_activity(project_id=project_id, task_key=key, limit=1)
        assert [item.source_id for item in activity.items] == [batch.id]
        assert activity.next_cursor is None
    for scope_project, key in ((project_id, "unrelated"), (foreign_id, "row-0")):
        assert context.tracker_status_timeline(project_id=scope_project, task_key=key).items == []
        assert (
            context.latest_tracker_status_event_id(project_id=scope_project, task_key=key) is None
        )
        assert repo.task_activity(project_id=scope_project, task_key=key).items == []
    assert context.tracker_status_timeline(project_id=project_id, after_id=batch.id).items == []
    replay = _save(repo, project_id, tasks["row-61"], anchor_task_key="row-0", placement="before")
    assert replay.replayed and replay.rev == result.rev
    with pytest.raises(ConflictError):
        _save(
            repo,
            project_id,
            tasks["row-0"],
            "stale-large",
            anchor_task_key="row-30",
            placement="after",
        )
    assert len(context.tracker_status_timeline(project_id=project_id).items) == 1


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


@pytest.mark.parametrize("failure_point", ["_record_revision", "commit"])
def test_failed_board_save_rolls_back_ranks_events_revision_and_receipt(
    session, project_id, monkeypatch, failure_point
):
    repo, tasks = _seed(session, project_id)
    before = repo.get(project_id=project_id)

    def fail(*args, **kwargs):
        raise RuntimeError("failed revision")

    monkeypatch.setattr(
        repo if failure_point == "_record_revision" else session, failure_point, fail
    )
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
    metadata = (
        ContextRepository(session)
        .tracker_status_timeline(project_id=project_id, task_key="old")
        .items[0]
        .metadata_json
    )
    assert next(member for member in metadata["affected_tasks"] if member["task_key"] == "old")[
        "changed_fields"
    ] == ["order"]


def test_existing_agent_priority_writer_emits_genuine_task_change(session, project_id):
    repo, tasks = _seed(session, project_id)
    repo.update_task(
        project_id=project_id, task_key=tasks["new"].key, patch_json={"priority_key": "p3"}
    )
    assert [event.event_type for event in session.exec(select(ProjectEvent)).all()] == [
        "tracker.task.changed"
    ]


@pytest.mark.parametrize(
    "members",
    [
        [{"nested": {"task_key": "new", "task_id": 3}}],
        ["new", None, 3, ["new"]],
        {"task_key": "new", "task_id": 3},
        [{"task_key": "new", "task_id": "3"}],
    ],
)
def test_batch_scope_ignores_unstructured_members_even_with_top_level_lookalike(
    session, project_id, members
):
    repo, _ = _seed(session, project_id)
    session.add(
        ProjectEvent(
            project_id=project_id,
            source_type="tracker",
            event_type="tracker.tasks.changed",
            metadata_json={
                "task_key": "new",
                "affected_task_keys": ["new"],
                "affected_tasks": members,
            },
        )
    )
    session.commit()
    context = ContextRepository(session)
    assert context.tracker_status_timeline(project_id=project_id, task_key="new").items == []
    assert context.latest_tracker_status_event_id(project_id=project_id, task_key="new") is None
    assert repo.task_activity(project_id=project_id, task_key="new").items == []


def test_duplicate_batch_members_do_not_duplicate_history_and_later_events_remain_visible(
    session, project_id
):
    repo, tasks = _seed(session, project_id)
    moved = _save(repo, project_id, tasks["last"], anchor_task_key="new", placement="before")
    batch = session.exec(select(ProjectEvent)).one()
    metadata = dict(batch.metadata_json)
    metadata["affected_tasks"] = metadata["affected_tasks"] * 2
    batch.metadata_json = metadata
    session.add(batch)
    session.commit()
    batch_id = batch.id
    # A retained single-task producer must still follow the batch in the same scope.
    repo.update_task(project_id=project_id, task_key="new", patch_json={"priority_key": "p3"})
    repo.update_task(project_id=project_id, task_key="new", patch_json={"status": "in-progress"})
    context = ContextRepository(session)
    page = context.tracker_status_timeline(project_id=project_id, task_key="new", limit=1)
    assert [item.id for item in page.items] == [batch_id]
    assert page.total_estimate == 3
    later = context.tracker_status_timeline(
        project_id=project_id, task_key="new", after_id=page.next_cursor
    )
    assert [item.event_type for item in later.items] == [
        "tracker.task.changed",
        "tracker.task.status_changed",
    ]
    assert (
        context.latest_tracker_status_event_id(project_id=project_id, task_key="new")
        == later.items[-1].id
    )
    activity = repo.task_activity(project_id=project_id, task_key="new", limit=1)
    items = list(activity.items)
    while activity.next_cursor:
        activity = repo.task_activity(
            project_id=project_id, task_key="new", limit=1, cursor=activity.next_cursor
        )
        items.extend(activity.items)
    assert [item.source_id for item in items] == [later.items[1].id, later.items[0].id, batch_id]
    # Replaying the old reorder does not obscure or replace the later independent events.
    replay = _save(repo, project_id, tasks["last"], anchor_task_key="new", placement="before")
    assert replay.replayed and replay.rev == moved.rev


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
