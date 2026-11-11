from __future__ import annotations

import importlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlmodel import Session, create_engine, select

from stackos.config import Settings
from stackos.db.migrate import upgrade_to_head
from stackos.db.models import (
    ProjectEvent,
    RunPlanStepStatus,
    TrackerItemStatus,
    TrackerRevision,
    TrackerTask,
)
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import TrackerRepository
from stackos.repositories.tracker.ordering import NORMALIZATION_KIND, normalize_default_columns
from tests.helpers.sqlite_schema import capture_database_snapshot, restore_database_snapshot


def _order(session, project_id, status="not-started"):
    return [
        row.key
        for row in session.exec(
            select(TrackerTask)
            .where(TrackerTask.project_id == project_id, TrackerTask.status == status)
            .order_by(TrackerTask.order_index, TrackerTask.created_at.desc(), TrackerTask.id.desc())
        )
    ]


def test_arrivals_manual_order_same_status_and_reentry(session, project_id):
    repo = TrackerRepository(session)
    for key in ("old", "hidden", "new"):
        repo.create_task(project_id=project_id, key=key, title=key)
    assert _order(session, project_id) == ["new", "hidden", "old"]
    task = repo.get(project_id=project_id, task_key="old").tasks[0]
    repo.update_task_board(
        project_id=project_id,
        task_key="old",
        expected_status=task.status,
        expected_updated_at=task.updated_at,
        idempotency_key="manual",
        anchor_task_key="new",
        placement="before",
    )
    repo.update_task(
        project_id=project_id,
        task_key="old",
        patch_json={"status": "not-started", "title": "Renamed", "priority_key": "p0"},
    )
    session.expire_all()
    assert _order(session, project_id) == ["old", "new", "hidden"]
    repo.create_ticket(project_id=project_id, task_key="old", key="usage", title="Usage")
    repo.update_ticket(
        project_id=project_id,
        ticket_key="usage",
        patch_json={"completion_evidence_json": {"usage": {"input": 123, "output": 45}}},
    )
    assert _order(session, project_id) == ["old", "new", "hidden"]
    repo.create_task(project_id=project_id, key="arrival", title="Arrival")
    assert _order(session, project_id) == ["arrival", "old", "new", "hidden"]
    repo.update_task(project_id=project_id, task_key="new", patch_json={"status": "in-progress"})
    repo.patch(project_id=project_id, patch_json={"tasks": {"new": {"status": "not-started"}}})
    assert _order(session, project_id) == ["new", "arrival", "old", "hidden"]


def test_rollup_reopen_and_reject_arrive_before_existing_tasks(session, project_id):
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id, key="parent", title="Parent", create_default_ticket=True
    )
    for status in (
        TrackerItemStatus.IN_PROGRESS,
        TrackerItemStatus.COMPLETE,
        TrackerItemStatus.ABORTED,
    ):
        repo.create_task(
            project_id=project_id, key=f"existing-{status.value}", title="Existing", status=status
        )
    for status in ("in-progress", "complete"):
        repo.update_ticket(
            project_id=project_id, ticket_key="parent-work", patch_json={"status": status}
        )
        assert _order(session, project_id, status)[0] == "parent"
    repo.reopen_task(project_id=project_id, task_key="parent", reason="Follow-up")
    assert _order(session, project_id, "in-progress")[0] == "parent"
    repo.reject_task(project_id=project_id, task_key="parent", reason="Cancelled")
    assert _order(session, project_id, "aborted")[0] == "parent"


