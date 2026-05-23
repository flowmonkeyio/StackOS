"""Workspace binding + agent-session repositories.

These tables support the plugin-first developer experience: Codex/Claude run
from a real site repository, a thin plugin MCP bridge sends repo hints to the
singleton daemon, and the daemon resolves the durable StackOS project.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, col, select

from stackos.db.models import AgentSession, Project, WorkspaceBinding
from stackos.repositories.base import Envelope, NotFoundError, ValidationError


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


class WorkspaceBindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    repo_fingerprint: str
    git_remote_url: str | None
    normalized_repo_name: str | None
    last_known_root: str | None
    framework: str | None
    content_model_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None


class AgentSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None
    workspace_binding_id: int | None
    runtime: str
    cwd: str | None
    repo_fingerprint: str | None
    git_remote_url: str | None
    thread_id: str | None
    client_session_id: str | None
    created_at: datetime
    last_seen_at: datetime


class WorkspaceResolutionOut(BaseModel):
    """Result returned when a bridge asks which project owns a workspace."""

    binding: WorkspaceBindingOut | None
    project_id: int | None
    needs_connect: bool


class WorkspaceRepository:
    """Repo/project bindings owned by the singleton daemon DB."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def connect(
        self,
        *,
        project_id: int,
        repo_fingerprint: str,
        git_remote_url: str | None = None,
        normalized_repo_name: str | None = None,
        last_known_root: str | None = None,
        framework: str | None = None,
        content_model_json: dict[str, Any] | None = None,
    ) -> Envelope[WorkspaceBindingOut]:
        """Create or update a non-invasive repo binding for a project."""
        if not repo_fingerprint:
            raise ValidationError("repo_fingerprint is required")
        if self._s.get(Project, project_id) is None:
            raise NotFoundError(f"project {project_id} not found")

        row = self._s.exec(
            select(WorkspaceBinding).where(WorkspaceBinding.repo_fingerprint == repo_fingerprint)
        ).first()
        now = _utcnow()
        if row is None:
            row = WorkspaceBinding(
                project_id=project_id,
                repo_fingerprint=repo_fingerprint,
                git_remote_url=git_remote_url,
                normalized_repo_name=normalized_repo_name,
                last_known_root=last_known_root,
                framework=framework,
                content_model_json=content_model_json,
                last_seen_at=now,
            )
        else:
            row.project_id = project_id
            if git_remote_url is not None:
                row.git_remote_url = git_remote_url
            if normalized_repo_name is not None:
                row.normalized_repo_name = normalized_repo_name
            if last_known_root is not None:
                row.last_known_root = last_known_root
            if framework is not None:
                row.framework = framework
            if content_model_json is not None:
                row.content_model_json = content_model_json
            row.updated_at = now
            row.last_seen_at = now
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=WorkspaceBindingOut.model_validate(row), project_id=row.project_id)

    def resolve(
        self,
        *,
        repo_fingerprint: str | None = None,
        git_remote_url: str | None = None,
    ) -> WorkspaceResolutionOut:
        """Resolve a workspace by stable fingerprint first, then git remote."""
        row: WorkspaceBinding | None = None
        if repo_fingerprint:
            row = self._s.exec(
                select(WorkspaceBinding).where(
                    WorkspaceBinding.repo_fingerprint == repo_fingerprint
                )
            ).first()
        if row is None and git_remote_url:
            row = self._s.exec(
                select(WorkspaceBinding)
                .where(WorkspaceBinding.git_remote_url == git_remote_url)
                .order_by(col(WorkspaceBinding.updated_at).desc())
            ).first()
        if row is None:
            return WorkspaceResolutionOut(binding=None, project_id=None, needs_connect=True)

        row.last_seen_at = _utcnow()
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        out = WorkspaceBindingOut.model_validate(row)
        return WorkspaceResolutionOut(binding=out, project_id=row.project_id, needs_connect=False)

    def list_bindings(self, project_id: int | None = None) -> list[WorkspaceBindingOut]:
        stmt = select(WorkspaceBinding)
        if project_id is not None:
            stmt = stmt.where(WorkspaceBinding.project_id == project_id)
        rows = self._s.exec(stmt.order_by(WorkspaceBinding.id.asc())).all()  # type: ignore[union-attr]
        return [WorkspaceBindingOut.model_validate(row) for row in rows]

    def update_profile(
        self,
        binding_id: int,
        *,
        framework: str | None = None,
        content_model_json: dict[str, Any] | None = None,
    ) -> Envelope[WorkspaceBindingOut]:
        row = self._s.get(WorkspaceBinding, binding_id)
        if row is None:
            raise NotFoundError(f"workspace binding {binding_id} not found")
        if framework is not None:
            row.framework = framework
        if content_model_json is not None:
            row.content_model_json = content_model_json
        row.updated_at = _utcnow()
        row.last_seen_at = row.updated_at
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=WorkspaceBindingOut.model_validate(row), project_id=row.project_id)

    def start_session(
        self,
        *,
        runtime: str,
        cwd: str | None = None,
        repo_fingerprint: str | None = None,
        git_remote_url: str | None = None,
        thread_id: str | None = None,
        client_session_id: str | None = None,
    ) -> Envelope[AgentSessionOut]:
        """Register a plugin MCP bridge session and attach binding if known."""
        resolution = self.resolve(repo_fingerprint=repo_fingerprint, git_remote_url=git_remote_url)
        binding_id = resolution.binding.id if resolution.binding is not None else None
        row = AgentSession(
            project_id=resolution.project_id,
            workspace_binding_id=binding_id,
            runtime=runtime or "unknown",
            cwd=cwd,
            repo_fingerprint=repo_fingerprint,
            git_remote_url=git_remote_url,
            thread_id=thread_id,
            client_session_id=client_session_id,
        )
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=AgentSessionOut.model_validate(row), project_id=row.project_id)


__all__ = [
    "AgentSessionOut",
    "WorkspaceBindingOut",
    "WorkspaceRepository",
    "WorkspaceResolutionOut",
]
