"""Explicit project folder ownership; no session or binding-order inference."""

from pathlib import Path

import pytest
from sqlmodel import Session

from stackos.db.models import (
    AgentSession,
    Project,
    ScheduledJob,
    TaskTracker,
    TrackerItemStatus,
    TrackerSourceKind,
    TrackerTask,
    WorkspaceBinding,
)
from stackos.repositories.base import ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.workspaces import WorkspaceRepository


def test_initial_folder_setup_and_later_task_worktrees(session: Session, tmp_path: Path):
    primary = tmp_path / "primary"
    primary.mkdir()
    workspaces = WorkspaceRepository(session)
    setup = workspaces.bootstrap(cwd=str(primary))
    projects = ProjectRepository(session)
    assert projects.get(setup.project_id).primary_workspace_binding_id == setup.data.binding.id
    assert projects.get(setup.project_id).project_dir == str(primary)
    task = tmp_path / "task"
    task.mkdir()
    workspaces.connect(
        project_id=setup.project_id, repo_fingerprint="path:task", last_known_root=str(task)
    )
    started = workspaces.start_session(runtime="codex", cwd=str(task), repo_fingerprint="path:task")
    snapshot = session.get(AgentSession, started.data.id).model_dump()
    projects.update(setup.project_id, name="Renamed")
    assert projects.get(setup.project_id).project_dir == str(primary)
    assert session.get(AgentSession, started.data.id).model_dump() == snapshot
    # A reused Git identity or nested bootstrap cannot mutate the chosen root.
    with pytest.raises(ValidationError, match="cannot move implicitly"):
        workspaces.connect(
            project_id=setup.project_id,
            repo_fingerprint=setup.data.binding.repo_fingerprint,
            last_known_root=str(task),
        )
    nested = primary / "nested"
    nested.mkdir()
    with pytest.raises(ValidationError, match="cannot move implicitly"):
        workspaces.bootstrap(cwd=str(nested))
    assert projects.get(setup.project_id).project_dir == str(primary)


def test_existing_project_requires_explicit_choice_and_can_remain_folderless(
    session: Session, tmp_path: Path
):
    projects = ProjectRepository(session)
    project = projects.create(slug="existing", name="Existing", domain="existing.test").data
    workspaces = WorkspaceRepository(session)
    bindings = []
    for name in ("task-a", "primary", "task-b"):
        root = tmp_path / name
        root.mkdir()
        bindings.append(
            workspaces.connect(
                project_id=project.id, repo_fingerprint=f"path:{name}", last_known_root=str(root)
            ).data
        )
    assert projects.get(project.id).project_dir is None
    assert "project setup" in projects.get(project.id).project_folder_issue
    chosen = projects.update(project.id, primary_workspace_binding_id=bindings[1].id).data
    assert chosen.project_dir == str(tmp_path / "primary")
    assert chosen.primary_workspace_binding_id == bindings[1].id
    # Catalog operations keep the exact selected folder in their projections.
    assert projects.list(limit=1).items[0].project_dir == chosen.project_dir
    assert projects.move(project.id, placement="top").data.project_dir == chosen.project_dir
    unchanged = projects.update(project.id, name=project.name).data
    assert unchanged.project_dir == chosen.project_dir
    assert unchanged.updated_at == chosen.updated_at
    assert projects.update(project.id, primary_workspace_binding_id=None).data.project_dir is None
    global_session = workspaces.start_session(
        runtime="codex", global_session=True, project_id=project.id
    )
    assert global_session.data.workspace_binding_id is None
    assert projects.get(project.id).primary_workspace_binding_id is None


def test_setup_does_not_rewrite_saved_schedule_or_queued_running_snapshots(
    session: Session, tmp_path: Path
):
    projects = ProjectRepository(session)
    project = projects.create(slug="snapshots", name="Snapshots", domain="snapshot.test").data
    tracker = TaskTracker(
        project_id=project.id,
        key="default",
        name="Work",
        config_json={"delivery": {"host_key": "claude"}},
    )
    session.add(tracker)
    session.commit()
    tasks = []
    for status in (TrackerItemStatus.NOT_STARTED, TrackerItemStatus.IN_PROGRESS):
        task = TrackerTask(
            project_id=project.id,
            tracker_id=tracker.id,
            key=status.value,
            title=status.value,
            status=status,
            source_kind=TrackerSourceKind.MANUAL,
            metadata_json={
                "delivery": {
                    "project_dir": "/fixture/task",
                    "status": "queued" if status == TrackerItemStatus.NOT_STARTED else "running",
                    "host_key": "claude",
                    "agents": {"writer": {"instructions": "Frozen"}},
                }
            },
        )
        session.add(task)
        tasks.append(task)
    job = ScheduledJob(
        project_id=project.id,
        kind="old",
        cron_expr="* * * * *",
        enabled=False,
        execution_json={"project_dir": "/fixture/old", "host_key": "claude", "profile": "saved"},
    )
    session.add(job)
    session.commit()
    for row in [tracker, *tasks, job]:
        session.refresh(row)
    before = [row.model_dump() for row in [tracker, *tasks, job]]
    primary = (
        WorkspaceRepository(session)
        .connect(
            project_id=project.id, repo_fingerprint="path:primary", last_known_root=str(tmp_path)
        )
        .data
    )
    projects.update(project.id, primary_workspace_binding_id=primary.id)
    for row in [tracker, *tasks, job]:
        session.refresh(row)
    assert [row.model_dump() for row in [tracker, *tasks, job]] == before