def test_workflow_lifecycle_uses_arrival_order_without_same_status_reset(session, project_id):
    repo = TrackerRepository(session)
    plans = RunPlanRepository(session)
    repo.create_task(project_id=project_id, key="preexisting", title="Preexisting")
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "arrival-flow",
            "title": "Arrival",
            "steps": [{"id": "work", "title": "Work", "success_criteria": ["Done"]}],
        },
    ).data
    key = f"workflow-{plan.id}"
    assert _order(session, project_id)[0] == key
    for status in TrackerItemStatus:
        repo.create_task(
            project_id=project_id, key=f"peer-{status.value}", title="Peer", status=status
        )
    started = plans.start(plan.id, project_id=project_id).data
    assert _order(session, project_id, "in-progress")[0] == key
    task = repo.get(project_id=project_id, task_key=key).tasks[0]
    repo.update_task_board(
        project_id=project_id,
        task_key=key,
        expected_status=task.status,
        expected_updated_at=task.updated_at,
        idempotency_key="workflow-manual",
        anchor_task_key="peer-in-progress",
        placement="after",
    )
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="work", claimed_by="test")
    assert _order(session, project_id, "in-progress") == ["peer-in-progress", key]
    plans.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="work",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"summary": "Done"},
    )
    assert _order(session, project_id, "complete")[0] == key
    plans.reopen(project_id=project_id, run_plan_id=plan.id, reason="More", step_id="work")
    assert _order(session, project_id, "in-progress")[0] == key
    plans.abort(run_plan_id=plan.id, project_id=project_id, reason="Stop")
    assert _order(session, project_id, "aborted")[0] == key


@pytest.mark.parametrize("transition", [False, True])
def test_concurrent_arrivals_allocate_unique_ranks(session, project_id, tmp_path, transition):
    repo = TrackerRepository(session)
    for key in ("a", "b"):
        repo.create_task(project_id=project_id, key=key, title=key)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'arrivals.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    restore_database_snapshot(engine, capture_database_snapshot(session.get_bind()))
    barrier = Barrier(2)

    def arrive(key):
        with Session(engine) as writer:
            local = TrackerRepository(writer)
            local.get(project_id=project_id)  # Both writers may hold old ORM snapshots.
            barrier.wait(timeout=5)
            if transition:
                return local.update_task(
                    project_id=project_id, task_key=key, patch_json={"status": "in-progress"}
                ).data.task
            return local.create_task(project_id=project_id, key=f"new-{key}", title=key).data.task

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(arrive, ["a", "b"]))
        assert len({task.order_index for task in results}) == 2
        with Session(engine) as reader:
            status = "in-progress" if transition else "not-started"
            assert _order(reader, project_id, status)[:2] == [
                task.key for task in sorted(results, key=lambda task: task.order_index)
            ]
    finally:
        engine.dispose()


def _legacy_ranks(session, project_id):
    """Recreate the previous global oldest-first allocator and its real receipts."""
    for index, task in enumerate(
        session.exec(
            select(TrackerTask).where(TrackerTask.project_id == project_id).order_by(TrackerTask.id)
        ).all()
    ):
        task.order_index = index
        session.add(task)
        revision = session.exec(
            select(TrackerRevision).where(
                TrackerRevision.entity_kind == "task",
                TrackerRevision.entity_id == task.id,
                TrackerRevision.change_kind == "create",
            )
        ).one()
        revision.after_json = {**revision.after_json, "order_index": index}
        session.add(revision)
    session.commit()


