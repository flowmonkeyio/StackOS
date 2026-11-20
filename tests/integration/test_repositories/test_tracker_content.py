from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlmodel import Session, create_engine, select

from stackos.db.models import IdempotencyKey, RunPlanStepStatus, TrackerRevision
from stackos.repositories.base import ConflictError, NotFoundError, ValidationError
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import TrackerRepository
from tests.helpers.sqlite_schema import capture_database_snapshot, restore_database_snapshot


def _create(session, project_id):
    return (
        TrackerRepository(session)
        .create_task(
            project_id=project_id,
            key="content-task",
            title="Keep title",
            goal="Keep goal",
            description="Original",
            context_json={"source": {"ref": "source-1"}},
        )
        .data.task
    )


def _save(session, project_id, task, intent="input-1", **content):
    return TrackerRepository(session).update_task_content(
        project_id=project_id,
        task_key=task.key,
        expected_updated_at=task.updated_at,
        idempotency_key=intent,
        **content,
    )


def test_content_save_preserves_execution_and_appends_audited_input(session, project_id):
    task = _create(session, project_id)
    before = task.model_dump(exclude={"description", "context_json", "updated_at"})
    result = _save(session, project_id, task, description="Revised", additional_input="First input")
    saved = result.data.task
    assert saved.model_dump(exclude={"description", "context_json", "updated_at"}) == before
    assert saved.description == "Revised"
    assert saved.context_json["source"] == {"ref": "source-1"}
    assert saved.context_json["operator_inputs"] == [result.data.input.model_dump(mode="json")]
    second = _save(session, project_id, saved, "input-2", description="", additional_input="Second")
    assert second.data.task.description == ""
    assert [item["text"] for item in second.data.task.context_json["operator_inputs"]] == [
        "First input",
        "Second",
    ]
    revisions = session.exec(
        select(TrackerRevision).where(TrackerRevision.change_kind == "content")
    ).all()
    assert len(revisions) == 2
    assert revisions[0].actor == "local-user:operator"
    assert revisions[0].patch_json["additional_input"]["text"] == "First input"
    assert revisions[0].before_json["description"] == "Original"
    assert revisions[0].after_json["description"] == "Revised"


def test_retry_is_intent_bound_and_stale_content_fails_without_writes(session, project_id):
    task = _create(session, project_id)
    first = _save(session, project_id, task, additional_input="Once")
    replay = _save(session, project_id, task, additional_input="Once")
    assert replay.data.replayed is True
    assert replay.data.rev == first.data.rev
    assert len(replay.data.task.context_json["operator_inputs"]) == 1
    with pytest.raises(ConflictError, match="different task content submission"):
        _save(session, project_id, task, additional_input="Changed intent")
    other = (
        TrackerRepository(session)
        .create_task(project_id=project_id, key="other-task", title="Other task")
        .data.task
    )
    with pytest.raises(ConflictError, match="different task content submission"):
        _save(session, project_id, other, additional_input="Once")
    with pytest.raises(ConflictError, match="task changed") as error:
        _save(session, project_id, task, "new-intent", description="Stale")
    assert "current_updated_at" in error.value.data
    assert len(session.exec(select(IdempotencyKey)).all()) == 1
    assert (
        len(
            session.exec(
                select(TrackerRevision).where(TrackerRevision.change_kind == "content")
            ).all()
        )
        == 1
    )


def test_content_save_redacts_text_in_task_history_and_raw_receipt(session, project_id):
    task = _create(session, project_id)
    secret = "sk-proj-" + "a" * 48
    result = _save(
        session,
        project_id,
        task,
        description=f"api_key={secret}",
        additional_input=f"Authorization: Bearer {secret}",
    )
    assert secret not in result.model_dump_json()
    for row in session.exec(select(TrackerRevision)).all():
        assert secret not in row.model_dump_json()
    assert secret not in session.exec(select(IdempotencyKey)).one().model_dump_json()