def test_invalid_wrong_project_named_and_unavailable_binding(session: Session, tmp_path: Path):
    projects = ProjectRepository(session)
    first = projects.create(slug="first", name="First", domain="first.test").data
    second = projects.create(slug="second", name="Second", domain="second.test").data
    workspaces = WorkspaceRepository(session)
    wrong = workspaces.connect(
        project_id=second.id, repo_fingerprint="path:other", last_known_root=str(tmp_path)
    ).data
    named = workspaces.connect(project_id=first.id, workspace_alias="no-folder").data
    for bad in (wrong.id, named.id, 99999, True, "1", 0):
        with pytest.raises(ValidationError):
            projects.update(first.id, primary_workspace_binding_id=bad)
        assert projects.get(first.id).primary_workspace_binding_id is None
    root = tmp_path / "gone"
    root.mkdir()
    binding = workspaces.connect(
        project_id=first.id, repo_fingerprint="path:gone", last_known_root=str(root)
    ).data
    projects.update(first.id, primary_workspace_binding_id=binding.id)
    root.rmdir()
    assert projects.get(first.id).project_dir is None
    assert "unavailable" in projects.get(first.id).project_folder_issue
    root.mkdir()
    # Stale ownership must fail closed, even if an old primary reference remains.
    row = session.get(WorkspaceBinding, binding.id)
    row.project_id = second.id
    session.add(row)
    session.commit()
    assert projects.get(first.id).project_dir is None
    assert projects.get(first.id).project_folder_status == "conflict"
    assert session.get(Project, first.id).primary_workspace_binding_id == binding.id
    row.project_id = first.id
    row.last_known_root = "relative-folder"
    session.add(row)
    session.commit()
    assert projects.get(first.id).project_folder_status == "unavailable"
    assert projects.get(first.id).project_dir is None


def test_folder_states_are_distinct_and_reads_never_choose_or_repair_bindings(
    session: Session, tmp_path: Path
):
    projects = ProjectRepository(session)
    project = projects.create(slug="folder-states", name="Folder states", domain="states.test").data
    other = projects.create(slug="foreign-folder", name="Foreign", domain="foreign.test").data
    workspaces = WorkspaceRepository(session)
    workspaces.connect(project_id=project.id, workspace_alias="directoryless")
    assert projects.get(project.id).project_folder_status == "directoryless"
    assert "without a local folder" in projects.get(project.id).project_folder_issue
    root = tmp_path / "primary"
    root.mkdir()
    primary = workspaces.connect(
        project_id=project.id, repo_fingerprint="path:states", last_known_root=str(root)
    ).data
    assert projects.get(project.id).project_folder_status == "missing"
    assert projects.get(project.id).primary_workspace_binding_id is None
    chosen = projects.update(project.id, primary_workspace_binding_id=primary.id).data
    assert chosen.project_folder_status == "ready"
    assert chosen.project_dir == str(root)
    # A newer task tree must never replace the selected root, even when it disappears.
    latest = tmp_path / "latest"
    latest.mkdir()
    workspaces.connect(
        project_id=project.id, repo_fingerprint="path:latest", last_known_root=str(latest)
    )
    root.rmdir()
    unavailable = projects.get(project.id)
    assert unavailable.project_folder_status == "unavailable"
    assert unavailable.project_dir is None
    assert unavailable.primary_workspace_binding_id == primary.id
    root.mkdir()
    assert projects.get(project.id).project_folder_status == "ready"
    # Reproduce legacy conflicting rows that current binding writes reject.
    conflict = WorkspaceBinding(
        project_id=other.id, repo_fingerprint="path:legacy-conflict", last_known_root=str(root)
    )
    session.add(conflict)
    session.commit()
    assert projects.get(project.id).project_folder_status == "conflict"
    assert projects.get(project.id).project_dir is None
    assert "another project" in projects.get(project.id).project_folder_issue
    session.delete(conflict)
    session.commit()
    assert projects.get(project.id).project_dir == str(root)
    assert session.get(Project, project.id).primary_workspace_binding_id == primary.id


def test_stale_primary_reference_is_missing_or_conflicting_without_fallback(
    session: Session, tmp_path: Path
):
    projects = ProjectRepository(session)
    project = projects.create(slug="stale-folder", name="Stale", domain="stale.test").data
    row = session.get(Project, project.id)
    row.primary_workspace_binding_id = 99999
    # Inspect a corrupt legacy reference without disabling the current FK invariant.
    with session.no_autoflush:
        assert projects._out(row).project_folder_status == "missing"
    row.primary_workspace_binding_id = None
    foreign = projects.create(slug="foreign-primary", name="Foreign", domain="foreign.test").data
    binding = (
        WorkspaceRepository(session)
        .connect(
            project_id=foreign.id,
            repo_fingerprint="path:foreign-primary",
            last_known_root=str(tmp_path),
        )
        .data
    )
    row.primary_workspace_binding_id = binding.id
    session.add(row)
    session.commit()
    assert projects.get(project.id).project_folder_status == "conflict"
    assert projects.get(project.id).project_dir is None
