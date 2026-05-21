"""Workspace binding repository tests for the plugin-first install surface."""

from __future__ import annotations

import pytest
from sqlmodel import Session

from content_stack.repositories.base import NotFoundError, ValidationError
from content_stack.repositories.projects import ProjectRepository
from content_stack.repositories.workspaces import WorkspaceRepository


def _create_project(session: Session, slug: str = "workspace-site") -> int:
    env = ProjectRepository(session).create(
        slug=slug,
        name="Workspace Site",
        domain="workspace.example",
        locale="en-US",
    )
    return env.data.id


def test_resolve_unknown_workspace_requests_connect(session: Session) -> None:
    repo = WorkspaceRepository(session)

    resolution = repo.resolve(repo_fingerprint="git:unknown")

    assert resolution.needs_connect is True
    assert resolution.project_id is None
    assert resolution.binding is None


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


def test_connect_validates_project_and_fingerprint(session: Session) -> None:
    repo = WorkspaceRepository(session)

    with pytest.raises(ValidationError):
        repo.connect(project_id=1, repo_fingerprint="")

    with pytest.raises(NotFoundError):
        repo.connect(project_id=999, repo_fingerprint="git:abc123")
