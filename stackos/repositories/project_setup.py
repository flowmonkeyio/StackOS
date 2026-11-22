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
from sqlalchemy import select as sa_select
from sqlmodel import Session, col, select

from stackos.agents.loader import AgentPresetLoader
from stackos.db.models import (
    AgentRequest,
    AgentRequestAttentionStatus,
    AgentRequestStatus,
    AgentSession,
    ProjectSetupReceipt,
    ProjectWorkflowTemplate,
    RunPlan,
    RunPlanStep,
    TrackerTask,
    TrackerTicket,
    WorkflowTemplate,
    WorkflowTemplateExtension,
    WorkspaceBinding,
)
from stackos.host_mcp.project_agents import AgentSourceRef
from stackos.operations.tracker.schemas import AgentExecutionRequirements
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
    # Reports can reference the exact inspected source without echoing its body.
    # The receipt owner restores omitted content from current sources and stores
    # full snapshots so previous bases survive package/repo source removal.
    content: dict[str, Any] | None = Field(
        default=None,
        description="May be omitted in reports; the owner resolves the exact current source.",
    )


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


SetupScope = Literal["ready_to_use", "infrastructure_only"]


class SetupRoleProposal(SetupEvidenceModel):
    """A bounded selection for the existing defaults owner, never write authority."""

    source_ref: AgentSourceRef | None = None
    expected_digest: str = Field(pattern="^[0-9a-f]{64}$")
    host_key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=160)
    instructions: str | None = Field(default=None, min_length=1, max_length=100_000)
    provenance_ref: str = Field(min_length=1, max_length=1000)
    execution_requirements: AgentExecutionRequirements | None = None

    @model_validator(mode="after")
    def instruction_owner(self) -> SetupRoleProposal:
        if self.source_ref is not None:
            if self.host_key != self.source_ref.host_key or self.instructions is not None:
                raise ValueError("source refs own instructions and must match the selected host")
        elif self.host_key == "codex" or self.instructions is None:
            raise ValueError(
                "Codex requires a source ref; other hosts require their inline contract"
            )
        elif _digest(self.instructions) != self.expected_digest:
            raise ValueError("inline contract digest changed")
        return self


class SetupAdoptionProposal(SetupEvidenceModel):
    project_id: int = Field(strict=True, ge=1)
    binding_id: int = Field(strict=True, ge=1)
    project_dir: str = Field(min_length=1, max_length=4096)
    expected_delivery_digest: str = Field(pattern="^[0-9a-f]{64}$")
    roles: dict[str, SetupRoleProposal] = Field(min_length=1, max_length=100)


class SetupActivationBasis(SetupEvidenceModel):
    """Separate current source, defaults, local adaptation and environment identities."""

    project_id: int = Field(strict=True, ge=1)
    binding_id: int = Field(strict=True, ge=1)
    sources_digest: str = Field(
        pattern="^[0-9a-f]{64}$",
        description="SHA256 of canonical JSON sorted (ref, kind, key, origin, digest, version) "
        "tuples from verification.sources.",
    )
    delivery_digest: str = Field(
        pattern="^[0-9a-f]{64}$",
        description="SHA256 of canonical JSON saved project delivery defaults, "
        "excluding task overrides.",
    )
    adaptations_digest: str = Field(
        pattern="^[0-9a-f]{64}$",
        description="SHA256 of canonical JSON verification.local_adaptations in reported order.",
    )
    environment_ref: str = Field(min_length=1, max_length=1000)


class SetupProofReference(SetupEvidenceModel):
    """References are checked against their existing owner, not caller-supplied status."""

    kind: Literal["native_request", "workflow_step", "task_phase"]
    request_id: int | None = Field(default=None, strict=True, ge=1)
    invocation_id: str | None = Field(default=None, min_length=1, max_length=200)
    run_plan_id: int | None = Field(default=None, strict=True, ge=1)
    step_id: str | None = Field(default=None, min_length=1, max_length=200)
    task_id: int | None = Field(default=None, strict=True, ge=1)
    ticket_id: int | None = Field(default=None, strict=True, ge=1)
    attempt: int | None = Field(default=None, strict=True, ge=1)
    phase: Literal["delivery", "review"] | None = None
    outcome_digest: str = Field(
        pattern="^[0-9a-f]{64}$",
        description="SHA256 of canonical JSON native_execution.result or RunPlanStep.result_json.",
    )

    @model_validator(mode="after")
    def exact_owner(self) -> SetupProofReference:
        owners = {
            "native_request": {"request_id", "invocation_id"},
            "workflow_step": {"run_plan_id", "step_id"},
            "task_phase": {"task_id", "ticket_id", "attempt", "phase", "invocation_id"},
        }
        fields = set.union(*owners.values())
        if {name for name in fields if getattr(self, name) is not None} != owners[self.kind]:
            raise ValueError("select one exact native invocation, workflow step or task phase")
        return self


