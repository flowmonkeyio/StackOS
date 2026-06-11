"""Repository for provider action execution contexts."""

from __future__ import annotations

import builtins
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy import select as sa_select
from sqlmodel import Session, col, select

from stackos.actions.manifest import ExecutableActionManifest, parse_action_manifest
from stackos.actions.repository.utils import _schema_issues
from stackos.artifacts import redact_secrets
from stackos.db.models import (
    Action,
    Artifact,
    Credential,
    ExecutionContext,
    ExecutionContextArtifact,
    ExecutionContextLink,
    Plugin,
    Project,
    Provider,
)
from stackos.generated_inventory import (
    generated_action_public_key,
    generated_action_visible_for_project,
)
from stackos.repositories.base import ConflictError, Envelope, NotFoundError, Page, ValidationError
from stackos.workflows.run_plan_schema import find_run_plan_secret_paths

_REF_SAFE = re.compile(r"[^a-z0-9_]+")
_VALID_STATUSES = {"active", "disabled", "archived"}
_VALID_LINK_TYPES = {"task", "ticket", "run_plan", "run", "run_plan_step"}
_VALID_OUTPUT_MODES = {"inline", "file_if_large", "always_file"}


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


_VALID_REQUEST_BUDGET_FIELDS = {
    "max_parallel",
    "max_calls",
    "max_calls_per_run",
    "window_seconds",
    "notes",
}


class ExecutionContextLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    context_id: int
    link_type: str
    link_ref: str
    role: str
    metadata_json: dict[str, Any] | None = None
    created_at: datetime


class ExecutionContextOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    context_ref: str
    name: str
    description: str
    plugin_slug: str | None = None
    provider_key: str | None = None
    action_ref: str | None = None
    credential_ref: str | None = None
    credential_locked: bool
    provider_context_json: dict[str, Any] = Field(default_factory=dict)
    provider_context_locked_fields_json: list[str] = Field(default_factory=list)
    output_policy_json: dict[str, Any] = Field(default_factory=dict)
    request_budget_json: dict[str, Any] = Field(default_factory=dict)
    artifact_namespace: str | None = None
    status: str
    metadata_json: dict[str, Any] | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    links: list[ExecutionContextLinkOut] = Field(default_factory=list)
    artifact_count: int = 0


class ExecutionContextArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    context_id: int
    context_ref: str
    artifact_id: int
    action_call_id: int | None = None
    semantic_name: str | None = None
    action_ref: str | None = None
    input_hash: str | None = None
    metadata_json: dict[str, Any] | None = None
    created_at: datetime
    artifact: dict[str, Any] = Field(default_factory=dict)


class ExecutionContextResolveOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context: ExecutionContextOut
    action_ref: str | None = None
    compatible: bool
    issues: list[dict[str, Any]] = Field(default_factory=list)
    credential_ref: str | None = None
    provider_context_json: dict[str, Any] = Field(default_factory=dict)
    provider_context_schema_json: dict[str, Any] = Field(default_factory=dict)
    provider_context_schema_source: str | None = None
    output_policy_json: dict[str, Any] = Field(default_factory=dict)
    request_budget_json: dict[str, Any] = Field(default_factory=dict)
    artifact_namespace: str | None = None
    next_call: dict[str, Any] = Field(default_factory=dict)


class ExecutionContextDiscoveryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int
    filters_json: dict[str, Any] = Field(default_factory=dict)
    context_refs: list[str] = Field(default_factory=list)
    items: list[ExecutionContextOut] = Field(default_factory=list)
    next_cursor: int | None = None
    total_estimate: int = 0
    next_calls: dict[str, Any] = Field(default_factory=dict)


