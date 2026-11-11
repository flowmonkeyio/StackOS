"""Destructive proof uses only this test's disposable DB/data/state roots."""

from __future__ import annotations

import asyncio
import fcntl
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import JSON, Boolean, DateTime, Integer, LargeBinary, delete
from sqlmodel import Session, SQLModel, select

from stackos.browser.runtime import BrowserRuntime, _profile_lock, browser_profile_dir
from stackos.config import Settings
from stackos.db.connection import make_engine
from stackos.db.models import (
    ActionCall,
    ActionCallStatus,
    AgentSession,
    Artifact,
    DurableActionJob,
    DurableActionJobStatus,
    Project,
    RunKind,
    TrackerTask,
)
from stackos.project_cleanup import ProjectCleanupService
from stackos.repositories.base import ConflictError, NotFoundError, ValidationError
from stackos.repositories.projects import ProjectRepository, ScheduledJobRepository
from stackos.repositories.runs import RunRepository


@pytest.fixture
def fixture(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    settings.ensure_dirs()
    engine = make_engine(settings.db_path)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        projects = ProjectRepository(session)
        target = projects.create(slug="disposable-cleanup", name="Disposable", domain="test").data
        other = projects.create(slug="disposable-survivor", name="Survivor", domain="test").data
        yield session, settings, target, other
    engine.dispose()


def confirmation(target):
    return {"permanent": True, "project_id": target.id, "project_slug": target.slug}


def put(path: Path, value="sentinel"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    return path


@pytest.mark.asyncio
async def test_identity_validation_precedes_mutation_and_missing_never_resolves_by_name(fixture):
    s, settings, target, other = fixture
    service = ProjectCleanupService(s, settings)
    sentinel = put(settings.data_dir / "projects" / str(target.id) / "keep")
    for wrong in (
        {},
        {**confirmation(target), "permanent": False},
        {**confirmation(target), "project_id": other.id},
        {**confirmation(target), "project_slug": other.slug},
    ):
        with pytest.raises(ValidationError):
            await service.delete(target.id, confirmation=wrong)
        assert sentinel.exists()
        assert ProjectRepository(s).get(target.id).cleanup_json is None
    assert (await service.delete(target.id, confirmation=confirmation(target))).status == "deleted"
    replacement = (
        ProjectRepository(s).create(slug=target.slug, name=target.name, domain="test").data
    )
    assert replacement.id > target.id
    assert (await service.delete(target.id, confirmation=confirmation(target))).status == "missing"
    assert ProjectRepository(s).get(replacement.id).slug == target.slug
    with pytest.raises(NotFoundError):
        ProjectRepository(s).require_available(target.id)


@pytest.mark.asyncio
async def test_owned_files_removed_shared_and_external_bytes_preserved(fixture, tmp_path):
    s, settings, target, other = fixture
    service = ProjectCleanupService(s, settings)
    roots = service._roots(target.id)
    owned = [put(root / "owned.bin") for root in roots]
    survivors = [put(tmp_path / name / "keep") for name in ("repository", "export", "backup")]
    survivors += [put(root / "keep") for root in service._roots(other.id)]
    (roots[0] / "external-link").symlink_to(survivors[0].parent, target_is_directory=True)
    media = put(settings.generated_assets_dir / "provider" / "owned.png")
    shared = put(settings.generated_assets_dir / "provider" / "shared.png")
    arbitrary = put(settings.generated_assets_dir / "provider" / "unproven.png")
    for pid, path, provenance in (
        (target.id, media, {"source": "aignc-action"}),
        (target.id, shared, {"source": "aignc-action"}),
        (other.id, shared, None),
        (target.id, arbitrary, None),
    ):
        s.add(
            Artifact(
                project_id=pid,
                kind="image",
                uri="/generated-assets/"
                + path.relative_to(settings.generated_assets_dir).as_posix(),
                provenance_json=provenance,
            )
        )
    s.commit()
    outcome = await service.delete(target.id, confirmation=confirmation(target))
    assert outcome.status == "deleted"
    assert not any(path.exists() for path in [*owned, media])
    assert all(path.read_text() == "sentinel" for path in [*survivors, shared, arbitrary])
    assert s.exec(select(Artifact).where(Artifact.project_id == target.id)).all() == []
    assert len(s.exec(select(Artifact).where(Artifact.project_id == other.id)).all()) == 1


@pytest.mark.asyncio
async def test_cleanup_never_follows_directory_replaced_by_symlink(fixture, tmp_path, monkeypatch):
    import os

    s, settings, target, _ = fixture
    service = ProjectCleanupService(s, settings)
    owned = service._roots(target.id)[0] / "replace-during-cleanup"
    put(owned / "owned")
    external = put(tmp_path / "repository" / "keep")
    original = Path.iterdir
    original_open = os.open
    swapped = False

    def swap():
        nonlocal swapped
        if not swapped:
            swapped = True
            owned.rename(owned.with_name("retired-owned"))
            owned.symlink_to(external.parent, target_is_directory=True)

    def swap_before_listing(path):
        if path == owned:
            swap()
        return original(path)

    def swap_before_open(path, *args, **kwargs):
        if path == owned.name and kwargs.get("dir_fd") is not None:
            swap()
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "iterdir", swap_before_listing)
    monkeypatch.setattr(os, "open", swap_before_open)
    await service.delete(target.id, confirmation=confirmation(target))
    assert swapped
    assert external.read_text() == "sentinel"


@pytest.mark.asyncio
async def test_active_action_durable_and_admitted_delivery_are_blockers(fixture):
    s, settings, target, _ = fixture
    _seed_graph(s, target.id)
    service = ProjectCleanupService(s, settings)
    call = s.exec(select(ActionCall).where(ActionCall.project_id == target.id)).first()
    job = s.exec(select(DurableActionJob).where(DurableActionJob.project_id == target.id)).first()
    call.status = ActionCallStatus.RUNNING
    s.add(call)
    s.commit()
    assert (
        "action_call"
        in (await service.delete(target.id, confirmation=confirmation(target))).blockers
    )
    call.status = ActionCallStatus.SUCCESS
    s.add(call)
    for state in (
        DurableActionJobStatus.SCHEDULED,
        DurableActionJobStatus.RUNNING,
        DurableActionJobStatus.PAUSED,
        DurableActionJobStatus.UNKNOWN_HOLD,
    ):
        job.state = state
        s.add(job)
        s.commit()
        assert (
            "durable_action"
            in (await service.delete(target.id, confirmation=confirmation(target))).blockers
        )
    job.state = DurableActionJobStatus.COMPLETED
    task = s.exec(select(TrackerTask).where(TrackerTask.project_id == target.id)).first()
    task.metadata_json = {"delivery": {"status": "queued"}}
    s.add_all([job, task])
    s.commit()
    assert (
        "admitted_delivery"
        in (await service.delete(target.id, confirmation=confirmation(target))).blockers
    )
    task.metadata_json = {"delivery": {"status": "stopped"}}
    s.add(task)
    s.commit()
    assert (await service.delete(target.id, confirmation=confirmation(target))).status == "deleted"


@pytest.mark.asyncio
async def test_finance_publication_and_mappings_retire_without_touching_legacy_source(
    fixture, tmp_path
):
    import json

    from stackos.plugins.builtin_finance import FinanceConsole

    s, settings, target, other = fixture
    console = FinanceConsole(s, settings)
    state = console._new_state(s.get(Project, target.id), 1)
    state["publication"] = {"token": "a" * 32}
    root = settings.data_dir / "projects" / str(target.id)
    put(root / "finance-state.json", json.dumps(state))
    stage = console.config_dir / "staging" / ("a" * 32)
    put(stage / ".writer.lock")
    put(stage / "finance.json")
    legacy = put(tmp_path / "repository" / "finance.json")
    entries = [
        {
            "project_id": p.id,
            "access_id": f"access-{p.id}",
            "workspace_ref": "legacy",
            "canonical_root": str(legacy.parent),
            "root_device": 1,
            "root_inode": 1,
            "generation": 1,
        }
        for p in (target, other)
    ]
    put(
        console.config_path,
        json.dumps(
            {
                "format": "finance-access-v1",
                "entries": entries,
                "intents": {"target": {"project_id": target.id}, "other": {"project_id": other.id}},
            }
        ),
    )
    result = await ProjectCleanupService(s, settings).delete(
        target.id, confirmation=confirmation(target)
    )
    assert result.status == "deleted"
    assert not stage.exists() and not root.exists()
    assert legacy.read_text() == "sentinel"
    doc = json.loads(console.config_path.read_text())
    assert doc["entries"] == entries[1:]
    assert doc["intents"] == {"other": {"project_id": other.id}}


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["files", "database"])
async def test_partial_failure_retains_sources_fences_writes_and_retries_after_restart(
    fixture, monkeypatch, phase
):
    s, settings, target, other = fixture
    service = ProjectCleanupService(s, settings)
    evidence = Artifact(project_id=target.id, kind="evidence", uri="external:preserved")
    s.add(evidence)
    s.add(AgentSession(project_id=target.id, runtime="stale-client"))
    s.commit()
    artifact_id = evidence.id
    sentinel = put(service._roots(target.id)[0] / "owned")
    method = "_clean_files" if phase == "files" else "_purge"
    original = getattr(service, method)

    def fail(pid):
        if phase == "files":
            sentinel.unlink()  # Partial file removal is never represented as rollback.
        else:
            s.exec(delete(AgentSession).where(AgentSession.project_id == pid))
        raise OSError("fixture-only injected failure")

    monkeypatch.setattr(service, method, fail)
    result = await service.delete(target.id, confirmation=confirmation(target))
    assert result.status == "incomplete" and result.retryable
    assert not sentinel.exists()
    assert s.get(Artifact, artifact_id) is not None
    assert s.exec(select(AgentSession).where(AgentSession.project_id == target.id)).first()
    assert (
        ProjectRepository(s).list(active_only=False).items[0].cleanup_json["status"] == "incomplete"
    )
    with pytest.raises(ConflictError):
        RunRepository(s).start(project_id=target.id, kind=RunKind.MAINTENANCE)
    s.rollback()
    with pytest.raises(ConflictError):
        ScheduledJobRepository(s).set(project_id=target.id, kind="workflow", cron_expr="* * * * *")
    s.rollback()
    # New Session/service models daemon restart; no transient retry registry exists.
    monkeypatch.setattr(service, method, original)
    with Session(s.get_bind()) as restarted:
        retry = await ProjectCleanupService(restarted, settings).delete(
            target.id, confirmation=confirmation(target)
        )
    assert retry.status == "deleted" and retry.operation_id == result.operation_id
    s.expire_all()
    assert ProjectRepository(s).get(other.id).id == other.id


