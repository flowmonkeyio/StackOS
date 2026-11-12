"""Scoped rejection regressions for cleanup rollback and cold plan admission."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event, get_ident

from sqlalchemy import event
from sqlmodel import Session, select

from stackos.db.models import RunPlan
from stackos.project_cleanup import ProjectCleanupService
from stackos.repositories.base import ConflictError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.run_plans import RunPlanRepository
from stackos.workflows.template_loader import WorkflowTemplateLoader
from tests.integration.test_project_cleanup import confirmation, put
from tests.integration.test_project_cleanup import fixture as fixture


def test_failed_purge_retains_exclusion_through_rollback_and_error_persistence(
    fixture, monkeypatch
):
    session, settings, target, other = fixture
    engine = session.get_bind()
    rolled_back, retire_owner, overlapping_cleanup = Event(), Event(), Event()
    foreign = put(settings.data_dir / "projects" / str(other.id) / "keep")
    held_inode = []

    def first():
        with Session(engine) as owner:
            failed = False
            rollback = owner.rollback
            owner_thread = get_ident()

            def fail_delete(connection, cursor, statement, parameters, context, executemany):
                nonlocal failed
                if get_ident() == owner_thread and statement.startswith("DELETE FROM projects"):
                    failed = True
                    raise RuntimeError("fixture failure after file removal, before purge commit")

            def pause_after_rollback():
                rollback()
                if failed:
                    rolled_back.set()
                    assert retire_owner.wait(10)

            monkeypatch.setattr(owner, "rollback", pause_after_rollback)
            service = ProjectCleanupService(owner, settings)
            clean = service._clean_files

            def capture(pid):
                held_inode.append(service._cleanup_lock_path(pid).stat().st_ino)
                clean(pid)

            monkeypatch.setattr(service, "_clean_files", capture)
            event.listen(engine, "before_cursor_execute", fail_delete)
            try:
                return asyncio.run(service.delete(target.id, confirmation=confirmation(target)))
            finally:
                event.remove(engine, "before_cursor_execute", fail_delete)

    def concurrent_retry():
        with Session(engine) as retry:
            service = ProjectCleanupService(retry, settings)

            def must_not_clean(pid):
                overlapping_cleanup.set()
                raise AssertionError("second cleanup entered during first-owner failure handling")

            monkeypatch.setattr(service, "_clean_files", must_not_clean)
            return asyncio.run(service.delete(target.id, confirmation=confirmation(target)))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first)
        try:
            assert rolled_back.wait(5)
            path = ProjectCleanupService(session, settings)._cleanup_lock_path(target.id)
            rollback_inode = path.stat().st_ino if path.exists() else None
            second = pool.submit(concurrent_retry).result(timeout=5)
        finally:
            retire_owner.set()
        first_result = first_future.result(timeout=5)
    assert rollback_inode == held_inode[0]
    assert not overlapping_cleanup.is_set()
    assert second.status == "blocked" and "managed_storage_writer" in second.blockers
    assert first_result.status == "incomplete" and first_result.error_code == "RuntimeError"
    assert second.operation_id == first_result.operation_id
    assert foreign.read_text() == "sentinel"
    with Session(engine) as restarted:
        row = ProjectRepository(restarted).get(target.id)
        assert not row.is_active and row.cleanup_json["status"] == "incomplete"
        final = asyncio.run(
            ProjectCleanupService(restarted, settings).delete(
                target.id, confirmation=confirmation(target)
            )
        )
    assert final.status == "deleted" and final.operation_id == first_result.operation_id
    assert not path.exists() and foreign.read_text() == "sentinel"


def test_cold_template_load_rechecks_cleanup_before_plan_write(fixture, monkeypatch):
    session, settings, target, other = fixture
    loaded, proceed = Event(), Event()
    describe = WorkflowTemplateLoader.describe_template

    def pause_after_load(loader, **kwargs):
        result = describe(loader, **kwargs)
        if kwargs["project_id"] == target.id:
            loaded.set()
            assert proceed.wait(10)
        return result

    monkeypatch.setattr(WorkflowTemplateLoader, "describe_template", pause_after_load)

    def create():
        with Session(session.get_bind()) as writer:
            try:
                result = RunPlanRepository(writer).create(
                    project_id=target.id,
                    template_key="core.project-memory-review",
                    inputs_json={"goal": "Disposable fixture"},
                )
            except ConflictError:
                return None
            return result.data.id

    service = ProjectCleanupService(session, settings)

    def fail_files(pid):
        raise OSError("fixture retains committed cleanup fence")

    monkeypatch.setattr(service, "_clean_files", fail_files)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(create)
        try:
            assert loaded.wait(5)
            result = asyncio.run(service.delete(target.id, confirmation=confirmation(target)))
            assert result.status == "incomplete"
        finally:
            proceed.set()
        created_id = future.result(timeout=5)
    assert created_id is None
    with Session(session.get_bind()) as observer:
        assert not observer.exec(select(RunPlan).where(RunPlan.project_id == target.id)).all()
        repo = RunPlanRepository(observer)
        warm = repo.create(
            project_id=other.id,
            template_key="core.project-memory-review",
            inputs_json={"goal": "Warm template control"},
        ).data
        adhoc = repo.create(
            project_id=other.id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "adhoc-control",
                "title": "Ad-hoc control",
                "steps": [{"id": "work", "title": "Work"}],
            },
        ).data
        assert warm.project_id == adhoc.project_id == other.id


def test_committed_purge_prevents_retry_from_reopening_lock_before_retirement(fixture, monkeypatch):
    session, settings, target, other = fixture
    committed, release = Event(), Event()
    foreign = put(settings.data_dir / "projects" / str(other.id) / "keep")

    def cleanup():
        with Session(session.get_bind()) as owner:
            service = ProjectCleanupService(owner, settings)
            purge = service._purge

            def pause_after_commit(pid, **kwargs):
                purge(pid, **kwargs)
                committed.set()
                assert release.wait(10)

            monkeypatch.setattr(service, "_purge", pause_after_commit)
            return asyncio.run(service.delete(target.id, confirmation=confirmation(target)))

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(cleanup)
        try:
            assert committed.wait(5)
            path = ProjectCleanupService(session, settings)._cleanup_lock_path(target.id)
            held_inode = path.stat().st_ino
            with Session(session.get_bind()) as retry:
                result = asyncio.run(
                    ProjectCleanupService(retry, settings).delete(
                        target.id, confirmation=confirmation(target)
                    )
                )
            assert result.status == "missing"
            assert path.stat().st_ino == held_inode
        finally:
            release.set()
        assert future.result(timeout=5).status == "deleted"
    assert not path.exists() and foreign.read_text() == "sentinel"
