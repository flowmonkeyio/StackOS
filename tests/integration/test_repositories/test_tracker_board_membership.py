from __future__ import annotations

import pytest
from sqlmodel import Session, create_engine, select

from stackos.db.models import TaskTrackerLane, TrackerTask
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.tracker import TrackerRepository
from stackos.repositories.tracker.schema import TrackerTaskOut
from tests.helpers.sqlite_schema import capture_database_snapshot, restore_database_snapshot


def delivery(status):
    agent = {"name": "worker", "instructions": "Frozen contract"}
    return {
        "delivery": {
            "status": status,
            "job_ref": "task-delivery:fixture",
            "delivery_agent": agent,
            "review_agent": agent,
        }
    }


@pytest.mark.parametrize(
    ("status", "lane", "state", "column"),
    [
        ("not-started", "backlog", None, "backlog"),
        ("not-started", "planning", None, "todo"),
        ("not-started", "implementation", "queued", "todo"),
        ("not-started", "backlog", "running", "in-progress"),
        ("in-progress", "implementation", None, "in-progress"),
        ("in-progress", "implementation", "queued", "todo"),
        ("in-progress", "implementation", "running", "in-progress"),
        ("in-progress", "implementation", "stopped", "stopped"),
        ("complete", "done", "stopped", "done"),
        ("aborted", "done", "stopped", "aborted"),
        ("failed", "done", "stopped", "failed"),
        ("skipped", "done", "stopped", "skipped"),
        ("deferred", "done", "stopped", "backlog"),
    ],
)
def test_membership_is_a_projection_not_a_lifecycle(
    session, project_id, status, lane, state, column
):
    repo = TrackerRepository(session)
    task = repo.create_task(
        project_id=project_id,
        key="task",
        title="Task",
        status=status,
        lane_key=lane,
        metadata_json=delivery(state) if state else None,
    ).data.task
    assert task.board_column == column
    session.expire_all()
    reread = repo.get(project_id=project_id).tasks[0]
    assert reread.board_column == column
    assert reread.status == status


def test_placement_is_lane_only_idempotent_and_reorders_projected_cohort(session, project_id):
    repo = TrackerRepository(session)
    first = repo.create_task(project_id=project_id, key="first", title="First").data.task
    second = repo.create_task(project_id=project_id, key="second", title="Second").data.task
    backlog = repo.update_task_board(
        project_id=project_id,
        task_key=first.key,
        expected_status=first.status,
        expected_updated_at=first.updated_at,
        idempotency_key="backlog",
        board_column="backlog",
    ).data.task
    assert backlog.lane_key == backlog.board_column == "backlog"
    assert backlog.status == "not-started" and backlog.metadata_json is None
    with pytest.raises(ConflictError, match="anchor changed"):
        repo.update_task_board(
            project_id=project_id,
            task_key=backlog.key,
            expected_status=backlog.status,
            expected_updated_at=backlog.updated_at,
            idempotency_key="wrong-cohort",
            anchor_task_key=second.key,
            placement="before",
        )
    assert {task.key: task.board_column for task in repo.get(project_id=project_id).tasks} == {
        "first": "backlog",
        "second": "todo",
    }


def test_placement_retry_stale_and_resume_boundaries(session, project_id):
    repo = TrackerRepository(session)
    task = repo.create_task(
        project_id=project_id, key="legacy", title="Legacy", lane_key="planning"
    ).data.task
    intent = dict(
        project_id=project_id,
        task_key=task.key,
        expected_status=task.status,
        expected_updated_at=task.updated_at,
        idempotency_key="place",
        board_column="backlog",
    )
    saved = repo.update_task_board(**intent).data
    replay = repo.update_task_board(**intent).data
    assert replay.replayed and replay.rev == saved.rev
    with pytest.raises(ConflictError):
        repo.update_task_board(**{**intent, "idempotency_key": "stale"})
    with Session(session.get_bind()) as reader:
        reread = TrackerRepository(reader).get(project_id=project_id).tasks[0]
        assert reread.lane_key == reread.board_column == "backlog"
        assert reread.started_at is None
    deferred = repo.create_task(
        project_id=project_id,
        key="later",
        title="Later",
        status="deferred",
        metadata_json={
            "resume_condition": "Operator supplies input",
            "resume_owner_ref": "operator",
        },
    ).data.task
    with pytest.raises(ValidationError, match="resume deferred"):
        repo.update_task_board(
            project_id=project_id,
            task_key=deferred.key,
            expected_status=deferred.status,
            expected_updated_at=deferred.updated_at,
            idempotency_key="no-resume",
            board_column="todo",
        )
    assert (
        repo.get(project_id=project_id, task_key="later").tasks[0].metadata_json
        == deferred.metadata_json
    )


