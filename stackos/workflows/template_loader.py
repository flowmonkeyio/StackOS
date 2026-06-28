"""Workflow template loading, precedence, and project storage."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

import stackos
from stackos.db.models import (
    Project,
    ProjectWorkflowTemplate,
    WorkflowTemplate,
    WorkflowTemplateExtension,
    WorkflowTemplateVersion,
)
from stackos.plugins.manifest import plugin_sort_key
from stackos.repositories.base import ConflictError, Envelope, NotFoundError, ValidationError
from stackos.repositories.plugins import PluginRepository
from stackos.workflows.run_plan_schema import find_run_plan_secret_paths
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
_WORKFLOW_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:[-.][a-z0-9_]+)*$")
_TEMPLATE_OVERRIDE_ALIASES = {
    "metadata": "metadata_json",
    "extensions": "extensions_json",
    "ui": "ui_json",
}
_EXTENSION_JSON_FIELD_DEFAULTS: dict[str, Any] = {
    "input_defaults_json": {},
    "selected_context_json": {},
    "required_input_keys_json": [],
    "guardrails_json": {},
    "step_overrides_json": {},
    "template_overrides_json": {},
    "metadata_json": {},
}
_EXTENSION_JSON_FIELDS = frozenset(_EXTENSION_JSON_FIELD_DEFAULTS)
_EXTENSION_UPDATE_MODES = frozenset({"merge", "replace"})


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
    project_extension_id: int | None = None
    project_extension_enabled: bool = False


class WorkflowTemplateExtensionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    workflow_key: str
    enabled: bool
    input_defaults_json: dict[str, Any] = Field(default_factory=dict)
    selected_context_json: dict[str, Any] = Field(default_factory=dict)
    required_input_keys_json: list[str] = Field(default_factory=list)
    guardrails_json: dict[str, Any] = Field(default_factory=dict)
    step_overrides_json: dict[str, Any] = Field(default_factory=dict)
    template_overrides_json: dict[str, Any] = Field(default_factory=dict)
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class WorkflowTemplateExtensionUpsertOut(WorkflowTemplateExtensionOut):
    update_mode: str = "merge"
    changed_fields: list[str] = Field(default_factory=list)
    preserved_fields: list[str] = Field(default_factory=list)
    cleared_fields: list[str] = Field(default_factory=list)
    warnings: list[WorkflowTemplateIssue] = Field(default_factory=list)


class WorkflowTemplateExtensionListOut(BaseModel):
    extensions: list[WorkflowTemplateExtensionOut]


class WorkflowTemplateExtensionGetOut(BaseModel):
    extension: WorkflowTemplateExtensionOut | None = None


class WorkflowTemplateExtensionDeleteOut(BaseModel):
    deleted: WorkflowTemplateExtensionOut


class WorkflowTemplateExtensionValidationOut(BaseModel):
    valid: bool
    extension: WorkflowTemplateExtensionOut | None = None
    errors: list[WorkflowTemplateIssue] = Field(default_factory=list)
    warnings: list[WorkflowTemplateIssue] = Field(default_factory=list)


class LoadedWorkflowTemplate(BaseModel):
    summary: WorkflowTemplateSummaryOut
    spec: WorkflowTemplateSpec
    project_extension: WorkflowTemplateExtensionOut | None = None


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
        templates = [item.model_copy(deep=True) for item in resolved]
        if project_id is not None:
            self._attach_extension_summaries(templates, project_id=project_id)
        return WorkflowTemplateListOut(
            templates=[item.summary for item in templates],
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
        include_extension: bool = True,
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
        loaded = matches[0].model_copy(deep=True)
        if project_id is not None and include_extension:
            self._attach_extension(loaded, project_id=project_id)
        return loaded

    def validate_template(
        self,
        *,
        template_json: dict[str, Any] | None = None,
        template_yaml: str | None = None,
        key: str | None = None,
        project_id: int | None = None,
        repo_root: str | None = None,
        plugin_slug: str | None = None,
        source: str | None = None,
    ) -> WorkflowTemplateValidationOut:
        raw_inputs = [template_json is not None, template_yaml is not None, key is not None]
        if sum(1 for present in raw_inputs if present) == 0:
            return WorkflowTemplateValidationOut(
                valid=False,
                errors=[
                    WorkflowTemplateIssue(
                        path="$",
                        message="template_json, template_yaml, or key is required",
                        code="missing_template",
                    )
                ],
            )
        if sum(1 for present in raw_inputs if present) > 1:
            return WorkflowTemplateValidationOut(
                valid=False,
                errors=[
                    WorkflowTemplateIssue(
                        path="$",
                        message="pass only one of template_json, template_yaml, or key",
                        code="ambiguous_template",
                    )
                ],
            )
        if key is not None:
            loaded = self.describe_template(
                key=key,
                project_id=project_id,
                repo_root=repo_root,
                plugin_slug=plugin_slug,
                source=source,
                include_extension=True,
            )
            return validate_workflow_template_obj(loaded.spec.model_dump(mode="json"))
        if template_json is not None:
            return validate_workflow_template_obj(template_json)
        assert template_yaml is not None
        return validate_workflow_template_yaml(template_yaml)

    def get_extension(
        self,
        *,
        project_id: int,
        workflow_key: str,
        include_disabled: bool = True,
    ) -> WorkflowTemplateExtensionOut | None:
        self._require_project(project_id)
        stmt = select(WorkflowTemplateExtension).where(
            WorkflowTemplateExtension.project_id == project_id,
            WorkflowTemplateExtension.workflow_key == workflow_key,
        )
        if not include_disabled:
            stmt = stmt.where(col(WorkflowTemplateExtension.enabled).is_(True))
        row = self._s.exec(stmt).first()
        if row is None:
            return None
        return self._extension_out(row)

    def list_extensions(self, *, project_id: int) -> WorkflowTemplateExtensionListOut:
        self._require_project(project_id)
        rows = self._s.exec(
            select(WorkflowTemplateExtension)
            .where(WorkflowTemplateExtension.project_id == project_id)
            .order_by(WorkflowTemplateExtension.workflow_key)
        ).all()
        return WorkflowTemplateExtensionListOut(
            extensions=[self._extension_out(row) for row in rows],
        )

    def delete_extension(
        self,
        *,
        project_id: int,
        workflow_key: str,
    ) -> Envelope[WorkflowTemplateExtensionDeleteOut]:
        self._require_project(project_id)
        row = self._s.exec(
            select(WorkflowTemplateExtension).where(
                WorkflowTemplateExtension.project_id == project_id,
                WorkflowTemplateExtension.workflow_key == workflow_key,
            )
        ).first()
        if row is None:
            raise NotFoundError(
                f"workflow extension {workflow_key!r} not found",
                data={"project_id": project_id, "workflow_key": workflow_key},
            )
        deleted = self._extension_out(row)
        self._s.delete(row)
        self._s.commit()
        return Envelope(
            data=WorkflowTemplateExtensionDeleteOut(deleted=deleted),
            project_id=project_id,
        )

    def _extension_update_issues(
        self,
        *,
        update_mode: str,
        clear_fields_json: list[str] | None,
    ) -> list[WorkflowTemplateIssue]:
        issues: list[WorkflowTemplateIssue] = []
        if update_mode not in _EXTENSION_UPDATE_MODES:
            issues.append(
                WorkflowTemplateIssue(
                    path="update_mode",
                    code="invalid_update_mode",
                    message=(
                        "update_mode must be one of "
                        f"{', '.join(sorted(_EXTENSION_UPDATE_MODES))}"
                    ),
                )
            )
        if update_mode == "replace" and clear_fields_json:
            issues.append(
                WorkflowTemplateIssue(
                    path="clear_fields_json",
                    code="clear_fields_requires_merge",
                    message="clear_fields_json is only valid with update_mode='merge'",
                )
            )
        invalid_clear_fields = [
            field
            for field in list(dict.fromkeys(clear_fields_json or []))
            if field not in _EXTENSION_JSON_FIELDS
        ]
        if invalid_clear_fields:
            issues.append(
                WorkflowTemplateIssue(
                    path="clear_fields_json",
                    code="unknown_extension_field",
                    message=(
                        "clear_fields_json contains unknown extension fields: "
                        f"{', '.join(invalid_clear_fields)}"
                    ),
                )
            )
        return issues

    def _extension_current_values(
        self,
        row: WorkflowTemplateExtension | None,
    ) -> dict[str, Any]:
        if row is None:
            return {
                "enabled": True,
                **{
                    field: deepcopy(default)
                    for field, default in _EXTENSION_JSON_FIELD_DEFAULTS.items()
                },
            }
        return {
            "enabled": row.enabled,
            "input_defaults_json": deepcopy(row.input_defaults_json or {}),
            "selected_context_json": deepcopy(row.selected_context_json or {}),
            "required_input_keys_json": deepcopy(row.required_input_keys_json or []),
            "guardrails_json": deepcopy(row.guardrails_json or {}),
            "step_overrides_json": deepcopy(row.step_overrides_json or {}),
            "template_overrides_json": deepcopy(row.template_overrides_json or {}),
            "metadata_json": deepcopy(row.metadata_json or {}),
        }

    def _compose_extension_update_values(
        self,
        *,
        current: dict[str, Any],
        enabled: bool | None,
        provided_json: dict[str, Any],
        update_mode: str,
        clear_fields_json: list[str] | None,
    ) -> dict[str, Any]:
        if update_mode == "replace":
            return {
                "enabled": True if enabled is None else enabled,
                **{
                    field: deepcopy(provided_json[field])
                    if field in provided_json
                    else deepcopy(default)
                    for field, default in _EXTENSION_JSON_FIELD_DEFAULTS.items()
                },
            }
        desired = deepcopy(current)
        if enabled is not None:
            desired["enabled"] = enabled
        for field, value in provided_json.items():
            desired[field] = deepcopy(value)
        for field in dict.fromkeys(clear_fields_json or []):
            desired[field] = deepcopy(_EXTENSION_JSON_FIELD_DEFAULTS[field])
        return desired

    def validate_extension(
        self,
        *,
        project_id: int,
        workflow_key: str,
        enabled: bool | None = None,
        input_defaults_json: dict[str, Any] | None = None,
        selected_context_json: dict[str, Any] | None = None,
        required_input_keys_json: list[str] | None = None,
        guardrails_json: dict[str, Any] | None = None,
        step_overrides_json: dict[str, Any] | None = None,
        template_overrides_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        update_mode: str = "merge",
        clear_fields_json: list[str] | None = None,
        repo_root: str | None = None,
        plugin_slug: str | None = None,
        source: str | None = None,
    ) -> WorkflowTemplateExtensionValidationOut:
        self._require_project(project_id)
        errors: list[WorkflowTemplateIssue] = []
        warnings: list[WorkflowTemplateIssue] = []
        errors.extend(
            self._extension_update_issues(
                update_mode=update_mode,
                clear_fields_json=clear_fields_json,
            )
        )
        if not _WORKFLOW_KEY_RE.match(workflow_key):
            errors.append(
                WorkflowTemplateIssue(
                    path="workflow_key",
                    code="invalid_workflow_key",
                    message="workflow_key must be a lowercase snake/kebab/dotted identifier",
                )
            )

        loaded: LoadedWorkflowTemplate | None = None
        if not errors:
            try:
                loaded = self.describe_template(
                    key=workflow_key,
                    project_id=project_id,
                    repo_root=repo_root,
                    plugin_slug=plugin_slug,
                    source=source,
                    include_extension=False,
                )
            except Exception as exc:
                errors.append(
                    WorkflowTemplateIssue(
                        path="workflow_key",
                        code="unknown_workflow_template",
                        message=str(exc),
                    )
                )

        row = None
        if not errors:
            row = self._s.exec(
                select(WorkflowTemplateExtension).where(
                    WorkflowTemplateExtension.project_id == project_id,
                    WorkflowTemplateExtension.workflow_key == workflow_key,
                )
            ).first()
        provided_json = {
            field: value
            for field, value in {
                "input_defaults_json": input_defaults_json,
                "selected_context_json": selected_context_json,
                "required_input_keys_json": required_input_keys_json,
                "guardrails_json": guardrails_json,
                "step_overrides_json": step_overrides_json,
                "template_overrides_json": template_overrides_json,
                "metadata_json": metadata_json,
            }.items()
            if value is not None
        }
        safe_update_mode = update_mode if update_mode in _EXTENSION_UPDATE_MODES else "merge"
        safe_clear_fields = [
            field for field in (clear_fields_json or []) if field in _EXTENSION_JSON_FIELDS
        ]
        desired = self._compose_extension_update_values(
            current=self._extension_current_values(row),
            enabled=enabled,
            provided_json=provided_json,
            update_mode=safe_update_mode,
            clear_fields_json=safe_clear_fields,
        )

        payloads = {
            "input_defaults_json": desired["input_defaults_json"],
            "selected_context_json": desired["selected_context_json"],
            "required_input_keys_json": desired["required_input_keys_json"],
            "guardrails_json": desired["guardrails_json"],
            "step_overrides_json": desired["step_overrides_json"],
            "template_overrides_json": desired["template_overrides_json"],
            "metadata_json": desired["metadata_json"],
        }
        for label, value in payloads.items():
            paths = find_run_plan_secret_paths(value)
            if paths:
                errors.append(
                    WorkflowTemplateIssue(
                        path=label,
                        code="secret_reference",
                        message=(
                            "workflow extensions must not contain secrets; use safe refs. "
                            f"Suspicious paths: {', '.join(paths[:8])}"
                        ),
                    )
                )

        input_defaults = payloads["input_defaults_json"]
        selected_context = payloads["selected_context_json"]
        guardrails = payloads["guardrails_json"]
        step_overrides = payloads["step_overrides_json"]
        template_overrides = payloads["template_overrides_json"]
        if not isinstance(input_defaults, dict):
            errors.append(
                WorkflowTemplateIssue(
                    path="input_defaults_json",
                    code="invalid_extension_field",
                    message="input_defaults_json must be an object",
                )
            )
        if not isinstance(selected_context, dict):
            errors.append(
                WorkflowTemplateIssue(
                    path="selected_context_json",
                    code="invalid_extension_field",
                    message="selected_context_json must be an object",
                )
            )
        if not isinstance(guardrails, dict):
            errors.append(
                WorkflowTemplateIssue(
                    path="guardrails_json",
                    code="invalid_extension_field",
                    message="guardrails_json must be an object",
                )
            )
        if not isinstance(step_overrides, dict):
            errors.append(
                WorkflowTemplateIssue(
                    path="step_overrides_json",
                    code="invalid_extension_field",
                    message="step_overrides_json must be an object keyed by step id",
                )
            )
        if not isinstance(template_overrides, dict):
            errors.append(
                WorkflowTemplateIssue(
                    path="template_overrides_json",
                    code="invalid_extension_field",
                    message="template_overrides_json must be an object keyed by workflow field",
                )
            )

        effective_spec = loaded.spec if loaded is not None else None
        if loaded is not None and isinstance(template_overrides, dict) and template_overrides:
            override_validation = self._validate_template_overrides(
                loaded.spec,
                workflow_key=workflow_key,
                template_overrides_json=template_overrides,
            )
            if not override_validation.valid:
                errors.extend(override_validation.errors)
            else:
                effective_spec = override_validation.template

        template_input_keys = (
            {item.key for item in effective_spec.inputs} if effective_spec else set()
        )
        step_ids = {step.id for step in effective_spec.steps} if effective_spec else set()
        required_keys = payloads["required_input_keys_json"]
        if not isinstance(required_keys, list):
            errors.append(
                WorkflowTemplateIssue(
                    path="required_input_keys_json",
                    code="invalid_extension_field",
                    message="required_input_keys_json must be a list of input keys",
                )
            )
        else:
            seen: set[str] = set()
            for index, raw_key in enumerate(required_keys):
                path = f"required_input_keys_json[{index}]"
                if not isinstance(raw_key, str) or not _WORKFLOW_KEY_RE.match(raw_key):
                    errors.append(
                        WorkflowTemplateIssue(
                            path=path,
                            code="invalid_input_key",
                            message="required input keys must be lowercase identifiers",
                        )
                    )
                    continue
                if raw_key in seen:
                    errors.append(
                        WorkflowTemplateIssue(
                            path=path,
                            code="duplicate_required_input",
                            message=f"required input key {raw_key!r} is duplicated",
                        )
                    )
                seen.add(raw_key)
                if (
                    loaded is not None
                    and raw_key not in template_input_keys
                    and raw_key not in input_defaults
                ):
                    warnings.append(
                        WorkflowTemplateIssue(
                            path=path,
                            code="extension_only_required_input",
                            message=(
                                f"{raw_key!r} is not declared by the effective template. "
                                "It will still be enforced as a project extension input."
                            ),
                        )
                    )

        if isinstance(step_overrides, dict):
            allowed_keys = {
                "extra_instructions",
                "instructions_append",
                "instructions_prepend",
                "success_criteria",
                "success_criteria_append",
                "metadata",
                "metadata_json",
            }
            for step_id, override in step_overrides.items():
                if loaded is not None and step_id not in step_ids:
                    errors.append(
                        WorkflowTemplateIssue(
                            path=f"step_overrides_json.{step_id}",
                            code="unknown_step",
                            message=f"step override references unknown step {step_id!r}",
                        )
                    )
                    continue
                if not isinstance(override, dict):
                    errors.append(
                        WorkflowTemplateIssue(
                            path=f"step_overrides_json.{step_id}",
                            code="invalid_step_override",
                            message="step override must be an object",
                        )
                    )
                    continue
                for key, value in override.items():
                    if key not in allowed_keys:
                        warnings.append(
                            WorkflowTemplateIssue(
                                path=f"step_overrides_json.{step_id}.{key}",
                                code="unknown_step_override_key",
                                message=(
                                    f"{key!r} is stored as metadata but is not applied by "
                                    "runPlan.create"
                                ),
                            )
                        )
                        continue
                    if key in {
                        "extra_instructions",
                        "instructions_append",
                        "instructions_prepend",
                        "success_criteria",
                        "success_criteria_append",
                    } and (
                        not isinstance(value, list)
                        or any(not isinstance(item, str) for item in value)
                    ):
                        errors.append(
                            WorkflowTemplateIssue(
                                path=f"step_overrides_json.{step_id}.{key}",
                                code="invalid_step_override",
                                message=f"{key} must be a list of strings",
                            )
                        )
                    if key in {"metadata", "metadata_json"} and not isinstance(value, dict):
                        errors.append(
                            WorkflowTemplateIssue(
                                path=f"step_overrides_json.{step_id}.{key}",
                                code="invalid_step_override",
                                message=f"{key} must be an object",
                            )
                        )

        return WorkflowTemplateExtensionValidationOut(
            valid=not errors,
            errors=errors,
            warnings=warnings,
        )

    def upsert_extension(
        self,
        *,
        project_id: int,
        workflow_key: str,
        enabled: bool | None = None,
        input_defaults_json: dict[str, Any] | None = None,
        selected_context_json: dict[str, Any] | None = None,
        required_input_keys_json: list[str] | None = None,
        guardrails_json: dict[str, Any] | None = None,
        step_overrides_json: dict[str, Any] | None = None,
        template_overrides_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        update_mode: str = "merge",
        clear_fields_json: list[str] | None = None,
        repo_root: str | None = None,
        plugin_slug: str | None = None,
        source: str | None = None,
        created_by: str | None = None,
    ) -> Envelope[WorkflowTemplateExtensionUpsertOut]:
        update_issues = self._extension_update_issues(
            update_mode=update_mode,
            clear_fields_json=clear_fields_json,
        )
        if update_issues:
            raise ValidationError(
                "workflow extension update is invalid",
                data={
                    "errors": [item.model_dump(mode="json") for item in update_issues],
                    "allowed_update_modes": sorted(_EXTENSION_UPDATE_MODES),
                    "clearable_fields": sorted(_EXTENSION_JSON_FIELDS),
                },
            )

        now = _utcnow()
        row = self._s.exec(
            select(WorkflowTemplateExtension).where(
                WorkflowTemplateExtension.project_id == project_id,
                WorkflowTemplateExtension.workflow_key == workflow_key,
            )
        ).first()
        existing = row is not None
        current = self._extension_current_values(row)
        provided_json = {
            field: value
            for field, value in {
                "input_defaults_json": input_defaults_json,
                "selected_context_json": selected_context_json,
                "required_input_keys_json": required_input_keys_json,
                "guardrails_json": guardrails_json,
                "step_overrides_json": step_overrides_json,
                "template_overrides_json": template_overrides_json,
                "metadata_json": metadata_json,
            }.items()
            if value is not None
        }
        desired = self._compose_extension_update_values(
            current=current,
            enabled=enabled,
            provided_json=provided_json,
            update_mode=update_mode,
            clear_fields_json=clear_fields_json,
        )

        validation = self.validate_extension(
            project_id=project_id,
            workflow_key=workflow_key,
            enabled=desired["enabled"],
            input_defaults_json=desired["input_defaults_json"],
            selected_context_json=desired["selected_context_json"],
            required_input_keys_json=desired["required_input_keys_json"],
            guardrails_json=desired["guardrails_json"],
            step_overrides_json=desired["step_overrides_json"],
            template_overrides_json=desired["template_overrides_json"],
            metadata_json=desired["metadata_json"],
            update_mode="replace",
            repo_root=repo_root,
            plugin_slug=plugin_slug,
            source=source,
        )
        if not validation.valid:
            raise ValidationError(
                "workflow extension is invalid",
                data={"errors": [item.model_dump(mode="json") for item in validation.errors]},
            )

        changed_fields = [
            field
            for field in ("enabled", *_EXTENSION_JSON_FIELDS)
            if current.get(field) != desired.get(field)
        ]
        cleared_fields = [
            field
            for field in _EXTENSION_JSON_FIELDS
            if current.get(field) not in (None, [], {})
            and desired.get(field) == _EXTENSION_JSON_FIELD_DEFAULTS[field]
        ]
        preserved_fields = [
            field
            for field in ("enabled", *_EXTENSION_JSON_FIELDS)
            if field not in changed_fields
        ]
        if row is None:
            row = WorkflowTemplateExtension(
                project_id=project_id,
                workflow_key=workflow_key,
                enabled=desired["enabled"],
                input_defaults_json=desired["input_defaults_json"],
                selected_context_json=desired["selected_context_json"],
                required_input_keys_json=desired["required_input_keys_json"],
                guardrails_json=desired["guardrails_json"],
                step_overrides_json=desired["step_overrides_json"],
                template_overrides_json=desired["template_overrides_json"],
                metadata_json=desired["metadata_json"],
                created_by=created_by,
            )
        else:
            row.enabled = desired["enabled"]
            row.input_defaults_json = desired["input_defaults_json"]
            row.selected_context_json = desired["selected_context_json"]
            row.required_input_keys_json = desired["required_input_keys_json"]
            row.guardrails_json = desired["guardrails_json"]
            row.step_overrides_json = desired["step_overrides_json"]
            row.template_overrides_json = desired["template_overrides_json"]
            row.metadata_json = desired["metadata_json"]
            if created_by is not None:
                row.created_by = created_by
            row.updated_at = now
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        extension = self._extension_out(row).model_dump(mode="python")
        extension.update(
            {
                "update_mode": update_mode,
                "changed_fields": sorted(changed_fields),
                "preserved_fields": sorted(preserved_fields),
                "cleared_fields": sorted(cleared_fields),
                "warnings": validation.warnings,
            }
        )
        if existing and update_mode == "replace" and cleared_fields:
            extension["warnings"] = [
                *extension["warnings"],
                WorkflowTemplateIssue(
                    path="update_mode",
                    code="replace_cleared_omitted_fields",
                    message=(
                        "update_mode='replace' cleared omitted workflow extension fields; "
                        "use update_mode='merge' for partial updates."
                    ),
                ),
            ]
        return Envelope(
            data=WorkflowTemplateExtensionUpsertOut.model_validate(extension),
            project_id=project_id,
        )

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
                *plugin_sort_key(item.summary.plugin_slug, None),
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

    def _attach_extension_summaries(
        self,
        templates: list[LoadedWorkflowTemplate],
        *,
        project_id: int,
    ) -> None:
        rows = self._s.exec(
            select(WorkflowTemplateExtension).where(
                WorkflowTemplateExtension.project_id == project_id,
            )
        ).all()
        by_key = {row.workflow_key: row for row in rows}
        for template in templates:
            row = by_key.get(template.summary.key)
            if row is None:
                continue
            extension = self._extension_out(row)
            template.project_extension = extension
            self._apply_extension_to_loaded(template, extension)

    def _attach_extension(
        self,
        loaded: LoadedWorkflowTemplate,
        *,
        project_id: int,
    ) -> None:
        row = self._s.exec(
            select(WorkflowTemplateExtension).where(
                WorkflowTemplateExtension.project_id == project_id,
                WorkflowTemplateExtension.workflow_key == loaded.summary.key,
            )
        ).first()
        if row is None:
            return
        extension = self._extension_out(row)
        loaded.project_extension = extension
        self._apply_extension_to_loaded(loaded, extension)

    def _apply_extension_to_loaded(
        self,
        loaded: LoadedWorkflowTemplate,
        extension: WorkflowTemplateExtensionOut,
    ) -> None:
        loaded.summary.project_extension_id = extension.id
        loaded.summary.project_extension_enabled = bool(extension.enabled)
        if not extension.enabled:
            return
        if extension.template_overrides_json:
            loaded.spec = self._apply_template_overrides(
                loaded.spec,
                workflow_key=extension.workflow_key,
                template_overrides_json=extension.template_overrides_json,
            )
            loaded.summary.key = loaded.spec.key
            loaded.summary.name = loaded.spec.name
            loaded.summary.version = loaded.spec.version
            loaded.summary.description = loaded.spec.description
            loaded.summary.domain = loaded.spec.domain

    def _validate_template_overrides(
        self,
        spec: WorkflowTemplateSpec,
        *,
        workflow_key: str,
        template_overrides_json: dict[str, Any],
    ) -> WorkflowTemplateValidationOut:
        data = self._template_override_data(spec, template_overrides_json)
        validation = validate_workflow_template_obj(data)
        if not validation.valid or validation.template is None:

            def _issue_path(issue: WorkflowTemplateIssue) -> str:
                return (
                    "template_overrides_json"
                    if issue.path == "$"
                    else f"template_overrides_json.{issue.path}"
                )

            return WorkflowTemplateValidationOut(
                valid=False,
                errors=[
                    WorkflowTemplateIssue(
                        path=_issue_path(issue),
                        code=issue.code,
                        message=issue.message,
                    )
                    for issue in validation.errors
                ],
                warnings=validation.warnings,
            )
        if validation.template.key != workflow_key:
            return WorkflowTemplateValidationOut(
                valid=False,
                errors=[
                    WorkflowTemplateIssue(
                        path="template_overrides_json.key",
                        code="workflow_key_mismatch",
                        message=(
                            "template_overrides_json.key must match workflow_key; "
                            "use workflowTemplate.fork for a new workflow identity"
                        ),
                    )
                ],
            )
        return validation

    def _apply_template_overrides(
        self,
        spec: WorkflowTemplateSpec,
        *,
        workflow_key: str,
        template_overrides_json: dict[str, Any],
    ) -> WorkflowTemplateSpec:
        validation = self._validate_template_overrides(
            spec,
            workflow_key=workflow_key,
            template_overrides_json=template_overrides_json,
        )
        if not validation.valid or validation.template is None:
            messages = "; ".join(item.message for item in validation.errors) or "invalid override"
            raise ValueError(messages)
        return validation.template

    @staticmethod
    def _template_override_data(
        spec: WorkflowTemplateSpec,
        template_overrides_json: dict[str, Any],
    ) -> dict[str, Any]:
        data = spec.model_dump(mode="json")
        for key, value in template_overrides_json.items():
            target_key = _TEMPLATE_OVERRIDE_ALIASES.get(key, key)
            if target_key != key and target_key in data:
                data.pop(key, None)
            data[target_key] = value
        return data

    def _extension_out(self, row: WorkflowTemplateExtension) -> WorkflowTemplateExtensionOut:
        if row.id is None:
            raise RuntimeError("expected persisted workflow extension")
        return WorkflowTemplateExtensionOut(
            id=row.id,
            project_id=row.project_id,
            workflow_key=row.workflow_key,
            enabled=row.enabled,
            input_defaults_json=row.input_defaults_json or {},
            selected_context_json=row.selected_context_json or {},
            required_input_keys_json=row.required_input_keys_json or [],
            guardrails_json=row.guardrails_json or {},
            step_overrides_json=row.step_overrides_json or {},
            template_overrides_json=row.template_overrides_json or {},
            metadata_json=row.metadata_json or {},
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

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
    "WorkflowTemplateExtensionGetOut",
    "WorkflowTemplateExtensionListOut",
    "WorkflowTemplateExtensionOut",
    "WorkflowTemplateExtensionUpsertOut",
    "WorkflowTemplateExtensionValidationOut",
    "WorkflowTemplateListOut",
    "WorkflowTemplateLoader",
    "WorkflowTemplateSummaryOut",
]
