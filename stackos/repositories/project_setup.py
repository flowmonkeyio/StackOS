"""Project-owned applied setup evidence, independent of workflow execution.

Agents assess source obligations and adaptations. This owner validates their
explicit evidence contract; it never infers semantic equivalence from hashes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel import Session, col, select

from stackos.agents.loader import AgentPresetLoader
from stackos.db.models import (
    ProjectSetupReceipt,
    ProjectWorkflowTemplate,
    WorkflowTemplate,
    WorkflowTemplateExtension,
)
from stackos.repositories.base import ConflictError, RepositoryError, ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.skill_presets.loader import SkillPresetLoader
from stackos.workflows.template_loader import WorkflowTemplateLoader


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class SetupEvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetupSource(SetupEvidenceModel):
    kind: Literal["template", "main_agent", "specialist", "override"]
    key: str
    origin: str
    ref: str
    version: str | None = None
    digest: str
    # Receipt-owned bounded evidence keeps the previous base resolvable even
    # after an ephemeral package/repo source disappears. Live overrides stay put.
    content: dict[str, Any]


class SetupRoleMapping(SetupEvidenceModel):
    source_ref: str
    role: str
    decision: Literal["materialized", "session_only", "omitted"]
    target_paths: list[str] = Field(default_factory=list)
    reason: str | None = None


class SetupObligation(SetupEvidenceModel):
    source_ref: str
    obligation_ref: str = Field(min_length=1)
    required: bool = True
    disposition: Literal["retained", "adapted", "omitted", "unresolved"]
    target_paths: list[str] = Field(default_factory=list)
    reason: str | None = None
    omission_permitted: bool = False
    omission_policy_ref: str | None = None


class SetupCheck(SetupEvidenceModel):
    status: Literal["passed", "failed", "not_applicable", "unknown"]
    evidence_ref: str = Field(min_length=1)
    reason: str | None = None


class SetupReadiness(SetupEvidenceModel):
    structurally_ready: bool
    context_status: str = Field(min_length=1)
    required_providers_ready: bool
    execution_ready: bool
    structural_validation_ref: str = Field(min_length=1)
    strict_validation_ref: str = Field(min_length=1)
    deferred_inputs: list[str] = Field(default_factory=list)


class SetupLocalAdaptation(SetupEvidenceModel):
    path: str = Field(min_length=1)
    digest: str = Field(pattern="^[0-9a-f]{64}$")
    provenance: str = Field(min_length=1)


class VerifiedSetup(SetupEvidenceModel):
    sources: list[SetupSource] = Field(min_length=1, max_length=100)
    selected_optional_presets: list[str] = Field(default_factory=list, max_length=100)
    role_mapping: list[SetupRoleMapping]
    obligations: list[SetupObligation] = Field(min_length=1, max_length=2000)
    # Each adapted file/entrypoint records digest, provenance and local decisions.
    local_adaptations: list[SetupLocalAdaptation] = Field(min_length=1, max_length=100)
    checks: dict[Literal["syntax", "registration", "references", "behavior"], SetupCheck]
    activation: Literal["verified_loaded", "reload_required", "unknown"]
    activation_evidence_ref: str = Field(min_length=1)
    readiness: SetupReadiness

    @model_validator(mode="after")
    def evidence_complete(self) -> VerifiedSetup:
        if len(self.model_dump_json().encode()) > 2_000_000:
            raise ValueError("setup evidence exceeds 2 MB")
        refs = {source.ref for source in self.sources}
        if len(refs) != len(self.sources):
            raise ValueError("source refs must be unique")
        for source in self.sources:
            if source.digest != _digest(source.content):
                raise ValueError("source evidence digest does not match content")
        if set(self.checks) != {"syntax", "registration", "references", "behavior"}:
            raise ValueError("all host and behavior checks are required")
        for check in self.checks.values():
            if check.status not in {"passed", "not_applicable"} or (
                check.status == "not_applicable" and not check.reason
            ):
                raise ValueError("setup checks must pass or justify non-applicability")
        if not self.readiness.structurally_ready:
            raise ValueError("structural readiness is required")
        for item in self.obligations:
            if item.source_ref not in refs:
                raise ValueError("obligation refers to an unknown source")
            if item.disposition == "unresolved" or (
                item.disposition == "omitted"
                and (
                    not item.reason
                    or (
                        item.required and not (item.omission_permitted and item.omission_policy_ref)
                    )
                )
            ):
                raise ValueError("unresolved or unjustified obligation loss prevents success")
            if item.disposition in {"retained", "adapted"} and not item.target_paths:
                raise ValueError("retained obligations need target paths")
            if item.disposition == "adapted" and not item.reason:
                raise ValueError("compatible adaptations need a reason")
        if {item.source_ref for item in self.obligations} != refs:
            raise ValueError("every source needs explicit obligation coverage")
        return self


class SetupResult(SetupEvidenceModel):
    attempt_ref: str = Field(min_length=1, max_length=300)
    result_ref: str = Field(min_length=1, max_length=300)
    status: Literal["success", "failed", "unfinished", "needs_input", "cancelled", "interrupted"]
    changes: list[str] = Field(default_factory=list, max_length=100)
    diagnostics: list[str] = Field(default_factory=list, max_length=100)
    verification: VerifiedSetup | None = None

    @model_validator(mode="after")
    def success_needs_proof(self) -> SetupResult:
        if self.status == "success" and self.verification is None:
            raise ValueError("success requires verified setup evidence")
        if len(self.model_dump_json().encode()) > 2_000_000:
            raise ValueError("setup result exceeds 2 MB")
        return self


class SetupWorkflowOut(BaseModel):
    workflow_key: str
    receipt_id: int | None = None
    revision: int = 0
    status: Literal[
        "unknown", "current", "update_available", "source_unavailable", "activation_pending"
    ]
    successful: dict[str, Any] | None = None
    latest_result: dict[str, Any] | None = None
    available_sources: list[SetupSource] = Field(default_factory=list)
    diagnostic: str | None = None
    latest_status: str | None = None
    partial_changes: bool = False
    attempt_ref: str | None = None
    result_ref: str | None = None


class ProjectSetupOut(BaseModel):
    project_id: int
    eligible: bool
    folder_status: Literal["directoryless", "available", "unavailable"]
    status: Literal[
        "ineligible", "folder_unavailable", "setup_needed", "updates_available", "current"
    ]
    binding_id: int | None = None
    project_folder_issue: str | None = None
    setup_reminder_dismissed_at: datetime | None = None
    update_reminder_dismissed_at: datetime | None = None
    configured_count: int = 0
    update_count: int = 0
    unknown_count: int = 0
    workflows: list[SetupWorkflowOut] = Field(default_factory=list)
    detail_ref: dict[str, Any]
    next_action: str


class ProjectSetupRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def inspect_sources(
        self,
        project_id: int,
        workflow_key: str,
        *,
        selected_optional_presets: list[str] | None = None,
        _comparison: bool = False,
    ) -> list[SetupSource]:
        project = ProjectRepository(self._s)
        project.require_available(project_id)
        root = project.get(project_id).project_dir
        # Reads must not synchronize the plugin catalog or commit the project
        # availability reservation held by a receipt writer.
        workflow = WorkflowTemplateLoader(self._s, sync_plugins=False).describe_template(
            key=workflow_key, project_id=project_id, repo_root=root
        )
        summary = workflow.summary
        origin = f"{summary.source}:{summary.plugin_slug or summary.template_id or ''}"
        sources = [
            self._source(
                "template",
                workflow_key,
                origin,
                summary.version,
                workflow.spec.model_dump(mode="json"),
            )
        ]
        selected = set(selected_optional_presets or [])
        declared_optional = {
            req.skill_preset_ref
            for req in workflow.spec.skill_preset_requirements
            if req.requirement == "optional"
        } | {
            req.agent_preset_ref
            for req in workflow.spec.agent_requirements
            if req.requirement == "optional"
        }
        if _comparison:
            # Applied selections describe the previous template. Promotions and
            # removals change current declarations without invalidating history.
            selected.intersection_update(declared_optional)
        elif selected - declared_optional:
            raise ValidationError("selected optional presets must be declared by the workflow")
        for requirement in workflow.spec.skill_preset_requirements:
            if (
                requirement.requirement == "optional"
                and requirement.skill_preset_ref not in selected
            ):
                continue
            loaded = SkillPresetLoader().describe_preset(
                key=requirement.skill_preset_ref, repo_root=root
            )
            sources.append(
                self._source(
                    "main_agent",
                    loaded.preset.key,
                    f"{loaded.summary.source}:"
                    f"{loaded.summary.origin_path or loaded.summary.plugin_slug}",
                    loaded.preset.version,
                    loaded.preset.model_dump(mode="json"),
                )
            )
        for agent_requirement in workflow.spec.agent_requirements:
            if (
                agent_requirement.requirement == "optional"
                and agent_requirement.agent_preset_ref not in selected
            ):
                continue
            loaded_agent = AgentPresetLoader().describe_preset(
                key=agent_requirement.agent_preset_ref, repo_root=root
            )
            sources.append(
                self._source(
                    "specialist",
                    loaded_agent.preset.key,
                    f"{loaded_agent.summary.source}:"
                    f"{loaded_agent.summary.origin_path or loaded_agent.summary.plugin_slug}",
                    loaded_agent.preset.version,
                    loaded_agent.preset.model_dump(mode="json"),
                )
            )
        if workflow.project_extension is not None:
            extension = workflow.project_extension.model_dump(mode="json")
            # Timestamps/IDs are provenance, not a reason to call identical setup stale.
            content = {
                key: value
                for key, value in extension.items()
                if key.endswith("_json") or key == "enabled"
            }
            sources.append(
                self._source("override", workflow_key, "project_extension", None, content)
            )
        return list({item.ref: item for item in sources}.values())

    @staticmethod
    def _source(
        kind: Any, key: str, origin: str, version: str | None, content: dict[str, Any]
    ) -> SetupSource:
        return SetupSource(
            kind=kind,
            key=key,
            origin=origin,
            ref=f"{kind}:{origin}:{key}",
            version=version,
            digest=_digest(content),
            content=content,
        )

    def record_result(
        self, project_id: int, workflow_key: str, *, expected_revision: int, result: SetupResult
    ) -> SetupWorkflowOut:
        # Mutable in-process models cannot bypass the persisted success contract.
        result = SetupResult.model_validate(result.model_dump(mode="json"))
        projects = ProjectRepository(self._s)
        projects.require_available(project_id, reserve=True)
        project = projects.get(project_id)
        if result.status == "success" and project.project_dir is None:
            raise ValidationError(
                "setup needs an available selected project folder",
                data={
                    "project_id": project_id,
                    "project_folder_issue": project.project_folder_issue,
                },
            )
        row = self._row(project_id, workflow_key)
        if expected_revision != (row.revision if row else 0):
            raise ConflictError(
                "setup receipt revision changed",
                data={
                    "project_id": project_id,
                    "workflow_key": workflow_key,
                    "revision": row.revision if row else 0,
                },
            )
        if result.status == "success":
            proof = result.verification
            assert proof is not None
            current = self.inspect_sources(
                project_id, workflow_key, selected_optional_presets=proof.selected_optional_presets
            )
            if self._identity(current) != self._identity(proof.sources):
                raise ConflictError(
                    "setup sources changed; inspect and revalidate before reporting",
                    data={"project_id": project_id, "workflow_key": workflow_key},
                )
            self._validate_roles(current, proof)
        now = datetime.now(UTC)
        if row is None:
            row = ProjectSetupReceipt(project_id=project_id, workflow_key=workflow_key)
        row.revision += 1
        row.latest_result_json = {**result.model_dump(mode="json"), "checked_at": now.isoformat()}
        if result.status == "success":
            row.successful_json = {**row.latest_result_json, "revision": row.revision}
        row.updated_at = now
        self._s.add(row)
        self._s.commit()
        return self._workflow(row, project_id, workflow_key)

    @staticmethod
    def _identity(sources: list[SetupSource]) -> list[tuple[str, str, str | None]]:
        return sorted((item.ref, item.digest, item.version) for item in sources)

    @staticmethod
    def _validate_roles(sources: list[SetupSource], proof: VerifiedSetup) -> None:
        template = next(item for item in sources if item.kind == "template")
        for field, key, kind in (
            ("agent_requirements", "agent_preset_ref", "specialist"),
            ("skill_preset_requirements", "skill_preset_ref", "main_agent"),
        ):
            for requirement in template.content.get(field, []):
                if (
                    requirement["requirement"] == "optional"
                    and requirement[key] not in proof.selected_optional_presets
                ):
                    continue
                source = next(
                    item for item in sources if item.kind == kind and item.key == requirement[key]
                )
                role = requirement.get("role", requirement[key])
                mappings = [
                    item
                    for item in proof.role_mapping
                    if item.source_ref == source.ref and item.role == role
                ]
                if len(mappings) != 1:
                    raise ValidationError(
                        "each applicable role needs one source-to-target decision"
                    )
                mapping = mappings[0]
                if mapping.decision == "omitted":
                    if requirement["requirement"] == "required" or not mapping.reason:
                        raise ValidationError(
                            "required roles cannot be omitted; recommendations need reasons"
                        )
                elif not mapping.target_paths:
                    raise ValidationError("materialized/session roles need target paths")

    def _row(self, project_id: int, workflow_key: str) -> ProjectSetupReceipt | None:
        return self._s.exec(
            select(ProjectSetupReceipt)
            .where(
                ProjectSetupReceipt.project_id == project_id,
                ProjectSetupReceipt.workflow_key == workflow_key,
            )
            .execution_options(populate_existing=True)
        ).first()

    def _workflow(
        self, row: ProjectSetupReceipt | None, project_id: int, workflow_key: str
    ) -> SetupWorkflowOut:
        out = SetupWorkflowOut(
            workflow_key=workflow_key,
            receipt_id=row.id if row else None,
            revision=row.revision if row else 0,
            successful=row.successful_json if row else None,
            latest_result=row.latest_result_json if row else None,
            status="unknown",
        )
        if row:
            latest = row.latest_result_json
            out.latest_status = latest.get("status")
            out.partial_changes = latest.get("status") != "success" and bool(latest.get("changes"))
            out.attempt_ref = latest.get("attempt_ref")
            out.result_ref = latest.get("result_ref")
        project = ProjectRepository(self._s).get(project_id)
        if project.primary_workspace_binding_id is not None and project.project_dir is None:
            out.status = "source_unavailable"
            out.diagnostic = project.project_folder_issue
            return out
        try:
            optional = (
                row.successful_json["verification"].get("selected_optional_presets", [])
                if row and row.successful_json
                else []
            )
            out.available_sources = self.inspect_sources(
                project_id, workflow_key, selected_optional_presets=optional, _comparison=True
            )
        except (RepositoryError, ValueError, yaml.YAMLError, OSError) as exc:
            out.status = "source_unavailable"
            # YAML/schema/read exceptions can contain source text. Keep repair
            # guidance bounded and avoid copying local content into diagnostics.
            detail = str(exc)[:300] if isinstance(exc, RepositoryError) else type(exc).__name__
            if isinstance(exc, yaml.YAMLError):
                detail = f"invalid YAML: {type(exc).__name__}"
            mark = getattr(exc, "problem_mark", None)
            if mark is not None:
                detail += f" at line {mark.line + 1}, column {mark.column + 1}"
            out.diagnostic = (
                f"Setup sources for {workflow_key} are unavailable ({detail}). "
                "Check the effective workflow and local .stackos/workflows, "
                ".stackos/agent-presets and .stackos/skill-presets sources. "
                "Repair the source and retry projectSetup.get; "
                "retained setup evidence is unchanged."
            )[:800]
            return out
        if row and row.successful_json:
            applied = VerifiedSetup.model_validate(row.successful_json["verification"])
            out.status = (
                "current"
                if self._identity(applied.sources) == self._identity(out.available_sources)
                else "update_available"
            )
            if out.status == "current" and applied.activation != "verified_loaded":
                out.status = "activation_pending"
        return out

    def get(self, project_id: int, *, workflow_key: str | None = None) -> ProjectSetupOut:
        projects = ProjectRepository(self._s)
        row = projects.require_available(project_id)
        project = projects.get(project_id)
        eligible = row.primary_workspace_binding_id is not None
        folder: Literal["directoryless", "available", "unavailable"] = (
            "directoryless"
            if not eligible
            else ("available" if project.project_dir else "unavailable")
        )
        receipts = self._s.exec(
            select(ProjectSetupReceipt).where(ProjectSetupReceipt.project_id == project_id)
        ).all()
        keys = {item.workflow_key for item in receipts}
        # Existing declared setup is unknown until inspected/adopted. No empty
        # extension or fabricated receipt is created by this read.
        keys.update(
            self._s.exec(
                select(WorkflowTemplateExtension.workflow_key).where(
                    WorkflowTemplateExtension.project_id == project_id,
                    WorkflowTemplateExtension.enabled == True,  # noqa: E712
                )
            ).all()
        )
        keys.update(
            self._s.exec(
                select(WorkflowTemplate.key)
                .join(
                    ProjectWorkflowTemplate,
                    col(ProjectWorkflowTemplate.template_id) == col(WorkflowTemplate.id),
                )
                .where(
                    ProjectWorkflowTemplate.project_id == project_id,
                    ProjectWorkflowTemplate.enabled == True,  # noqa: E712
                )
            ).all()
        )
        aggregate_keys = keys.copy()
        if workflow_key:
            keys.add(workflow_key)
        workflows = [
            self._workflow(self._row(project_id, key), project_id, key) for key in sorted(keys)
        ]
        aggregate = [item for item in workflows if item.workflow_key in aggregate_keys]
        configured = sum(item.successful is not None for item in aggregate)
        updates = sum(item.status == "update_available" for item in aggregate) if eligible else 0
        unknown = sum(
            item.status != "current" and item.status != "update_available" for item in aggregate
        )
        status: Literal[
            "ineligible", "folder_unavailable", "setup_needed", "updates_available", "current"
        ] = (
            "ineligible"
            if not eligible
            else "folder_unavailable"
            if folder == "unavailable"
            else "setup_needed"
            if not configured or unknown
            else "updates_available"
            if updates
            else "current"
        )
        return ProjectSetupOut(
            project_id=project_id,
            eligible=eligible,
            folder_status=folder,
            status=status,
            binding_id=row.primary_workspace_binding_id,
            project_folder_issue=project.project_folder_issue,
            setup_reminder_dismissed_at=row.setup_reminder_dismissed_at,
            update_reminder_dismissed_at=row.update_reminder_dismissed_at,
            configured_count=configured,
            update_count=updates,
            unknown_count=unknown,
            workflows=[
                item
                for item in workflows
                if workflow_key is None or item.workflow_key == workflow_key
            ],
            detail_ref={
                "tool_name": "projectSetup.get",
                "arguments": {
                    "project_id": project_id,
                    "response_mode": "raw",
                    **({"workflow_key": workflow_key} if workflow_key else {}),
                },
            },
            next_action="repair_project_folder"
            if folder == "unavailable"
            else ("none" if not eligible else "inspect_setup"),
        )

    def set_reminders(
        self, project_id: int, *, dismiss_setup: bool = False, dismiss_updates: bool = False
    ) -> ProjectSetupOut:
        row = ProjectRepository(self._s).require_available(project_id, reserve=True)
        now = datetime.now(UTC)
        if dismiss_setup and row.setup_reminder_dismissed_at is None:
            row.setup_reminder_dismissed_at = now
        if dismiss_updates and row.update_reminder_dismissed_at is None:
            row.update_reminder_dismissed_at = now
        self._s.add(row)
        self._s.commit()
        return self.get(project_id)
