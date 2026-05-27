"""Workspace binding + agent-session repositories.

These tables support the plugin-first developer experience: Codex/Claude run
from a real site repository, a thin plugin MCP bridge sends repo hints to the
singleton daemon, and the daemon resolves the durable StackOS project.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, select

from stackos.db.models import AgentSession, Project, WorkspaceBinding
from stackos.repositories.base import Envelope, NotFoundError, ValidationError
from stackos.repositories.projects import ProjectOut, ProjectRepository


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _normalize_path(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(Path(value).expanduser().resolve())
    except OSError:
        return str(Path(value).expanduser().absolute())


def _is_same_or_child(path: str, root: str) -> bool:
    if path == root:
        return True
    return path.startswith(root.rstrip("/") + "/")


def _path_fingerprint(path: str | None) -> str | None:
    if not path:
        return None
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:24]
    return f"path:{digest}"


def _slugify(value: str, *, fallback: str = "project") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or fallback)[:80].strip("-") or fallback


def _title_from_slug(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("-") if part) or "Project"


def _repo_name_from_remote(remote: str | None) -> str | None:
    if not remote:
        return None
    trimmed = remote.rstrip("/")
    if ":" in trimmed and "://" not in trimmed:
        trimmed = trimmed.split(":", 1)[1]
    if "://" in trimmed:
        trimmed = trimmed.split("://", 1)[1]
        parts = trimmed.split("/", 1)
        trimmed = parts[1] if len(parts) > 1 else parts[0]
    if trimmed.endswith(".git"):
        trimmed = trimmed[:-4]
    if "/" in trimmed:
        trimmed = trimmed.rsplit("/", 1)[-1]
    return trimmed or None


def _repo_name_from_root(root: str | None) -> str | None:
    if not root:
        return None
    name = Path(root).name
    return name or None


def _ui_paths(project_id: int | None) -> dict[str, str]:
    if project_id is None:
        return {"projects": "/"}
    return {
        "setup": f"/projects/{project_id}/setup",
        "connections": f"/projects/{project_id}/connections",
        "tasks": f"/projects/{project_id}/tasks",
        "workflow_templates": f"/projects/{project_id}/workflow-templates",
    }


def _repo_hints(
    *,
    repo_fingerprint: str | None,
    git_remote_url: str | None,
    cwd: str | None,
) -> dict[str, Any]:
    return {
        "repo_fingerprint": repo_fingerprint,
        "git_remote_url": git_remote_url,
        "cwd": cwd,
        "normalized_cwd": _normalize_path(cwd),
        "fingerprint_format": (
            "The StackOS bridge sends path:<sha256(workspace_root)[:24]> by default; "
            "git:<stable-repo-id> is also accepted when a host supplies it."
        ),
    }


def _project_candidate(project: ProjectOut) -> WorkspaceProjectCandidateOut:
    return WorkspaceProjectCandidateOut(
        id=project.id,
        slug=project.slug,
        name=project.name,
        domain=project.domain,
        is_active=project.is_active,
        ui_paths=_ui_paths(project.id),
    )


def _connect_required_next_step(
    *,
    repo_fingerprint: str | None,
    git_remote_url: str | None,
    cwd: str | None,
) -> dict[str, Any]:
    bootstrap_args: dict[str, Any] = {}
    if repo_fingerprint:
        bootstrap_args["repo_fingerprint"] = repo_fingerprint
    if git_remote_url:
        bootstrap_args["git_remote_url"] = git_remote_url
    if cwd:
        bootstrap_args["cwd"] = cwd
    return {
        "status": "bootstrap_required",
        "recommended_tool": "workspace.bootstrap",
        "call_via": "toolbox.call",
        "recommended_arguments": bootstrap_args,
        "why": (
            "This workspace has no daemon-owned project binding yet. "
            "workspace.bootstrap explicitly creates or reuses one project for this "
            "workspace root and stores the binding."
        ),
        "alternatives": [
            {
                "tool": "project.list",
                "call_via": "toolbox.call",
                "when": "Choose an existing project intentionally before binding.",
            },
            {
                "tool": "project.create",
                "call_via": "toolbox.call",
                "when": "Create a project with explicit operator-provided metadata.",
            },
            {
                "tool": "workspace.connect",
                "call_via": "toolbox.call",
                "when": "Bind to a known project_id, project_slug, or project_name.",
            },
        ],
    }


def _connected_next_step(project_id: int) -> dict[str, Any]:
    return {
        "status": "ready",
        "call_via": "toolbox.call",
        "recommended_tools": [
            "workflowTemplate.list",
            "agentPreset.list",
            "tracker.status",
            "auth.status",
        ],
        "ui_paths": _ui_paths(project_id),
    }


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
    needs_connect: bool = False
    auto_bootstrap: bool = False
    project_was_created: bool | None = None
    binding_was_created: bool | None = None
    candidate_projects: list[WorkspaceProjectCandidateOut] = Field(default_factory=list)
    repo_hints: dict[str, Any] = Field(default_factory=dict)
    ui_paths: dict[str, str] = Field(default_factory=dict)
    next_step: dict[str, Any] | None = None


class WorkspaceProjectCandidateOut(BaseModel):
    id: int
    slug: str
    name: str
    domain: str
    is_active: bool
    ui_paths: dict[str, str]


class WorkspaceResolutionOut(BaseModel):
    """Result returned when a bridge asks which project owns a workspace."""

    binding: WorkspaceBindingOut | None
    project_id: int | None
    needs_connect: bool
    candidate_projects: list[WorkspaceProjectCandidateOut] = Field(default_factory=list)
    repo_hints: dict[str, Any] = Field(default_factory=dict)
    ui_paths: dict[str, str] = Field(default_factory=dict)
    next_step: dict[str, Any] | None = None


class WorkspaceBootstrapOut(BaseModel):
    """Explicit bootstrap result for ensuring one project per workspace root."""

    project_id: int
    project: ProjectOut
    binding: WorkspaceBindingOut
    project_was_created: bool
    binding_was_created: bool
    needs_connect: bool = False
    repo_hints: dict[str, Any] = Field(default_factory=dict)
    ui_paths: dict[str, str] = Field(default_factory=dict)
    next_step: dict[str, Any] | None = None


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

        normalized_root = _normalize_path(last_known_root)
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
                last_known_root=normalized_root,
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
                row.last_known_root = normalized_root
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

    def bootstrap(
        self,
        *,
        repo_fingerprint: str | None = None,
        git_remote_url: str | None = None,
        normalized_repo_name: str | None = None,
        cwd: str | None = None,
        last_known_root: str | None = None,
        framework: str | None = None,
        content_model_json: dict[str, Any] | None = None,
        project_id: int | None = None,
        project_slug: str | None = None,
        project_name: str | None = None,
        domain: str | None = None,
        niche: str | None = None,
        locale: str = "en-US",
        rebind_existing: bool = False,
    ) -> Envelope[WorkspaceBootstrapOut]:
        """Explicitly ensure a project and binding for the current workspace."""
        normalized_root = _normalize_path(last_known_root or cwd)
        effective_fingerprint = repo_fingerprint or _path_fingerprint(normalized_root)
        if not effective_fingerprint:
            raise ValidationError("repo_fingerprint or cwd/last_known_root is required")

        repo_name = (
            normalized_repo_name
            or _repo_name_from_remote(git_remote_url)
            or _repo_name_from_root(normalized_root)
            or "project"
        )
        resolution = self.resolve(
            repo_fingerprint=effective_fingerprint,
            git_remote_url=git_remote_url,
            cwd=normalized_root,
        )
        explicit_project = any((project_id is not None, project_slug, project_name))
        project_created = False
        projects = ProjectRepository(self._s)

        if resolution.binding is not None:
            current_project = projects.get(resolution.project_id or 0)
            target_project = current_project
            if explicit_project:
                target_project, project_created = self._resolve_or_create_project(
                    project_id=project_id,
                    project_slug=project_slug,
                    project_name=project_name,
                    domain=domain,
                    niche=niche,
                    locale=locale,
                    repo_name=repo_name,
                    repo_fingerprint=effective_fingerprint,
                    explicit_slug=project_slug is not None,
                )
                if target_project.id != current_project.id and not rebind_existing:
                    raise ValidationError(
                        "workspace is already bound to another project; "
                        "pass rebind_existing=true to move it",
                    )
            binding = self._update_binding_row(
                binding_id=resolution.binding.id,
                project_id=target_project.id,
                repo_fingerprint=effective_fingerprint,
                git_remote_url=git_remote_url,
                normalized_repo_name=repo_name,
                last_known_root=normalized_root,
                framework=framework,
                content_model_json=content_model_json,
            )
            return Envelope(
                data=self._bootstrap_out(
                    project=target_project,
                    binding=binding,
                    project_was_created=project_created,
                    binding_was_created=False,
                    repo_fingerprint=effective_fingerprint,
                    git_remote_url=git_remote_url,
                    cwd=normalized_root,
                ),
                project_id=target_project.id,
            )

        target_project, project_created = self._resolve_or_create_project(
            project_id=project_id,
            project_slug=project_slug,
            project_name=project_name,
            domain=domain,
            niche=niche,
            locale=locale,
            repo_name=repo_name,
            repo_fingerprint=effective_fingerprint,
            explicit_slug=project_slug is not None,
        )
        connected = self.connect(
            project_id=target_project.id,
            repo_fingerprint=effective_fingerprint,
            git_remote_url=git_remote_url,
            normalized_repo_name=repo_name,
            last_known_root=normalized_root,
            framework=framework,
            content_model_json=content_model_json,
        )
        return Envelope(
            data=self._bootstrap_out(
                project=target_project,
                binding=connected.data,
                project_was_created=project_created,
                binding_was_created=True,
                repo_fingerprint=effective_fingerprint,
                git_remote_url=git_remote_url,
                cwd=normalized_root,
            ),
            project_id=target_project.id,
        )

    def resolve(
        self,
        *,
        repo_fingerprint: str | None = None,
        git_remote_url: str | None = None,
        cwd: str | None = None,
    ) -> WorkspaceResolutionOut:
        """Resolve a workspace by fingerprint, root directory, then git remote."""
        row: WorkspaceBinding | None = None
        if repo_fingerprint:
            row = self._s.exec(
                select(WorkspaceBinding).where(
                    WorkspaceBinding.repo_fingerprint == repo_fingerprint
                )
            ).first()
        normalized_cwd = _normalize_path(cwd)
        if row is None and normalized_cwd:
            rows = self._s.exec(
                select(WorkspaceBinding)
                .where(col(WorkspaceBinding.last_known_root).is_not(None))
                .order_by(col(WorkspaceBinding.updated_at).desc())
            ).all()
            matching = [
                candidate
                for candidate in rows
                if candidate.last_known_root
                and _is_same_or_child(normalized_cwd, candidate.last_known_root)
            ]
            if matching:
                row = max(matching, key=lambda candidate: len(candidate.last_known_root or ""))
        if row is None and git_remote_url:
            row = self._s.exec(
                select(WorkspaceBinding)
                .where(WorkspaceBinding.git_remote_url == git_remote_url)
                .order_by(col(WorkspaceBinding.updated_at).desc())
            ).first()
        if row is None:
            return WorkspaceResolutionOut(
                binding=None,
                project_id=None,
                needs_connect=True,
                candidate_projects=self._project_candidates(),
                repo_hints=_repo_hints(
                    repo_fingerprint=repo_fingerprint,
                    git_remote_url=git_remote_url,
                    cwd=cwd,
                ),
                ui_paths=_ui_paths(None),
                next_step=_connect_required_next_step(
                    repo_fingerprint=repo_fingerprint,
                    git_remote_url=git_remote_url,
                    cwd=cwd,
                ),
            )

        row.last_seen_at = _utcnow()
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        out = WorkspaceBindingOut.model_validate(row)
        return WorkspaceResolutionOut(
            binding=out,
            project_id=row.project_id,
            needs_connect=False,
            repo_hints=_repo_hints(
                repo_fingerprint=repo_fingerprint or row.repo_fingerprint,
                git_remote_url=git_remote_url or row.git_remote_url,
                cwd=cwd,
            ),
            ui_paths=_ui_paths(row.project_id),
            next_step=_connected_next_step(row.project_id),
        )

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
        project_id: int | None = None,
        framework: str | None = None,
        content_model_json: dict[str, Any] | None = None,
    ) -> Envelope[WorkspaceBindingOut]:
        row = self._s.get(WorkspaceBinding, binding_id)
        if row is None:
            raise NotFoundError(f"workspace binding {binding_id} not found")
        if project_id is not None and row.project_id != project_id:
            raise NotFoundError(
                f"workspace binding {binding_id} not found in project {project_id}",
                data={"project_id": project_id, "binding_id": binding_id},
            )
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
        auto_bootstrap: bool = True,
    ) -> Envelope[AgentSessionOut]:
        """Register a plugin MCP bridge session and attach binding if known."""
        resolution = self.resolve(
            repo_fingerprint=repo_fingerprint,
            git_remote_url=git_remote_url,
            cwd=cwd,
        )
        bootstrap: Envelope[WorkspaceBootstrapOut] | None = None
        normalized_cwd = _normalize_path(cwd)
        if resolution.needs_connect and auto_bootstrap and normalized_cwd is not None:
            bootstrap = self.bootstrap(
                repo_fingerprint=repo_fingerprint,
                git_remote_url=git_remote_url,
                cwd=normalized_cwd,
                last_known_root=normalized_cwd,
            )
            resolution = WorkspaceResolutionOut(
                binding=bootstrap.data.binding,
                project_id=bootstrap.project_id,
                needs_connect=False,
                repo_hints=bootstrap.data.repo_hints,
                ui_paths=bootstrap.data.ui_paths,
                next_step=bootstrap.data.next_step,
            )
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
        out = AgentSessionOut.model_validate(row)
        if resolution.needs_connect:
            out = out.model_copy(
                update={
                    "needs_connect": True,
                    "candidate_projects": resolution.candidate_projects,
                    "repo_hints": resolution.repo_hints,
                    "ui_paths": resolution.ui_paths,
                    "next_step": resolution.next_step,
                }
            )
        elif resolution.project_id is not None:
            out = out.model_copy(
                update={
                    "needs_connect": False,
                    "auto_bootstrap": bootstrap is not None,
                    "project_was_created": (
                        bootstrap.data.project_was_created if bootstrap is not None else None
                    ),
                    "binding_was_created": (
                        bootstrap.data.binding_was_created if bootstrap is not None else None
                    ),
                    "repo_hints": resolution.repo_hints,
                    "ui_paths": resolution.ui_paths,
                    "next_step": resolution.next_step,
                }
            )
        return Envelope(data=out, project_id=row.project_id)

    def _project_candidates(self) -> list[WorkspaceProjectCandidateOut]:
        page = ProjectRepository(self._s).list(active_only=False, limit=10)
        return [_project_candidate(project) for project in page.items]

    def _resolve_or_create_project(
        self,
        *,
        project_id: int | None,
        project_slug: str | None,
        project_name: str | None,
        domain: str | None,
        niche: str | None,
        locale: str,
        repo_name: str,
        repo_fingerprint: str,
        explicit_slug: bool,
    ) -> tuple[ProjectOut, bool]:
        projects = ProjectRepository(self._s)
        if project_id is not None:
            return projects.get(project_id), False
        if project_slug:
            try:
                return projects.get(project_slug), False
            except NotFoundError:
                slug = _slugify(project_slug)
                return (
                    projects.create(
                        slug=slug,
                        name=project_name or _title_from_slug(slug),
                        domain=domain or f"{slug}.local",
                        niche=niche,
                        locale=locale,
                    ).data,
                    True,
                )
        if project_name:
            rows = self._s.exec(select(Project).where(Project.name == project_name)).all()
            if len(rows) == 1:
                return ProjectOut.model_validate(rows[0]), False
            if len(rows) > 1:
                raise ValidationError(
                    "project_name matches multiple projects; pass project_id or project_slug"
                )

        base_slug = _slugify(project_name or repo_name)
        slug = (
            base_slug if explicit_slug else self._unique_project_slug(base_slug, repo_fingerprint)
        )
        return (
            projects.create(
                slug=slug,
                name=project_name or _title_from_slug(base_slug),
                domain=domain or f"{slug}.local",
                niche=niche,
                locale=locale,
            ).data,
            True,
        )

    def _unique_project_slug(self, base_slug: str, repo_fingerprint: str) -> str:
        if not self._project_slug_exists(base_slug):
            return base_slug
        suffix = _slugify(repo_fingerprint.split(":", 1)[-1])[:8] or "workspace"
        candidate_base = f"{base_slug[: max(1, 79 - len(suffix))]}-{suffix}"
        candidate = candidate_base[:80].strip("-")
        counter = 2
        while self._project_slug_exists(candidate):
            counter_suffix = f"{suffix}-{counter}"
            candidate = f"{base_slug[: max(1, 79 - len(counter_suffix))]}-{counter_suffix}"
            candidate = candidate[:80].strip("-")
            counter += 1
        return candidate

    def _project_slug_exists(self, slug: str) -> bool:
        return self._s.exec(select(Project.id).where(Project.slug == slug)).first() is not None

    def _update_binding_row(
        self,
        *,
        binding_id: int,
        project_id: int,
        repo_fingerprint: str,
        git_remote_url: str | None,
        normalized_repo_name: str | None,
        last_known_root: str | None,
        framework: str | None,
        content_model_json: dict[str, Any] | None,
    ) -> WorkspaceBindingOut:
        row = self._s.get(WorkspaceBinding, binding_id)
        if row is None:
            raise NotFoundError(f"workspace binding {binding_id} not found")
        if row.repo_fingerprint != repo_fingerprint:
            conflict = self._s.exec(
                select(WorkspaceBinding).where(
                    WorkspaceBinding.repo_fingerprint == repo_fingerprint,
                    WorkspaceBinding.id != binding_id,
                )
            ).first()
            if conflict is not None:
                raise ValidationError(
                    "repo_fingerprint is already bound to another workspace binding"
                )
            row.repo_fingerprint = repo_fingerprint
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
        row.updated_at = _utcnow()
        row.last_seen_at = row.updated_at
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return WorkspaceBindingOut.model_validate(row)

    def _bootstrap_out(
        self,
        *,
        project: ProjectOut,
        binding: WorkspaceBindingOut,
        project_was_created: bool,
        binding_was_created: bool,
        repo_fingerprint: str,
        git_remote_url: str | None,
        cwd: str | None,
    ) -> WorkspaceBootstrapOut:
        return WorkspaceBootstrapOut(
            project_id=project.id,
            project=project,
            binding=binding,
            project_was_created=project_was_created,
            binding_was_created=binding_was_created,
            repo_hints=_repo_hints(
                repo_fingerprint=repo_fingerprint,
                git_remote_url=git_remote_url,
                cwd=cwd,
            ),
            ui_paths=_ui_paths(project.id),
            next_step=_connected_next_step(project.id),
        )


__all__ = [
    "AgentSessionOut",
    "WorkspaceBindingOut",
    "WorkspaceBootstrapOut",
    "WorkspaceProjectCandidateOut",
    "WorkspaceRepository",
    "WorkspaceResolutionOut",
]