@pytest.mark.asyncio
async def test_storage_and_actual_native_browser_admission_block_without_abort(
    fixture, monkeypatch
):
    s, settings, target, _ = fixture
    runtime = BrowserRuntime(data_dir=settings.data_dir)
    service = ProjectCleanupService(s, settings, browser=runtime)
    lockfile = put(service._roots(target.id)[0] / ".writer.lock")
    with lockfile.open("r+") as writer:
        fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        blocked = await service.delete(target.id, confirmation=confirmation(target))
        assert "managed_storage_writer" in blocked.blockers
    from stackos.host_mcp.service import (
        ActiveInvocation,
        AgentExecutionScope,
        _register,
        _unregister,
    )

    invocation = ActiveInvocation(
        invocation_id="cleanup-disposable-native",
        host_key="codex",
        model=None,
        started_at=datetime.now().isoformat(),
        scope=AgentExecutionScope(
            project_id=target.id,
            task_key="disposable-native",
            ticket_key="fixture",
            agent="fixture-only",
        ),
    )
    _register(invocation)
    try:
        blocked = await service.delete(target.id, confirmation=confirmation(target))
        assert "native_invocation" in blocked.blockers
        assert not invocation.cancelled.is_set()
        assert not invocation.process_control.cancelled.is_set()
    finally:
        _unregister(invocation)
    profile = browser_profile_dir(settings.data_dir, project_id=target.id, profile_key="fixture")
    async with _profile_lock(profile):
        blocked = await service.delete(target.id, confirmation=confirmation(target))
        assert "browser_session" in blocked.blockers

    async def discover(**kwargs):
        return {"fixture-owned-unhealthy": SimpleNamespace(owned=True, healthy=False)}

    monkeypatch.setattr(runtime, "discover_sessions", discover)
    blocked = await service.delete(target.id, confirmation=confirmation(target))
    assert "browser_session" in blocked.blockers
    assert ProjectRepository(s).get(target.id).cleanup_json is None