class ExecutionContextRepository:
    """Read and write agent-facing execution contexts."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self,
        *,
        project_id: int,
        name: str,
        context_ref: str | None = None,
        description: str = "",
        plugin_slug: str | None = None,
        provider_key: str | None = None,
        action_ref: str | None = None,
        credential_ref: str | None = None,
        credential_locked: bool = False,
        provider_context_json: dict[str, Any] | None = None,
        provider_context_locked_fields_json: list[str] | None = None,
        output_policy_json: dict[str, Any] | None = None,
        request_budget_json: dict[str, Any] | None = None,
        artifact_namespace: str | None = None,
        metadata_json: dict[str, Any] | None = None,
        links_json: list[dict[str, Any]] | None = None,
        created_by: str | None = None,
    ) -> Envelope[ExecutionContextOut]:
        self._require_project(project_id)
        name = _clean_required(name, "name")
        action_manifest = self._manifest_for_ref(
            action_ref,
            project_id=project_id,
            credential_ref=credential_ref,
        )
        plugin_slug, provider_key = self._derive_scope(
            action_manifest=action_manifest,
            plugin_slug=plugin_slug,
            provider_key=provider_key,
        )
        provider_context = self._clean_object(provider_context_json, "provider_context_json")
        output_policy = self._clean_object(output_policy_json, "output_policy_json")
        self._validate_output_policy(output_policy)
        request_budget = self._clean_object(request_budget_json, "request_budget_json")
        self._validate_request_budget(request_budget)
        metadata = self._clean_optional_object(metadata_json, "metadata_json")
        locked_fields = self._clean_string_list(
            provider_context_locked_fields_json,
            "provider_context_locked_fields_json",
        )
        self._validate_locked_fields(locked_fields)
        self._validate_provider_context(action_manifest, provider_context)
        self._validate_credential(
            project_id=project_id,
            credential_ref=credential_ref,
            provider_key=provider_key,
        )
        ref = self._create_ref(project_id=project_id, name=name, context_ref=context_ref)
        row = ExecutionContext(
            project_id=project_id,
            context_ref=ref,
            name=name,
            description=description or "",
            plugin_slug=plugin_slug,
            provider_key=provider_key,
            action_ref=action_manifest.action_ref if action_manifest else action_ref,
            credential_ref=credential_ref,
            credential_locked=credential_locked,
            provider_context_json=provider_context,
            provider_context_locked_fields_json=locked_fields,
            output_policy_json=output_policy,
            request_budget_json=request_budget,
            artifact_namespace=artifact_namespace,
            status="active",
            metadata_json=metadata,
            created_by=created_by,
        )
        self._s.add(row)
        self._s.flush()
        assert row.id is not None
        for link in links_json or []:
            self._upsert_link(row, **self._normalize_link_args(link))
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._out(row), project_id=project_id)

    def update(
        self,
        *,
        project_id: int,
        context_ref: str,
        patch_json: dict[str, Any],
    ) -> Envelope[ExecutionContextOut]:
        row = self._row(context_ref=context_ref, project_id=project_id)
        patch = dict(patch_json or {})
        allowed = {
            "name",
            "description",
            "credential_ref",
            "credential_locked",
            "provider_context_json",
            "provider_context_locked_fields_json",
            "output_policy_json",
            "request_budget_json",
            "artifact_namespace",
            "status",
            "metadata_json",
        }
        unknown = sorted(set(patch) - allowed)
        if unknown:
            raise ValidationError(
                "unsupported execution context patch fields",
                data={"fields": unknown},
            )
        if "name" in patch:
            row.name = _clean_required(patch["name"], "name")
        if "description" in patch:
            row.description = str(patch["description"] or "")
        if "credential_ref" in patch:
            credential_ref = patch["credential_ref"]
            if credential_ref is not None and not isinstance(credential_ref, str):
                raise ValidationError("credential_ref must be a string")
            self._validate_credential(
                project_id=row.project_id,
                credential_ref=credential_ref,
                provider_key=row.provider_key,
            )
            row.credential_ref = credential_ref
        if "credential_locked" in patch:
            row.credential_locked = bool(patch["credential_locked"])
        if "provider_context_json" in patch:
            provider_context = self._clean_object(
                patch["provider_context_json"],
                "provider_context_json",
            )
            self._validate_provider_context(
                self._manifest_for_ref(row.action_ref, project_id=row.project_id),
                provider_context,
            )
            row.provider_context_json = provider_context
        if "provider_context_locked_fields_json" in patch:
            row.provider_context_locked_fields_json = self._clean_string_list(
                patch["provider_context_locked_fields_json"],
                "provider_context_locked_fields_json",
            )
            self._validate_locked_fields(row.provider_context_locked_fields_json)
        if "output_policy_json" in patch:
            row.output_policy_json = self._clean_object(
                patch["output_policy_json"],
                "output_policy_json",
            )
            self._validate_output_policy(row.output_policy_json)
        if "request_budget_json" in patch:
            row.request_budget_json = self._clean_object(
                patch["request_budget_json"],
                "request_budget_json",
            )
            self._validate_request_budget(row.request_budget_json)
        if "artifact_namespace" in patch:
            value = patch["artifact_namespace"]
            row.artifact_namespace = str(value) if value not in {None, ""} else None
        if "status" in patch:
            status = str(patch["status"] or "")
            if status not in _VALID_STATUSES:
                raise ValidationError("invalid execution context status", data={"status": status})
            row.status = status
        if "metadata_json" in patch:
            row.metadata_json = self._clean_optional_object(patch["metadata_json"], "metadata_json")
        row.updated_at = _utcnow()
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._out(row), project_id=row.project_id)

    def get(
        self,
        *,
        context_ref: str,
        project_id: int,
    ) -> ExecutionContextOut:
        return self._out(self._row(context_ref=context_ref, project_id=project_id))

    def list(
        self,
        *,
        project_id: int,
        plugin_slug: str | None = None,
        provider_key: str | None = None,
        action_ref: str | None = None,
        status: str | None = "active",
        task_key: str | None = None,
        ticket_key: str | None = None,
        run_plan_id: int | None = None,
        run_id: int | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[ExecutionContextOut]:
        n = _normalise_limit(limit)
        filters: list[Any] = [col(ExecutionContext.project_id) == project_id]
        if plugin_slug is not None:
            filters.append(col(ExecutionContext.plugin_slug) == plugin_slug)
        if provider_key is not None:
            filters.append(col(ExecutionContext.provider_key) == provider_key)
        if action_ref is not None:
            filters.append(col(ExecutionContext.action_ref) == action_ref)
        if status is not None:
            filters.append(col(ExecutionContext.status) == status)
        link_refs = _link_filters(
            task_key=task_key,
            ticket_key=ticket_key,
            run_plan_id=run_plan_id,
            run_id=run_id,
        )
        if link_refs:
            link_ids = self._context_ids_for_links(project_id=project_id, link_refs=link_refs)
            if not link_ids:
                return Page(items=[], total_estimate=0)
            filters.append(col(ExecutionContext.id).in_(link_ids))
        count_stmt = sa_select(func.count()).select_from(ExecutionContext).where(*filters)
        total = _count_value(self._s.exec(count_stmt).one())  # type: ignore[call-overload]
        row_filters = list(filters)
        if after_id is not None:
            row_filters.append(col(ExecutionContext.id) > after_id)
        rows = list(
            self._s.exec(
                select(ExecutionContext)
                .where(*row_filters)
                .order_by(col(ExecutionContext.id).asc())
                .limit(n + 1)
            ).all()
        )
        page_rows = rows[:n]
        next_cursor = page_rows[-1].id if len(rows) > n and page_rows else None
        return Page(
            items=[self._out(row) for row in page_rows],
            next_cursor=next_cursor,
            total_estimate=total,
        )

    def discover(
        self,
        *,
        project_id: int,
        plugin_slug: str | None = None,
        provider_key: str | None = None,
        action_ref: str | None = None,
        status: str | None = "active",
        task_key: str | None = None,
        ticket_key: str | None = None,
        run_plan_id: int | None = None,
        run_id: int | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> ExecutionContextDiscoveryOut:
        filters_json = _clean_filter_payload(
            plugin_slug=plugin_slug,
            provider_key=provider_key,
            action_ref=action_ref,
            status=status,
            task_key=task_key,
            ticket_key=ticket_key,
            run_plan_id=run_plan_id,
            run_id=run_id,
        )
        page = self.list(
            project_id=project_id,
            plugin_slug=plugin_slug,
            provider_key=provider_key,
            action_ref=action_ref,
            status=status,
            task_key=task_key,
            ticket_key=ticket_key,
            run_plan_id=run_plan_id,
            run_id=run_id,
            limit=limit,
            after_id=after_id,
        )
        context_refs = [item.context_ref for item in page.items]
        link_calls = _link_call_hints(
            project_id=project_id,
            task_key=task_key,
            ticket_key=ticket_key,
            run_plan_id=run_plan_id,
            run_id=run_id,
        )
        create_call = _create_call_hint(
            project_id=project_id,
            plugin_slug=plugin_slug,
            provider_key=provider_key,
            action_ref=action_ref,
            task_key=task_key,
            ticket_key=ticket_key,
            run_plan_id=run_plan_id,
            run_id=run_id,
        )
        return ExecutionContextDiscoveryOut(
            project_id=project_id,
            filters_json=filters_json,
            context_refs=context_refs,
            items=page.items,
            next_cursor=page.next_cursor,
            total_estimate=page.total_estimate,
            next_calls={
                "list": {
                    "operation": "executionContext.list",
                    "arguments": {"project_id": project_id, **filters_json},
                },
                "link": {
                    "operation": "executionContext.link",
                    "arguments": link_calls[0],
                },
                "link_all_supplied_scopes": [
                    {"operation": "executionContext.link", "arguments": link_call}
                    for link_call in link_calls
                ],
                "create": {
                    "operation": "executionContext.create",
                    "arguments": create_call,
                    "notes": [
                        "add credential_ref only when selecting a stored credential reference",
                        (
                            "add provider_context_json only for provider/account scope, "
                            "never endpoint payload"
                        ),
                    ],
                },
                "resolve": [
                    {
                        "operation": "executionContext.resolve",
                        "arguments": {
                            "project_id": project_id,
                            "context_ref": context_ref,
                            **({"action_ref": action_ref} if action_ref else {}),
                        },
                    }
                    for context_ref in context_refs[:5]
                ],
            },
        )

    def resolve(
        self,
        *,
        context_ref: str,
        project_id: int,
        action_ref: str | None = None,
    ) -> ExecutionContextResolveOut:
        row = self._row(context_ref=context_ref, project_id=project_id)
        issues: list[dict[str, Any]] = []
        resolved_action_ref = action_ref or row.action_ref
        manifest: ExecutableActionManifest | None = None
        if resolved_action_ref is not None:
            try:
                manifest = self._manifest_for_ref(
                    resolved_action_ref,
                    project_id=row.project_id,
                    credential_ref=row.credential_ref,
                )
            except NotFoundError as exc:
                issues.append(
                    {
                        "path": "$.action_ref",
                        "code": "action_not_found",
                        "message": exc.detail,
                        "data": exc.data,
                    }
                )
            except ValidationError as exc:
                issues.append(
                    {
                        "path": "$.action_ref",
                        "code": "action_invalid",
                        "message": exc.detail,
                        "data": exc.data,
                    }
                )
        if row.status != "active":
            issues.append(
                {
                    "path": "$.context_ref",
                    "code": "context_not_active",
                    "message": f"execution context is {row.status}",
                }
            )
        if manifest is not None:
            if row.plugin_slug and row.plugin_slug != manifest.plugin_slug:
                issues.append(
                    {
                        "path": "$.action_ref",
                        "code": "context_plugin_mismatch",
                        "message": "context plugin does not match action",
                    }
                )
            if row.provider_key and row.provider_key != manifest.provider_key:
                issues.append(
                    {
                        "path": "$.action_ref",
                        "code": "context_provider_mismatch",
                        "message": "context provider does not match action",
                    }
                )
        issues.extend(self._provider_context_issues(manifest, row.provider_context_json))
        if row.credential_ref is not None:
            try:
                self._validate_credential(
                    project_id=row.project_id,
                    credential_ref=row.credential_ref,
                    provider_key=row.provider_key or (manifest.provider_key if manifest else None),
                )
            except (ValidationError, NotFoundError) as exc:
                issues.append(
                    {
                        "path": "$.credential_ref",
                        "code": "credential_invalid",
                        "message": exc.detail,
                        "data": exc.data,
                    }
                )
        return ExecutionContextResolveOut(
            context=self._out(row),
            action_ref=resolved_action_ref,
            compatible=not issues,
            issues=issues,
            credential_ref=row.credential_ref,
            provider_context_json=dict(row.provider_context_json or {}),
            provider_context_schema_json=(
                dict(manifest.provider_context_schema_json or {}) if manifest else {}
            ),
            provider_context_schema_source=manifest.action_ref if manifest else None,
            output_policy_json=dict(row.output_policy_json or {}),
            request_budget_json=dict(row.request_budget_json or {}),
            artifact_namespace=row.artifact_namespace,
            next_call={
                key: value
                for key, value in {
                    "context_ref": row.context_ref,
                    "action_ref": resolved_action_ref,
                }.items()
                if value is not None
            },
        )

    def link(
        self,
        *,
        context_ref: str,
        project_id: int,
        link_type: str,
        link_ref: str,
        role: str = "default",
        metadata_json: dict[str, Any] | None = None,
    ) -> Envelope[ExecutionContextOut]:
        row = self._row(context_ref=context_ref, project_id=project_id)
        self._upsert_link(
            row,
            link_type=link_type,
            link_ref=link_ref,
            role=role,
            metadata_json=metadata_json,
        )
        self._s.commit()
        return Envelope(data=self._out(row), project_id=row.project_id)

    def unlink(
        self,
        *,
        context_ref: str,
        project_id: int,
        link_type: str,
        link_ref: str,
        role: str | None = None,
    ) -> Envelope[ExecutionContextOut]:
        row = self._row(context_ref=context_ref, project_id=project_id)
        filters: list[Any] = [
            col(ExecutionContextLink.context_id) == row.id,
            col(ExecutionContextLink.link_type) == link_type,
            col(ExecutionContextLink.link_ref) == link_ref,
        ]
        if role is not None:
            filters.append(col(ExecutionContextLink.role) == role)
        links = list(self._s.exec(select(ExecutionContextLink).where(*filters)).all())
        for link in links:
            self._s.delete(link)
        self._s.commit()
        return Envelope(data=self._out(row), project_id=row.project_id)

    def register_artifact(
        self,
        *,
        context_ref: str,
        artifact_id: int,
        project_id: int,
        action_call_id: int | None = None,
        semantic_name: str | None = None,
        action_ref: str | None = None,
        input_hash: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> ExecutionContextArtifactOut:
        row = self._row(context_ref=context_ref, project_id=project_id)
        artifact = self._s.get(Artifact, artifact_id)
        if artifact is None or artifact.project_id != row.project_id:
            raise NotFoundError(
                f"artifact {artifact_id} not found",
                data={"project_id": row.project_id, "artifact_id": artifact_id},
            )
        existing = self._s.exec(
            select(ExecutionContextArtifact).where(
                col(ExecutionContextArtifact.context_id) == row.id,
                col(ExecutionContextArtifact.artifact_id) == artifact_id,
            )
        ).first()
        if existing is None:
            existing = ExecutionContextArtifact(
                project_id=row.project_id,
                context_id=row.id,
                artifact_id=artifact_id,
            )
        existing.action_call_id = action_call_id
        existing.semantic_name = semantic_name
        existing.action_ref = action_ref
        existing.input_hash = input_hash
        existing.metadata_json = self._clean_optional_object(metadata_json, "metadata_json")
        self._s.add(existing)
        self._s.commit()
        self._s.refresh(existing)
        return self._artifact_out(existing, row, artifact)

    def get_artifact(
        self,
        *,
        context_ref: str,
        artifact_id: int,
        project_id: int,
    ) -> ExecutionContextArtifactOut:
        row = self._row(context_ref=context_ref, project_id=project_id)
        result = self._s.exec(
            select(ExecutionContextArtifact, Artifact)
            .join(Artifact, col(ExecutionContextArtifact.artifact_id) == col(Artifact.id))
            .where(
                col(ExecutionContextArtifact.context_id) == row.id,
                col(ExecutionContextArtifact.artifact_id) == artifact_id,
            )
        ).first()
        if result is None:
            raise NotFoundError(
                "artifact is not registered under this execution context",
                data={"context_ref": context_ref, "artifact_id": artifact_id},
            )
        link, artifact = result
        return self._artifact_out(link, row, artifact)

    def list_artifacts(
        self,
        *,
        context_ref: str,
        project_id: int,
        action_ref: str | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[ExecutionContextArtifactOut]:
        row = self._row(context_ref=context_ref, project_id=project_id)
        n = _normalise_limit(limit)
        filters: list[Any] = [col(ExecutionContextArtifact.context_id) == row.id]
        if action_ref is not None:
            filters.append(col(ExecutionContextArtifact.action_ref) == action_ref)
        count_stmt = sa_select(func.count()).select_from(ExecutionContextArtifact).where(*filters)
        total = _count_value(self._s.exec(count_stmt).one())  # type: ignore[call-overload]
        row_filters = list(filters)
        if after_id is not None:
            row_filters.append(col(ExecutionContextArtifact.id) > after_id)
        rows = list(
            self._s.exec(
                select(ExecutionContextArtifact, Artifact)
                .join(Artifact, col(ExecutionContextArtifact.artifact_id) == col(Artifact.id))
                .where(*row_filters)
                .order_by(col(ExecutionContextArtifact.id).asc())
                .limit(n + 1)
            ).all()
        )
        page_rows = rows[:n]
        next_cursor = page_rows[-1][0].id if len(rows) > n and page_rows else None
        return Page(
            items=[self._artifact_out(link, row, artifact) for link, artifact in page_rows],
            next_cursor=next_cursor,
            total_estimate=total,
        )

    def _out(self, row: ExecutionContext) -> ExecutionContextOut:
        assert row.id is not None
        links = list(
            self._s.exec(
                select(ExecutionContextLink)
                .where(col(ExecutionContextLink.context_id) == row.id)
                .order_by(col(ExecutionContextLink.id).asc())
            ).all()
        )
        artifact_count = _count_value(
            self._s.exec(
                sa_select(func.count())
                .select_from(ExecutionContextArtifact)
                .where(col(ExecutionContextArtifact.context_id) == row.id)
            ).one()  # type: ignore[call-overload]
        )
        return ExecutionContextOut(
            id=row.id,
            project_id=row.project_id,
            context_ref=row.context_ref,
            name=row.name,
            description=row.description,
            plugin_slug=row.plugin_slug,
            provider_key=row.provider_key,
            action_ref=row.action_ref,
            credential_ref=row.credential_ref,
            credential_locked=row.credential_locked,
            provider_context_json=dict(row.provider_context_json or {}),
            provider_context_locked_fields_json=list(row.provider_context_locked_fields_json or []),
            output_policy_json=dict(row.output_policy_json or {}),
            request_budget_json=dict(row.request_budget_json or {}),
            artifact_namespace=row.artifact_namespace,
            status=row.status,
            metadata_json=row.metadata_json,
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
            links=[ExecutionContextLinkOut.model_validate(link) for link in links],
            artifact_count=artifact_count,
        )

    def _artifact_out(
        self,
        row: ExecutionContextArtifact,
        context: ExecutionContext,
        artifact: Artifact,
    ) -> ExecutionContextArtifactOut:
        assert row.id is not None
        assert artifact.id is not None
        return ExecutionContextArtifactOut(
            id=row.id,
            project_id=row.project_id,
            context_id=row.context_id,
            context_ref=context.context_ref,
            artifact_id=artifact.id,
            action_call_id=row.action_call_id,
            semantic_name=row.semantic_name,
            action_ref=row.action_ref,
            input_hash=row.input_hash,
            metadata_json=row.metadata_json,
            created_at=row.created_at,
            artifact={
                "id": artifact.id,
                "kind": artifact.kind,
                "uri": artifact.uri,
                "name": artifact.name,
                "mime_type": artifact.mime_type,
                "size_bytes": artifact.size_bytes,
                "metadata_json": artifact.metadata_json,
                "provenance_json": artifact.provenance_json,
                "created_at": artifact.created_at.isoformat(),
            },
        )

    def _row(self, *, context_ref: str, project_id: int) -> ExecutionContext:
        if project_id is None:
            raise ValidationError("project_id is required for execution context lookup")
        ref = _clean_required(context_ref, "context_ref")
        filters: list[Any] = [
            col(ExecutionContext.project_id) == project_id,
            col(ExecutionContext.context_ref) == ref,
        ]
        row = self._s.exec(select(ExecutionContext).where(*filters)).first()
        if row is None:
            raise NotFoundError(f"execution context {context_ref!r} not found")
        return row

    def _create_ref(self, *, project_id: int, name: str, context_ref: str | None) -> str:
        if context_ref is not None:
            ref = _clean_context_ref(context_ref)
            if self._ref_exists(project_id=project_id, context_ref=ref):
                raise ConflictError(
                    "execution context ref already exists",
                    data={"context_ref": ref},
                )
            return ref
        base = _REF_SAFE.sub("_", name.strip().lower()).strip("_") or "context"
        digest = _short_digest({"project_id": project_id, "name": name})
        ref = f"ctx_{base[:60]}_{digest}"
        suffix = 1
        candidate = ref
        while self._ref_exists(project_id=project_id, context_ref=candidate):
            suffix += 1
            candidate = f"{ref}_{suffix}"
        return candidate

    def _ref_exists(self, *, project_id: int, context_ref: str) -> bool:
        return (
            self._s.exec(
                select(ExecutionContext.id).where(
                    col(ExecutionContext.project_id) == project_id,
                    col(ExecutionContext.context_ref) == context_ref,
                )
            ).first()
            is not None
        )

    def _require_project(self, project_id: int) -> None:
        if self._s.get(Project, project_id) is None:
            raise NotFoundError(f"project {project_id} not found")

    def _manifest_for_ref(
        self,
        action_ref: str | None,
        *,
        project_id: int | None,
        credential_ref: str | None = None,
    ) -> ExecutableActionManifest | None:
        if not action_ref:
            return None
        if "." not in action_ref:
            raise ValidationError("action_ref must be plugin.action")
        plugin_slug, action_key = action_ref.split(".", 1)
        row: tuple[Action, Plugin, Provider | None] | None = self._s.exec(
            select(Action, Plugin, Provider)
            .join(Plugin, col(Action.plugin_id) == col(Plugin.id))
            .outerjoin(Provider, col(Action.provider_id) == col(Provider.id))
            .where(col(Plugin.slug) == plugin_slug, col(Action.key) == action_key)
        ).first()
        if row is not None:
            action, _plugin, _provider = row
            public_action_key = generated_action_public_key(action.config_json)
            public_key_mismatch = public_action_key is not None and public_action_key != action_key
            if public_key_mismatch or not generated_action_visible_for_project(
                config_json=action.config_json,
                project_id=project_id,
                action_key=action.key,
            ):
                row = None
        if row is None:
            row = self._generated_public_action_row(
                plugin_slug=plugin_slug,
                action_key=action_key,
                project_id=project_id,
                credential_ref=credential_ref,
            )
        if row is None:
            raise NotFoundError(f"action {action_ref!r} not found")
        action, plugin, provider = row
        return parse_action_manifest(action=action, plugin=plugin, provider=provider)

    def _generated_public_action_row(
        self,
        *,
        plugin_slug: str,
        action_key: str,
        project_id: int | None,
        credential_ref: str | None,
    ) -> tuple[Action, Plugin, Provider | None] | None:
        if project_id is None:
            return None
        rows = self._s.exec(
            select(Action, Plugin, Provider)
            .join(Plugin, col(Action.plugin_id) == col(Plugin.id))
            .outerjoin(Provider, col(Action.provider_id) == col(Provider.id))
            .where(col(Plugin.slug) == plugin_slug)
        ).all()
        candidates: list[tuple[Action, Plugin, Provider | None]] = []
        for candidate in rows:
            action, _plugin, _provider = candidate
            if generated_action_public_key(action.config_json) != action_key:
                continue
            if not generated_action_visible_for_project(
                config_json=action.config_json,
                project_id=project_id,
                action_key=action.key,
            ):
                continue
            candidates.append(candidate)
        if credential_ref:
            filtered = [
                candidate
                for candidate in candidates
                if isinstance(candidate[0].config_json, dict)
                and candidate[0].config_json.get("inventory_credential_ref") == credential_ref
            ]
            if filtered:
                candidates = filtered
        if not candidates:
            return None
        if len(candidates) > 1:
            candidates.sort(key=lambda candidate: candidate[0].updated_at, reverse=True)
            latest_updated_at = candidates[0][0].updated_at
            latest = [
                candidate
                for candidate in candidates
                if candidate[0].updated_at == latest_updated_at
            ]
            if len(latest) > 1:
                raise ConflictError(
                    "generated action ref is ambiguous for this project",
                    data={
                        "action_key": action_key,
                        "project_id": project_id,
                        "candidate_keys": [candidate[0].key for candidate in latest[:8]],
                    },
                )
        return candidates[0]

    def _derive_scope(
        self,
        *,
        action_manifest: ExecutableActionManifest | None,
        plugin_slug: str | None,
        provider_key: str | None,
    ) -> tuple[str | None, str | None]:
        if action_manifest is None:
            return plugin_slug, provider_key
        if plugin_slug is not None and plugin_slug != action_manifest.plugin_slug:
            raise ValidationError(
                "plugin_slug does not match action_ref",
                data={"plugin_slug": plugin_slug, "action_ref": action_manifest.action_ref},
            )
        if provider_key is not None and provider_key != action_manifest.provider_key:
            raise ValidationError(
                "provider_key does not match action_ref",
                data={"provider_key": provider_key, "action_ref": action_manifest.action_ref},
            )
        return action_manifest.plugin_slug, action_manifest.provider_key

    def _validate_provider_context(
        self,
        action_manifest: ExecutableActionManifest | None,
        provider_context: dict[str, Any],
    ) -> None:
        issues = self._provider_context_issues(action_manifest, provider_context)
        if issues:
            raise ValidationError(
                "provider context is invalid",
                data={"issues": issues},
            )

    def _provider_context_issues(
        self,
        action_manifest: ExecutableActionManifest | None,
        provider_context: dict[str, Any],
    ) -> builtins.list[dict[str, Any]]:
        if not provider_context:
            return []
        if action_manifest is None:
            return [
                {
                    "path": "$.provider_context_json",
                    "code": "provider_context_schema_required",
                    "message": (
                        "provider context requires an action_ref with provider_context_schema"
                    ),
                }
            ]
        if not action_manifest.provider_context_schema_json:
            return [
                {
                    "path": "$.provider_context_json",
                    "code": "provider_context_not_allowed",
                    "message": "provider context is not allowed for this action",
                    "data": {"action_ref": action_manifest.action_ref},
                }
            ]
        return [
            issue.model_dump(mode="json")
            for issue in _schema_issues(
                action_manifest.provider_context_schema_json,
                provider_context,
                path="$.provider_context_json",
            )
        ]

    def _validate_credential(
        self,
        *,
        project_id: int,
        credential_ref: str | None,
        provider_key: str | None,
    ) -> None:
        if credential_ref is None:
            return
        credential = self._s.exec(
            select(Credential).where(col(Credential.credential_ref) == credential_ref)
        ).first()
        if credential is None:
            raise NotFoundError(f"credential ref {credential_ref!r} not found")
        if credential.revoked_at is not None:
            raise ValidationError("credential is revoked", data={"credential_ref": credential_ref})
        if credential.status != "connected":
            raise ValidationError(
                f"credential is {credential.status}",
                data={"credential_ref": credential_ref},
            )
        if credential.project_id is not None and credential.project_id != project_id:
            raise ValidationError(
                "credential does not belong to this project",
                data={"credential_ref": credential_ref, "project_id": project_id},
            )
        if provider_key is not None and credential.provider_key != provider_key:
            raise ValidationError(
                "credential provider does not match execution context provider",
                data={
                    "credential_ref": credential_ref,
                    "credential_provider": credential.provider_key,
                    "provider_key": provider_key,
                },
            )

    def _clean_object(self, value: dict[str, Any] | None, field: str) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValidationError(f"{field} must be an object")
        _reject_secrets(value, field=field)
        return redact_secrets(dict(value))

    def _clean_optional_object(
        self,
        value: dict[str, Any] | None,
        field: str,
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        return self._clean_object(value, field)

    def _clean_string_list(
        self,
        value: builtins.list[str] | None,
        field: str,
    ) -> builtins.list[str]:
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValidationError(f"{field} must be a string array")
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    def _validate_locked_fields(self, fields: builtins.list[str]) -> None:
        unsupported = [field for field in fields if _locked_field_key(field) is None]
        if unsupported:
            raise ValidationError(
                "provider_context_locked_fields_json supports top-level provider "
                "context fields only",
                data={"fields": unsupported},
            )

    def _validate_output_policy(self, policy: dict[str, Any]) -> None:
        if not policy:
            return
        allowed = {"mode", "max_inline_bytes", "semantic_name", "content_type"}
        unknown = sorted(set(policy) - allowed)
        if unknown:
            raise ValidationError("unsupported output_policy_json fields", data={"fields": unknown})
        mode = str(policy.get("mode") or "inline")
        if mode not in _VALID_OUTPUT_MODES:
            raise ValidationError(
                "invalid output policy mode",
                data={"mode": mode, "accepted": sorted(_VALID_OUTPUT_MODES)},
            )
        max_inline_bytes = policy.get("max_inline_bytes")
        if max_inline_bytes is not None and (
            not isinstance(max_inline_bytes, int)
            or isinstance(max_inline_bytes, bool)
            or max_inline_bytes < 1
        ):
            raise ValidationError("output_policy_json.max_inline_bytes must be a positive integer")
        for field in ("semantic_name", "content_type"):
            value = policy.get(field)
            if value is not None and not isinstance(value, str):
                raise ValidationError(f"output_policy_json.{field} must be a string")
        content_type = policy.get("content_type")
        if content_type is not None and content_type != "application/json":
            raise ValidationError(
                "output_policy_json.content_type must be application/json for file-backed outputs",
                data={"content_type": content_type},
            )

    def _validate_request_budget(self, budget: dict[str, Any]) -> None:
        if not budget:
            return
        unknown = sorted(set(budget) - _VALID_REQUEST_BUDGET_FIELDS)
        if unknown:
            raise ValidationError(
                "unsupported request_budget_json fields",
                data={"fields": unknown},
            )
        for field in ("max_parallel", "max_calls", "max_calls_per_run", "window_seconds"):
            value = budget.get(field)
            if value is None:
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValidationError(f"request_budget_json.{field} must be a positive integer")
        notes = budget.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise ValidationError("request_budget_json.notes must be a string")

    def _normalize_link_args(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValidationError("links_json items must be objects")
        return {
            "link_type": str(raw.get("link_type") or ""),
            "link_ref": str(raw.get("link_ref") or ""),
            "role": str(raw.get("role") or "default"),
            "metadata_json": (
                raw.get("metadata_json") if isinstance(raw.get("metadata_json"), dict) else None
            ),
        }

    def _upsert_link(
        self,
        row: ExecutionContext,
        *,
        link_type: str,
        link_ref: str,
        role: str = "default",
        metadata_json: dict[str, Any] | None = None,
    ) -> None:
        assert row.id is not None
        link_type = _clean_required(link_type, "link_type")
        link_ref = _clean_required(link_ref, "link_ref")
        role = _clean_required(role, "role")
        if link_type not in _VALID_LINK_TYPES:
            raise ValidationError(
                "invalid execution context link_type",
                data={"link_type": link_type},
            )
        metadata = self._clean_optional_object(metadata_json, "metadata_json")
        existing = self._s.exec(
            select(ExecutionContextLink).where(
                col(ExecutionContextLink.context_id) == row.id,
                col(ExecutionContextLink.link_type) == link_type,
                col(ExecutionContextLink.link_ref) == link_ref,
                col(ExecutionContextLink.role) == role,
            )
        ).first()
        if existing is None:
            existing = ExecutionContextLink(
                project_id=row.project_id,
                context_id=row.id,
                link_type=link_type,
                link_ref=link_ref,
                role=role,
                metadata_json=metadata,
            )
        else:
            existing.metadata_json = metadata
        self._s.add(existing)

    def _context_ids_for_links(
        self,
        *,
        project_id: int,
        link_refs: builtins.list[tuple[str, str]],
    ) -> builtins.list[int]:
        matching: set[int] | None = None
        for link_type, link_ref in link_refs:
            ids = {
                int(item)
                for item in self._s.exec(
                    select(ExecutionContextLink.context_id).where(
                        col(ExecutionContextLink.project_id) == project_id,
                        col(ExecutionContextLink.link_type) == link_type,
                        col(ExecutionContextLink.link_ref) == link_ref,
                    )
                ).all()
            }
            matching = ids if matching is None else matching.intersection(ids)
            if not matching:
                return []
        return sorted(matching or set())


def _clean_required(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} is required")
    return value.strip()


def _clean_context_ref(value: str) -> str:
    ref = _clean_required(value, "context_ref")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{3,160}", ref):
        raise ValidationError(
            "context_ref must be 3-160 characters using letters, numbers, _, ., :, or -"
        )
    return ref


def _locked_field_key(value: str) -> str | None:
    field = value.strip()
    if not field:
        return None
    if field.startswith("$."):
        field = field[2:]
    if field.startswith("."):
        field = field[1:]
    if "." in field or "[" in field or "]" in field:
        return None
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:-]*", field):
        return None
    return field


def _reject_secrets(value: dict[str, Any], *, field: str) -> None:
    secret_paths = find_run_plan_secret_paths(value)
    if secret_paths:
        raise ValidationError(
            f"{field} must not contain secrets; use opaque credential_ref values",
            data={"paths": secret_paths[:8]},
        )


def _short_digest(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def _normalise_limit(limit: int | None) -> int:
    if limit is None:
        return 50
    if limit < 1:
        raise ValidationError("limit must be >= 1")
    if limit > 200:
        raise ValidationError("limit must be <= 200")
    return limit


def _count_value(value: Any) -> int:
    try:
        return int(value[0])
    except (TypeError, KeyError, IndexError):
        return int(value)


def _link_filters(
    *,
    task_key: str | None,
    ticket_key: str | None,
    run_plan_id: int | None,
    run_id: int | None,
) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    if task_key:
        refs.append(("task", task_key))
    if ticket_key:
        refs.append(("ticket", ticket_key))
    if run_plan_id is not None:
        refs.append(("run_plan", str(run_plan_id)))
    if run_id is not None:
        refs.append(("run", str(run_id)))
    return refs


def _link_call_hints(
    *,
    project_id: int,
    task_key: str | None,
    ticket_key: str | None,
    run_plan_id: int | None,
    run_id: int | None,
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    if task_key:
        targets.append({"task_key": task_key})
    if ticket_key:
        targets.append({"ticket_key": ticket_key})
    if run_plan_id is not None:
        targets.append({"run_plan_id": run_plan_id})
    if run_id is not None:
        targets.append({"run_id": run_id})
    if not targets:
        targets.append({"task_key": "task_or_workflow_key"})
    return [
        {"project_id": project_id, "context_ref": "ctx_existing", **target} for target in targets
    ]


def _create_call_hint(
    *,
    project_id: int,
    plugin_slug: str | None,
    provider_key: str | None,
    action_ref: str | None,
    task_key: str | None,
    ticket_key: str | None,
    run_plan_id: int | None,
    run_id: int | None,
) -> dict[str, Any]:
    context_name = action_ref or provider_key or plugin_slug or "provider action"
    args: dict[str, Any] = {
        "project_id": project_id,
        "name": f"{context_name} context",
    }
    for key, value in {
        "plugin_slug": plugin_slug,
        "provider_key": provider_key,
        "action_ref": action_ref,
        "task_key": task_key,
        "ticket_key": ticket_key,
        "run_plan_id": run_plan_id,
        "run_id": run_id,
    }.items():
        if value is not None and value != "":
            args[key] = value
    return args


def _clean_filter_payload(
    *,
    plugin_slug: str | None,
    provider_key: str | None,
    action_ref: str | None,
    status: str | None,
    task_key: str | None,
    ticket_key: str | None,
    run_plan_id: int | None,
    run_id: int | None,
) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "plugin_slug": plugin_slug,
            "provider_key": provider_key,
            "action_ref": action_ref,
            "status": status,
            "task_key": task_key,
            "ticket_key": ticket_key,
            "run_plan_id": run_plan_id,
            "run_id": run_id,
        }.items()
        if value is not None and value != ""
    }


__all__ = [
    "ExecutionContextArtifactOut",
    "ExecutionContextDiscoveryOut",
    "ExecutionContextLinkOut",
    "ExecutionContextOut",
    "ExecutionContextRepository",
    "ExecutionContextResolveOut",
]