def test_normalization_preserves_manual_columns_uses_entry_history_and_is_once(session, project_id):
    repo = TrackerRepository(session)
    other = (
        ProjectRepository(session)
        .create(slug="other-arrivals", name="Other", domain="other.test")
        .data.id
    )
    for key in ("old", "new", "manual-a", "manual-b"):
        repo.create_task(
            project_id=project_id,
            key=key,
            title=key,
            status=TrackerItemStatus.COMPLETE
            if key.startswith("manual")
            else TrackerItemStatus.NOT_STARTED,
        )
    repo.create_task(project_id=other, key="other", title="Other")
    _legacy_ranks(session, project_id)
    # Earlier-created old entered its present column most recently. unrelated
    # updated_at on new is deliberately later and must not determine order.
    task = session.exec(select(TrackerTask).where(TrackerTask.key == "old")).one()
    session.add(
        ProjectEvent(
            project_id=project_id,
            source_type="tracker_task",
            source_id=task.id,
            event_type="tracker.task.status_changed",
            metadata_json={"new_status": "not-started"},
            occurred_at=datetime(2090, 1, 1),
        )
    )
    newer = session.exec(select(TrackerTask).where(TrackerTask.key == "new")).one()
    newer.updated_at = datetime(2100, 1, 1)
    session.add(newer)
    session.commit()
    manual = repo.get(project_id=project_id, task_key="manual-b").tasks[0]
    repo.update_task_board(
        project_id=project_id,
        task_key=manual.key,
        expected_status=manual.status,
        expected_updated_at=manual.updated_at,
        idempotency_key="keep-manual",
        anchor_task_key="manual-a",
        placement="before",
    )
    before_manual = [
        task.model_dump()
        for task in repo.get(project_id=project_id).tasks
        if task.status == "complete"
    ]
    other_before = repo.get(project_id=other)
    reports = normalize_default_columns(session)
    session.commit()
    assert _order(session, project_id) == ["old", "new"]
    assert [
        task.model_dump()
        for task in repo.get(project_id=project_id).tasks
        if task.status == "complete"
    ] == before_manual
    assert reports[0]["preserved"][0]["reason"] == "saved manual ordering"
    assert reports[0]["creation_fallback"] == ["new"]
    # Only the other tracker's own normalization receipt changes its revision.
    assert repo.get(project_id=other).tasks == other_before.tasks
    repo.create_task(project_id=project_id, key="later", title="Later")
    assert normalize_default_columns(session) == []
    assert _order(session, project_id) == ["later", "old", "new"]


@pytest.mark.parametrize("ambiguity", ["gap", "rank", "column", "status"])
def test_normalization_preserves_ambiguous_history(session, project_id, ambiguity):
    repo = TrackerRepository(session)
    for key in ("old", "new"):
        repo.create_task(project_id=project_id, key=key, title=key)
    _legacy_ranks(session, project_id)
    task = session.exec(select(TrackerTask).where(TrackerTask.key == "old")).one()
    if ambiguity == "gap":
        session.delete(session.exec(select(TrackerRevision)).first())
    elif ambiguity == "rank":
        task.order_index = 99
        session.add(task)
    elif ambiguity == "column":
        tracker = repo.ensure_tracker(project_id=project_id)
        repo._record_revision(
            tracker,
            actor="legacy",
            change_kind="board",
            entity_kind="task",
            entity_id=task.id,
            entity_key=task.key,
            summary="Unknown column",
            after_json={"order": [{"task_key": task.key, "order_index": task.order_index}]},
        )
    else:
        session.add(
            ProjectEvent(
                project_id=project_id,
                source_type="tracker_task",
                source_id=task.id,
                event_type="tracker.task.status_changed",
                metadata_json={"new_status": "complete"},
            )
        )
    session.commit()
    before = repo.get(project_id=project_id).tasks
    report = normalize_default_columns(session)[0]
    session.commit()
    assert report["normalized"] == []
    assert set(report["preserved"][0]["task_keys"]) == {"old", "new"}
    assert repo.get(project_id=project_id).tasks == before
    assert session.exec(
        select(TrackerRevision).where(TrackerRevision.change_kind == NORMALIZATION_KIND)
    ).one()


def test_creation_fallback_and_project_scoped_arrivals(session, project_id):
    repo = TrackerRepository(session)
    other = (
        ProjectRepository(session)
        .create(slug="isolation", name="Isolation", domain="isolation.test")
        .data.id
    )
    for scope in (project_id, other):
        for key in ("old", "new"):
            repo.create_task(project_id=scope, key=key, title=key)
        _legacy_ranks(session, scope)
    reports = normalize_default_columns(session)
    session.commit()
    assert all(report["creation_fallback"] == ["new", "old"] for report in reports)
    assert _order(session, project_id) == ["new", "old"]
    untouched = repo.get(project_id=other)
    repo.update_task(project_id=project_id, task_key="old", patch_json={"status": "in-progress"})
    repo.create_task(project_id=project_id, key="arrival", title="Arrival")
    assert repo.get(project_id=other) == untouched