def test_queue_uses_actual_receipts_and_stop_resume_preserves_order(session, project_id):
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id,
        key="work",
        title="Work",
        status="in-progress",
        metadata_json=delivery("queued"),
    )
    ticket = repo.create_ticket(
        project_id=project_id, task_key="work", key="child", title="Child", status="in-progress"
    ).data.ticket
    identity = {
        "agent": "worker",
        "host_key": "codex",
        "model": "model",
        "started_at": "2026-11-12T00:00:00+00:00",
        "ticket_key": ticket.key,
        "invocation_id": "actual-call",
    }
    # A launch attempt without actual execution still belongs to Todo.
    evidence = {"automatic_delivery": {"attempt": 1, "delivery": {"launch": identity}}}
    repo.update_ticket(
        project_id=project_id,
        ticket_key=ticket.key,
        patch_json={"completion_evidence_json": evidence},
    )
    assert repo.get(project_id=project_id).tasks[0].board_column == "todo"
    evidence["automatic_delivery"]["delivery"]["execution"] = identity
    repo.update_ticket(
        project_id=project_id,
        ticket_key=ticket.key,
        patch_json={"completion_evidence_json": evidence},
    )
    task = repo.get(project_id=project_id).tasks[0]
    assert task.board_column == "in-progress"
    other = repo.create_task(
        project_id=project_id,
        key="other",
        title="Other",
        status="in-progress",
        metadata_json=delivery("stopped"),
    ).data.task
    stopped = repo.update_task(
        project_id=project_id, task_key="work", patch_json={"metadata_json": delivery("stopped")}
    ).data.task
    assert stopped.board_column == "stopped" and stopped.order_index < other.order_index
    same = repo.update_task(
        project_id=project_id, task_key="work", patch_json={"title": "Still stopped"}
    ).data.task
    assert same.order_index == stopped.order_index
    resumed = repo.update_task(
        project_id=project_id, task_key="work", patch_json={"metadata_json": delivery("queued")}
    ).data.task
    assert resumed.board_column == "in-progress"
    # A waiting gap does not erase the actual invocation after a fresh DB session.
    with Session(session.get_bind()) as reader:
        assert (
            TrackerRepository(reader)
            .get(project_id=project_id, task_key="work")
            .tasks[0]
            .board_column
            == "in-progress"
        )


def test_reorder_joins_raw_statuses_only_within_the_projected_column(session, project_id):
    repo = TrackerRepository(session)
    deferred = repo.create_task(
        project_id=project_id, key="later", title="Later", status="deferred"
    ).data.task
    backlog = repo.create_task(
        project_id=project_id, key="backlog", title="Backlog", lane_key="backlog"
    ).data.task
    todo = repo.create_task(project_id=project_id, key="todo", title="Todo").data.task
    result = repo.update_task_board(
        project_id=project_id,
        task_key=backlog.key,
        expected_status=backlog.status,
        expected_updated_at=backlog.updated_at,
        idempotency_key="mixed",
        anchor_task_key=deferred.key,
        placement="after",
    ).data
    assert result.changed
    rows = {row.key: row for row in session.exec(select(TrackerTask)).all()}
    assert rows["later"].order_index < rows["backlog"].order_index
    assert rows["todo"].order_index == todo.order_index


def test_legacy_receipts_and_patch_writer_use_the_same_projection(session, project_id):
    repo = TrackerRepository(session)
    task = repo.create_task(
        project_id=project_id, key="legacy-done", title="Done", status="complete"
    ).data.task
    old_receipt = task.model_dump(exclude={"board_column"})
    assert TrackerTaskOut.model_validate(old_receipt).board_column == "done"
    # A lookalike status without the validated handoff cannot manufacture Stopped.
    work = repo.create_task(
        project_id=project_id,
        key="work",
        title="Work",
        status="in-progress",
        metadata_json={"delivery": {"status": "stopped"}},
    ).data.task
    assert work.board_column == "in-progress"
    repo.create_task(
        project_id=project_id,
        key="stopped",
        title="Stopped",
        status="in-progress",
        metadata_json=delivery("stopped"),
    )
    result = repo.patch(
        project_id=project_id,
        patch_json={
            "tasks": {
                "work": {"metadata_json": delivery("stopped")},
            }
        },
    )
    stopped = result.data.task
    assert stopped.board_column == "stopped"
    rows = {task.key: task for task in repo.get(project_id=project_id).tasks}
    assert rows["work"].order_index < rows["stopped"].order_index
    before = rows["work"].order_index
    repo.patch(project_id=project_id, patch_json={"tasks": {"work": {"title": "Same column"}}})
    assert repo.get(project_id=project_id, task_key="work").tasks[0].order_index == before


