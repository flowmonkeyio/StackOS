"""Admission invariants on independent SQLite and real Postgres connections."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, event
from sqlmodel import Session, SQLModel, select

from stackos.db.connection import make_engine, make_settings_engine
from stackos.db.models import AgentSession, IdempotencyKey, Project, TrackerRevision, TrackerTask
from stackos.repositories.base import ConflictError, NotFoundError, ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.tracker import TrackerRepository
from stackos.repositories.workspaces import WorkspaceRepository
from tests.integration.test_repositories.test_task_delivery import (
    QueuedScheduler,
    prepared_task,
    submit,
)
from tests.integration.test_repositories.test_task_delivery import (
    git_project as git_project,
)
from tests.integration.test_repositories.test_task_delivery import (
    native_source_fixtures as native_source_fixtures,
)


@pytest.fixture(params=["sqlite", "postgres"])
def admission_engine(request, tmp_path, monkeypatch):
    if request.param == "postgres":
        env_file = os.environ.get("STACKOS_TEST_POSTGRES_ENV_FILE")
        if not env_file:
            pytest.skip("real Postgres requires explicit STACKOS_TEST_POSTGRES_ENV_FILE")
        from stackos.config import get_settings
        from stackos.db.migrate import upgrade_to_head

        monkeypatch.setenv("STACKOS_ENV_FILE", env_file)
        settings = get_settings()
        assert settings.process_role == "server"
        upgrade_to_head(settings)
        engine = make_settings_engine(settings)
        assert engine.dialect.name == "postgresql"
    else:
        engine = make_engine(tmp_path / "admission.sqlite")
        SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@contextmanager
def _project(admission_engine):
    with Session(admission_engine) as session:
        project = (
            ProjectRepository(session)
            .create(
                slug=f"admission-{uuid4().hex}", name="Admission fixture", domain="example.test"
            )
            .data
        )
    try:
        yield project.id
    finally:
        with Session(admission_engine) as session:
            session.execute(delete(Project).where(Project.id == project.id))
            session.commit()


@pytest.fixture
def admission_project(admission_engine):
    with _project(admission_engine) as project_id:
        yield project_id


@pytest.fixture
def admission_other_project(admission_engine):
    with _project(admission_engine) as project_id:
        yield project_id


@pytest.mark.parametrize("scope", ["same-task", "same-project", "other-project"])
def test_delivery_admits_one_writer(
    admission_engine, admission_project, admission_other_project, tmp_path, scope, monkeypatch
):
    # Host readiness is outside this database-writer race. Use the retained
    # Codex native-source fixture, as in the directory-collision lifecycle tests.
    monkeypatch.setattr("stackos.task_delivery.agent_selection_error", lambda *args, **kwargs: None)
    with Session(admission_engine) as session:
        prepared_task(session, admission_project)
        prepared_task(session, admission_other_project)
    scheduler = QueuedScheduler()
    ready = Barrier(2)

    def handoff(target):
        project_id, key = target
        with Session(admission_engine) as session:
            ready.wait(timeout=10)
            try:
                if scope == "other-project":
                    from stackos.task_delivery import _queue_delivery

                    # Fresh native sources cannot share a project binding.
                    # Exercise the shared DB reservation owner directly here;
                    # ordinary submit above/below retains native admission proof.
                    state, queued = _queue_delivery(
                        session,
                        TrackerRepository(session),
                        project_id,
                        key,
                        status="queued",
                        admission_id="database-race",
                        slot_reserved=False,
                        project_dir=str(tmp_path.resolve()),
                    )
                    if queued:
                        scheduler.jobs.append(state)
                    return state
                return submit(
                    session, project_id, tmp_path, scheduler, task_key=key, host_key="codex"
                )
            except ValidationError as error:
                return error

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(
            workers.map(
                handoff,
                [
                    (admission_project, "deliver"),
                    (
                        admission_other_project if scope == "other-project" else admission_project,
                        "deliver" if scope == "same-task" else "unrelated",
                    ),
                ],
            )
        )
    assert len(scheduler.jobs) == 1, [str(result) for result in results]
    if scope == "same-task":
        assert results[0] == results[1]
    else:
        loser = next(result for result in results if isinstance(result, ValidationError))
        assert loser.data == {
            "project_dir": str(tmp_path.resolve()),
            "next_operations": ["tracker.get"],
        }
    with Session(admission_engine) as session:
        tasks = session.exec(
            select(TrackerTask).where(
                TrackerTask.project_id.in_([admission_project, admission_other_project])
            )
        ).all()
        active = [task for task in tasks if (task.metadata_json or {}).get("delivery")]
        assert len(active) == 1
        assert active[0].metadata_json["delivery"]["status"] == "queued"


@pytest.mark.parametrize("same_intent", [False, True])
def test_content_admits_one_revision(admission_engine, admission_project, same_intent):
    with Session(admission_engine) as session:
        task = (
            TrackerRepository(session)
            .create_task(
                project_id=admission_project, key="content", title="Content", description="Original"
            )
            .data.task
        )
    ready = Barrier(2)

    def save(index):
        with Session(admission_engine) as session:
            ready.wait(timeout=10)
            try:
                return (
                    TrackerRepository(session)
                    .update_task_content(
                        project_id=admission_project,
                        task_key="content",
                        expected_updated_at=task.updated_at,
                        idempotency_key="same" if same_intent else f"intent-{index}",
                        additional_input="Once" if same_intent else f"Input {index}",
                    )
                    .data
                )
            except ConflictError as error:
                return error

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(save, [1, 2]))
    if same_intent:
        assert sorted(result.replayed for result in results) == [False, True]
        assert results[0].rev == results[1].rev
    else:
        loser = next(result for result in results if isinstance(result, ConflictError))
        assert loser.data["next_operations"] == ["tracker.get"]
        assert "current_updated_at" in loser.data
    with Session(admission_engine) as session:
        repo = TrackerRepository(session)
        saved = repo.get(project_id=admission_project).tasks[0]
        assert len(saved.context_json["operator_inputs"]) == 1
        assert (
            len(
                session.exec(
                    select(IdempotencyKey).where(IdempotencyKey.project_id == admission_project)
                ).all()
            )
            == 1
        )
        assert (
            len(
                session.exec(
                    select(TrackerRevision).where(
                        TrackerRevision.project_id == admission_project,
                        TrackerRevision.change_kind == "content",
                    )
                ).all()
            )
            == 1
        )


def test_global_startup_rechecks_binding(
    admission_engine, admission_project, tmp_path, monkeypatch
):
    client_id = uuid4().hex
    with Session(admission_engine) as reader, Session(admission_engine) as writer:
        repo = WorkspaceRepository(reader)
        resolve = repo.resolve
        checks = []

        def raced_resolve(**kwargs):
            result = resolve(**kwargs)
            checks.append(result.project_id)
            if len(checks) == 1:
                WorkspaceRepository(writer).connect(
                    project_id=admission_project,
                    repo_fingerprint=f"path:{uuid4().hex}",
                    last_known_root=str(tmp_path),
                )
            return result

        monkeypatch.setattr(repo, "resolve", raced_resolve)
        with pytest.raises(ValidationError, match="already bound") as error:
            repo.start_session(
                runtime="codex", cwd=str(tmp_path), global_session=True, client_session_id=client_id
            )
        assert error.value.data == {"project_id": admission_project, "global_session": False}
        assert checks == [None, admission_project]
        assert (
            reader.exec(
                select(AgentSession).where(AgentSession.client_session_id == client_id)
            ).all()
            == []
        )


def test_global_startup_reserves_binding_decision(admission_engine, admission_project, tmp_path):
    """A canonical binding writer waits until the global decision commits."""
    from threading import Event

    locked = Event()
    writer_started = Event()
    writer_finished = Event()

    def bind():
        assert locked.wait(timeout=10)
        with Session(admission_engine) as session:
            WorkspaceRepository(session).connect(
                project_id=admission_project,
                repo_fingerprint=f"path:{uuid4().hex}",
                last_known_root=str(tmp_path),
            )
            writer_finished.set()

    def before_write(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO workspace_bindings"):
            writer_started.set()

    def after_lock(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith(("BEGIN IMMEDIATE", "LOCK TABLE workspace_bindings")):
            locked.set()
            assert writer_started.wait(timeout=10)
            assert not writer_finished.wait(timeout=0.1)

    event.listen(admission_engine, "before_cursor_execute", before_write)
    event.listen(admission_engine, "after_cursor_execute", after_lock)
    try:
        with ThreadPoolExecutor(max_workers=1) as workers:
            writer = workers.submit(bind)
            with Session(admission_engine) as session:
                result = WorkspaceRepository(session).start_session(
                    runtime="codex",
                    cwd=str(tmp_path),
                    global_session=True,
                    project_id=admission_project,
                )
                assert result.data.global_session
                assert result.data.workspace_binding_id is None
            writer.result(timeout=10)
    finally:
        event.remove(admission_engine, "before_cursor_execute", before_write)
        event.remove(admission_engine, "after_cursor_execute", after_lock)
    with Session(admission_engine) as session:
        assert (
            WorkspaceRepository(session).resolve(cwd=str(tmp_path)).project_id == admission_project
        )


def test_identity_nonreuse_and_source_conflict(admission_engine, admission_project):
    with Session(admission_engine) as session:
        repo = TrackerRepository(session)
        original = repo.create_task(
            project_id=admission_project, key="source", title="Original", source_json={"ref": "one"}
        ).data.task
        with pytest.raises(ConflictError) as error:
            repo.create_task(
                project_id=admission_project,
                key="source",
                title="Replacement",
                source_json={"ref": "two"},
            )
        assert error.value.data == {"task_key": "source"}
        assert repo.get(project_id=admission_project).tasks[0].source_json == original.source_json
        projects = ProjectRepository(session)
        deleted = projects.create(
            slug=f"deleted-{uuid4().hex}", name="Deleted", domain="example.test"
        ).data.id
        session.execute(delete(Project).where(Project.id == deleted))
        session.commit()
    with Session(admission_engine) as session:
        replacement = (
            ProjectRepository(session)
            .create(slug=f"replacement-{uuid4().hex}", name="Replacement", domain="example.test")
            .data.id
        )
        assert replacement > deleted
        with pytest.raises(NotFoundError):
            WorkspaceRepository(session).start_session(
                runtime="codex", global_session=True, project_id=deleted
            )
        session.execute(delete(Project).where(Project.id == replacement))
        session.commit()
