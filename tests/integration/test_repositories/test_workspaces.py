"""Workspace binding repository tests for the plugin-first install surface."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session

from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.workspaces import WorkspaceRepository


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


def test_connect_validates_project_and_fingerprint(session: Session) -> None:
    repo = WorkspaceRepository(session)

    with pytest.raises(ValidationError):
        repo.connect(project_id=1, repo_fingerprint="")

    with pytest.raises(NotFoundError):
        repo.connect(project_id=999, repo_fingerprint="git:abc123")