@pytest.mark.parametrize("state", ["queued", "running", "stopped", "complete"])
def test_placement_does_not_change_open_handoff_or_restart_completed_one(
    session, project_id, state
):
    repo = TrackerRepository(session)
    task = repo.create_task(
        project_id=project_id, key="task", title="Task", metadata_json=delivery(state)
    ).data.task
    intent = dict(
        project_id=project_id,
        task_key=task.key,
        expected_status=task.status,
        expected_updated_at=task.updated_at,
        idempotency_key="place",
        board_column="backlog",
    )
    if state == "complete":
        saved = repo.update_task_board(**intent).data.task
        assert saved.board_column == "backlog"
        assert saved.metadata_json == task.metadata_json
    else:
        with pytest.raises(ValidationError, match="open delivery handoff"):
            repo.update_task_board(**intent)
        assert repo.get(project_id=project_id).tasks[0] == task


def test_legacy_setup_adds_backlog_without_rewriting_placement(session, project_id):
    repo = TrackerRepository(session)
    task = repo.create_task(
        project_id=project_id, key="legacy", title="Legacy", lane_key="planning"
    ).data.task
    lanes = session.exec(select(TaskTrackerLane)).all()
    for lane in lanes:
        if lane.key == "backlog":
            session.delete(lane)
    session.commit()
    prior = {lane.key: (lane.label, lane.position) for lane in lanes if lane.key != "backlog"}
    repo.ensure_tracker(project_id=project_id)
    session.commit()
    after = {
        lane.key: (lane.label, lane.position)
        for lane in session.exec(select(TaskTrackerLane)).all()
    }
    assert after.keys() == prior.keys() | {"backlog"}
    assert {key: after[key] for key in prior} == prior
    assert repo.get(project_id=project_id).tasks[0] == task


def test_placement_survives_closing_and_reopening_file_database(session, project_id, tmp_path):
    url = f"sqlite:///{tmp_path / 'board.db'}"
    engine = create_engine(url)
    restore_database_snapshot(engine, capture_database_snapshot(session.get_bind()))
    try:
        with Session(engine) as writer:
            repo = TrackerRepository(writer)
            task = repo.create_task(project_id=project_id, key="durable", title="Durable").data.task
            repo.update_task_board(
                project_id=project_id,
                task_key=task.key,
                expected_status=task.status,
                expected_updated_at=task.updated_at,
                idempotency_key="disk",
                board_column="backlog",
            )
    finally:
        engine.dispose()
    reopened = create_engine(url)
    try:
        with Session(reopened) as reader:
            task = TrackerRepository(reader).get(project_id=project_id, task_key="durable").tasks[0]
            assert task.lane_key == task.board_column == "backlog"
            assert task.status == "not-started" and task.started_at is None
    finally:
        reopened.dispose()


@pytest.mark.parametrize("default_ticket", [False, True])
def test_initial_receipt_import_also_enters_at_top(session, project_id, default_ticket):
    repo = TrackerRepository(session)
    other = repo.create_task(
        project_id=project_id, key="other", title="Other", status="in-progress"
    ).data.task
    identity = {
        "agent": "worker",
        "host_key": "codex",
        "model": "model",
        "started_at": "2026-11-12T00:00:00+00:00",
        "ticket_key": "work-work",
        "invocation_id": "recorded-call",
    }
    evidence = {
        "automatic_delivery": {
            "attempt": 1,
            "delivery": {
                "launch": identity,
                "execution": identity,
            },
        }
    }
    task = repo.create_task(
        project_id=project_id,
        key="work",
        title="Work",
        status="in-progress",
        metadata_json=delivery("queued"),
        create_default_ticket=default_ticket,
        completion_evidence_json=evidence if default_ticket else None,
    ).data.task
    if not default_ticket:
        task = repo.create_ticket(
            project_id=project_id,
            task_key="work",
            key="work-work",
            title="Recorded work",
            status="in-progress",
            completion_evidence_json=evidence,
        ).data.task
    assert task.board_column == "in-progress"
    assert task.order_index < other.order_index