@pytest.mark.asyncio
async def test_pending_state_beats_admission_waiting_on_existing_database_writer(fixture):
    s, settings, target, _ = fixture
    ready = __import__("threading").Event()
    released = __import__("threading").Event()
    service = ProjectCleanupService(s, settings)
    pool = ThreadPoolExecutor(max_workers=1)

    def late_admission():
        with Session(s.get_bind()) as writer:
            # Load identity before cleanup; a stale identity-map object cannot admit.
            writer.get(Project, target.id)
            ready.set()
            released.wait(5)
            with pytest.raises((ConflictError, NotFoundError)):
                RunRepository(writer).start(project_id=target.id, kind=RunKind.MAINTENANCE)

    future = pool.submit(late_admission)
    assert ready.wait(5)
    original = service._clean_files

    def after_pending(pid):
        released.set()
        original(pid)

    service._clean_files = after_pending
    result = await service.delete(target.id, confirmation=confirmation(target))
    assert result.status == "deleted"
    await asyncio.to_thread(future.result, 5)
    pool.shutdown()


def _seed_graph(session, project_id):
    """Populate the real FK graph, including indirect revision/history tables.

    This deliberately exercises DB integrity independent of public API validation.
    Catalog parents are shared fixture rows. Every direct project owner and every
    cascade child gets one populated row; enums use terminal states for cleanup.
    """
    tables = SQLModel.metadata.tables
    seeded = {"projects": project_id}
    owned = {t.name for t in tables.values() if "project_id" in t.c}
    owned.add("oauth_states")
    while True:
        expanded = owned | {
            t.name
            for t in tables.values()
            for fk in t.foreign_keys
            if fk.column.table.name in owned and fk.ondelete == "CASCADE"
        }
        if expanded == owned:
            break
        owned = expanded

    def seed(name):
        if name in seeded:
            return seeded[name]
        table = tables[name]
        values = {}
        for col in table.c:
            if col.primary_key:
                continue
            if col.name in {"project_id", "attach_project_id"}:
                values[col.name] = project_id
            elif col.foreign_keys:
                fk = next(iter(col.foreign_keys))
                if fk.column.table.name == name:
                    continue
                values[col.name] = seed(fk.column.table.name)
            elif col.nullable or col.default is not None:
                continue
            elif hasattr(col.type, "enums"):
                choices = col.type.enums
                values[col.name] = next(
                    (v for v in ("success", "completed", "resolved", "stopped") if v in choices),
                    choices[-1],
                )
            elif isinstance(col.type, JSON):
                values[col.name] = {}
            elif isinstance(col.type, DateTime):
                values[col.name] = datetime(2026, 1, 1)
            elif isinstance(col.type, Boolean):
                values[col.name] = False
            elif isinstance(col.type, Integer):
                values[col.name] = 1
            elif isinstance(col.type, LargeBinary):
                values[col.name] = b"fixture-only"
            else:
                values[col.name] = "fixture-" + col.name
        # Exercise cleanup with inactive stored work, rather than active leases.
        if name == "durable_action_jobs":
            values["state"] = DurableActionJobStatus.COMPLETED
        if name == "action_calls":
            values["status"] = ActionCallStatus.SUCCESS
        if name == "runs":
            values["status"] = "success"
        result = session.execute(table.insert().values(**values))
        seeded[name] = result.inserted_primary_key[0]
        return seeded[name]

    for name in sorted(owned - {"projects"}):
        seed(name)
    session.commit()
    return owned, seeded


@pytest.mark.asyncio
async def test_complete_ownership_inventory_and_atomic_purge_preserve_catalog(fixture):
    s, settings, target, other = fixture
    owned, seeded = _seed_graph(s, target.id)
    global_counts = {
        t.name: len(s.execute(select(t)).all())
        for t in SQLModel.metadata.tables.values()
        if t.name not in owned and t.name != "projects"
    }
    service = ProjectCleanupService(s, settings)
    result = await service.delete(target.id, confirmation=confirmation(target))
    assert result.status == "deleted", result
    for name in owned - {"projects"}:
        table = SQLModel.metadata.tables[name]
        assert s.execute(select(table)).all() == [], name
    assert ProjectRepository(s).get(other.id).name == other.name
    for name, count in global_counts.items():
        assert len(s.execute(select(SQLModel.metadata.tables[name])).all()) == count, name
    assert s.connection().exec_driver_sql("PRAGMA foreign_key_check").all() == []
    assert "durable_action_artifacts" in seeded