class SetupExecutionBinding(SetupEvidenceModel):
    """Identity of the role which actually performed a declared check."""

    role: str = Field(min_length=1, max_length=160)
    agent: str = Field(min_length=1, max_length=160)
    host_key: str = Field(min_length=1, max_length=100)
    binding_id: int = Field(strict=True, ge=1)
    source_ref: AgentSourceRef | None = None
    source_digest: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    settings_digest: str = Field(pattern="^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def source_pair(self) -> SetupExecutionBinding:
        if (self.source_ref is None) != (self.source_digest is None):
            raise ValueError("source-backed execution needs both source ref and digest")
        return self


class SetupRoleClaim(SetupEvidenceModel):
    """The workflow declares applicability using existing role requirements."""

    role: str = Field(min_length=1, max_length=160, pattern=r"^\S+$")
    when_execution_requirements: dict[str, bool] = Field(default_factory=dict, max_length=100)


class SetupActivationCheck(SetupEvidenceModel):
    claims: list[str] = Field(min_length=1, max_length=100)
    origin: Literal["reused", "executed", "simulated"]
    status: Literal["passed", "failed"] = "passed"
    basis: SetupActivationBasis
    reference: SetupProofReference
    evidence_ref: str = Field(min_length=1, max_length=1000)
    execution: SetupExecutionBinding | None = None


class SetupRoleCheckRequest(SetupEvidenceModel):
    """Project-selected bounded proof, executed by the existing setup owner."""

    role: str = Field(min_length=1, max_length=160)
    claims: list[str] = Field(min_length=1, max_length=100)
    instructions: str = Field(min_length=1, max_length=10000)
    environment_ref: str = Field(min_length=1, max_length=1000)
    evidence_ref: str = Field(min_length=1, max_length=1000)


class SetupNativeRoleCheck(SetupEvidenceModel):
    workflow_key: str = Field(min_length=1, max_length=200)
    request: SetupRoleCheckRequest
    agent_snapshot: dict[str, Any]
    execution: SetupExecutionBinding


class SetupActivationEvidence(SetupEvidenceModel):
    method_ref: str = Field(min_length=1, max_length=1000)
    status: Literal["pending", "blocked", "verified"] = "pending"
    required_claims: list[str] = Field(min_length=1, max_length=100)
    checks: list[SetupActivationCheck] = Field(default_factory=list, max_length=100)
    missing_claims: list[str] = Field(default_factory=list, max_length=100)
    diagnostic: str | None = Field(default=None, max_length=2000)

    def gaps(self) -> list[str]:
        # Domain method may require more proof, but cannot drop these shared boundaries.
        required = {"saved_defaults", "native_consumption", *self.required_claims}
        covered = {
            claim
            for check in self.checks
            if check.origin != "simulated" and check.status == "passed"
            for claim in check.claims
        }
        return sorted((required - covered) | set(self.missing_claims))

    @model_validator(mode="after")
    def truthful_coverage(self) -> SetupActivationEvidence:
        claims = self.required_claims + self.missing_claims
        if any(not claim.strip() or len(claim) > 200 for claim in claims) or any(
            not claim.strip() or len(claim) > 200 for check in self.checks for claim in check.claims
        ):
            raise ValueError("activation claims must be bounded nonblank names")
        if len(set(self.required_claims)) != len(self.required_claims):
            raise ValueError("required activation claims must be unique")
        if self.status == "verified" and self.gaps():
            raise ValueError("verified activation requires all required claims")
        if self.status == "blocked" and not self.diagnostic:
            raise ValueError("blocked activation needs an actionable diagnostic")
        return self


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
    activation_proof: SetupActivationEvidence | None = None

    @model_validator(mode="after")
    def evidence_complete(self) -> VerifiedSetup:
        if len(self.model_dump_json().encode()) > 2_000_000:
            raise ValueError("setup evidence exceeds 2 MB")
        if (
            self.activation_proof is not None
            and self.activation_proof.status == "verified"
            and self.activation != "verified_loaded"
        ):
            raise ValueError("verified activation coverage requires observed native loading")
        refs = {source.ref for source in self.sources}
        if len(refs) != len(self.sources):
            raise ValueError("source refs must be unique")
        for source in self.sources:
            if source.content is not None and source.digest != _digest(source.content):
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
    # Missing in stored records means historical preparation, never inferred activation.
    requested_scope: SetupScope = "infrastructure_only"

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
    requested_scope: SetupScope = "infrastructure_only"
    activation_status: Literal["unknown", "pending", "blocked", "verified"] = "unknown"
    missing_activation_claims: list[str] = Field(default_factory=list)


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
    role_verification_request: SetupRoleCheckRequest | None = None
    status: Literal[
        "running", "needs_input", "success", "partial", "failed", "interrupted", "cancelled"
    ]
    progress: SetupProgress
    workflow_results: list[SetupWorkflowReport] = Field(default_factory=list, max_length=100)
    workflow_keys: list[str] | None = Field(default=None, max_length=100)
    question: SetupQuestion | None = None
    recovery_condition: str | None = Field(default=None, max_length=2000)
    adoption_proposal: SetupAdoptionProposal | None = None

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
    activation_status: Literal["unknown", "pending", "blocked", "verified"] = "unknown"
    missing_activation_claims: list[str] = Field(default_factory=list)


class SetupReportReceipt(SetupEvidenceModel):
    revision: int
    status: str
    progress: SetupProgress
    question: SetupQuestion | None
    result_refs: list[str]
    checked_at: datetime
    adoption_proposal: SetupAdoptionProposal | None = None
    report_digest: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")


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
    # Set only by authenticated managed admission, never by a worker report.
    adoption_authorized: bool = False
    phase: Literal["preparation", "verification", "role_verification", "completion"] = "preparation"
    adopted_delivery_digest: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    verification_request_id: int | None = None
    coordinator_thread_id: str | None = None
    role_check: SetupNativeRoleCheck | None = None
    role_verification_request_ids: list[int] = Field(default_factory=list, max_length=100)
    preflight_failed: bool = False


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
    requested_scope: SetupScope = "infrastructure_only"
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
    adoption_proposal: SetupAdoptionProposal | None = None


class SetupStartOut(BaseModel):
    attempt: SetupAttempt
    attached: bool
    claim_token: str | None = None
    guidance_ref: dict[str, Any] = Field(default_factory=lambda: dict(SETUP_GUIDANCE_REF))
    next_action: str


class SetupAdmissionState(BaseModel):
    """Scalar ownership evidence, independent of full setup/report compatibility."""

    model_config = ConfigDict(strict=True)
    project_id: int
    request_id: int
    entry_kind: Literal["managed_ui"]
    status: Literal[
        "queued",
        "running",
        "needs_input",
        "success",
        "partial",
        "failed",
        "interrupted",
        "cancelled",
    ]
    reservation: Literal["true", "false"]
    admission_id: str = Field(min_length=1)
    project_dir: str = Field(min_length=1)

    @property
    def slot_reserved(self) -> bool:
        return self.reservation == "true"


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
    delivery_defaults_digest: str
    attempt_diagnostic: str | None = None
    guidance_ref: dict[str, Any] = Field(default_factory=lambda: dict(SETUP_GUIDANCE_REF))
    notice: str | None = None
    detail_ref: dict[str, Any]
    next_action: str


class ProjectSetupRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def managed_admissions(
        self,
        *,
        project_id: int | None = None,
        admission_id: str | None = None,
        job_ref: str | None = None,
    ) -> list[SetupAdmissionState]:
        """Exclude only proven retired work before projecting ownership scalars.

        An admission filter selects this daemon's unreserved queue in request
        order; a job filter selects an exact recovery target. Neither reads
        report, snapshot or native-result bodies, nor grants a writer claim.
        """
        metadata = AgentRequest.metadata_json
        status = func.json_extract(metadata, "$.project_setup.status")
        reservation = func.json_type(metadata, "$.project_setup.managed.slot_reserved")
        managed = func.json_type(metadata, "$.project_setup.managed")
        entry_kind = func.json_extract(metadata, "$.project_setup.entry_kind")
        admission = func.json_extract(metadata, "$.project_setup.managed.admission_id")
        query = (
            sa_select(
                col(AgentRequest.project_id),
                col(AgentRequest.id).label("request_id"),
                entry_kind.label("entry_kind"),
                func.json_extract(metadata, "$.project_setup.project_id").label("owner_project_id"),
                func.json_extract(metadata, "$.project_setup.request_id").label("owner_request_id"),
                status.label("status"),
                reservation.label("reservation"),
                admission.label("admission_id"),
                func.json_extract(metadata, "$.project_setup.managed.settings.project_dir").label(
                    "project_dir"
                ),
            )
            .where(
                or_(
                    col(AgentRequest.source_kind) == SETUP_NAMESPACE,
                    func.json_type(metadata, "$.project_setup").is_not(None),
                ),
                # Only an established external attempt is outside managed
                # ownership. Unclassified setup is not evidence of free work.
                or_(
                    managed.is_not(None) & (managed != "null"),
                    entry_kind.is_(None),
                    entry_kind != "existing_agent",
                ),
                or_(
                    entry_kind.is_(None),
                    entry_kind != "managed_ui",
                    status.is_(None),
                    status.not_in(
                        ("needs_input", "success", "partial", "failed", "interrupted", "cancelled")
                    ),
                    reservation.is_(None),
                    reservation != "false",
                ),
            )
            .order_by(col(AgentRequest.id))
        )
        if project_id is not None:
            query = query.where(col(AgentRequest.project_id) == project_id)
        if admission_id is not None:
            query = query.where(
                status == "queued", reservation == "false", admission == admission_id
            )
        if job_ref is not None:
            query = query.where(
                func.json_extract(metadata, "$.project_setup.managed.job_ref") == job_ref
            )
        states = []
        for row in self._s.execute(query).all():
            values = row._asdict()
            owner_project = values.pop("owner_project_id")
            owner_request = values.pop("owner_request_id")
            try:
                state = SetupAdmissionState.model_validate(values)
                if owner_project != state.project_id or owner_request != state.request_id:
                    raise ValueError("inconsistent setup identity")
            except (ModelValidationError, ValueError) as exc:
                raise ConflictError(
                    "managed setup ownership is uncertain; inspect before admitting another writer",
                    data={
                        "project_id": row.project_id,
                        "request_id": row.request_id,
                        "next_action": "inspect_request",
                    },
                ) from exc
            states.append(state)
        return states

    def inspect_sources(
        self,
        project_id: int,
        workflow_key: str,
        *,
        selected_optional_presets: list[str] | None = None,
        _comparison: bool = False,
        _project: Any | None = None,
        _source_cache: dict[str, Any] | None = None,
    ) -> list[SetupSource]:
        if _project is None:
            project = ProjectRepository(self._s)
            project.require_available(project_id)
            root = project.get(project_id).project_dir
        else:
            root = _project.project_dir
        # Reads must not synchronize the plugin catalog or commit the project
        # availability reservation held by a receipt writer.
        template_loader = WorkflowTemplateLoader(self._s, sync_plugins=False)
        template_candidates = _source_cache.get("templates") if _source_cache is not None else None
        if template_candidates is None:
            template_candidates = template_loader._load_candidates(
                project_id=project_id, repo_root=root, plugin_slug=None
            )
            if _source_cache is not None:
                _source_cache["templates"] = template_candidates
        workflow = template_loader.describe_template(
            key=workflow_key,
            project_id=project_id,
            repo_root=root,
            _candidates=template_candidates,
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
        skill_loader = SkillPresetLoader()
        skill_candidates = _source_cache.get("skills") if _source_cache is not None else None
        for requirement in workflow.spec.skill_preset_requirements:
            if (
                requirement.requirement == "optional"
                and requirement.skill_preset_ref not in selected
            ):
                continue
            if skill_candidates is None:
                skill_candidates = skill_loader._load_candidates(repo_root=root, plugin_slug=None)
                if _source_cache is not None:
                    _source_cache["skills"] = skill_candidates
            loaded = skill_loader.describe_preset(
                key=requirement.skill_preset_ref,
                repo_root=root,
                _candidates=skill_candidates,
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
        agent_loader = AgentPresetLoader()
        agent_candidates = _source_cache.get("agents") if _source_cache is not None else None
        for agent_requirement in workflow.spec.agent_requirements:
            if (
                agent_requirement.requirement == "optional"
                and agent_requirement.agent_preset_ref not in selected
            ):
                continue
            if agent_candidates is None:
                agent_candidates = agent_loader._load_candidates(repo_root=root, plugin_slug=None)
                if _source_cache is not None:
                    _source_cache["agents"] = agent_candidates
            loaded_agent = agent_loader.describe_preset(
                key=agent_requirement.agent_preset_ref,
                repo_root=root,
                _candidates=agent_candidates,
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
        proof = result.verification
        current_sources = None
        if proof is not None and (
            result.status == "success"
            or proof.activation_proof is not None
            or any(source.content is None for source in proof.sources)
        ):
            current = self.inspect_sources(
                project_id, workflow_key, selected_optional_presets=proof.selected_optional_presets
            )
            current_sources = current
            if self._identity(current) != self._identity(proof.sources):
                raise ConflictError(
                    "setup sources changed; inspect and revalidate before reporting",
                    data={"project_id": project_id, "workflow_key": workflow_key},
                )
            by_ref = {source.ref: source for source in current}
            for source in proof.sources:
                if source.content is None:
                    source.content = by_ref[source.ref].model_copy(deep=True).content
            if any(source.content is None for source in proof.sources):
                raise ValidationError("resolved setup sources must include content")
            # Recheck content integrity and size after hydration. result is a
            # private validated copy, so the caller/idempotency input stays intact.
            result = SetupResult.model_validate(result.model_dump(mode="json"))
            proof = result.verification
            assert proof is not None
            if result.status == "success":
                self._validate_roles(current, proof)
        if proof is not None and proof.activation_proof is not None:
            self._validate_activation(project_id, proof, current_sources=current_sources)
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
        requested_scope: SetupScope = "ready_to_use",
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
                requested_scope=requested_scope,
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
            # External file edits may precede the first result report.
            current.partial_changes_possible = True
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
        _native_final: bool = False,
    ) -> SetupAttempt:
        try:
            report = SetupReport.model_validate(report.model_dump(mode="json"))
            report_digest = _digest(report.model_dump(mode="json"))
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
            final_replay = bool(
                _native_final
                and _managed
                and not _session_report
                and current.reports
                # Continuations retain prior reports. Matching content alone
                # cannot make that history a report applied by this invocation.
                and current.status == report.status
                and current.reports[-1].report_digest == report_digest
            )
            final_confirmation = (
                _native_final
                and _managed
                and not _session_report
                and current.status == "success"
                and report.status == "success"
                and not report.workflow_results
            )
            if (
                _native_final
                and current.status == "needs_input"
                and report.status not in {"needs_input", "failed", "interrupted", "cancelled"}
            ):
                raise ConflictError(
                    "Native final outcome cannot discard an unanswered setup question"
                )
            reconcile_final = (
                _native_final
                and _managed
                and not _session_report
                and (
                    current.status == "partial"
                    or report.status in {"failed", "interrupted", "cancelled"}
                    or final_replay
                    or final_confirmation
                )
            )
            if (
                current.status not in {"running", "needs_input"} and not reconcile_final
            ) or current.revision != expected_revision:
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
                if final_replay:
                    # The exact report was already applied by this invocation.
                    # Revalidate final success below without writing receipts twice.
                    report.workflow_results = []
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
            if report.adoption_proposal is not None:
                if (
                    current.managed is not None
                    and current.managed.phase != "preparation"
                    and report.adoption_proposal != current.adoption_proposal
                ):
                    raise ConflictError("Adopted role selection changed; start a new setup review")
                self._validate_adoption_proposal(project_id, report.adoption_proposal)
                current.adoption_proposal = report.adoption_proposal
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
                # The owning request fixes intent. A report cannot silently downgrade it.
                item.result.requested_scope = current.requested_scope
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
                    activation_status=saved.activation_status,
                    missing_activation_claims=saved.missing_activation_claims,
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
                    if current.requested_scope == "ready_to_use":
                        activation = proof.activation_proof
                        if (
                            proof.activation != "verified_loaded"
                            or activation is None
                            or activation.status != "verified"
                            or activation.gaps()
                        ):
                            raise ValidationError(
                                "required workflow activation is incomplete",
                                data={
                                    "workflow_key": key,
                                    "requested_scope": current.requested_scope,
                                    "activation_status": activation.status
                                    if activation
                                    else "pending",
                                    "missing_claims": activation.gaps()
                                    if activation
                                    else ["saved_defaults", "native_consumption"],
                                    "next_action": "complete_activation",
                                },
                            )
                        self._validate_activation(project_id, proof, current_sources=sources)
            if final_replay or final_confirmation:
                # A native final confirmation may summarize the success already
                # reported by this invocation. The retained proof was revalidated
                # above; preserve its receipts and the accepted report unchanged.
                self._s.commit()
                return current
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
                    adoption_proposal=report.adoption_proposal,
                    report_digest=report_digest,
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
                    "requested_scope",
                    "entry_kind",
                    "status",
                },
            )
            if state.current_attempt
            else None
        )
        return summary

    @staticmethod
    def _identity(sources: list[SetupSource]) -> list[tuple[str, str, str, str, str, str | None]]:
        return sorted(
            (item.ref, item.kind, item.key, item.origin, item.digest, item.version)
            for item in sources
        )

    def activation_basis(
        self, project_id: int, proof: VerifiedSetup, *, environment_ref: str
    ) -> SetupActivationBasis:
        """Read current identities without adopting settings or granting any authority."""
        from stackos.repositories.tracker import TrackerRepository

        project = ProjectRepository(self._s).require_available(project_id)
        if project.primary_workspace_binding_id is None:
            raise ValidationError("activation requires a bound project folder")
        return SetupActivationBasis(
            project_id=project_id,
            binding_id=project.primary_workspace_binding_id,
            sources_digest=_digest(self._identity(proof.sources)),
            delivery_digest=_digest(
                TrackerRepository(self._s).delivery_defaults(project_id=project_id)
            ),
            adaptations_digest=_digest(
                [item.model_dump(mode="json") for item in proof.local_adaptations]
            ),
            environment_ref=environment_ref,
        )

    def _validate_adoption_proposal(self, project_id: int, proposal: SetupAdoptionProposal) -> None:
        from pathlib import Path

        from stackos.repositories.tracker import TrackerRepository
        from stackos.task_delivery import _project_agent_source

        project = ProjectRepository(self._s).get(project_id)
        if (
            proposal.project_id != project_id
            or proposal.binding_id != project.primary_workspace_binding_id
            or proposal.project_dir != project.project_dir
            or proposal.expected_delivery_digest
            != _digest(TrackerRepository(self._s).delivery_defaults(project_id=project_id))
        ):
            raise ConflictError(
                "setup adoption proposal has a foreign or stale basis",
                data={"project_id": project_id, "next_action": "refresh_adoption_proposal"},
            )
        for role, selection in proposal.roles.items():
            if not role.strip() or len(role) > 160:
                raise ValidationError("setup adoption role must be a bounded nonblank name")
            if selection.source_ref is not None:
                source = _project_agent_source(
                    self._s,
                    project_id,
                    Path(proposal.project_dir),
                    selection.source_ref.model_dump(),
                )
                if (
                    source.digest != selection.expected_digest
                    or source.fields is None
                    or selection.name != source.source_ref.native_name
                ):
                    raise ConflictError(
                        "setup adoption proposal source changed",
                        data={
                            "project_id": project_id,
                            "role": role,
                            "next_action": "refresh_adoption_proposal",
                        },
                    )
        # Deliberately no configure/adopt call. Only the authorized managed owner
        # may consume this proposal through the canonical defaults writer.

    def _validate_activation(
        self,
        project_id: int,
        proof: VerifiedSetup,
        *,
        current_sources: list[SetupSource] | None = None,
    ) -> None:
        activation = proof.activation_proof
        assert activation is not None
        if current_sources is None:
            template = next((source for source in proof.sources if source.kind == "template"), None)
            if template is None:
                raise ValidationError("activation needs the effective workflow template source")
            current_sources = self.inspect_sources(
                project_id, template.key, selected_optional_presets=proof.selected_optional_presets
            )
        if self._identity(current_sources) != self._identity(proof.sources):
            raise ConflictError(
                "activation sources changed; revalidate current coverage",
                data={"project_id": project_id, "next_action": "revalidate_activation"},
            )
        role_claims = self.activation_role_claims(project_id, current_sources)
        if not set(role_claims) <= set(activation.required_claims):
            raise ValidationError(
                "activation must declare the workflow's required role claims",
                data={"required_role_claims": role_claims, "next_action": "complete_activation"},
            )
        for check in activation.checks:
            basis = self.activation_basis(
                project_id, proof, environment_ref=check.basis.environment_ref
            )
            if basis != check.basis:
                raise ConflictError(
                    "activation evidence has a foreign or stale basis",
                    data={"project_id": project_id, "next_action": "revalidate_activation"},
                )
            self._validate_activation_reference(project_id, check.reference, check.status)
            required_roles = {role_claims[claim] for claim in check.claims if claim in role_claims}
            if required_roles or check.execution is not None:
                self._validate_role_check(
                    project_id,
                    check.reference,
                    execution=check.execution,
                    claims=check.claims,
                    status=check.status,
                    origin=check.origin,
                    environment_ref=check.basis.environment_ref,
                    evidence_ref=check.evidence_ref,
                    required_roles=required_roles,
                )

    def activation_role_claims(self, project_id: int, sources: list[SetupSource]) -> dict[str, str]:
        template = next(source for source in sources if source.kind == "template")
        method: dict[str, Any] = next(
            (
                p
                for p in (template.content or {}).get("policies", [])
                if p["key"] == "setup_activation_method"
            ),
            {},
        )
        declared_claims = method.get("config_json", {}).get("required_role_claims", {})
        if not isinstance(declared_claims, dict) or any(
            not isinstance(claim, str) or not claim.strip() or len(claim) > 200
            for claim in declared_claims
        ):
            raise ValidationError("setup activation method has invalid required role claims")
        from stackos.repositories.tracker import TrackerRepository
        from stackos.repositories.tracker.setup import _delivery_role_defaults

        defaults = TrackerRepository(self._s).delivery_defaults(project_id=project_id)
        role_claims: dict[str, str] = {}
        for claim, value in declared_claims.items():
            try:
                requirement = SetupRoleClaim.model_validate(value)
            except ModelValidationError as error:
                raise ValidationError(
                    "setup activation method has invalid role claim", data={"claim": claim}
                ) from error
            actual_requirements = (
                _delivery_role_defaults(defaults, requirement.role).get("execution_requirements")
                or {}
            )
            if all(
                actual_requirements.get(key) is expected
                for key, expected in requirement.when_execution_requirements.items()
            ):
                role_claims[claim] = requirement.role
        return role_claims

    def _validate_activation_reference(
        self, project_id: int, ref: SetupProofReference, status: str
    ) -> None:
        if ref.kind == "native_request":
            row = self._s.get(AgentRequest, ref.request_id, populate_existing=True)
            native = (row.metadata_json or {}).get("native_execution", {}) if row else {}
            passed = row is not None and self._native_activation_passed(row, native)
            if (
                row is None
                or row.project_id != project_id
                or not native.get("finished_at")
                or passed != (status == "passed")
                or native.get("execution", {}).get("invocation_id") != ref.invocation_id
                or _digest(native.get("result")) != ref.outcome_digest
            ):
                raise ConflictError(
                    "activation reference is not the completed native outcome",
                    data={
                        "project_id": project_id,
                        "request_id": ref.request_id,
                        "next_action": "revalidate_activation",
                    },
                )
        elif ref.kind == "workflow_step":
            plan = self._s.get(RunPlan, ref.run_plan_id, populate_existing=True)
            step = self._s.exec(
                select(RunPlanStep)
                .where(
                    RunPlanStep.run_plan_id == ref.run_plan_id,
                    RunPlanStep.step_id == ref.step_id,
                )
                .execution_options(populate_existing=True)
            ).first()
            if (
                plan is None
                or plan.project_id != project_id
                or plan.status.value not in {"completed", "failed", "aborted"}
                or step is None
                or step.result_json is None
                or _digest(step.result_json) != ref.outcome_digest
                or (plan.status.value == "completed" and step.status.value == "success")
                != (status == "passed")
            ):
                raise ConflictError(
                    "activation reference is not the completed workflow outcome",
                    data={
                        "project_id": project_id,
                        "run_plan_id": ref.run_plan_id,
                        "step_id": ref.step_id,
                        "next_action": "revalidate_activation",
                    },
                )
        else:
            self._task_phase_outcome(project_id, ref, status)

    # Coverage selection is the workflow method's judgment. These checks
    # validate identities/outcomes, not the semantics of a domain assertion.

    def execution_binding(
        self, project_id: int, role: str, agent: dict[str, Any], *, binding_id: int | None = None
    ) -> SetupExecutionBinding:
        """Canonical digest of resolved settings; never infer inline source refs."""
        project = ProjectRepository(self._s).require_available(project_id)
        binding_id = binding_id if binding_id is not None else project.primary_workspace_binding_id
        if binding_id is None:
            raise ValidationError("activation role claim requires a bound project folder")
        source_binding = self._s.get(WorkspaceBinding, binding_id)
        if source_binding is None or source_binding.project_id != project_id:
            raise ValidationError("activation role claim requires a bound project folder")
        settings: dict[str, Any] = {
            key: agent[key]
            for key in ("model", "model_reasoning_effort", "provider", "profile", "native_settings")
            if key in agent
        }
        settings["sandbox"] = self._launch_sandbox(agent, role)
        if not agent.get("source_ref"):
            settings["instructions_digest"] = _digest(agent.get("instructions"))
        return SetupExecutionBinding(
            role=role,
            agent=agent["name"],
            host_key=agent["host_key"],
            binding_id=binding_id,
            source_ref=agent.get("source_ref"),
            source_digest=agent.get("source_digest"),
            settings_digest=_digest(settings),
        )

    def _task_phase_outcome(
        self, project_id: int, ref: SetupProofReference, status: str
    ) -> dict[str, Any]:
        task = self._s.get(TrackerTask, ref.task_id, populate_existing=True)
        ticket = self._s.get(TrackerTicket, ref.ticket_id, populate_existing=True)
        automatic = (
            (ticket.completion_evidence_json or {}).get("automatic_delivery", {}) if ticket else {}
        )
        attempts = [automatic, *automatic.get("attempts", [])]
        matching = [item for item in attempts if item.get("attempt") == ref.attempt]
        receipt = matching[0].get(ref.phase, {}) if len(matching) == 1 else {}
        execution = receipt.get("execution") or {}
        passed = (
            receipt.get("validated_outcome") == "success"
            and receipt.get("exit_code") == 0
            and not receipt.get("error")
            and not receipt.get("validation_error")
            and not receipt.get("cancelled")
            and not receipt.get("superseded")
            and (receipt.get("result") or {}).get("status") == "success"
        )
        if (
            task is None
            or task.project_id != project_id
            or ticket is None
            or ticket.project_id != project_id
            or ticket.task_id != task.id
            or not receipt.get("finished_at")
            or not receipt.get("thread_id")
            or execution.get("invocation_id") != ref.invocation_id
            or execution.get("ticket_key") != ticket.key
            or execution.get("agent") != receipt.get("agent")
            or execution.get("host_key") != receipt.get("launch", {}).get("host_key")
            or _digest(receipt.get("result")) != ref.outcome_digest
            or passed != (status == "passed")
        ):
            raise ConflictError("activation reference is not the completed task phase outcome")
        return {"receipt": receipt, "sessions": automatic.get("sessions", {})}

    def _validate_role_check(
        self,
        project_id: int,
        reference: SetupProofReference,
        *,
        execution: SetupExecutionBinding | None,
        claims: list[str],
        status: str,
        origin: str,
        environment_ref: str,
        evidence_ref: str,
        required_roles: set[str],
    ) -> None:
        from pathlib import Path

        from stackos.task_delivery import resolve_delivery_settings

        binding = execution
        receipt: dict[str, Any]
        # A generic coordinator conversation or workflow assertion has no launched
        # specialist identity. Keep those refs for their original non-role claims.
        if binding is None or (required_roles and required_roles != {binding.role}):
            raise ValidationError("activation role claim needs its selected execution binding")
        if reference.kind == "native_request":
            row = self._s.get(AgentRequest, reference.request_id, populate_existing=True)
            native = (row.metadata_json or {}).get("native_execution", {}) if row else {}
            launch = native.get("snapshot") or {}
            try:
                role_check = SetupNativeRoleCheck.model_validate(
                    launch.get("context", {}).get("role_check")
                )
            except ModelValidationError as error:
                raise ValidationError(
                    "activation role claim lacks an actual specialist native request"
                ) from error
            actual = role_check.agent_snapshot
            if (
                role_check.execution != binding
                or role_check.request.role != binding.role
                or not set(claims) <= set(role_check.request.claims)
                or role_check.request.environment_ref != environment_ref
                or role_check.request.evidence_ref != evidence_ref
                or launch.get("instructions") != actual.get("instructions")
            ):
                raise ConflictError(
                    "activation role claim has a mismatched native request snapshot"
                )
            launch = {
                **launch,
                "agent": native.get("execution", {}).get("agent"),
                **{key: actual[key] for key in ("source_ref", "source_digest") if key in actual},
            }
            receipt = {"result": native.get("result")}
        elif reference.kind == "task_phase":
            outcome = self._task_phase_outcome(project_id, reference, status)
            receipt = outcome["receipt"]
            launch = receipt.get("launch") or {}
            retained = outcome["sessions"].get(reference.phase, {})
            actual = retained.get("agent_snapshot")
            if (
                receipt.get("selected_role") != binding.role
                or not isinstance(actual, dict)
                or retained.get("thread_id") != receipt["thread_id"]
            ):
                raise ConflictError("activation role claim lacks the actual native snapshot")
        else:
            raise ValidationError("activation role claim needs an actual native role execution")
        project = ProjectRepository(self._s).get(project_id)
        if project.project_dir is None:
            raise ValidationError("activation role claim requires an available project folder")
        selected = resolve_delivery_settings(
            self._s,
            project_id=project_id,
            project_dir=project.project_dir,
            role_keys={binding.role},
        )["agents"][binding.role]
        source_binding = self._s.get(WorkspaceBinding, binding.binding_id)
        if (
            source_binding is None
            or source_binding.project_id != project_id
            or not source_binding.last_known_root
            or not launch.get("project_dir")
            or Path(source_binding.last_known_root).resolve()
            != Path(launch["project_dir"]).resolve()
        ):
            raise ConflictError("activation role claim has a foreign or mismatched source binding")
        actual_binding = self.execution_binding(
            project_id, binding.role, actual, binding_id=binding.binding_id
        )
        if (
            binding != actual_binding
            or binding
            != self.execution_binding(
                project_id, binding.role, selected, binding_id=binding.binding_id
            )
            or launch.get("agent") != binding.agent
            or launch.get("host_key") != binding.host_key
            or launch.get("sandbox") != self._launch_sandbox(actual, binding.role)
            or any(
                launch.get(key) != actual.get(key)
                for key in (
                    "source_ref",
                    "source_digest",
                    "model",
                    "model_reasoning_effort",
                    "provider",
                    "profile",
                    "native_settings",
                )
            )
        ):
            raise ConflictError("activation role claim has a stale or mismatched native execution")
        try:
            outputs = json.loads(receipt["result"]["outputs"])
            observations = outputs["setup_activation_checks"]
            covered = any(
                set(claims) <= set(item["claims"])
                and item["status"] == status
                and item["environment_ref"] == environment_ref
                and item["evidence_ref"] == evidence_ref
                for item in observations
            )
        except (KeyError, TypeError, ValueError):
            covered = False
        if not covered or origin == "simulated":
            raise ValidationError(
                "activation role claim lacks the native bounded-check observation"
            )

    @staticmethod
    def _launch_sandbox(agent: dict[str, Any], role: str) -> str:
        from stackos.host_mcp.service import agent_review_sandbox, effective_agent_sandbox

        return effective_agent_sandbox(
            agent["host_key"],
            agent_review_sandbox(agent["host_key"])
            if role == "review_agent"
            else "workspace-write",
            agent.get("native_settings"),
        )

    @staticmethod
    def _native_activation_passed(row: AgentRequest, native: dict[str, Any]) -> bool:
        from jsonschema import SchemaError

        from stackos.host_mcp.result import AgentRunResult, validate_agent_result

        # Native retirement precedes the caller's interpretation and request
        # completion. Neither process exit nor a result digest proves success.
        result = AgentRunResult(
            exit_code=native.get("exit_code"),
            result=native.get("result"),
            error=native.get("error"),
        )
        snapshot = native.get("snapshot")
        schema = snapshot.get("output_schema") if isinstance(snapshot, dict) else None
        if (
            row.status != AgentRequestStatus.RESOLVED
            or not result.ok
            or not isinstance(result.result, dict)
            or not result.result
            or native.get("cancelled")
            or native.get("stop_error")
            or not isinstance(schema, dict)
            or not schema
        ):
            return False
        try:
            if not validate_agent_result(result, schema, "activation evidence").ok:
                return False
        except SchemaError:
            return False
        if row.source_kind == SETUP_NAMESPACE:
            context = (snapshot or {}).get("context", {})
            if context.get("role_check") is not None:
                try:
                    attempt = SetupAttempt.model_validate(
                        (row.metadata_json or {}).get(SETUP_NAMESPACE)
                    )
                    role_check = SetupNativeRoleCheck.model_validate(context["role_check"])
                except ModelValidationError:
                    return False
                return bool(
                    attempt.project_id == row.project_id
                    and attempt.request_id == row.id
                    and attempt.managed is not None
                    and not attempt.managed.cancel_requested
                    and attempt.status == "partial"
                    and row.id in attempt.managed.role_verification_request_ids
                    and context.get("project_id") == row.project_id
                    and role_check.workflow_key in attempt.workflow_keys
                    and isinstance(native.get("execution", {}).get("thread_id"), str)
                    and bool(native["execution"]["thread_id"].strip())
                    and native["execution"]["thread_id"] != attempt.managed.coordinator_thread_id
                    and native["execution"].get("host_key") == (snapshot or {}).get("host_key")
                    and native.get("execution", {}).get("agent")
                    == role_check.agent_snapshot.get("name")
                    and result.result.get("status") == "success"
                )
            # Managed setup owns its wrapped logical report and persisted outcome.
            report_json = result.result.get("report_json")
            if not isinstance(report_json, str):
                return False
            if result.result.get("verification_status") in {"failed", "blocked"}:
                return False
            try:
                attempt = SetupAttempt.model_validate(
                    (row.metadata_json or {}).get(SETUP_NAMESPACE)
                )
                report = SetupReport.model_validate_json(report_json)
            except ModelValidationError:
                return False
            completed_setup = (
                attempt.project_id == row.project_id
                and attempt.request_id == row.id
                and attempt.entry_kind == "managed_ui"
                and attempt.managed is not None
                and not attempt.managed.cancel_requested
                and attempt.status == "success"
                and report.status == "success"
            )
            # A bounded verification turn has its own explicit logical outcome.
            # It is not overall setup success: a subsequent current claim must
            # still validate all workflow receipts against this retired result.
            completed_check = (
                attempt.project_id == row.project_id
                and attempt.request_id == row.id
                and attempt.entry_kind == "managed_ui"
                and attempt.managed is not None
                and attempt.managed.adoption_authorized
                and not attempt.managed.cancel_requested
                and attempt.managed.phase in {"role_verification", "completion"}
                and attempt.managed.verification_request_id == row.id
                and attempt.status == "partial"
                and report.status == "partial"
                and result.result.get("verification_status") == "passed"
                and "verification_status" in schema.get("properties", {})
            )
            return completed_setup or completed_check
        # The other current native request caller returns its logical status
        # directly. Unknown/missing outcomes cannot receive positive coverage.
        return result.result.get("status") == "success"

    @staticmethod
    def _validate_roles(sources: list[SetupSource], proof: VerifiedSetup) -> None:
        template = next(item for item in sources if item.kind == "template")
        assert template.content is not None
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
        self,
        row: ProjectSetupReceipt | None,
        project_id: int,
        workflow_key: str,
        *,
        project: Any | None = None,
        source_cache: dict[str, Any] | None = None,
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
            out.requested_scope = latest.get("requested_scope", "infrastructure_only")
            latest_proof = (latest.get("verification") or {}).get("activation_proof")
            if latest_proof:
                activation = SetupActivationEvidence.model_validate(latest_proof)
                out.activation_status = activation.status
                out.missing_activation_claims = activation.gaps()
                out.diagnostic = activation.diagnostic
            elif out.requested_scope == "ready_to_use":
                out.activation_status = "pending"
                out.missing_activation_claims = ["saved_defaults", "native_consumption"]
        if project is None:
            project = ProjectRepository(self._s).get(project_id)
        if project.primary_workspace_binding_id is not None and project.project_dir is None:
            out.status = "source_unavailable"
            out.diagnostic = project.project_folder_issue
            if out.requested_scope == "ready_to_use":
                out.activation_status = "pending"
                out.missing_activation_claims = ["current_environment"]
            return out
        try:
            optional = (
                row.successful_json["verification"].get("selected_optional_presets", [])
                if row and row.successful_json
                else []
            )
            out.available_sources = self.inspect_sources(
                project_id,
                workflow_key,
                selected_optional_presets=optional,
                _comparison=True,
                _project=project,
                _source_cache=source_cache,
            )
        except (RepositoryError, ValueError, yaml.YAMLError, OSError) as exc:
            out.status = "source_unavailable"
            if out.requested_scope == "ready_to_use":
                out.activation_status = "pending"
                out.missing_activation_claims = ["current_sources"]
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
            if out.status == "update_available" and out.requested_scope == "ready_to_use":
                out.activation_status = "pending"
                out.missing_activation_claims = ["current_sources"]
            if out.status == "current" and applied.activation != "verified_loaded":
                out.status = "activation_pending"
            if out.status == "current" and out.requested_scope == "ready_to_use":
                if (
                    applied.activation_proof is None
                    or applied.activation_proof.status != "verified"
                ):
                    out.status = "activation_pending"
                else:
                    try:
                        self._validate_activation(
                            project_id, applied, current_sources=out.available_sources
                        )
                    except (ConflictError, ValidationError):
                        out.status = "activation_pending"
                        out.activation_status = "pending"
                        out.missing_activation_claims = ["current_activation_evidence"]
                        out.diagnostic = (
                            "Activation evidence changed; revalidate the missing coverage."
                        )
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
        receipts_by_key = {item.workflow_key: item for item in receipts}
        aggregate_keys = keys.copy()
        if workflow_key:
            keys.add(workflow_key)
        source_cache: dict[str, Any] = {}
        workflows = [
            self._workflow(
                receipts_by_key.get(key),
                project_id,
                key,
                project=project,
                source_cache=source_cache,
            )
            for key in sorted(keys)
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
        delivery_defaults = TrackerRepository(self._s).delivery_defaults(project_id=project_id)
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
            launch_preferences=delivery_defaults.get("main_agent", {}),
            delivery_defaults_digest=_digest(delivery_defaults),
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