def test_malformed_inputs_are_not_replaced_and_failed_save_is_atomic(
    session, project_id, monkeypatch
):
    task = _create(session, project_id)
    repo = TrackerRepository(session)
    broken = repo.update_task(
        project_id=project_id,
        task_key=task.key,
        patch_json={"context_json": {"operator_inputs": "broken"}},
    ).data.task
    with pytest.raises(ValidationError, match="need repair"):
        _save(session, project_id, broken, description="Should not save", additional_input="No")
    current = repo.get(project_id=project_id).tasks[0]
    assert current.description == "Original"
    assert current.context_json["operator_inputs"] == "broken"
    original_record = repo._record_revision

    def fail_after_revision(*args, **kwargs):
        original_record(*args, **kwargs)
        raise RuntimeError("synthetic transaction failure")

    monkeypatch.setattr(repo, "_record_revision", fail_after_revision)
    with pytest.raises(RuntimeError, match="synthetic"):
        repo.update_task_content(
            project_id=project_id,
            task_key=current.key,
            expected_updated_at=current.updated_at,
            idempotency_key="failed-save",
            description="Never commit",
        )
    assert repo.get(project_id=project_id).tasks[0].description == "Original"
    assert session.exec(select(IdempotencyKey)).all() == []
    assert (
        session.exec(select(TrackerRevision).where(TrackerRevision.change_kind == "content")).all()
        == []
    )


def test_task_scope_and_mirror_content_survive_controller_sync(session, project_id):
    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "content-mirror",
            "title": "Workflow title",
            "goal": "Canonical goal",
            "steps": [{"id": "prepare", "title": "Prepare"}],
        },
        created_by="codex",
    ).data
    repo = TrackerRepository(session)
    before = repo.get(project_id=project_id, run_plan_id=plan.id)
    task = before.tasks[0]
    result = _save(
        session,
        project_id,
        task,
        description="Human description",
        additional_input="Retain this input",
    )
    after = repo.get(project_id=project_id, run_plan_id=plan.id)
    assert after.tickets == before.tickets
    assert after.dependencies == before.dependencies
    started = plans.start(plan.id, project_id=project_id).data
    plans.claim_step(
        run_plan_id=plan.id, run_id=started.run_id, step_id="prepare", claimed_by="codex"
    )
    plans.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="prepare",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"summary": "Done"},
    )
    synced = repo.get(project_id=project_id, run_plan_id=plan.id).tasks[0]
    assert synced.description == "Human description"
    assert (
        synced.context_json["operator_inputs"] == result.data.task.context_json["operator_inputs"]
    )
    assert (synced.title, synced.goal) == (task.title, task.goal)
    with pytest.raises(NotFoundError):
        repo.update_task_content(
            project_id=project_id,
            task_key="unknown",
            expected_updated_at=task.updated_at,
            idempotency_key="missing",
            description="No",
        )


def test_concurrent_saves_serialize_and_reject_the_stale_writer(session, project_id, tmp_path):
    task = _create(session, project_id)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'content.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    restore_database_snapshot(engine, capture_database_snapshot(session.get_bind()))
    barrier = Barrier(2)

    def submit(index):
        with Session(engine) as writer:
            barrier.wait(timeout=5)
            try:
                return _save(
                    writer,
                    project_id,
                    task,
                    f"concurrent-{index}",
                    additional_input=f"Input {index}",
                ).data
            except ConflictError:
                return None

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, [1, 2]))
        assert sum(result is not None for result in results) == 1
        with Session(engine) as reader:
            saved = TrackerRepository(reader).get(project_id=project_id).tasks[0]
            assert len(saved.context_json["operator_inputs"]) == 1
            assert len(reader.exec(select(IdempotencyKey)).all()) == 1
            assert (
                len(
                    reader.exec(
                        select(TrackerRevision).where(TrackerRevision.change_kind == "content")
                    ).all()
                )
                == 1
            )
    finally:
        engine.dispose()
