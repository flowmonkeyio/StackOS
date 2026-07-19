"""Workspace binding + agent-session repositories.

These tables support the plugin-first developer experience: Codex/Claude run
from a real site repository, a thin plugin MCP bridge sends repo hints to the
singleton daemon, and the daemon resolves the durable StackOS project.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, select

from stackos.db.models import AgentSession, Project, WorkspaceBinding
from stackos.repositories.base import Envelope, NotFoundError, ValidationError
from stackos.repositories.projects import ProjectOut, ProjectRepository
from stackos.repositories.workspace_guidance import (
    _connect_required_next_step,
    _connected_next_step,
    _repo_hints,
    _setup_state,
    _ui_paths,
)
from stackos.workspace_identity import (
    NAMED_WORKSPACE_PREFIX as _NAMED_WORKSPACE_PREFIX,
)
from stackos.workspace_identity import (
    derive_project_name as _derive_project_name,
)
from stackos.workspace_identity import (
    is_filesystem_root_fingerprint as _is_filesystem_root_fingerprint,
)
from stackos.workspace_identity import (
    is_named_workspace_fingerprint as _is_named_workspace_fingerprint,
)
from stackos.workspace_identity import (
    is_usable_workspace_root as _is_usable_workspace_root,
)
from stackos.workspace_identity import (
    named_workspace_fingerprint as _named_workspace_fingerprint,
)
from stackos.workspace_identity import (
    normalize_path as _normalize_path,
)
from stackos.workspace_identity import (
    path_fingerprint as _path_fingerprint,
)
from stackos.workspace_identity import (
    workspace_alias_from_fingerprint as _workspace_alias_from_fingerprint,
)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _is_same_or_child(path: str, root: str) -> bool:
    if not _is_usable_workspace_root(root):
        return False
    if path == root:
        return True
    return path.startswith(root.rstrip("/") + "/")


def _slugify(value: str, *, fallback: str = "project") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or fallback)[:80].strip("-") or fallback


def _title_from_slug(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("-") if part) or "Project"


def _effective_workspace_fingerprint(
    *,
    repo_fingerprint: str | None,
    workspace_alias: str | None,
) -> str | None:
    alias_fingerprint = _named_workspace_fingerprint(workspace_alias)
    if repo_fingerprint and _is_named_workspace_fingerprint(repo_fingerprint):
        repo_alias = _workspace_alias_from_fingerprint(repo_fingerprint)
        if repo_alias is None:
            raise ValidationError("workspace_alias must be a usable business alias")
        canonical = _named_workspace_fingerprint(repo_alias)
        if alias_fingerprint and alias_fingerprint != canonical:
            raise ValidationError("repo_fingerprint workspace alias conflicts with workspace_alias")
        return canonical
    if repo_fingerprint and alias_fingerprint:
        raise ValidationError(
            "workspace_alias cannot be combined with a non-named repo_fingerprint"
        )
    return repo_fingerprint or alias_fingerprint


def _project_candidate(project: ProjectOut) -> WorkspaceProjectCandidateOut:
    return WorkspaceProjectCandidateOut(
        id=project.id,
        slug=project.slug,
        name=project.name,
        domain=project.domain,
        is_active=project.is_active,
        ui_paths=_ui_paths(project.id),
    )


def _named_workspace_candidate(
    *,
    binding: WorkspaceBinding,
    project: Project,
) -> NamedWorkspaceCandidateOut | None:
    alias = _workspace_alias_from_fingerprint(binding.repo_fingerprint)
    if alias is None:
        return None
    return NamedWorkspaceCandidateOut(
        workspace_alias=alias,
        binding_id=binding.id or 0,
        project_id=project.id or 0,
        project_slug=project.slug,
        project_name=project.name,
        ui_paths=_ui_paths(project.id),
    )


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
    binding_kind: str = "directory"
    workspace_alias: str | None = None


def _workspace_binding_out(row: WorkspaceBinding) -> WorkspaceBindingOut:
    return WorkspaceBindingOut.model_validate(row).model_copy(
        update={
            "binding_kind": (
                "named" if _is_named_workspace_fingerprint(row.repo_fingerprint) else "directory"
            ),
            "workspace_alias": _workspace_alias_from_fingerprint(row.repo_fingerprint),
        }
    )


class NamedWorkspaceCandidateOut(BaseModel):
    workspace_alias: str
    binding_id: int
    project_id: int
    project_slug: str
    project_name: str
    ui_paths: dict[str, str]
    ui_urls: dict[str, str] = Field(default_factory=dict)


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
    candidate_workspaces: list[NamedWorkspaceCandidateOut] = Field(default_factory=list)
    candidate_projects: list[WorkspaceProjectCandidateOut] = Field(default_factory=list)
    repo_hints: dict[str, Any] = Field(default_factory=dict)
    ui_paths: dict[str, str] = Field(default_factory=dict)
    ui_urls: dict[str, str] = Field(default_factory=dict)
    ui_health: dict[str, Any] = Field(default_factory=dict)
    setup_state: dict[str, Any] = Field(default_factory=dict)
    next_step: dict[str, Any] | None = None


class WorkspaceProjectCandidateOut(BaseModel):
    id: int
    slug: str
    name: str
    domain: str
    is_active: bool
    ui_paths: dict[str, str]
    ui_urls: dict[str, str] = Field(default_factory=dict)


class WorkspaceResolutionOut(BaseModel):
    """Result returned when a bridge asks which project owns a workspace."""

    binding: WorkspaceBindingOut | None
    project_id: int | None
    needs_connect: bool
    candidate_workspaces: list[NamedWorkspaceCandidateOut] = Field(default_factory=list)
    candidate_projects: list[WorkspaceProjectCandidateOut] = Field(default_factory=list)
    repo_hints: dict[str, Any] = Field(default_factory=dict)
    ui_paths: dict[str, str] = Field(default_factory=dict)
    ui_urls: dict[str, str] = Field(default_factory=dict)
    ui_health: dict[str, Any] = Field(default_factory=dict)
    setup_state: dict[str, Any] = Field(default_factory=dict)
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
    ui_urls: dict[str, str] = Field(default_factory=dict)
    ui_health: dict[str, Any] = Field(default_factory=dict)
    setup_state: dict[str, Any] = Field(default_factory=dict)
    next_step: dict[str, Any] | None = None


class WorkspaceRepository:
    """Repo/project bindings owned by the singleton daemon DB."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def connect(
        self,
        *,
        project_id: int,
        repo_fingerprint: str | None = None,
        git_remote_url: str | None = None,
        normalized_repo_name: str | None = None,
        last_known_root: str | None = None,
        framework: str | None = None,
        content_model_json: dict[str, Any] | None = None,
        rebind_existing: bool = False,
        workspace_alias: str | None = None,
    ) -> Envelope[WorkspaceBindingOut]:
        """Create or update a non-invasive repo binding for a project."""
        effective_fingerprint = _effective_workspace_fingerprint(
            repo_fingerprint=repo_fingerprint,
            workspace_alias=workspace_alias,
        )
        if not effective_fingerprint:
            raise ValidationError("repo_fingerprint or workspace_alias is required")
        named_binding = _is_named_workspace_fingerprint(effective_fingerprint)
        if _is_filesystem_root_fingerprint(effective_fingerprint):
            raise ValidationError("workspace root must be a usable project directory")
        if self._s.get(Project, project_id) is None:
            raise NotFoundError(f"project {project_id} not found")

        normalized_root = _normalize_path(last_known_root)
        if normalized_root is not None and not _is_usable_workspace_root(normalized_root):
            raise ValidationError("workspace root must be a usable project directory")
        if named_binding:
            normalized_root = None
        row = self._s.exec(
            select(WorkspaceBinding).where(
                WorkspaceBinding.repo_fingerprint == effective_fingerprint
            )
        ).first()
        now = _utcnow()
        if row is None:
            row = WorkspaceBinding(
                project_id=project_id,
                repo_fingerprint=effective_fingerprint,
                git_remote_url=git_remote_url,
                normalized_repo_name=normalized_repo_name or workspace_alias,
                last_known_root=normalized_root,
                framework=framework,
                content_model_json=content_model_json,
                last_seen_at=now,
            )
        else:
            if row.project_id != project_id and not rebind_existing:
                raise ValidationError(
                    "workspace is already bound to another project; "
                    "pass rebind_existing=true to move it",
                    data={
                        "binding_id": row.id,
                        "repo_fingerprint": effective_fingerprint,
                        "current_project_id": row.project_id,
                        "requested_project_id": project_id,
                    },
                )
            row.project_id = project_id
            if git_remote_url is not None:
                row.git_remote_url = git_remote_url
            if normalized_repo_name is not None or workspace_alias is not None:
                row.normalized_repo_name = normalized_repo_name or workspace_alias
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
        return Envelope(data=_workspace_binding_out(row), project_id=row.project_id)

    def bootstrap(
        self,
        *,
        repo_fingerprint: str | None = None,
        git_remote_url: str | None = None,
        normalized_repo_name: str | None = None,
        cwd: str | None = None,
        last_known_root: str | None = None,
        workspace_alias: str | None = None,
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
        if normalized_root is not None and not _is_usable_workspace_root(normalized_root):
            raise ValidationError("workspace root must be a usable project directory")
        derived_project_name = _derive_project_name(
            normalized_repo_name=normalized_repo_name,
            git_remote_url=git_remote_url,
            workspace_root=normalized_root,
        )
        project_id_alias_source = None
        if (
            project_id is not None
            and repo_fingerprint is None
            and workspace_alias is None
            and project_slug is None
            and project_name is None
            and derived_project_name is None
            and normalized_root is None
        ):
            project_id_alias_source = ProjectRepository(self._s).get(project_id).slug
        alias_source = (
            workspace_alias
            or project_slug
            or project_name
            or derived_project_name
            or project_id_alias_source
        )
        effective_fingerprint = (
            _effective_workspace_fingerprint(
                repo_fingerprint=repo_fingerprint,
                workspace_alias=workspace_alias,
            )
            or _path_fingerprint(normalized_root)
            or _effective_workspace_fingerprint(
                repo_fingerprint=None,
                workspace_alias=alias_source,
            )
        )
        if not effective_fingerprint:
            raise ValidationError(
                "repo_fingerprint, cwd/last_known_root, or workspace_alias is required"
            )
        named_binding = _is_named_workspace_fingerprint(effective_fingerprint)
        if named_binding:
            normalized_root = None
        named_alias = _workspace_alias_from_fingerprint(effective_fingerprint)
        if derived_project_name is None and named_alias is not None:
            derived_project_name = _title_from_slug(named_alias)

        explicit_project = any((project_id is not None, project_slug, project_name))
        project_identity_available = explicit_project or derived_project_name is not None
        if not project_identity_available:
            raise ValidationError(
                "project_name or project_slug is required because StackOS could not "
                "derive a reliable project name from normalized_repo_name, "
                "git_remote_url, or workspace directory name",
                data={
                    "project_identity_required": True,
                    "recommended_arguments": {"project_name": "<operator-provided project name>"},
                    "accepted_arguments": ["project_name", "project_slug", "workspace_alias"],
                },
            )
        resolution = self.resolve(
            repo_fingerprint=effective_fingerprint,
            git_remote_url=git_remote_url,
            cwd=normalized_root,
        )
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
                    repo_name=derived_project_name,
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
                normalized_repo_name=derived_project_name or named_alias,
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
            repo_name=derived_project_name,
            repo_fingerprint=effective_fingerprint,
            explicit_slug=project_slug is not None,
        )
        connected = self.connect(
            project_id=target_project.id,
            repo_fingerprint=effective_fingerprint,
            git_remote_url=git_remote_url,
            normalized_repo_name=derived_project_name,
            last_known_root=normalized_root,
            framework=framework,
            content_model_json=content_model_json,
            workspace_alias=named_alias,
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
        workspace_alias: str | None = None,
    ) -> WorkspaceResolutionOut:
        """Resolve a workspace by fingerprint, root directory, then git remote."""
        row: WorkspaceBinding | None = None
        normalized_cwd = _normalize_path(cwd)
        usable_cwd = normalized_cwd if _is_usable_workspace_root(normalized_cwd) else None
        effective_fingerprint = _effective_workspace_fingerprint(
            repo_fingerprint=repo_fingerprint,
            workspace_alias=workspace_alias,
        )
        if _is_filesystem_root_fingerprint(effective_fingerprint):
            effective_fingerprint = None
        if effective_fingerprint:
            row = self._s.exec(
                select(WorkspaceBinding).where(
                    WorkspaceBinding.repo_fingerprint == effective_fingerprint
                )
            ).first()
        if row is None and usable_cwd:
            rows = self._s.exec(
                select(WorkspaceBinding)
                .where(col(WorkspaceBinding.last_known_root).is_not(None))
                .order_by(col(WorkspaceBinding.updated_at).desc())
            ).all()
            matching = [
                candidate
                for candidate in rows
                if candidate.last_known_root
                and _is_same_or_child(usable_cwd, candidate.last_known_root)
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
                candidate_workspaces=self._named_workspace_candidates(),
                candidate_projects=self._project_candidates(),
                repo_hints=_repo_hints(
                    repo_fingerprint=effective_fingerprint,
                    git_remote_url=git_remote_url,
                    cwd=cwd,
                    normalized_cwd=normalized_cwd,
                    workspace_root_usable=usable_cwd is not None,
                ),
                ui_paths=_ui_paths(None),
                setup_state=_setup_state(binding=None, needs_connect=True),
                next_step=_connect_required_next_step(
                    repo_fingerprint=effective_fingerprint,
                    git_remote_url=git_remote_url,
                    cwd=cwd,
                    project_identity_required=(
                        _derive_project_name(
                            normalized_repo_name=None,
                            git_remote_url=git_remote_url,
                            workspace_root=normalized_cwd,
                        )
                        is None
                    ),
                    has_workspace_identity=any(
                        (effective_fingerprint, git_remote_url, normalized_cwd)
                    ),
                ),
            )

        row.last_seen_at = _utcnow()
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        out = _workspace_binding_out(row)
        return WorkspaceResolutionOut(
            binding=out,
            project_id=row.project_id,
            needs_connect=False,
            repo_hints=_repo_hints(
                repo_fingerprint=effective_fingerprint or row.repo_fingerprint,
                git_remote_url=git_remote_url or row.git_remote_url,
                cwd=cwd,
                normalized_cwd=normalized_cwd,
                workspace_root_usable=usable_cwd is not None,
            ),
            ui_paths=_ui_paths(row.project_id),
            setup_state=_setup_state(binding=out, needs_connect=False),
            next_step=_connected_next_step(row.project_id),
        )

    def list_bindings(self, project_id: int | None = None) -> list[WorkspaceBindingOut]:
        stmt = select(WorkspaceBinding)
        if project_id is not None:
            stmt = stmt.where(WorkspaceBinding.project_id == project_id)
        rows = self._s.exec(stmt.order_by(WorkspaceBinding.id.asc())).all()  # type: ignore[union-attr]
        return [_workspace_binding_out(row) for row in rows]

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
        return Envelope(data=_workspace_binding_out(row), project_id=row.project_id)

    def start_session(
        self,
        *,
        runtime: str,
        cwd: str | None = None,
        repo_fingerprint: str | None = None,
        git_remote_url: str | None = None,
        workspace_alias: str | None = None,
        thread_id: str | None = None,
        client_session_id: str | None = None,
        auto_bootstrap: bool = True,
    ) -> Envelope[AgentSessionOut]:
        """Register a plugin MCP bridge session and attach binding if known."""
        resolution = self.resolve(
            repo_fingerprint=repo_fingerprint,
            git_remote_url=git_remote_url,
            cwd=cwd,
            workspace_alias=workspace_alias,
        )
        bootstrap: Envelope[WorkspaceBootstrapOut] | None = None
        normalized_cwd = _normalize_path(cwd)
        has_derived_project_name = (
            _derive_project_name(
                normalized_repo_name=None,
                git_remote_url=git_remote_url,
                workspace_root=normalized_cwd,
            )
            is not None
        )
        if (
            resolution.needs_connect
            and auto_bootstrap
            and normalized_cwd is not None
            and _is_usable_workspace_root(normalized_cwd)
            and has_derived_project_name
        ):
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
                setup_state=bootstrap.data.setup_state,
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
                    "candidate_workspaces": resolution.candidate_workspaces,
                    "candidate_projects": resolution.candidate_projects,
                    "repo_hints": resolution.repo_hints,
                    "ui_paths": resolution.ui_paths,
                    "setup_state": resolution.setup_state,
                    "next_step": resolution.next_step,
                }
            )
        elif resolution.project_id is not None:
            auto_bootstrap = bootstrap is not None
            setup_state = dict(resolution.setup_state)
            if setup_state:
                setup_state["auto_bootstrap"] = auto_bootstrap
            out = out.model_copy(
                update={
                    "needs_connect": False,
                    "auto_bootstrap": auto_bootstrap,
                    "project_was_created": (
                        bootstrap.data.project_was_created if bootstrap is not None else None
                    ),
                    "binding_was_created": (
                        bootstrap.data.binding_was_created if bootstrap is not None else None
                    ),
                    "repo_hints": resolution.repo_hints,
                    "ui_paths": resolution.ui_paths,
                    "setup_state": setup_state,
                    "next_step": resolution.next_step,
                }
            )
        return Envelope(data=out, project_id=row.project_id)

    def _project_candidates(self) -> list[WorkspaceProjectCandidateOut]:
        page = ProjectRepository(self._s).list(active_only=False, limit=10)
        return [_project_candidate(project) for project in page.items]

    def _named_workspace_candidates(self) -> list[NamedWorkspaceCandidateOut]:
        rows = self._s.exec(
            select(WorkspaceBinding)
            .where(col(WorkspaceBinding.repo_fingerprint).like(f"{_NAMED_WORKSPACE_PREFIX}%"))
            .order_by(col(WorkspaceBinding.updated_at).desc())
            .limit(10)
        ).all()
        candidates: list[NamedWorkspaceCandidateOut] = []
        for row in rows:
            project = self._s.get(Project, row.project_id)
            if project is None:
                continue
            candidate = _named_workspace_candidate(binding=row, project=project)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _resolve_or_create_project(
        self,
        *,
        project_id: int | None,
        project_slug: str | None,
        project_name: str | None,
        domain: str | None,
        niche: str | None,
        locale: str,
        repo_name: str | None,
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

        if project_name is None and repo_name is None:
            raise ValidationError("project_name or project_slug is required")
        source_name = project_name or repo_name
        if source_name is None:
            raise ValidationError("project_name or project_slug is required")
        base_slug = _slugify(source_name)
        if project_name is None and repo_name is not None:
            existing_slug = self._s.exec(select(Project).where(Project.slug == base_slug)).first()
            if existing_slug is not None:
                return ProjectOut.model_validate(existing_slug), False
            candidate_names = {repo_name, _title_from_slug(base_slug)}
            existing_names = self._s.exec(
                select(Project).where(col(Project.name).in_(candidate_names))
            ).all()
            if len(existing_names) == 1:
                return ProjectOut.model_validate(existing_names[0]), False
            if len(existing_names) > 1:
                raise ValidationError(
                    "derived repo name matches multiple projects; pass project_id or project_slug"
                )
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
        return _workspace_binding_out(row)

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
        normalized_cwd = _normalize_path(cwd)
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
                normalized_cwd=normalized_cwd,
                workspace_root_usable=_is_usable_workspace_root(normalized_cwd),
            ),
            ui_paths=_ui_paths(project.id),
            setup_state=_setup_state(
                binding=binding,
                needs_connect=False,
                auto_bootstrap=binding_was_created,
            ),
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
