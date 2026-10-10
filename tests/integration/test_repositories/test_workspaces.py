"""Workspace binding repository tests for the plugin-first install surface."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, select

from stackos.db.connection import make_engine
from stackos.db.models import AgentSession, Project, WorkspaceBinding
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.workspaces import WorkspaceRepository


@pytest.mark.parametrize("selected", [True, False])
@pytest.mark.parametrize("cwd", [None, "/", "/tmp/hi", "/tmp/Claude/scratch-workspaces/hi"])
def test_global_startup_has_no_workspace_or_incidental_project(
    session: Session, selected: bool, cwd: str | None
) -> None:
    target = _create_project(session)
    out = WorkspaceRepository(session).start_session(
        runtime="codex",
        cwd=cwd,
        global_session=True,
        project_id=target if selected else None,
        auto_bootstrap=True,
    )
    assert out.project_id == (target if selected else None)
    assert out.data.global_session is True
    assert out.data.workspace_binding_id is None
    assert out.data.cwd is None and out.data.repo_fingerprint is None
    assert out.data.git_remote_url is None
    assert out.data.setup_state["workspace_bound"] is False
    assert out.data.setup_state["project_scoped_tools_usable"] is selected
    assert out.data.auto_bootstrap is False
    assert len(session.exec(select(Project)).all()) == 1
    assert session.exec(select(WorkspaceBinding)).all() == []


@pytest.mark.parametrize("global_value", [None, False])
def test_unbound_non_global_project_id_refuses_before_writes(
    session: Session, global_value: bool | None
) -> None:
    target = _create_project(session)
    kwargs = {} if global_value is None else {"global_session": global_value}
    with pytest.raises(ValidationError, match="declare global_session"):
        WorkspaceRepository(session).start_session(
            runtime="codex", cwd="/tmp/hi", project_id=target, **kwargs
        )
    assert len(session.exec(select(Project)).all()) == 1
    assert session.exec(select(WorkspaceBinding)).all() == []
    assert session.exec(select(AgentSession)).all() == []


@pytest.mark.parametrize("cwd", [None, "/", "/Applications/StackOS.app/Contents/Resources"])
def test_folderless_startup_can_select_project_directly(session: Session, cwd: str | None) -> None:
    target = _create_project(session)
    result = WorkspaceRepository(session).start_session(runtime="agent", cwd=cwd, project_id=target)
    assert result.project_id == target
    assert result.data.global_session
    assert result.data.workspace_binding_id is None


def test_global_startup_invalid_or_conflicting_selector_has_no_writes(session: Session) -> None:
    target = _create_project(session)
    repo = WorkspaceRepository(session)
    with pytest.raises(NotFoundError):
        repo.start_session(
            runtime="codex", cwd="/tmp/hi", global_session=True, project_id=target + 1
        )
    with pytest.raises(ValidationError):
        repo.start_session(
            runtime="codex", global_session=True, project_id=target, workspace_alias="a"
        )
    assert session.exec(select(AgentSession)).all() == []
    assert session.exec(select(WorkspaceBinding)).all() == []


def test_global_startup_keeps_archive_access_and_rejects_deleted_saved_id(session: Session) -> None:
    projects = ProjectRepository(session)
    target = _create_project(session)
    projects.delete(target)
    repo = WorkspaceRepository(session)
    assert (
        repo.start_session(runtime="agent", global_session=True, project_id=target).project_id
        == target
    )
    projects.delete(target, hard=True)
    replacement = _create_project(session)
    assert replacement > target
    before = len(session.exec(select(AgentSession)).all())
    with pytest.raises(NotFoundError):
        repo.start_session(runtime="agent", global_session=True, project_id=target)
    assert len(session.exec(select(AgentSession)).all()) == before
    assert session.exec(select(WorkspaceBinding)).all() == []


def test_bound_startup_project_must_match_and_cannot_downgrade(session: Session) -> None:
    repo = WorkspaceRepository(session)
    bound = repo.bootstrap(cwd="/tmp/bound")
    other = _create_project(session)
    original = repo.list_bindings()[0].model_dump()
    for arguments in ({"global_session": True}, {"project_id": other}):
        with pytest.raises(ValidationError):
            repo.start_session(runtime="codex", cwd="/tmp/bound", **arguments)
    assert repo.list_bindings()[0].model_dump() == original
    assert session.exec(select(AgentSession)).all() == []
    result = repo.start_session(runtime="codex", cwd="/tmp/bound", project_id=bound.project_id)
    assert result.project_id == bound.project_id
    assert not result.data.global_session


def test_global_startup_rechecks_binding_after_preflight_with_two_connections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine(tmp_path / "race.db")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as reader, Session(engine) as writer:
        target = _create_project(writer)
        repo = WorkspaceRepository(reader)
        resolve = repo.resolve
        checks = []

        def raced_resolve(**kwargs):
            result = resolve(**kwargs)
            checks.append(result.project_id)
            if len(checks) == 1:
                WorkspaceRepository(writer).connect(
                    project_id=target, repo_fingerprint="path:race", last_known_root="/tmp/race"
                )
            return result

        monkeypatch.setattr(repo, "resolve", raced_resolve)
        with pytest.raises(ValidationError, match="already bound"):
            repo.start_session(runtime="codex", cwd="/tmp/race", global_session=True)
        assert checks == [None, target]
        assert reader.exec(select(AgentSession)).all() == []
        assert len(reader.exec(select(WorkspaceBinding)).all()) == 1
    engine.dispose()


def test_global_startup_lookup_failure_does_not_accept_session(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = WorkspaceRepository(session)
    resolve = repo.resolve
    calls = 0

    def failed_recheck(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("lookup unavailable")
        return resolve(**kwargs)

    monkeypatch.setattr(repo, "resolve", failed_recheck)
    with pytest.raises(RuntimeError, match="lookup unavailable"):
        repo.start_session(runtime="agent", cwd="/tmp/hi", global_session=True)
    assert session.exec(select(AgentSession)).all() == []
    assert session.exec(select(Project)).all() == []


def _create_project(session: Session, slug: str = "workspace-site") -> int:
    env = ProjectRepository(session).create(
        slug=slug,
        name="Workspace Site",
        domain="workspace.example",
        locale="en-US",
    )
    return env.data.id


def test_resolve_unknown_workspace_requests_connect(session: Session) -> None:
    project_id = _create_project(session)
    repo = WorkspaceRepository(session)

    resolution = repo.resolve(repo_fingerprint="git:unknown")

    assert resolution.needs_connect is True
    assert resolution.project_id is None
    assert resolution.binding is None
    assert resolution.next_step is not None
    assert resolution.next_step["recommended_tool"] == "workspace.bootstrap"
    assert [project.id for project in resolution.candidate_projects] == [project_id]


def test_connect_resolves_by_fingerprint_and_git_remote(session: Session) -> None:
    project_id = _create_project(session)
    repo = WorkspaceRepository(session)

    created = repo.connect(
        project_id=project_id,
        repo_fingerprint="git:abc123",
        git_remote_url="git@github.com:org/site.git",
        normalized_repo_name="org/site",
        last_known_root="/tmp/site",
        framework="nuxt",
        content_model_json={"primary_resource": "content-piece"},
    )

    by_fingerprint = repo.resolve(repo_fingerprint="git:abc123")
    by_remote = repo.resolve(git_remote_url="git@github.com:org/site.git")

    assert created.project_id == project_id
    assert by_fingerprint.needs_connect is False
    assert by_fingerprint.project_id == project_id
    assert by_fingerprint.binding is not None
    assert by_fingerprint.binding.framework == "nuxt"
    assert by_remote.project_id == project_id


def test_bootstrap_creates_project_once_for_workspace_root(session: Session) -> None:
    repo = WorkspaceRepository(session)

    first = repo.bootstrap(
        cwd="/tmp/revtrix",
        git_remote_url="git@github.com:org/revtrix.git",
        framework="nuxt",
    )
    second = repo.bootstrap(
        cwd="/tmp/revtrix",
        git_remote_url="git@github.com:org/revtrix.git",
    )
    bindings = repo.list_bindings(project_id=first.project_id)

    assert first.data.project_was_created is True
    assert first.data.binding_was_created is True
    assert first.data.project.slug == "revtrix"
    assert first.data.binding.framework == "nuxt"
    assert second.data.project_was_created is False
    assert second.data.binding_was_created is False
    assert second.data.project_id == first.data.project_id
    assert second.data.binding.id == first.data.binding.id
    assert [binding.id for binding in bindings] == [first.data.binding.id]


def test_start_session_autobootstrap_reuses_existing_project_slug(session: Session) -> None:
    existing = ProjectRepository(session).create(
        slug="revtrix",
        name="Revtrix",
        domain="revtrix.example",
        locale="en-US",
    )
    repo = WorkspaceRepository(session)

    started = repo.start_session(
        runtime="codex",
        cwd="/tmp/revtrix",
        git_remote_url="git@github.com:org/revtrix.git",
    )

    assert started.project_id == existing.data.id
    assert started.data.project_id == existing.data.id
    assert started.data.needs_connect is False
    assert started.data.auto_bootstrap is True
    assert started.data.project_was_created is False
    assert started.data.binding_was_created is True
    assert repo.list_bindings(project_id=existing.data.id)[0].last_known_root == str(
        Path("/tmp/revtrix").resolve()
    )


@pytest.mark.parametrize("folder_name", ["Resources", "Contents", "MacOS", "Project"])
def test_bootstrap_rejects_generic_inferred_project_name(
    session: Session,
    folder_name: str,
) -> None:
    repo = WorkspaceRepository(session)

    with pytest.raises(ValidationError, match="project_name or project_slug is required"):
        repo.bootstrap(cwd=f"/tmp/{folder_name}")

    assert repo.list_bindings() == []


def test_bootstrap_rejects_inferred_name_without_slug_content(session: Session) -> None:
    repo = WorkspaceRepository(session)

    with pytest.raises(ValidationError, match="project_name or project_slug is required"):
        repo.bootstrap(cwd="/tmp/---")

    assert repo.list_bindings() == []


def test_bootstrap_allows_explicit_project_name_for_generic_folder(session: Session) -> None:
    repo = WorkspaceRepository(session)

    bootstrapped = repo.bootstrap(
        cwd="/tmp/Resources",
        project_name="Acme Operations",
    )

    assert bootstrapped.data.project.name == "Acme Operations"
    assert bootstrapped.data.project.slug == "acme-operations"
    assert bootstrapped.data.binding.last_known_root == str(Path("/tmp/Resources").resolve())


def test_bootstrap_creates_named_workspace_without_directory(session: Session) -> None:
    repo = WorkspaceRepository(session)

    first = repo.bootstrap(project_name="Flowmonkey", workspace_alias="flowmonkey")
    second = repo.bootstrap(workspace_alias="flowmonkey")
    resolved = repo.resolve(workspace_alias="flowmonkey")

    assert first.data.project.name == "Flowmonkey"
    assert first.data.project.slug == "flowmonkey"
    assert first.data.binding.binding_kind == "named"
    assert first.data.binding.workspace_alias == "flowmonkey"
    assert first.data.binding.repo_fingerprint == "workspace:flowmonkey"
    assert first.data.binding.last_known_root is None
    assert second.data.project_was_created is False
    assert second.data.binding_was_created is False
    assert second.data.project_id == first.data.project_id
    assert resolved.project_id == first.data.project_id
    assert resolved.needs_connect is False


def test_bootstrap_project_id_without_directory_creates_named_workspace(
    session: Session,
) -> None:
    project_id = _create_project(session, slug="stackos-local")
    repo = WorkspaceRepository(session)

    bootstrapped = repo.bootstrap(project_id=project_id)
    second = repo.bootstrap(project_id=project_id)

    assert bootstrapped.project_id == project_id
    assert bootstrapped.data.project_was_created is False
    assert bootstrapped.data.binding_was_created is True
    assert bootstrapped.data.binding.binding_kind == "named"
    assert bootstrapped.data.binding.workspace_alias == "stackos-local"
    assert bootstrapped.data.binding.repo_fingerprint == "workspace:stackos-local"
    assert bootstrapped.data.binding.last_known_root is None
    assert second.data.project_was_created is False
    assert second.data.binding_was_created is False
    assert second.data.binding.id == bootstrapped.data.binding.id


def test_start_session_without_identity_lists_named_workspaces_without_autobind(
    session: Session,
) -> None:
    repo = WorkspaceRepository(session)
    named = repo.bootstrap(project_name="Flowmonkey", workspace_alias="flowmonkey")

    started = repo.start_session(runtime="claude-desktop", auto_bootstrap=True)

    assert started.project_id is None
    assert started.data.project_id is None
    assert started.data.workspace_binding_id is None
    assert started.data.needs_connect is True
    assert repo.list_bindings(project_id=named.project_id)[0].workspace_alias == "flowmonkey"
    assert [candidate.workspace_alias for candidate in started.data.candidate_workspaces] == [
        "flowmonkey"
    ]
    assert started.data.next_step is not None
    assert started.data.next_step["status"] == "project_selection_required"
    assert "last-used" in started.data.next_step["why"]


def test_connect_named_workspace_reuses_alias_and_preserves_rebind_guard(
    session: Session,
) -> None:
    first_project_id = _create_project(session, slug="flowmonkey")
    second_project_id = _create_project(session, slug="other-flowmonkey")
    repo = WorkspaceRepository(session)

    connected = repo.connect(project_id=first_project_id, workspace_alias="flowmonkey")

    assert connected.data.binding_kind == "named"
    assert connected.data.workspace_alias == "flowmonkey"
    assert connected.data.last_known_root is None
    assert repo.resolve(workspace_alias="flowmonkey").project_id == first_project_id

    with pytest.raises(ValidationError, match="rebind_existing=true") as excinfo:
        repo.connect(project_id=second_project_id, workspace_alias="flowmonkey")

    assert excinfo.value.data["current_project_id"] == first_project_id
    assert excinfo.value.data["requested_project_id"] == second_project_id


def test_named_workspace_fingerprint_is_canonicalized_and_validated(session: Session) -> None:
    project_id = _create_project(session, slug="flowmonkey")
    repo = WorkspaceRepository(session)

    connected = repo.connect(project_id=project_id, repo_fingerprint="workspace:FlowMonkey")

    assert connected.data.repo_fingerprint == "workspace:flowmonkey"
    assert connected.data.workspace_alias == "flowmonkey"

    with pytest.raises(ValidationError, match="usable business alias"):
        repo.connect(project_id=project_id, repo_fingerprint="workspace:Resources")


def test_workspace_alias_rejects_non_named_repo_fingerprint_mix(session: Session) -> None:
    project_id = _create_project(session, slug="flowmonkey")
    repo = WorkspaceRepository(session)

    with pytest.raises(ValidationError, match="cannot be combined"):
        repo.connect(
            project_id=project_id,
            repo_fingerprint="path:abc123",
            workspace_alias="flowmonkey",
        )

    with pytest.raises(ValidationError, match="cannot be combined"):
        repo.bootstrap(
            project_name="Flowmonkey",
            repo_fingerprint="git:abc123",
            workspace_alias="flowmonkey",
        )


def test_bootstrap_rejects_app_bundle_workspace_root(session: Session) -> None:
    repo = WorkspaceRepository(session)

    with pytest.raises(ValidationError, match="usable project directory"):
        repo.bootstrap(
            cwd="/Applications/StackOS.app/Contents/Resources",
            project_name="Acme Operations",
        )

    assert repo.list_bindings() == []


def test_bootstrap_can_bind_existing_project_by_slug(session: Session) -> None:
    project_id = _create_project(session, slug="existing-site")
    repo = WorkspaceRepository(session)

    bootstrapped = repo.bootstrap(
        project_slug="existing-site",
        cwd="/tmp/existing-site",
    )

    assert bootstrapped.project_id == project_id
    assert bootstrapped.data.project_was_created is False
    assert bootstrapped.data.binding_was_created is True
    assert bootstrapped.data.project.slug == "existing-site"
    assert bootstrapped.data.project.is_active is True


def test_reconnect_preserves_omitted_detected_profile_fields(session: Session) -> None:
    project_id = _create_project(session)
    repo = WorkspaceRepository(session)

    first = repo.connect(
        project_id=project_id,
        repo_fingerprint="git:abc123",
        git_remote_url="git@github.com:org/site.git",
        framework="nuxt",
        content_model_json={"primary_resource": "content-piece"},
    )
    second = repo.connect(project_id=project_id, repo_fingerprint="git:abc123")

    assert second.data.id == first.data.id
    assert second.data.git_remote_url == "git@github.com:org/site.git"
    assert second.data.framework == "nuxt"
    assert second.data.content_model_json == {"primary_resource": "content-piece"}


def test_connect_requires_explicit_rebind_for_existing_project_move(session: Session) -> None:
    first_project_id = _create_project(session, slug="first-workspace-site")
    second_project_id = _create_project(session, slug="second-workspace-site")
    repo = WorkspaceRepository(session)
    first = repo.connect(
        project_id=first_project_id,
        repo_fingerprint="git:abc123",
        git_remote_url="git@github.com:org/site.git",
        framework="nuxt",
    )

    with pytest.raises(ValidationError) as excinfo:
        repo.connect(project_id=second_project_id, repo_fingerprint="git:abc123")

    assert "rebind_existing=true" in str(excinfo.value)
    assert excinfo.value.data == {
        "binding_id": first.data.id,
        "repo_fingerprint": "git:abc123",
        "current_project_id": first_project_id,
        "requested_project_id": second_project_id,
    }

    moved = repo.connect(
        project_id=second_project_id,
        repo_fingerprint="git:abc123",
        rebind_existing=True,
    )

    assert moved.data.id == first.data.id
    assert moved.data.project_id == second_project_id
    assert moved.data.git_remote_url == "git@github.com:org/site.git"
    assert moved.data.framework == "nuxt"


def test_update_profile_and_start_session_attach_binding(session: Session) -> None:
    project_id = _create_project(session)
    repo = WorkspaceRepository(session)
    binding = repo.connect(project_id=project_id, repo_fingerprint="git:abc123")

    updated = repo.update_profile(
        binding.data.id,
        framework="astro",
        content_model_json={"entry_collection": "posts"},
    )
    started = repo.start_session(
        runtime="codex",
        cwd="/tmp/site",
        repo_fingerprint="git:abc123",
        thread_id="thread-1",
        client_session_id="session-1",
    )

    assert updated.data.framework == "astro"
    assert updated.data.content_model_json == {"entry_collection": "posts"}
    assert started.data.project_id == project_id
    assert started.data.workspace_binding_id == binding.data.id
    assert started.data.runtime == "codex"


def test_start_session_without_binding_is_allowed(session: Session) -> None:
    repo = WorkspaceRepository(session)

    started = repo.start_session(runtime="claude", repo_fingerprint="git:unknown")

    assert started.project_id is None
    assert started.data.project_id is None
    assert started.data.workspace_binding_id is None
    assert started.data.needs_connect is True


def test_start_session_autobootstraps_workspace_root(session: Session) -> None:
    repo = WorkspaceRepository(session)

    started = repo.start_session(
        runtime="codex",
        cwd="/tmp/revtrix",
        git_remote_url="git@github.com:org/revtrix.git",
    )
    second = repo.start_session(
        runtime="codex",
        cwd="/tmp/revtrix/packages/site",
        git_remote_url="git@github.com:org/revtrix.git",
    )
    bindings = repo.list_bindings(project_id=started.project_id)

    assert started.project_id is not None
    assert started.data.project_id == started.project_id
    assert started.data.workspace_binding_id == bindings[0].id
    assert started.data.needs_connect is False
    assert started.data.auto_bootstrap is True
    assert started.data.project_was_created is True
    assert started.data.binding_was_created is True
    assert ProjectRepository(session).get(started.project_id).is_active is True
    assert started.data.next_step is not None
    assert "workflowTemplate.list" in started.data.next_step["recommended_tools"]
    assert bindings[0].last_known_root == str(Path("/tmp/revtrix").resolve())
    assert second.project_id == started.project_id
    assert second.data.workspace_binding_id == started.data.workspace_binding_id
    assert second.data.auto_bootstrap is False


def test_start_session_does_not_bootstrap_filesystem_root(session: Session) -> None:
    repo = WorkspaceRepository(session)

    started = repo.start_session(runtime="claude-desktop", cwd="/")

    assert started.project_id is None
    assert started.data.project_id is None
    assert started.data.workspace_binding_id is None
    assert started.data.needs_connect is True
    assert repo.list_bindings() == []
    assert started.data.repo_hints is not None
    assert "workspace_hint_warning" in started.data.repo_hints


@pytest.mark.parametrize("folder_name", ["Resources", "Contents", "MacOS", "Project"])
def test_start_session_does_not_autobootstrap_generic_project_name(
    session: Session,
    folder_name: str,
) -> None:
    repo = WorkspaceRepository(session)

    started = repo.start_session(runtime="claude-desktop", cwd=f"/tmp/{folder_name}")

    assert started.project_id is None
    assert started.data.project_id is None
    assert started.data.workspace_binding_id is None
    assert started.data.needs_connect is True
    assert repo.list_bindings() == []
    assert started.data.next_step is not None
    assert started.data.next_step["project_identity_required"] is True
    assert started.data.next_step["recommended_arguments"]["project_name"]


def test_start_session_does_not_autobootstrap_name_without_slug_content(
    session: Session,
) -> None:
    repo = WorkspaceRepository(session)

    started = repo.start_session(runtime="claude-desktop", cwd="/tmp/---")

    assert started.project_id is None
    assert started.data.project_id is None
    assert started.data.workspace_binding_id is None
    assert started.data.needs_connect is True
    assert repo.list_bindings() == []
    assert started.data.next_step is not None
    assert started.data.next_step["project_identity_required"] is True
    assert started.data.next_step["recommended_arguments"]["project_name"]


def test_start_session_does_not_bind_app_bundle_workspace_root(session: Session) -> None:
    repo = WorkspaceRepository(session)

    started = repo.start_session(
        runtime="claude-desktop",
        cwd="/Applications/StackOS.app/Contents/Resources",
    )

    assert started.project_id is None
    assert started.data.project_id is None
    assert started.data.workspace_binding_id is None
    assert started.data.needs_connect is True
    assert repo.list_bindings() == []
    assert started.data.repo_hints is not None
    assert "workspace_hint_warning" in started.data.repo_hints


def test_resolve_does_not_reuse_legacy_filesystem_root_binding(session: Session) -> None:
    project_id = _create_project(session, slug="legacy-root")
    root_fingerprint = "path:" + hashlib.sha256(b"/").hexdigest()[:24]
    session.add(
        WorkspaceBinding(
            project_id=project_id,
            repo_fingerprint=root_fingerprint,
            normalized_repo_name="project",
            last_known_root="/",
        )
    )
    session.commit()
    repo = WorkspaceRepository(session)

    by_root = repo.resolve(cwd="/", repo_fingerprint=root_fingerprint)
    by_child = repo.resolve(cwd="/tmp/some-real-workspace")

    assert by_root.needs_connect is True
    assert by_root.project_id is None
    assert by_child.needs_connect is True
    assert by_child.project_id is None


def test_bootstrap_rejects_filesystem_root(session: Session) -> None:
    repo = WorkspaceRepository(session)

    with pytest.raises(ValidationError, match="usable project directory"):
        repo.bootstrap(cwd="/")


@pytest.mark.parametrize("workspace_root", ["/", "/Applications/StackOS.app/Contents/Resources"])
def test_connect_rejects_unusable_workspace_roots(
    session: Session,
    workspace_root: str,
) -> None:
    project_id = _create_project(session)
    repo = WorkspaceRepository(session)

    with pytest.raises(ValidationError, match="usable project directory"):
        repo.connect(
            project_id=project_id,
            repo_fingerprint=f"path:unusable-{Path(workspace_root).name or 'root'}",
            last_known_root=workspace_root,
        )

    assert repo.list_bindings() == []


def test_connect_rejects_filesystem_root_fingerprint(session: Session) -> None:
    project_id = _create_project(session)
    repo = WorkspaceRepository(session)
    root_fingerprint = "path:" + hashlib.sha256(b"/").hexdigest()[:24]

    with pytest.raises(ValidationError, match="usable project directory"):
        repo.connect(project_id=project_id, repo_fingerprint=root_fingerprint)

    assert repo.list_bindings() == []


def test_connect_validates_project_and_fingerprint(session: Session) -> None:
    repo = WorkspaceRepository(session)

    with pytest.raises(ValidationError):
        repo.connect(project_id=1, repo_fingerprint="")

    with pytest.raises(NotFoundError):
        repo.connect(project_id=999, repo_fingerprint="git:abc123")
