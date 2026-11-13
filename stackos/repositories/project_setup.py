"""Project-owned applied setup evidence, independent of workflow execution.

Agents assess source obligations and adaptations. This owner validates their
explicit evidence contract; it never infers semantic equivalence from hashes.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import ValidationError as ModelValidationError
from sqlalchemy import func, or_
from sqlmodel import Session, col, select

from stackos.agents.loader import AgentPresetLoader
from stackos.db.models import (
    AgentRequest,
    AgentRequestAttentionStatus,
    AgentRequestStatus,
    AgentSession,
    ProjectSetupReceipt,
    ProjectWorkflowTemplate,
    WorkflowTemplate,
    WorkflowTemplateExtension,
)
from stackos.repositories.agent_requests import AgentRequestRepository, _hash_token
from stackos.repositories.base import ConflictError, RepositoryError, ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.skill_presets.loader import SkillPresetLoader
from stackos.workflows.authoring_guide import PROJECT_SETUP_NOTICES
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


SETUP_NAMESPACE = "project_setup"
SETUP_GUIDANCE_REF = {
    "tool_name": "workflowTemplate.authoringGuide",
    "arguments": {"response_mode": "raw"},
}


class SetupProgress(SetupEvidenceModel):
    message: str = Field(min_length=1, max_length=2000)
    evidence_ref: str = Field(min_length=1, max_length=1000)


class SetupQuestion(SetupEvidenceModel):
    question_id: str = Field(min_length=1, max_length=120)
    revision: int = Field(strict=True, ge=1)
    prompt: str = Field(min_length=1, max_length=2000)
    workflow_keys: list[str] = Field(max_length=100)
    answer_state: Literal["pending", "answered"] = "pending"
    response_channel: Literal["existing_agent", "managed_ui"] = "existing_agent"


class SetupAnswer(SetupEvidenceModel):
    question_id: str = Field(min_length=1, max_length=120)
    question_revision: int = Field(strict=True, ge=1)
    question_request_id: int
    answer: str = Field(min_length=1, max_length=8000)
    answered_at: datetime


class SetupWorkflowReport(SetupEvidenceModel):
    workflow_key: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(strict=True, ge=0)
    result: SetupResult


class SetupReport(SetupEvidenceModel):
    status: Literal[
        "running", "needs_input", "success", "partial", "failed", "interrupted", "cancelled"
    ]
    progress: SetupProgress
    workflow_results: list[SetupWorkflowReport] = Field(default_factory=list, max_length=100)
    workflow_keys: list[str] | None = Field(default=None, max_length=100)
    question: SetupQuestion | None = None
    recovery_condition: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def valid_question(self) -> SetupReport:
        if (self.status == "needs_input") != (self.question is not None):
            raise ValueError(
                "needs_input requires a concrete question; other states cannot ask one"
            )
        if self.question and self.question.answer_state != "pending":
            raise ValueError("new reports can only ask pending questions")
        keys = [item.workflow_key for item in self.workflow_results]
        if len(keys) != len(set(keys)):
            raise ValueError("report each workflow only once")
        if self.workflow_keys is not None and (
            len(self.workflow_keys) != len(set(self.workflow_keys))
            or any(not key.strip() or len(key) > 200 for key in self.workflow_keys)
        ):
            raise ValueError("select unique nonblank workflow keys")
        if (
            self.question
            and self.question.response_channel == "existing_agent"
            and not self.question.workflow_keys
        ):
            raise ValueError("existing-session questions require selected workflow keys")
        return self


class SetupWorkflowOutcome(SetupEvidenceModel):
    receipt_id: int
    revision: int
    status: Literal["success", "failed", "unfinished", "needs_input", "cancelled", "interrupted"]
    result_ref: str


class SetupReportReceipt(SetupEvidenceModel):
    revision: int
    status: str
    progress: SetupProgress
    question: SetupQuestion | None
    result_refs: list[str]
    checked_at: datetime


class SetupManagedExecution(SetupEvidenceModel):
    settings: dict[str, Any]
    binding_id: int
    admission_id: str
    job_ref: str
    slot_reserved: bool = False
    callback_started: bool = False
    cancel_requested: bool = False
    diagnostic: str | None = None
    resume_thread_id: str | None = None


class SetupAttempt(SetupEvidenceModel):
    """Versioned AgentRequest metadata; native execution evidence stays alongside it."""

    schema_version: Literal[1] = 1
    project_id: int
    attempt_ref: str
    root_request_id: int
    request_id: int
    predecessor_request_id: int | None = None
    successor_request_id: int | None = None
    revision: int = 1
    mode: Literal["setup", "update"]
    entry_kind: Literal["existing_agent", "managed_ui"]
    session_id: int | None
    runtime: str
    thread_id: str | None = None
    workflow_keys: list[str]
    observed_receipt_revisions: dict[str, int]
    outcomes: dict[str, SetupWorkflowOutcome] = Field(default_factory=dict)
    status: Literal[
        "queued",
        "running",
        "needs_input",
        "success",
        "partial",
        "failed",
        "interrupted",
        "cancelled",
    ] = "running"
    managed: SetupManagedExecution | None = None
    progress: SetupProgress | None = None
    question: SetupQuestion | None = None
    recovery_condition: str | None = None
    reports: list[SetupReportReceipt] = Field(default_factory=list)
    answers: list[SetupAnswer] = Field(default_factory=list)
    remaining_workflow_keys: list[str] = Field(default_factory=list)
    revalidate_workflow_keys: list[str] = Field(default_factory=list)
    partial_changes_possible: bool = False


class SetupStartOut(BaseModel):
    attempt: SetupAttempt
    attached: bool
    claim_token: str | None = None
    guidance_ref: dict[str, Any] = Field(default_factory=lambda: dict(SETUP_GUIDANCE_REF))
    next_action: str


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
    current_attempt: SetupAttempt | None = None
    launch_preferences: dict[str, Any] = Field(default_factory=dict)
    attempt_diagnostic: str | None = None
    guidance_ref: dict[str, Any] = Field(default_factory=lambda: dict(SETUP_GUIDANCE_REF))
    notice: str | None = None
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
        self,
        project_id: int,
        workflow_key: str,
        *,
        expected_revision: int,
        result: SetupResult,
        _commit: bool = True,
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
        if _commit:
            self._s.commit()
        else:
            self._s.flush()
        return self._workflow(row, project_id, workflow_key)

    def _attempts(self, project_id: int) -> list[tuple[AgentRequest, SetupAttempt]]:
        rows = self._s.exec(
            select(AgentRequest)
            .where(
                AgentRequest.project_id == project_id,
                or_(
                    col(AgentRequest.source_kind) == SETUP_NAMESPACE,
                    func.json_type(AgentRequest.metadata_json, "$.project_setup").is_not(None),
                ),
            )
            .order_by(col(AgentRequest.id).desc())
        ).all()
        attempts = []
        for row in rows:
            try:
                attempt = SetupAttempt.model_validate(
                    (row.metadata_json or {}).get(SETUP_NAMESPACE)
                )
            except ModelValidationError as exc:
                raise ConflictError(
                    "setup request has unknown legacy metadata; "
                    "inspect before starting another writer",
                    data={
                        "project_id": project_id,
                        "request_id": row.id,
                        "next_action": "inspect_request",
                    },
                ) from exc
            if attempt.project_id != project_id or attempt.request_id != row.id:
                raise ConflictError(
                    "setup request identity is inconsistent", data={"request_id": row.id}
                )
            attempts.append((row, attempt))
        return attempts

    def _current_attempt(self, project_id: int) -> SetupAttempt | None:
        attempts = self._attempts(project_id)
        active = [
            a
            for _, a in attempts
            if a.successor_request_id is None
            and (
                a.status in {"queued", "running", "needs_input"}
                or (a.managed is not None and a.managed.slot_reserved)
            )
        ]
        if len(active) > 1:
            raise ConflictError(
                "multiple setup writers require reconciliation", data={"project_id": project_id}
            )
        return active[0] if active else (attempts[0][1] if attempts else None)

    def start(
        self,
        project_id: int,
        *,
        session_id: int | None,
        mode: Literal["setup", "update"],
        workflow_keys: list[str],
        managed: SetupManagedExecution | None = None,
    ) -> SetupStartOut:
        """Reserve the project before admission; attachment never issues writer authority."""
        try:
            ProjectRepository(self._s).require_available(project_id, reserve=True)
            state = self.get(project_id)
            if not state.eligible or state.folder_status != "available":
                raise ValidationError(
                    "setup requires an available selected project folder",
                    data={
                        "project_id": project_id,
                        "folder_status": state.folder_status,
                        "next_action": state.next_action,
                    },
                )
            session = self._s.get(AgentSession, session_id) if session_id is not None else None
            if managed is None and (session is None or session.project_id != project_id):
                raise ConflictError(
                    "agent session does not belong to the setup project",
                    data={"project_id": project_id},
                )
            current = self._current_attempt(project_id)
            if current and (
                current.status in {"queued", "running", "needs_input"}
                or (current.managed is not None and current.managed.slot_reserved)
            ):
                self._s.commit()
                return SetupStartOut(
                    attempt=current, attached=True, next_action="observe_existing_attempt"
                )
            if (
                mode not in {"setup", "update"}
                or (not workflow_keys and managed is None)
                or len(workflow_keys) > 100
                or any(not key.strip() or len(key) > 200 for key in workflow_keys)
                or len(set(workflow_keys)) != len(workflow_keys)
            ):
                raise ValidationError("select unique workflow keys and setup/update mode")
            token = secrets.token_urlsafe(32)
            now = datetime.now(UTC).replace(tzinfo=None)
            row = AgentRequest(
                project_id=project_id,
                request_key=f"project-setup:{secrets.token_hex(16)}",
                title="Project setup" if mode == "setup" else "Project workflow update",
                source_kind=SETUP_NAMESPACE,
                status=AgentRequestStatus.CLAIMED,
                attention_status=AgentRequestAttentionStatus.READ,
                claimed_by=f"agent-session:{session_id}",
                claim_token_hash=_hash_token(token),
                claimed_at=now,
                created_at=now,
                updated_at=now,
            )
            self._s.add(row)
            self._s.flush()
            assert row.id is not None
            attempt = SetupAttempt(
                project_id=project_id,
                attempt_ref=f"agent-request:{row.id}",
                root_request_id=row.id,
                request_id=row.id,
                mode=mode,
                entry_kind="managed_ui" if managed else "existing_agent",
                session_id=session_id,
                runtime=managed.settings["host_key"]
                if managed
                else (session.runtime if session else ""),
                thread_id=session.thread_id if session else None,
                managed=managed,
                status="queued" if managed else "running",
                workflow_keys=workflow_keys,
                observed_receipt_revisions={
                    key: (receipt.revision if (receipt := self._row(project_id, key)) else 0)
                    for key in set(workflow_keys)
                    | ({item.workflow_key for item in state.workflows} if managed else set())
                },
            )
            row.metadata_json = {SETUP_NAMESPACE: attempt.model_dump(mode="json")}
            self._s.add(row)
            self._s.commit()
            return SetupStartOut(
                attempt=attempt,
                attached=False,
                claim_token=token,
                next_action="observe_managed_setup" if managed else "execute_in_existing_session",
            )
        except Exception:
            self._s.rollback()
            raise

    def cancel_existing(
        self,
        project_id: int,
        *,
        request_id: int,
        expected_revision: int | None,
        external_work_stopped: bool,
    ) -> SetupAttempt:
        """Fence only the acknowledged external writer, under the project reservation."""
        try:
            ProjectRepository(self._s).require_available(project_id, reserve=True)
            row = AgentRequestRepository(self._s)._fetch(
                project_id=project_id, request_id=request_id
            )
            current = self._current_attempt(project_id)
            if (
                current is None
                or current.request_id != request_id
                or current.entry_kind != "existing_agent"
                or current.status not in {"running", "needs_input"}
                or current.revision != expected_revision
                or external_work_stopped is not True
            ):
                raise ConflictError(
                    "Stop previous setup work in the agent app, then confirm the current revision",
                    data={
                        "project_id": project_id,
                        "request_id": request_id,
                        "next_action": "inspect_setup",
                    },
                )
            current.status = "cancelled"
            current.revision += 1
            current.recovery_condition = (
                "Operator acknowledged stopping external setup work. StackOS revoked this "
                "claim; it did not stop the agent or verify native retirement. Completed "
                "results are retained. Files may have changed; no rollback is implied. "
                "Start a separate setup deliberately when ready."
            )
            row.metadata_json = {
                **(row.metadata_json or {}),
                SETUP_NAMESPACE: current.model_dump(mode="json"),
            }
            row.status = AgentRequestStatus.FAILED
            row.claim_token_hash = None
            row.completed_at = row.updated_at = datetime.now(UTC).replace(tzinfo=None)
            self._s.add(row)
            self._s.commit()
            return current
        except Exception:
            self._s.rollback()
            raise

    def report(
        self,
        project_id: int,
        *,
        request_id: int,
        session_id: int | None,
        claim_token: str,
        expected_revision: int,
        report: SetupReport,
        _managed: bool = False,
        _session_report: bool = False,
    ) -> SetupAttempt:
        try:
            report = SetupReport.model_validate(report.model_dump(mode="json"))
            ProjectRepository(self._s).require_available(project_id, reserve=True)
            requests = AgentRequestRepository(self._s)
            row = requests._fetch(project_id=project_id, request_id=request_id)
            requests._verify_claim(row, claim_token, _setup_owner=True)
            current = self._current_attempt(project_id)
            if (
                current is None
                or current.request_id != request_id
                or (not _managed and current.session_id != session_id)
            ):
                raise ConflictError(
                    "report does not own the current setup attempt",
                    data={"project_id": project_id, "request_id": request_id},
                )
            if (
                current.status not in {"running", "needs_input"}
                or current.revision != expected_revision
            ):
                raise ConflictError(
                    "setup attempt revision or status changed",
                    data={
                        "request_id": request_id,
                        "revision": current.revision,
                        "status": current.status,
                    },
                )
            if (current.entry_kind == "managed_ui") != _managed:
                raise ConflictError("this reporting entry requires an existing agent session")
            if report.workflow_keys is not None and not _managed:
                raise ConflictError("existing-session workflow selection belongs to start")
            if _managed:
                if current.managed is None or current.managed.cancel_requested:
                    raise ConflictError("managed setup reporting is fenced by cancellation")
                if _session_report:
                    from stackos.host_mcp.service import active_request_execution

                    owner = self._s.get(AgentSession, session_id)
                    live = active_request_execution(project_id, request_id)
                    if (
                        owner is None
                        or owner.project_id != project_id
                        or live is None
                        or owner.runtime != current.runtime
                        or current.session_id not in {None, session_id}
                    ):
                        raise ConflictError(
                            "managed report requires the exact active native session"
                        )
                    current.session_id = session_id
                    # Some supported hosts return native session identity only
                    # at exit. The private claim, active request and first bound
                    # reporting session already establish this writer's ownership.
                    current.thread_id = live.get("thread_id") or current.thread_id
                if not current.workflow_keys:
                    current.workflow_keys = (
                        report.workflow_keys
                        if report.workflow_keys is not None
                        else list(
                            dict.fromkeys(
                                [item.workflow_key for item in report.workflow_results]
                                + (report.question.workflow_keys if report.question else [])
                            )
                        )
                    )
                    for key in current.workflow_keys:
                        current.observed_receipt_revisions.setdefault(key, 0)
                elif report.workflow_keys is not None and set(report.workflow_keys) != set(
                    current.workflow_keys
                ):
                    raise ConflictError(
                        "managed workflow selection is already fixed for this attempt"
                    )
            if report.question is not None and (
                report.question.response_channel != current.entry_kind
                or not set(report.question.workflow_keys).issubset(current.workflow_keys)
            ):
                raise ValidationError(
                    "question must stay in the owning session and selected workflows"
                )
            if report.question and any(
                answer.question_id == report.question.question_id
                and answer.question_revision >= report.question.revision
                for answer in current.answers
            ):
                raise ConflictError("question was already answered; consume the recorded answer")
            outcomes = dict(current.outcomes)
            for item in report.workflow_results:
                if (
                    item.workflow_key not in current.workflow_keys
                    or item.result.attempt_ref != current.attempt_ref
                ):
                    raise ConflictError("workflow result does not belong to this setup attempt")
                if item.expected_revision != current.observed_receipt_revisions[item.workflow_key]:
                    raise ConflictError(
                        "report receipt revision differs from the attempt observation"
                    )
                saved = self.record_result(
                    project_id,
                    item.workflow_key,
                    expected_revision=item.expected_revision,
                    result=item.result,
                    _commit=False,
                )
                current.observed_receipt_revisions[item.workflow_key] = saved.revision
                assert saved.receipt_id is not None
                outcomes[item.workflow_key] = SetupWorkflowOutcome(
                    receipt_id=saved.receipt_id,
                    revision=saved.revision,
                    status=item.result.status,
                    result_ref=item.result.result_ref,
                )
            if report.status == "success" and (
                not current.workflow_keys
                or any(
                    key not in outcomes or outcomes[key].status != "success"
                    for key in current.workflow_keys
                )
            ):
                raise ValidationError(
                    "every selected workflow needs verified success before attempt success"
                )
            if report.status == "success":
                project = ProjectRepository(self._s).get(project_id)
                if project.project_dir is None:
                    raise ConflictError(
                        "setup completion needs an available selected project folder",
                        data={"project_id": project_id, "next_action": "repair_project_folder"},
                    )
                # A previous progress report can precede another source/receipt
                # change. Closing the attempt must not bless that stale proof.
                for key, outcome in outcomes.items():
                    receipt = self._row(project_id, key)
                    if (
                        receipt is None
                        or receipt.revision != outcome.revision
                        or receipt.successful_json is None
                    ):
                        raise ConflictError(
                            "workflow receipt changed; revalidate before completion",
                            data={"workflow_key": key},
                        )
                    proof = VerifiedSetup.model_validate(receipt.successful_json["verification"])
                    sources = self.inspect_sources(
                        project_id, key, selected_optional_presets=proof.selected_optional_presets
                    )
                    if self._identity(sources) != self._identity(proof.sources):
                        raise ConflictError(
                            "workflow sources changed; revalidate before completion",
                            data={"workflow_key": key},
                        )
            current.revision += 1
            current.status = report.status
            current.progress = report.progress
            current.question = report.question
            current.recovery_condition = report.recovery_condition
            current.outcomes = outcomes
            current.partial_changes_possible = current.partial_changes_possible or bool(
                any(item.result.changes for item in report.workflow_results)
            )
            current.reports.append(
                SetupReportReceipt(
                    revision=current.revision,
                    status=report.status,
                    progress=report.progress,
                    question=report.question,
                    result_refs=[item.result.result_ref for item in report.workflow_results],
                    checked_at=datetime.now(UTC),
                )
            )
            if len(current.model_dump_json().encode()) > 2_000_000:
                raise ValidationError(
                    "setup attempt exceeds 2 MB; retain detailed evidence by reference"
                )
            row.metadata_json = {
                **(row.metadata_json or {}),
                SETUP_NAMESPACE: current.model_dump(mode="json"),
            }
            row.updated_at = datetime.now(UTC).replace(tzinfo=None)
            if report.status not in {"running", "needs_input"} and not _managed:
                row.status = (
                    AgentRequestStatus.RESOLVED
                    if report.status == "success"
                    else AgentRequestStatus.FAILED
                )
                row.completed_at = row.updated_at
                row.claim_token_hash = None
            self._s.add(row)
            self._s.commit()
            return current
        except Exception:
            self._s.rollback()
            raise

    def startup_summary(self, project_id: int) -> dict[str, Any]:
        state = self.get(project_id)
        summary = state.model_dump(
            mode="json",
            include={
                "project_id",
                "eligible",
                "folder_status",
                "status",
                "configured_count",
                "update_count",
                "unknown_count",
                "attempt_diagnostic",
                "guidance_ref",
                "notice",
                "detail_ref",
                "next_action",
            },
        )
        summary["current_attempt"] = (
            state.current_attempt.model_dump(
                mode="json",
                include={
                    "attempt_ref",
                    "root_request_id",
                    "request_id",
                    "revision",
                    "mode",
                    "entry_kind",
                    "status",
                },
            )
            if state.current_attempt
            else None
        )
        return summary

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
        from stackos.repositories.tracker import TrackerRepository

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
        attempt = None
        attempt_diagnostic = None
        try:
            attempt = self._current_attempt(project_id)
        except ConflictError as exc:
            attempt_diagnostic = str(exc)
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
            current_attempt=attempt,
            launch_preferences=TrackerRepository(self._s)
            .delivery_defaults(project_id=project_id)
            .get("main_agent", {}),
            attempt_diagnostic=attempt_diagnostic,
            notice=(
                None
                if (status == "setup_needed" and row.setup_reminder_dismissed_at)
                or (status == "updates_available" and row.update_reminder_dismissed_at)
                else PROJECT_SETUP_NOTICES.get(status)
            ),
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
