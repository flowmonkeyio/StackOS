"""Barrier-controlled cleanup/launch proof in disposable DB and filesystem roots."""

import asyncio
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event, get_ident

import pytest
from sqlalchemy import event
from sqlmodel import Session

from stackos.actions import ActionRepository
from stackos.db.models import Project, RunKind
from stackos.host_mcp.agent_execution import InvocationCancelled, ManagedProcessControl
from stackos.project_cleanup import ProjectCleanupService
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.base import ConflictError, NotFoundError
from stackos.repositories.browser import BrowserRepository
from stackos.repositories.projects import ProjectRepository, ScheduledJobRepository
from stackos.repositories.resources import ArtifactRepository, ResourceRepository
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.runs import RunRepository
from tests.integration.test_project_cleanup import confirmation, put
from tests.integration.test_project_cleanup import fixture as fixture


@pytest.mark.parametrize("phase", ["recovery", "files", "purge"])
@pytest.mark.parametrize("fail", [False, True])
def test_slow_cleanup_releases_launch_boundary_and_retains_project_fence(
    fixture, monkeypatch, phase, fail
):
    s, settings, target, other = fixture
    entered, release, stale_ready = Event(), Event(), Event()
    engine = s.get_bind()
    foreign = put(settings.data_dir / "projects" / str(other.id) / "foreign")
    owned = put(settings.data_dir / "projects" / str(target.id) / "owned")

    def pause():
        entered.set()
        assert release.wait(10), "test did not release cleanup barrier"
        if fail:
            raise OSError("disposable fixture failure")

    if phase == "recovery":
        original = ActionRepository.recover_generated_file_custody

        def recover(repo, **kwargs):
            pause()
            return original(repo, **kwargs)

        monkeypatch.setattr(ActionRepository, "recover_generated_file_custody", recover)
    else:
        method = "_clean_files" if phase == "files" else "_purge"
        original = getattr(ProjectCleanupService, method)

        def delayed(service, pid, **kwargs):
            pause()
            return original(service, pid, **kwargs)

        monkeypatch.setattr(ProjectCleanupService, method, delayed)

    def cleanup():
        with Session(engine) as session:
            return asyncio.run(ProjectCleanupService(session, settings).delete(
                target.id, confirmation=confirmation(target)
            ))

    def launch(pid):
        def allowed():
            with Session(engine) as session:
                try:
                    ProjectRepository(session).require_available(pid)
                except (ConflictError, NotFoundError):
                    return False
                return True

        process = ManagedProcessControl(allowed).spawn(
            [sys.executable, "-c", "print('launched')"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        return stdout.strip()

    def late_writer():
        with Session(engine) as writer:
            writer.get(Project, target.id)  # Identity loaded before the fence.
            stale_ready.set()
            assert entered.wait(5)
            with pytest.raises(ConflictError):
                RunRepository(writer).start(project_id=target.id, kind=RunKind.MAINTENANCE)

    with ThreadPoolExecutor(max_workers=4) as pool:
        late = pool.submit(late_writer)
        assert stale_ready.wait(5)
        deletion = pool.submit(cleanup)
        try:
            assert entered.wait(5)
            unrelated = pool.submit(launch, other.id)
            assert unrelated.result(timeout=2) == "launched"
            with pytest.raises(InvocationCancelled):
                pool.submit(launch, target.id).result(timeout=2)
            late.result(timeout=2)
            # A simultaneous retry cannot enter recovery or change operation ID.
            with Session(engine) as retry_session:
                concurrent = asyncio.run(ProjectCleanupService(retry_session, settings).delete(
                    target.id, confirmation=confirmation(target)
                ))
            assert concurrent.status == "blocked"
            assert "managed_storage_writer" in concurrent.blockers
            with Session(engine) as observer:
                fenced = ProjectRepository(observer).get(target.id)
                assert not fenced.is_active and fenced.cleanup_json["status"] == "pending"
                assert concurrent.operation_id == fenced.cleanup_json["operation_id"]
        finally:
            release.set()
        result = deletion.result(timeout=5)
    assert foreign.read_text() == "sentinel"
    assert result.status == ("incomplete" if fail else "deleted")
    if fail:
        assert result.retryable and result.error_code == "OSError"
        with Session(engine) as observer:
            row = ProjectRepository(observer).get(target.id)
            assert row.slug == target.slug and not row.is_active
            assert row.cleanup_json["operation_id"] == result.operation_id
            with pytest.raises(ConflictError):
                RunRepository(observer).start(project_id=target.id, kind=RunKind.MAINTENANCE)
        monkeypatch.undo()
        with Session(engine) as restarted:
            retry = asyncio.run(ProjectCleanupService(restarted, settings).delete(
                target.id, confirmation=confirmation(target)
            ))
        assert retry.status == "deleted" and retry.operation_id == result.operation_id
    assert not owned.exists()
    assert foreign.read_text() == "sentinel"
    assert not ProjectCleanupService(s, settings)._cleanup_lock_path(target.id).exists()


@pytest.mark.asyncio
async def test_pending_cleanup_rejects_claim_of_preexisting_request(fixture, monkeypatch):
    s, settings, target, _ = fixture
    request = AgentRequestRepository(s).create(
        project_id=target.id, request_key="before-fence", title="Fixture",
        source_kind="manual",
    ).data
    service = ProjectCleanupService(s, settings)

    def claim_after_fence(pid):
        with Session(s.get_bind()) as writer, pytest.raises(ConflictError):
            AgentRequestRepository(writer).claim(
                project_id=pid, request_id=request.id, claimed_by="fixture"
            )

    monkeypatch.setattr(service, "_clean_files", claim_after_fence)
    assert (await service.delete(target.id, confirmation=confirmation(target))).status == "deleted"


@pytest.mark.asyncio
@pytest.mark.parametrize("producer", [
    "request", "artifact", "resource", "profile", "browser_session", "schedule",
    "run", "plan", "action", "durable_claim", "finance",
])
async def test_actual_producer_entry_points_reject_committed_fence(
    fixture, monkeypatch, producer
):
    from types import SimpleNamespace

    from stackos.plugins.builtin_finance import FinanceConsole

    s, settings, target, _ = fixture
    profile = BrowserRepository(s).create_profile(
        project_id=target.id, profile_key="fixture", name="Fixture", metadata_json=None
    ).data
    service = ProjectCleanupService(s, settings)

    async def denied(pid):
        with Session(s.get_bind()) as writer:
            # Retain the real identity and profile rows across the fence.
            with pytest.raises(ConflictError) as failure:
                match producer:
                    case "request":
                        AgentRequestRepository(writer).create(
                            project_id=pid, request_key="late", title="Fixture"
                        )
                    case "artifact":
                        ArtifactRepository(writer).create(
                            project_id=pid, kind="evidence", uri="external:fixture"
                        )
                    case "resource":
                        ResourceRepository(writer).upsert_record(
                            project_id=pid, plugin_slug="core", resource_key="learning",
                            data_json={"body": "Fixture"},
                        )
                    case "profile":
                        BrowserRepository(writer).create_profile(
                            project_id=pid, profile_key="late", name="Late", metadata_json=None
                        )
                    case "browser_session":
                        repo = BrowserRepository(writer)
                        row = repo.get_profile(project_id=pid, profile_ref=profile.profile_ref)
                        repo.create_or_update_session(
                            project_id=pid, profile=row, metadata_json=None,
                            session_ref=repo.session_ref(
                                project_id=pid, profile_key="fixture", session_key="late"
                            ),
                        )
                    case "schedule":
                        ScheduledJobRepository(writer).set(
                            project_id=pid, kind="workflow", cron_expr="* * * * *"
                        )
                    case "run":
                        RunRepository(writer).start(project_id=pid, kind=RunKind.MAINTENANCE)
                    case "plan":
                        RunPlanRepository(writer).create(project_id=pid, run_plan_json={})
                    case "action":
                        await ActionRepository(writer).execute(
                            project_id=pid, action_ref="core.fixture", dry_run=True
                        )
                    case "durable_claim":
                        ActionRepository(writer).claim_durable_action_items(
                            project_id=pid, job_id=0
                        )
                    case "finance":
                        FinanceConsole(writer, settings).status(SimpleNamespace(project_id=pid))
            assert failure.value.data["project_id"] == pid
            assert failure.value.data["cleanup"]["status"] == "pending"
            # No denied call can persist anything from its partially read state.
            writer.rollback()

    def after_fence(pid):
        # The delete owner stays synchronous; run the real async entry point in
        # another fixture thread, without moving production cleanup off-loop.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(lambda: asyncio.run(denied(pid))).result(timeout=5)

    monkeypatch.setattr(service, "_clean_files", after_fence)
    result = await service.delete(target.id, confirmation=confirmation(target))
    assert result.status == "deleted"


@pytest.mark.asyncio
async def test_final_purge_rechecks_new_foreign_dependency(fixture, monkeypatch):
    from stackos.db.models import ProjectWorkflowTemplate, WorkflowTemplate

    s, settings, target, other = fixture
    template = WorkflowTemplate(
        project_id=target.id, key="fixture", name="Fixture", source="project"
    )
    s.add(template)
    s.commit()
    template_id = template.id
    service = ProjectCleanupService(s, settings)
    original = service._clean_files

    def late_dependency(pid):
        original(pid)
        with Session(s.get_bind()) as writer:
            writer.add(ProjectWorkflowTemplate(project_id=other.id, template_id=template_id))
            writer.commit()

    monkeypatch.setattr(service, "_clean_files", late_dependency)
    result = await service.delete(target.id, confirmation=confirmation(target))
    assert result.status == "incomplete" and result.error_code == "ConflictError"
    assert s.get(WorkflowTemplate, template_id) is not None
    assert not ProjectRepository(s).get(target.id).is_active


def test_admission_that_reserved_before_fence_wins_without_blocking_other_launches(fixture):
    s, settings, target, other = fixture
    engine = s.get_bind()
    admitted, release, cleanup_update = Event(), Event(), Event()
    cleanup_thread = []

    def observe_update(connection, cursor, statement, parameters, context, executemany):
        if cleanup_thread == [get_ident()] and statement.startswith("UPDATE projects"):
            cleanup_update.set()

    def writer():
        with Session(engine) as session:
            def before_commit(session, flush_context, instances):
                admitted.set()  # Run.start already reserved the project row.
                assert release.wait(10)

            event.listen(session, "before_flush", before_commit, once=True)
            return RunRepository(session).start(
                project_id=target.id, kind=RunKind.MAINTENANCE
            ).data.id

    def cleanup():
        cleanup_thread.append(get_ident())
        with Session(engine) as session:
            return asyncio.run(ProjectCleanupService(session, settings).delete(
                target.id, confirmation=confirmation(target)
            ))

    def unrelated_launch():
        def allowed():
            with Session(engine) as session:
                ProjectRepository(session).require_available(other.id)
                return True

        child = ManagedProcessControl(allowed).spawn(
            [sys.executable, "-c", "pass"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        child.communicate(timeout=5)
        assert child.returncode == 0

    event.listen(engine, "before_cursor_execute", observe_update)
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            admission = pool.submit(writer)
            assert admitted.wait(5)
            deletion = pool.submit(cleanup)
            try:
                assert cleanup_update.wait(5)
                pool.submit(unrelated_launch).result(timeout=2)
            finally:
                release.set()
            run_id = admission.result(timeout=5)
            result = deletion.result(timeout=5)
        assert result.status == "blocked" and "running_work" in result.blockers
        assert ProjectRepository(s).get(target.id).cleanup_json is None
        assert ProjectRepository(s).get(target.id).is_active
        RunRepository(s).finish(run_id, status="success")
        assert asyncio.run(ProjectCleanupService(s, settings).delete(
            target.id, confirmation=confirmation(target)
        )).status == "deleted"
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", observe_update)


def test_duplicate_request_releases_admission_reservation(fixture):
    s, _settings, target, other = fixture
    repo = AgentRequestRepository(s)
    first = repo.create(project_id=target.id, request_key="same", title="Fixture").data
    duplicate = repo.create(project_id=target.id, request_key="same", title="Fixture").data
    assert duplicate.id == first.id

    def unrelated_write():
        with Session(s.get_bind()) as writer:
            return AgentRequestRepository(writer).create(
                project_id=other.id, request_key="other", title="Fixture"
            ).data.project_id

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(unrelated_write)
        try:
            assert future.result(timeout=2) == other.id
        finally:
            s.rollback()
