"""Workflow template loading, precedence, and project storage."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

import stackos
from stackos.db.models import (
    Project,
    ProjectWorkflowTemplate,
    WorkflowTemplate,
    WorkflowTemplateVersion,
)
from stackos.repositories.base import ConflictError, Envelope, NotFoundError
from stackos.repositories.plugins import PluginRepository
from stackos.workflows.template_schema import (
    TemplateBaseSpec,
    WorkflowTemplateIssue,
    WorkflowTemplateSpec,
    WorkflowTemplateValidationOut,
    parse_workflow_template_yaml,
    validate_workflow_template_obj,
    validate_workflow_template_yaml,
)

PLUGIN_TEMPLATE_PRECEDENCE = 10
PROJECT_TEMPLATE_PRECEDENCE = 20
REPO_TEMPLATE_PRECEDENCE = 30
MAX_TEMPLATE_FILE_BYTES = 256_000


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _checksum(spec: WorkflowTemplateSpec) -> str:
    payload = json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _package_parent() -> Path:
    return Path(stackos.__file__).resolve().parent.parent


def _clone_plugins_root() -> Path | None:
    root = _package_parent() / "plugins"
    return root if root.is_dir() else None


def _bundled_plugins_root() -> Traversable | None:
    root = resources.files("stackos").joinpath("_assets").joinpath("plugins")
    return root if root.is_dir() else None


def _iter_yaml_paths(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        [
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
        ]
    )


def _iter_traversable_yaml(root: Traversable) -> list[Traversable]:
    out: list[Traversable] = []

    def walk(node: Traversable) -> None:
        for child in node.iterdir():
            if child.is_dir():
                walk(child)
            elif child.name.lower().endswith((".yaml", ".yml")):
                out.append(child)

    walk(root)
    return sorted(out, key=lambda item: str(item))


class WorkflowTemplateSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    name: str
    version: str
    description: str = ""
    domain: str | None = None
    source: str
    precedence: int
    plugin_slug: str | None = None
    project_id: int | None = None
    origin_path: str | None = None
    template_id: int | None = None
    version_id: int | None = None
    shadowed_by: str | None = None


class LoadedWorkflowTemplate(BaseModel):
    summary: WorkflowTemplateSummaryOut
    spec: WorkflowTemplateSpec


class WorkflowTemplateListOut(BaseModel):
    templates: list[WorkflowTemplateSummaryOut]
    include_shadowed: bool = False


@dataclass(frozen=True)
class _Candidate:
    template: LoadedWorkflowTemplate
    order: int


class WorkflowTemplateLoader:
    """Load workflow templates from plugin files, project DB rows, and repo overrides."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def list_templates(
        self,
        *,
        project_id: int | None = None,
        repo_root: str | None = None,
        plugin_slug: str | None = None,
        include_shadowed: bool = False,
    ) -> WorkflowTemplateListOut:
        if project_id is not None:
            self._require_project(project_id)
        candidates = self._load_candidates(
            project_id=project_id,
            repo_root=repo_root,
            plugin_slug=plugin_slug,
        )
        resolved = self._resolve_candidates(candidates, include_shadowed=include_shadowed)
        return WorkflowTemplateListOut(
            templates=[item.summary for item in resolved],
            include_shadowed=include_shadowed,
        )

    def describe_template(
        self,
        *,
        key: str,
        project_id: int | None = None,
        repo_root: str | None = None,
        plugin_slug: str | None = None,
        source: str | None = None,
    ) -> LoadedWorkflowTemplate:
        if project_id is not None:
            self._require_project(project_id)
        candidates = self._load_candidates(
            project_id=project_id,
            repo_root=repo_root,
            plugin_slug=plugin_slug,
        )
        resolved = self._resolve_candidates(candidates, include_shadowed=True)
        matches = [
            item
            for item in resolved
            if item.summary.key == key and (source is None or item.summary.source == source)
        ]
        if not matches:
            raise NotFoundError(
                f"workflow template {key!r} not found",
                data={"key": key, "project_id": project_id, "plugin_slug": plugin_slug},
            )
        matches.sort(key=lambda item: item.summary.precedence, reverse=True)
        return matches[0]

    def validate_template(
        self,
        *,
        template_json: dict[str, Any] | None = None,
        template_yaml: str | None = None,
    ) -> WorkflowTemplateValidationOut:
        if template_json is None and template_yaml is None:
            return WorkflowTemplateValidationOut(
                valid=False,
                errors=[
                    WorkflowTemplateIssue(
                        path="$",
                        message="template_json or template_yaml is required",
                        code="missing_template",
                    )
                ],
            )
        if template_json is not None and template_yaml is not None:
            return WorkflowTemplateValidationOut(
                valid=False,
                errors=[
                    WorkflowTemplateIssue(
                        path="$",
                        message="pass only one of template_json or template_yaml",
                        code="ambiguous_template",
                    )
                ],
            )
        if template_json is not None:
            return validate_workflow_template_obj(template_json)
        assert template_yaml is not None
        return validate_workflow_template_yaml(template_yaml)

    def save_project_template(
        self,
        *,
        project_id: int,
        spec: WorkflowTemplateSpec,
        source: str = "project",
        origin_path: str | None = None,
        created_by: str | None = None,
        enabled: bool = True,
    ) -> Envelope[LoadedWorkflowTemplate]:
        if source not in {"project", "user"}:
            raise ConflictError("only project/user templates can be saved through this API")
        self._require_project(project_id)
        now = _utcnow()
        checksum = _checksum(spec)
        row = self._s.exec(
            select(WorkflowTemplate).where(
                WorkflowTemplate.project_id == project_id,
                WorkflowTemplate.key == spec.key,
                WorkflowTemplate.source == source,
            )
        ).first()
        if row is None:
            row = WorkflowTemplate(
                project_id=project_id,
                key=spec.key,
                name=spec.name,
                description=spec.description,
                source=source,
                origin_path=origin_path,
                metadata_json=spec.metadata_json,
            )
        else:
            row.name = spec.name
            row.description = spec.description
            row.origin_path = origin_path if origin_path is not None else row.origin_path
            row.metadata_json = spec.metadata_json
            row.status = "active"
            row.updated_at = now
        self._s.add(row)
        self._s.flush()
        assert row.id is not None

        version = self._s.exec(
            select(WorkflowTemplateVersion).where(
                WorkflowTemplateVersion.template_id == row.id,
                WorkflowTemplateVersion.version == spec.version,
            )
        ).first()
        if version is not None and version.checksum != checksum:
            self._s.rollback()
            raise ConflictError(
                "workflow template version already exists with different content",
                data={"template_id": row.id, "version": spec.version},
            )
        if version is None:
            version = WorkflowTemplateVersion(
                template_id=row.id,
                version=spec.version,
                spec_json=spec.model_dump(mode="json"),
                checksum=checksum,
                created_by=created_by,
            )
            self._s.add(version)
            self._s.flush()
        assert version.id is not None

        link = self._s.exec(
            select(ProjectWorkflowTemplate).where(
                ProjectWorkflowTemplate.project_id == project_id,
                ProjectWorkflowTemplate.template_id == row.id,
            )
        ).first()
        if link is None:
            link = ProjectWorkflowTemplate(
                project_id=project_id,
                template_id=row.id,
                active_version_id=version.id,
                enabled=enabled,
            )
        else:
            link.active_version_id = version.id
            link.enabled = enabled
            link.updated_at = now
        self._s.add(link)
        try:
            self._s.commit()
        except IntegrityError as exc:
            self._s.rollback()
            raise ConflictError(
                "workflow template could not be saved",
                data={"project_id": project_id, "key": spec.key, "source": source},
            ) from exc
        loaded = self._loaded_from_db(row, version)
        return Envelope(data=loaded, project_id=project_id)

    def fork_template(
        self,
        *,
        project_id: int,
        key: str,
        new_key: str,
        repo_root: str | None = None,
        name: str | None = None,
        version: str = "0.1.0",
        created_by: str | None = None,
    ) -> Envelope[LoadedWorkflowTemplate]:
        source = self.describe_template(project_id=project_id, repo_root=repo_root, key=key)
        spec_data = source.spec.model_dump(mode="json")
        spec_data["key"] = new_key
        spec_data["name"] = name or f"{source.spec.name} Fork"
        spec_data["version"] = version
        spec_data["based_on"] = TemplateBaseSpec(
            key=source.spec.key,
            version=source.spec.version,
            source=source.summary.source,
            origin_path=source.summary.origin_path,
        ).model_dump(mode="json", exclude_none=True)
        forked = WorkflowTemplateSpec.model_validate(spec_data)
        return self.save_project_template(
            project_id=project_id,
            spec=forked,
            source="project",
            created_by=created_by,
        )

    def _load_candidates(
        self,
        *,
        project_id: int | None,
        repo_root: str | None,
        plugin_slug: str | None,
    ) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        order = 0
        for loaded in self._load_plugin_templates(plugin_slug=plugin_slug):
            candidates.append(_Candidate(loaded, order))
            order += 1
        if project_id is not None:
            project_templates = self._load_project_templates(
                project_id=project_id,
                plugin_slug=plugin_slug,
            )
            for loaded in project_templates:
                candidates.append(_Candidate(loaded, order))
                order += 1
        if repo_root is not None:
            for loaded in self._load_repo_templates(repo_root=repo_root, plugin_slug=plugin_slug):
                candidates.append(_Candidate(loaded, order))
                order += 1
        return candidates

    def _resolve_candidates(
        self,
        candidates: list[_Candidate],
        *,
        include_shadowed: bool,
    ) -> list[LoadedWorkflowTemplate]:
        by_key: dict[str, _Candidate] = {}
        for candidate in candidates:
            current = by_key.get(candidate.template.summary.key)
            if current is None or self._beats(candidate, current):
                by_key[candidate.template.summary.key] = candidate
        resolved: list[LoadedWorkflowTemplate] = []
        for candidate in candidates:
            winner = by_key[candidate.template.summary.key]
            template = candidate.template.model_copy(deep=True)
            if candidate is not winner:
                template.summary.shadowed_by = winner.template.summary.source
                if not include_shadowed:
                    continue
            resolved.append(template)
        resolved.sort(
            key=lambda item: (
                item.summary.key,
                -item.summary.precedence,
                item.summary.source,
            )
        )
        return resolved

    def _beats(self, left: _Candidate, right: _Candidate) -> bool:
        if left.template.summary.precedence != right.template.summary.precedence:
            return left.template.summary.precedence > right.template.summary.precedence
        return left.order > right.order

    def _load_plugin_templates(self, *, plugin_slug: str | None) -> list[LoadedWorkflowTemplate]:
        self._sync_builtin_plugins()
        loaded: list[LoadedWorkflowTemplate] = []
        seen_paths: set[str] = set()
        clone_root = _clone_plugins_root()
        if clone_root is not None:
            for path in _iter_yaml_paths(clone_root):
                if "/workflows/" not in path.as_posix():
                    continue
                if plugin_slug is not None and path.relative_to(clone_root).parts[0] != plugin_slug:
                    continue
                seen_paths.add(path.resolve().as_posix())
                loaded.append(self._loaded_from_file_path(path, clone_root=clone_root))
        bundled_root = _bundled_plugins_root()
        if bundled_root is not None:
            for node in _iter_traversable_yaml(bundled_root):
                node_key = str(node)
                if node_key in seen_paths:
                    continue
                parts = Path(node_key).parts
                if "workflows" not in parts:
                    continue
                plugin = parts[parts.index("workflows") - 1]
                if plugin_slug is not None and plugin != plugin_slug:
                    continue
                loaded.append(self._loaded_from_traversable(node, plugin_slug=plugin))
        return loaded

    def _load_repo_templates(
        self,
        *,
        repo_root: str,
        plugin_slug: str | None,
    ) -> list[LoadedWorkflowTemplate]:
        if plugin_slug is not None:
            return []
        root = Path(repo_root).expanduser().resolve()
        workflow_root = root / ".stackos" / "workflows"
        return [
            self._loaded_from_repo_path(path)
            for path in _iter_yaml_paths(workflow_root)
            if path.is_file()
        ]

    def _load_project_templates(
        self,
        *,
        project_id: int,
        plugin_slug: str | None,
    ) -> list[LoadedWorkflowTemplate]:
        if plugin_slug is not None:
            return []
        stmt = (
            select(WorkflowTemplate, WorkflowTemplateVersion)
            .join(
                ProjectWorkflowTemplate,
                col(ProjectWorkflowTemplate.template_id) == col(WorkflowTemplate.id),
            )
            .join(
                WorkflowTemplateVersion,
                col(ProjectWorkflowTemplate.active_version_id) == col(WorkflowTemplateVersion.id),
            )
            .where(
                col(WorkflowTemplate.project_id) == project_id,
                col(ProjectWorkflowTemplate.enabled).is_(True),
            )
        )
        rows = self._s.exec(stmt).all()
        return [self._loaded_from_db(template, version) for template, version in rows]

    def _loaded_from_file_path(self, path: Path, *, clone_root: Path) -> LoadedWorkflowTemplate:
        if path.stat().st_size > MAX_TEMPLATE_FILE_BYTES:
            raise ConflictError("workflow template file is too large", data={"path": str(path)})
        spec = parse_workflow_template_yaml(path.read_text(encoding="utf-8"))
        plugin_slug = path.relative_to(clone_root).parts[0]
        return self._loaded(
            spec,
            source="plugin",
            precedence=PLUGIN_TEMPLATE_PRECEDENCE,
            plugin_slug=plugin_slug,
            origin_path=path.as_posix(),
        )

    def _loaded_from_traversable(
        self,
        node: Traversable,
        *,
        plugin_slug: str,
    ) -> LoadedWorkflowTemplate:
        spec = parse_workflow_template_yaml(node.read_text(encoding="utf-8"))
        return self._loaded(
            spec,
            source="plugin",
            precedence=PLUGIN_TEMPLATE_PRECEDENCE,
            plugin_slug=plugin_slug,
            origin_path=str(node),
        )

    def _loaded_from_repo_path(self, path: Path) -> LoadedWorkflowTemplate:
        if path.stat().st_size > MAX_TEMPLATE_FILE_BYTES:
            raise ConflictError("workflow template file is too large", data={"path": str(path)})
        spec = parse_workflow_template_yaml(path.read_text(encoding="utf-8"))
        return self._loaded(
            spec,
            source="repo",
            precedence=REPO_TEMPLATE_PRECEDENCE,
            origin_path=path.as_posix(),
        )

    def _loaded_from_db(
        self,
        row: WorkflowTemplate,
        version: WorkflowTemplateVersion,
    ) -> LoadedWorkflowTemplate:
        spec = WorkflowTemplateSpec.model_validate(version.spec_json)
        return self._loaded(
            spec,
            source=row.source,
            precedence=PROJECT_TEMPLATE_PRECEDENCE,
            project_id=row.project_id,
            origin_path=row.origin_path,
            template_id=row.id,
            version_id=version.id,
        )

    def _loaded(
        self,
        spec: WorkflowTemplateSpec,
        *,
        source: str,
        precedence: int,
        plugin_slug: str | None = None,
        project_id: int | None = None,
        origin_path: str | None = None,
        template_id: int | None = None,
        version_id: int | None = None,
    ) -> LoadedWorkflowTemplate:
        return LoadedWorkflowTemplate(
            summary=WorkflowTemplateSummaryOut(
                key=spec.key,
                name=spec.name,
                version=spec.version,
                description=spec.description,
                domain=spec.domain,
                source=source,
                precedence=precedence,
                plugin_slug=plugin_slug,
                project_id=project_id,
                origin_path=origin_path,
                template_id=template_id,
                version_id=version_id,
            ),
            spec=spec,
        )

    def _sync_builtin_plugins(self) -> None:
        PluginRepository(self._s).sync_builtin_plugins()

    def _require_project(self, project_id: int) -> None:
        if self._s.get(Project, project_id) is None:
            raise NotFoundError(f"project {project_id} not found")


__all__ = [
    "PLUGIN_TEMPLATE_PRECEDENCE",
    "PROJECT_TEMPLATE_PRECEDENCE",
    "REPO_TEMPLATE_PRECEDENCE",
    "LoadedWorkflowTemplate",
    "WorkflowTemplateListOut",
    "WorkflowTemplateLoader",
    "WorkflowTemplateSummaryOut",
]