def test_migration_commits_atomically_with_alembic_and_retries_once(
    session, project_id, tmp_path, monkeypatch
):
    repo = TrackerRepository(session)
    for key in ("old", "new"):
        repo.create_task(project_id=project_id, key=key, title=key)
    _legacy_ranks(session, project_id)
    engine = create_engine(f"sqlite:///{tmp_path / 'upgrade.db'}")
    restore_database_snapshot(engine, capture_database_snapshot(session.get_bind()))
    migration = importlib.import_module(
        "stackos.db.migrations.versions.0039_tracker_board_arrivals"
    )
    original = TrackerRepository._record_revision

    def fail(*args, **kwargs):
        raise RuntimeError("audit failure")

    try:
        monkeypatch.setattr(TrackerRepository, "_record_revision", fail)
        with (
            pytest.raises(RuntimeError, match="audit failure"),
            engine.begin() as connection,
            Operations.context(MigrationContext.configure(connection)),
        ):
            migration.upgrade()
        with Session(engine) as reader:
            assert _order(reader, project_id) == ["old", "new"]
            assert reader.exec(select(ProjectEvent)).all() == []
        monkeypatch.setattr(TrackerRepository, "_record_revision", original)
        for _ in range(2):
            with (
                engine.begin() as connection,
                Operations.context(MigrationContext.configure(connection)),
            ):
                migration.upgrade()
        with Session(engine) as reader:
            assert _order(reader, project_id) == ["new", "old"]
            assert (
                len(
                    reader.exec(
                        select(TrackerRevision).where(
                            TrackerRevision.change_kind == NORMALIZATION_KIND
                        )
                    ).all()
                )
                == 1
            )
            assert len(reader.exec(select(ProjectEvent)).all()) == 2
    finally:
        engine.dispose()


def test_transition_failure_rolls_back_placement_and_event(session, project_id, monkeypatch):
    repo = TrackerRepository(session)
    for key in ("old", "new"):
        repo.create_task(project_id=project_id, key=key, title=key)
    before = repo.get(project_id=project_id)

    def fail(*args, **kwargs):
        raise RuntimeError("audit failure")

    monkeypatch.setattr(repo, "_record_revision", fail)
    with pytest.raises(RuntimeError, match="audit failure"):
        repo.update_task(
            project_id=project_id, task_key="old", patch_json={"status": "in-progress"}
        )
    session.rollback()
    assert repo.get(project_id=project_id) == before
    assert session.exec(select(ProjectEvent)).all() == []


def test_legacy_create_all_stamp_also_normalizes_existing_columns(session, project_id, tmp_path):
    repo = TrackerRepository(session)
    for key in ("old", "new"):
        repo.create_task(project_id=project_id, key=key, title=key)
    _legacy_ranks(session, project_id)
    selected = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    selected.ensure_dirs()
    engine = create_engine(f"sqlite:///{selected.db_path}")
    restore_database_snapshot(engine, capture_database_snapshot(session.get_bind()))
    try:
        outcome = upgrade_to_head(selected)
        assert outcome.stamped_existing_schema
        with Session(engine) as reader:
            assert _order(reader, project_id) == ["new", "old"]
    finally:
        engine.dispose()


def test_normalization_leaves_empty_tracker_configuration_and_revision_untouched(
    session, project_id
):
    repo = TrackerRepository(session)
    repo.ensure_tracker(project_id=project_id)
    session.commit()
    before = repo.get(project_id=project_id)
    assert normalize_default_columns(session) == []
    session.commit()
    assert repo.get(project_id=project_id) == before
