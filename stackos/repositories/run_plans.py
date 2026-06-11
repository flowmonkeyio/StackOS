"""Repository for StackOS run plans and approval gates."""

from __future__ import annotations

import builtins
import json
import secrets
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, select

from stackos.db.models import (
    APPROVAL_REQUEST_STATUS_TRANSITIONS,
    RUN_PLAN_STATUS_TRANSITIONS,
    RUN_PLAN_STEP_STATUS_TRANSITIONS,
    ApprovalRequest,
    ApprovalRequestStatus,
    ContextSnapshot,
    Project,
    Run,
    RunKind,
    RunPlan,
    RunPlanStatus,
    RunPlanStep,
    RunPlanStepStatus,
    RunStatus,
)
from stackos.repositories.base import (
    ConflictError,
    Envelope,
    NotFoundError,
    Page,
    ValidationError,
    cursor_paginate,
    validate_transition,
)
from stackos.repositories.run_plan_dependencies import (
    completed_dependency_step_ids,
    incomplete_step_dependencies,
    transitive_step_dependencies,
)
from stackos.repositories.run_plan_lifecycle import (
    RunPlanConsistencyIssueOut,
    RunPlanConsistencyOut,
    RunPlanLifecycleReconciler,
)
from stackos.repositories.run_plan_state import TERMINAL_STEP_STATUSES
from stackos.repositories.runs import RunOut, RunRepository
from stackos.repositories.tracker import TrackerRepository
from stackos.workflows.run_plan_grants import allowed_tools_for_run_plan_step
from stackos.workflows.run_plan_schema import (
    RunPlanIssue,
    RunPlanValidationOut,
    find_run_plan_secret_paths,
    parse_run_plan_obj,
    run_plan_from_template,
    run_plan_readiness_warnings,
    validate_run_plan_obj,
)
from stackos.workflows.template_loader import LoadedWorkflowTemplate, WorkflowTemplateLoader

RUN_PLAN_CONTROLLER_SKILL = "stackos/run-plan-controller"


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _token() -> str:
    return secrets.token_urlsafe(32)


def _required_id(value: int | None) -> int:
    if value is None:
        raise RuntimeError("expected persisted row id")
    return int(value)


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _ensure_no_secrets(value: Any, *, label: str) -> None:
    paths = find_run_plan_secret_paths(value)
    if paths:
        raise ValidationError(
            f"{label} must not contain secrets; use opaque credential_ref values",
            data={"paths": paths[:8]},
        )


class RunPlanStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_plan_id: int
    step_id: str
    title: str
    purpose: str
    position: int
    status: RunPlanStepStatus
    depends_on_json: list[str]
    input_refs_json: list[str]
    context_refs_json: list[str]
    action_refs_json: list[str]
    resource_refs_json: list[str]
    policy_refs_json: list[str]
    approval_refs_json: list[str]
    output_refs_json: list[str]
    instructions_json: list[str]
    success_criteria_json: list[str]
    action_payloads_json: list[dict[str, Any]] | None
    expected_outputs_json: dict[str, Any] | None
    result_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    allowed_tools: list[str] = Field(default_factory=list)
    action_execution_guidance: dict[str, Any] = Field(default_factory=dict)
    error: str | None
    claimed_by: str | None
    claimed_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApprovalRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    run_plan_id: int
    run_plan_step_id: int | None
    approval_key: str
    title: str
    description: str
    required_when: str
    approver: str | None
    status: ApprovalRequestStatus
    requested_by: str | None
    decided_by: str | None
    requested_at: datetime
    decided_at: datetime | None
    decision_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class RunPlanSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    run_id: int | None
    template_id: int | None
    template_version_id: int | None
    context_snapshot_id: int | None
    key: str
    title: str
    goal: str
    status: RunPlanStatus
    template_key: str | None
    template_version: str | None
    template_source: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class RunPlanOut(RunPlanSummaryOut):
    template_origin_path: str | None
    template_snapshot_json: dict[str, Any] | None
    inputs_json: dict[str, Any]
    selected_context_json: dict[str, Any] | None
    context_filters_json: dict[str, Any] | None
    grant_snapshot_json: dict[str, Any] | None
    budget_snapshot_json: dict[str, Any] | None
    policy_snapshot_json: dict[str, Any] | None
    output_contract_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    steps: list[RunPlanStepOut] = Field(default_factory=list)
    approval_requests: list[ApprovalRequestOut] = Field(default_factory=list)
    consistency_issues: list[RunPlanConsistencyIssueOut] = Field(default_factory=list)


class RunPlanStartOut(BaseModel):
    plan: RunPlanOut
    run: RunOut
    run_token: str
    run_id: int


class RunPlanReopenOut(BaseModel):
    plan: RunPlanOut
    run: RunOut
    run_token: str
    run_id: int
    reopened_step_id: str
    reset_step_ids: list[str]
    next_operations: list[str] = Field(default_factory=list)


class RunPlanRepository:
    """Concrete run-plan storage and lifecycle.

    This repository owns StackOS run-plan rows. It links to ``runs`` when a
    plan starts so execution has a generic audit row.
    """

    def __init__(self, session: Session) -> None:
        self._s = session

    def validate_plan(
        self,
        *,
        run_plan_json: dict[str, Any] | None = None,
        template_key: str | None = None,
        project_id: int | None = None,
        repo_root: str | None = None,
        plugin_slug: str | None = None,
        source: str | None = None,
        inputs_json: dict[str, Any] | None = None,
        selected_context_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        enforce_required_inputs: bool = False,
    ) -> RunPlanValidationOut:
        if run_plan_json is not None:
            return validate_run_plan_obj(run_plan_json)
        if template_key is None:
            return RunPlanValidationOut(
                valid=False,
                errors=[
                    RunPlanIssue(
                        path="$",
                        message="run_plan_json or template_key is required",
                        code="missing_plan",
                    )
                ],
            )
        try:
            loaded = self._load_template(
                key=template_key,
                project_id=project_id,
                repo_root=repo_root,
                plugin_slug=plugin_slug,
                source=source,
            )
            plan = run_plan_from_template(
                loaded,
                inputs_json=inputs_json,
                selected_context_json=selected_context_json,
                metadata_json=metadata_json,
                enforce_required_inputs=enforce_required_inputs,
            )
        except Exception as exc:
            return RunPlanValidationOut(
                valid=False,
                errors=[RunPlanIssue(path="$", message=str(exc), code="template_error")],
            )
        return RunPlanValidationOut(
            valid=True,
            plan=plan,
            warnings=run_plan_readiness_warnings(plan),
        )

    def create(
        self,
        *,
        project_id: int,
        run_plan_json: dict[str, Any] | None = None,
        template_key: str | None = None,
        repo_root: str | None = None,
        plugin_slug: str | None = None,
        source: str | None = None,
        key: str | None = None,
        title: str | None = None,
        inputs_json: dict[str, Any] | None = None,
        context_snapshot_id: int | None = None,
        selected_context_json: dict[str, Any] | None = None,
        created_by: str | None = None,
        metadata_json: dict[str, Any] | None = None,
        _commit: bool = True,
    ) -> Envelope[RunPlanOut]:
        self._require_project(project_id)
        loaded: LoadedWorkflowTemplate | None = None
        if template_key is not None:
            loaded = self._load_template(
                key=template_key,
                project_id=project_id,
                repo_root=repo_root,
                plugin_slug=plugin_slug,
                source=source,
            )

        if run_plan_json is not None:
            plan = parse_run_plan_obj(run_plan_json)
            if loaded is None and plan.template_key is not None:
                loaded = self._load_template(
                    key=plan.template_key,
                    project_id=project_id,
                    repo_root=repo_root,
                    plugin_slug=plugin_slug,
                    source=source,
                )
        elif loaded is not None:
            try:
                plan = run_plan_from_template(
                    loaded,
                    key=key,
                    title=title,
                    inputs_json=inputs_json,
                    context_snapshot_id=context_snapshot_id,
                    selected_context_json=selected_context_json,
                    metadata_json=metadata_json,
                )
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
        else:
            raise ValidationError("run_plan_json or template_key is required")

        snapshot_id = (
            context_snapshot_id if context_snapshot_id is not None else plan.context_snapshot_id
        )
        if snapshot_id is not None:
            self._require_context_snapshot(project_id, snapshot_id)

        now = _utcnow()
        row = RunPlan(
            project_id=project_id,
            template_id=loaded.summary.template_id if loaded is not None else None,
            template_version_id=loaded.summary.version_id if loaded is not None else None,
            context_snapshot_id=snapshot_id,
            key=plan.key,
            title=plan.title,
            goal=plan.goal,
            status=RunPlanStatus.DRAFT,
            template_key=loaded.summary.key if loaded is not None else plan.template_key,
            template_version=(
                loaded.summary.version if loaded is not None else plan.template_version
            ),
            template_source=loaded.summary.source if loaded is not None else plan.template_source,
            template_origin_path=loaded.summary.origin_path if loaded is not None else None,
            template_snapshot_json=(
                _jsonable(loaded.spec.model_dump(mode="json")) if loaded is not None else None
            ),
            inputs_json=_jsonable(plan.inputs_json),
            selected_context_json=(
                _jsonable(plan.selected_context_json)
                if plan.selected_context_json is not None
                else None
            ),
            context_filters_json=(
                _jsonable(plan.context_filters_json)
                if plan.context_filters_json is not None
                else None
            ),
            grant_snapshot_json=(
                _jsonable(plan.grant_snapshot_json)
                if plan.grant_snapshot_json is not None
                else None
            ),
            budget_snapshot_json=(
                _jsonable(plan.budget_snapshot_json)
                if plan.budget_snapshot_json is not None
                else None
            ),
            policy_snapshot_json=(
                _jsonable(plan.policy_snapshot_json)
                if plan.policy_snapshot_json is not None
                else None
            ),
            output_contract_json=(
                _jsonable(plan.output_contract_json)
                if plan.output_contract_json is not None
                else None
            ),
            metadata_json=_jsonable(plan.metadata_json) if plan.metadata_json is not None else None,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        self._s.add(row)
        self._s.flush()
        assert row.id is not None

        step_rows: dict[str, RunPlanStep] = {}
        for index, step in enumerate(plan.steps):
            step_row = RunPlanStep(
                run_plan_id=row.id,
                step_id=step.id,
                title=step.title,
                purpose=step.purpose,
                position=step.position if step.position is not None else index,
                status=RunPlanStepStatus.PENDING,
                depends_on_json=_jsonable(step.depends_on),
                input_refs_json=_jsonable(step.input_refs),
                context_refs_json=_jsonable(step.context_refs),
                action_refs_json=_jsonable(step.action_refs),
                resource_refs_json=_jsonable(step.resource_refs),
                policy_refs_json=_jsonable(step.policy_refs),
                approval_refs_json=_jsonable(step.approval_refs),
                output_refs_json=_jsonable(step.output_refs),
                instructions_json=_jsonable(step.instructions),
                success_criteria_json=_jsonable(step.success_criteria),
                action_payloads_json=(
                    _jsonable(step.action_payloads_json)
                    if step.action_payloads_json is not None
                    else None
                ),
                expected_outputs_json=(
                    _jsonable(step.expected_outputs_json)
                    if step.expected_outputs_json is not None
                    else None
                ),
                metadata_json=(
                    _jsonable(step.metadata_json) if step.metadata_json is not None else None
                ),
                created_at=now,
                updated_at=now,
            )
            self._s.add(step_row)
            self._s.flush()
            step_rows[step.id] = step_row

        for approval in plan.approvals:
            approval_row = ApprovalRequest(
                project_id=project_id,
                run_plan_id=row.id,
                run_plan_step_id=(
                    step_rows[approval.step_id].id if approval.step_id is not None else None
                ),
                approval_key=approval.key,
                title=approval.title or approval.key,
                description=approval.description,
                required_when=approval.required_when,
                approver=approval.approver,
                status=ApprovalRequestStatus.PENDING,
                requested_by=created_by,
                metadata_json=(
                    _jsonable(approval.metadata_json)
                    if approval.metadata_json is not None
                    else None
                ),
                created_at=now,
                updated_at=now,
            )
            self._s.add(approval_row)

        TrackerRepository(self._s).mirror_run_plan_created(
            plan=row,
            steps=list(step_rows.values()),
            created_by=created_by,
        )

        if _commit:
            self._s.commit()
            self._s.refresh(row)
        else:
            self._s.flush()
        return Envelope(data=self._plan_out(row), project_id=project_id)

    def start(self, run_plan_id: int, *, project_id: int) -> Envelope[RunPlanStartOut]:
        row = self._fetch_plan(run_plan_id)
        if row.project_id != project_id:
            raise NotFoundError(
                f"run plan {run_plan_id} not found in project {project_id}",
                data={"project_id": project_id, "run_plan_id": run_plan_id},
            )
        if row.status != RunPlanStatus.DRAFT:
            raise ConflictError(
                "run plan has already been started or closed",
                data={
                    "run_plan_id": run_plan_id,
                    "status": row.status.value,
                    "run_id": row.run_id,
                },
            )

        token = _token()
        env = RunRepository(self._s).start(
            project_id=row.project_id,
            kind=RunKind.RUN_PLAN,
            client_session_id=token,
            metadata_json={
                "stackos_type": "run-plan",
                "run_plan_id": row.id,
                "skill_name": RUN_PLAN_CONTROLLER_SKILL,
                "template_key": row.template_key,
            },
            _commit=False,
        )
        run = env.data
        validate_transition(
            row.status,
            RunPlanStatus.STARTED,
            RUN_PLAN_STATUS_TRANSITIONS,
            label="run_plan.status",
        )
        row.status = RunPlanStatus.STARTED
        row.run_id = run.id
        row.started_at = _utcnow()
        row.updated_at = row.started_at
        if row.metadata_json is None:
            row.metadata_json = {}
        row.metadata_json = {**row.metadata_json, "run_id": run.id}
        if row.context_snapshot_id is not None:
            snapshot = self._s.get(ContextSnapshot, row.context_snapshot_id)
            if snapshot is not None and snapshot.run_id is None:
                snapshot.run_id = run.id
                self._s.add(snapshot)
        self._s.add(row)
        TrackerRepository(self._s).mirror_run_plan_started(plan=row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(
            data=RunPlanStartOut(
                plan=self._plan_out(row),
                run=run,
                run_token=token,
                run_id=run.id,
            ),
            run_id=run.id,
            project_id=row.project_id,
        )

    def get(self, run_plan_id: int, *, project_id: int | None = None) -> RunPlanOut:
        row = self._fetch_plan(run_plan_id)
        self._require_plan_project(row, project_id)
        return self._plan_out(row)

    def check_consistency(
        self,
        run_plan_id: int,
        *,
        project_id: int | None = None,
    ) -> RunPlanConsistencyOut:
        row = self._fetch_plan(run_plan_id)
        self._require_plan_project(row, project_id)
        return RunPlanLifecycleReconciler(self._s).check_plan(row)

    def recover(
        self,
        *,
        run_plan_id: int,
        step_id: str,
        step_status: RunPlanStepStatus = RunPlanStepStatus.BLOCKED,
        project_id: int | None = None,
        reason: str | None = None,
        actor: str | None = None,
        result_json: dict[str, Any] | None = None,
        error: str | None = None,
        commit: bool = True,
    ) -> Envelope[RunPlanOut]:
        plan = self._fetch_plan(run_plan_id)
        self._require_plan_project(plan, project_id)
        step = self._fetch_step(run_plan_id, step_id)
        if result_json is not None:
            _ensure_no_secrets(result_json, label="run plan recovery result")
        if error is not None:
            _ensure_no_secrets({"error": error}, label="run plan recovery error")
        if reason is not None:
            _ensure_no_secrets({"reason": reason}, label="run plan recovery reason")
        RunPlanLifecycleReconciler(self._s).recover_plan(
            plan,
            step=step,
            step_status=step_status,
            result_json=_jsonable(result_json) if result_json is not None else None,
            error=error,
            reason=reason,
            actor=actor,
        )
        if commit:
            self._s.commit()
            self._s.refresh(plan)
        else:
            self._s.flush()
        return Envelope(data=self._plan_out(plan), run_id=plan.run_id, project_id=plan.project_id)

    def reopen(
        self,
        *,
        run_plan_id: int,
        project_id: int | None = None,
        step_id: str | None = None,
        reason: str,
        actor: str | None = None,
        commit: bool = True,
    ) -> Envelope[RunPlanReopenOut]:
        plan = self._fetch_plan(run_plan_id)
        self._require_plan_project(plan, project_id)
        reason = str(reason or "").strip()
        if not reason:
            raise ValidationError("reason is required to reopen a run plan")
        _ensure_no_secrets({"reason": reason}, label="run plan reopen reason")
        if actor is not None:
            _ensure_no_secrets({"actor": actor}, label="run plan reopen actor")
        if plan.status == RunPlanStatus.DRAFT:
            raise ConflictError(
                "draft run plans have not started; use runPlan.start",
                data={
                    "run_plan_id": run_plan_id,
                    "status": plan.status.value,
                    "next_operations": ["runPlan.start"],
                },
            )

        steps = self._step_rows(plan.id)
        if not steps:
            raise ConflictError(
                "run plan has no steps to reopen",
                data={"run_plan_id": run_plan_id},
            )
        step = self._reopen_step(steps, step_id=step_id)
        run = self._linked_run_for_reopen(plan)
        now = _utcnow()

        previous_plan_status = plan.status.value
        previous_run_status = run.status.value
        plan.status = RunPlanStatus.STARTED
        plan.completed_at = None
        plan.updated_at = now
        plan.metadata_json = self._reopen_plan_metadata(
            plan.metadata_json,
            reason=reason,
            actor=actor,
            reopened_at=now,
            previous_plan_status=previous_plan_status,
            previous_run_status=previous_run_status,
            step_id=step.step_id,
        )
        self._s.add(plan)

        if not run.client_session_id:
            run.client_session_id = _token()
        run.status = RunStatus.RUNNING
        run.error = None
        run.ended_at = None
        run.heartbeat_at = now
        run.last_step = step.step_id
        run.last_step_at = now
        run.metadata_json = self._reopen_run_metadata(
            run.metadata_json,
            plan=plan,
            reason=reason,
            actor=actor,
            reopened_at=now,
        )
        self._s.add(run)

        reset_step_ids: list[str] = []
        for item in steps:
            if item.position < step.position:
                continue
            reset_step_ids.append(item.step_id)
            item.status = RunPlanStepStatus.PENDING
            item.result_json = None
            item.error = None
            item.claimed_by = None
            item.claimed_at = None
            item.started_at = None
            item.completed_at = None
            item.updated_at = now
            self._s.add(item)

        TrackerRepository(self._s).mirror_run_plan_reopened(
            plan=plan,
            steps=steps,
            actor=actor,
            reason=reason,
        )
        if commit:
            self._s.commit()
            self._s.refresh(plan)
            self._s.refresh(run)
        else:
            self._s.flush()
        return Envelope(
            data=RunPlanReopenOut(
                plan=self._plan_out(plan),
                run=RunOut.model_validate(run),
                run_token=run.client_session_id or "",
                run_id=_required_id(run.id),
                reopened_step_id=step.step_id,
                reset_step_ids=reset_step_ids,
                next_operations=[
                    "runPlan.claimStep",
                    "tracker.get",
                    "tracker.createTicket",
                ],
            ),
            run_id=run.id,
            project_id=plan.project_id,
        )

    def list(
        self,
        *,
        project_id: int | None = None,
        run_id: int | None = None,
        status: RunPlanStatus | None = None,
        template_key: str | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[RunPlanSummaryOut]:
        stmt = select(RunPlan)
        if project_id is not None:
            stmt = stmt.where(RunPlan.project_id == project_id)
        if run_id is not None:
            stmt = stmt.where(RunPlan.run_id == run_id)
        if status is not None:
            stmt = stmt.where(RunPlan.status == status)
        if template_key is not None:
            stmt = stmt.where(RunPlan.template_key == template_key)
        return cursor_paginate(
            self._s,
            stmt,
            id_col=RunPlan.id,
            limit=limit,
            after_id=after_id,
            converter=RunPlanSummaryOut.model_validate,
        )

    def update(
        self,
        *,
        run_plan_id: int,
        metadata_json: dict[str, Any] | None = None,
        approval_key: str | None = None,
        approval_status: ApprovalRequestStatus | None = None,
        decided_by: str | None = None,
        decision_json: dict[str, Any] | None = None,
        project_id: int | None = None,
    ) -> Envelope[RunPlanOut]:
        row = self._fetch_plan(run_plan_id)
        self._require_plan_project(row, project_id)
        changed = False
        now = _utcnow()
        if metadata_json is not None:
            _ensure_no_secrets(metadata_json, label="run plan metadata")
            current = dict(row.metadata_json or {})
            current.update(_jsonable(metadata_json))
            row.metadata_json = current
            changed = True
        if approval_key is not None or approval_status is not None:
            if approval_key is None or approval_status is None:
                raise ValidationError("approval_key and approval_status must be passed together")
            approval = self._fetch_approval(run_plan_id, approval_key)
            if approval.status != approval_status:
                validate_transition(
                    approval.status,
                    approval_status,
                    APPROVAL_REQUEST_STATUS_TRANSITIONS,
                    label="approval_request.status",
                )
                approval.status = approval_status
                approval.decided_at = now
                approval.decided_by = decided_by
            if decision_json is not None:
                _ensure_no_secrets(decision_json, label="approval decision")
                approval.decision_json = _jsonable(decision_json)
            approval.updated_at = now
            self._s.add(approval)
            changed = True
        if changed:
            row.updated_at = now
            self._s.add(row)
            self._s.commit()
            self._s.refresh(row)
        return Envelope(data=self._plan_out(row), run_id=row.run_id, project_id=row.project_id)

    def abort(
        self,
        *,
        run_plan_id: int,
        project_id: int | None = None,
        reason: str | None = None,
        actor: str | None = None,
        commit: bool = True,
    ) -> Envelope[RunPlanOut]:
        plan = self._fetch_plan(run_plan_id)
        self._require_plan_project(plan, project_id)
        if plan.status in {RunPlanStatus.COMPLETED, RunPlanStatus.FAILED}:
            raise ConflictError(
                "terminal run plans cannot be aborted",
                data={"run_plan_id": run_plan_id, "status": plan.status.value},
            )
        if reason is not None:
            _ensure_no_secrets({"reason": reason}, label="run plan abort reason")
        RunPlanLifecycleReconciler(self._s).abort_plan(
            plan,
            reason=reason,
            actor=actor,
            linked_run_error="run-plan-aborted",
        )
        if commit:
            self._s.commit()
            self._s.refresh(plan)
        else:
            self._s.flush()
        return Envelope(data=self._plan_out(plan), run_id=plan.run_id, project_id=plan.project_id)

    def claim_step(
        self,
        *,
        run_plan_id: int,
        run_id: int | None = None,
        step_id: str | None = None,
        claimed_by: str | None = None,
        project_id: int | None = None,
    ) -> Envelope[RunPlanStepOut]:
        plan = self._fetch_plan(run_plan_id)
        self._require_plan_project(plan, project_id)
        self._require_bound_run(plan, run_id)
        if plan.status != RunPlanStatus.STARTED:
            raise ConflictError(
                "run plan must be started before claiming steps",
                data={"run_plan_id": run_plan_id, "status": plan.status.value},
            )
        running_step = self._s.exec(
            select(RunPlanStep).where(
                RunPlanStep.run_plan_id == run_plan_id,
                RunPlanStep.status == RunPlanStepStatus.RUNNING,
            )
        ).first()
        if running_step is not None:
            raise ConflictError(
                "run plan already has a running step; record it before claiming another",
                data={
                    "run_plan_id": run_plan_id,
                    "step_id": running_step.step_id,
                    "status": running_step.status.value,
                },
            )
        step = self._next_step(run_plan_id, step_id)
        pending_approvals = self._pending_approvals(
            run_plan_id,
            set(step.approval_refs_json or []),
            step_pk=step.id,
        )
        if pending_approvals:
            raise ConflictError(
                "step requires approval before it can be claimed",
                data={
                    "run_plan_id": run_plan_id,
                    "step_id": step.step_id,
                    "approval_keys": [item.approval_key for item in pending_approvals],
                },
            )
        validate_transition(
            step.status,
            RunPlanStepStatus.RUNNING,
            RUN_PLAN_STEP_STATUS_TRANSITIONS,
            label="run_plan_step.status",
        )
        now = _utcnow()
        step.status = RunPlanStepStatus.RUNNING
        step.claimed_by = claimed_by
        step.claimed_at = now
        step.started_at = now
        step.updated_at = now
        plan.updated_at = now
        self._s.add(step)
        self._s.add(plan)
        self._touch_linked_run(plan, step_id=step.step_id, now=now)
        TrackerRepository(self._s).mirror_run_plan_step_claimed(plan=plan, step=step)
        self._s.commit()
        self._s.refresh(step)
        return Envelope(
            data=self._step_out(step, plan),
            run_id=plan.run_id,
            project_id=plan.project_id,
        )

    def record_step(
        self,
        *,
        run_plan_id: int,
        run_id: int | None = None,
        step_id: str,
        status: RunPlanStepStatus,
        result_json: dict[str, Any] | None = None,
        error: str | None = None,
        project_id: int | None = None,
    ) -> Envelope[RunPlanOut]:
        plan = self._fetch_plan(run_plan_id)
        self._require_plan_project(plan, project_id)
        self._require_bound_run(plan, run_id)
        if plan.status != RunPlanStatus.STARTED:
            raise ConflictError(
                "run plan must be started before recording steps",
                data={"run_plan_id": run_plan_id, "status": plan.status.value},
            )
        step = self._fetch_step(run_plan_id, step_id)
        if step.status != RunPlanStepStatus.RUNNING:
            raise ConflictError(
                "run plan step must be running before recording a terminal result",
                data={"step_id": step_id, "status": step.status.value},
            )
        if status not in {
            RunPlanStepStatus.SUCCESS,
            RunPlanStepStatus.FAILED,
            RunPlanStepStatus.SKIPPED,
            RunPlanStepStatus.BLOCKED,
        }:
            raise ValidationError(
                "record_step status must be success, failed, skipped, or blocked",
                data={"status": status.value},
            )
        if status == RunPlanStepStatus.SUCCESS:
            self._ensure_dependencies_complete(run_plan_id, step)
        validate_transition(
            step.status,
            status,
            RUN_PLAN_STEP_STATUS_TRANSITIONS,
            label="run_plan_step.status",
        )
        now = _utcnow()
        if result_json is not None:
            _ensure_no_secrets(result_json, label="run plan step result")
        if error is not None:
            _ensure_no_secrets({"error": error}, label="run plan step error")
        step.status = status
        step.result_json = _jsonable(result_json) if result_json is not None else None
        step.error = error
        step.completed_at = None if status == RunPlanStepStatus.BLOCKED else now
        step.updated_at = now
        plan.updated_at = now
        self._s.add(step)
        self._s.add(plan)
        self._touch_linked_run(plan, step_id=step.step_id, now=now)
        TrackerRepository(self._s).mirror_run_plan_step_recorded(plan=plan, step=step)
        self._sync_terminal_status(plan, status, now=now)
        self._s.commit()
        self._s.refresh(plan)
        return Envelope(data=self._plan_out(plan), run_id=plan.run_id, project_id=plan.project_id)

    def _sync_terminal_status(
        self,
        plan: RunPlan,
        latest_step_status: RunPlanStepStatus,
        *,
        now: datetime,
    ) -> None:
        if latest_step_status == RunPlanStepStatus.FAILED:
            RunPlanLifecycleReconciler(self._s).complete_plan(
                plan,
                run_status=RunStatus.FAILED,
                error="run-plan-step-failed",
                now=now,
            )
            return
        steps = self._step_rows(plan.id)
        if steps and all(step.status in TERMINAL_STEP_STATUSES for step in steps):
            RunPlanLifecycleReconciler(self._s).complete_plan(
                plan,
                run_status=RunStatus.SUCCESS,
                now=now,
            )

    def _touch_linked_run(
        self,
        plan: RunPlan,
        *,
        step_id: str | None,
        now: datetime,
    ) -> None:
        if plan.run_id is None:
            return
        run = self._s.get(Run, plan.run_id)
        if run is None or run.status != RunStatus.RUNNING:
            return
        run.heartbeat_at = now
        run.last_step = step_id
        run.last_step_at = now
        self._s.add(run)

    def _linked_run_for_reopen(self, plan: RunPlan) -> Run:
        if plan.run_id is None:
            raise ConflictError(
                "run plan has no linked audit run to reopen",
                data={
                    "run_plan_id": plan.id,
                    "status": plan.status.value,
                    "next_operations": ["runPlan.get", "runPlan.start"],
                },
            )
        run = self._s.get(Run, plan.run_id)
        if run is None:
            raise ConflictError(
                "linked audit run was not found",
                data={
                    "run_plan_id": plan.id,
                    "run_id": plan.run_id,
                    "next_operations": ["runPlan.get", "runPlan.checkConsistency"],
                },
            )
        return run

    def _reopen_step(
        self,
        steps: builtins.list[RunPlanStep],
        *,
        step_id: str | None,
    ) -> RunPlanStep:
        if step_id is not None:
            for step in steps:
                if step.step_id == step_id:
                    return step
            raise NotFoundError(
                "run plan step not found",
                data={"step_id": step_id, "available_step_ids": [step.step_id for step in steps]},
            )
        failed_or_blocked = next(
            (
                step
                for step in sorted(steps, key=lambda item: item.position)
                if step.status in {RunPlanStepStatus.FAILED, RunPlanStepStatus.BLOCKED}
            ),
            None,
        )
        if failed_or_blocked is not None:
            return failed_or_blocked
        delivery = next((step for step in steps if step.step_id == "deliver-tickets"), None)
        if delivery is not None:
            return delivery
        return max(steps, key=lambda item: item.position)

    def _reopen_plan_metadata(
        self,
        metadata_json: dict[str, Any] | None,
        *,
        reason: str,
        actor: str | None,
        reopened_at: datetime,
        previous_plan_status: str,
        previous_run_status: str,
        step_id: str,
    ) -> dict[str, Any]:
        current = _jsonable(metadata_json or {})
        history = current.get("reopen_history")
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "reopened_at": reopened_at.isoformat(),
                "reason": reason,
                "actor": actor,
                "previous_plan_status": previous_plan_status,
                "previous_run_status": previous_run_status,
                "step_id": step_id,
            }
        )
        current.update(
            {
                "reopen_history": history[-25:],
                "last_reopened_at": reopened_at.isoformat(),
                "last_reopen_reason": reason,
                "last_reopened_by": actor,
                "last_reopened_step_id": step_id,
            }
        )
        return current

    def _reopen_run_metadata(
        self,
        metadata_json: dict[str, Any] | None,
        *,
        plan: RunPlan,
        reason: str,
        actor: str | None,
        reopened_at: datetime,
    ) -> dict[str, Any]:
        current = _jsonable(metadata_json or {})
        current.update(
            {
                "stackos_type": "run-plan",
                "run_plan_id": plan.id,
                "reopened_at": reopened_at.isoformat(),
                "reopen_reason": reason,
                "reopened_by": actor,
            }
        )
        return current

    def _load_template(
        self,
        *,
        key: str,
        project_id: int | None,
        repo_root: str | None,
        plugin_slug: str | None,
        source: str | None,
    ) -> LoadedWorkflowTemplate:
        return WorkflowTemplateLoader(self._s).describe_template(
            key=key,
            project_id=project_id,
            repo_root=repo_root,
            plugin_slug=plugin_slug,
            source=source,
        )

    def _require_project(self, project_id: int) -> None:
        if self._s.get(Project, project_id) is None:
            raise NotFoundError(f"project {project_id} not found")

    @staticmethod
    def _require_plan_project(plan: RunPlan, project_id: int | None) -> None:
        if project_id is None or plan.project_id == project_id:
            return
        raise NotFoundError(
            f"run plan {plan.id} not found in project {project_id}",
            data={"project_id": project_id, "run_plan_id": plan.id},
        )

    def _require_context_snapshot(self, project_id: int, snapshot_id: int) -> None:
        row = self._s.get(ContextSnapshot, snapshot_id)
        if row is None or row.project_id != project_id:
            raise NotFoundError(
                f"context snapshot {snapshot_id} not found in project {project_id}",
                data={"project_id": project_id, "context_snapshot_id": snapshot_id},
            )

    def _require_bound_run(self, plan: RunPlan, run_id: int | None) -> None:
        if plan.run_id is None:
            raise ConflictError(
                "run plan is not linked to a run",
                data={"run_plan_id": plan.id, "status": plan.status.value},
            )
        if run_id != plan.run_id:
            raise ConflictError(
                "run token is not bound to this run plan",
                data={"run_plan_id": plan.id, "run_id": run_id, "expected_run_id": plan.run_id},
            )
        run = self._s.get(Run, plan.run_id)
        if run is None:
            raise ConflictError(
                "linked run for this run plan is missing",
                data={
                    "run_plan_id": plan.id,
                    "run_id": plan.run_id,
                    "next_operations": ["runPlan.checkConsistency"],
                },
            )
        if run.status != RunStatus.RUNNING:
            raise ConflictError(
                "linked run is not running; reconcile the run plan before recording "
                "workflow progress",
                data={
                    "run_plan_id": plan.id,
                    "run_id": run.id,
                    "run_status": run.status.value,
                    "run_error": run.error,
                    "next_operations": ["runPlan.checkConsistency"],
                },
            )

    def _fetch_plan(self, run_plan_id: int) -> RunPlan:
        row = self._s.get(RunPlan, run_plan_id)
        if row is None:
            raise NotFoundError(f"run plan {run_plan_id} not found")
        return row

    def _fetch_step(self, run_plan_id: int, step_id: str) -> RunPlanStep:
        row = self._s.exec(
            select(RunPlanStep).where(
                RunPlanStep.run_plan_id == run_plan_id,
                RunPlanStep.step_id == step_id,
            )
        ).first()
        if row is None:
            raise NotFoundError(
                f"run plan step {step_id!r} not found",
                data={"run_plan_id": run_plan_id, "step_id": step_id},
            )
        return row

    def _fetch_approval(self, run_plan_id: int, approval_key: str) -> ApprovalRequest:
        row = self._s.exec(
            select(ApprovalRequest).where(
                ApprovalRequest.run_plan_id == run_plan_id,
                ApprovalRequest.approval_key == approval_key,
            )
        ).first()
        if row is None:
            raise NotFoundError(
                f"approval {approval_key!r} not found",
                data={"run_plan_id": run_plan_id, "approval_key": approval_key},
            )
        return row

    def _next_step(self, run_plan_id: int, step_id: str | None) -> RunPlanStep:
        if step_id is not None:
            step = self._fetch_step(run_plan_id, step_id)
            self._ensure_dependencies_complete(run_plan_id, step)
            return step
        rows = self._step_rows(run_plan_id)
        completed = completed_dependency_step_ids(rows)
        for step in rows:
            if step.status != RunPlanStepStatus.PENDING:
                continue
            if all(dep in completed for dep in transitive_step_dependencies(rows, step)):
                return step
        raise NotFoundError(
            "no claimable run plan step found",
            data={"run_plan_id": run_plan_id},
        )

    def _ensure_dependencies_complete(self, run_plan_id: int, step: RunPlanStep) -> None:
        rows = self._step_rows(run_plan_id)
        missing = incomplete_step_dependencies(rows, step)
        if missing:
            raise ConflictError(
                "run plan step dependencies are not complete",
                data={"run_plan_id": run_plan_id, "step_id": step.step_id, "missing": missing},
            )

    def _pending_approvals(
        self,
        run_plan_id: int,
        approval_refs: set[str],
        *,
        step_pk: int | None,
    ) -> builtins.list[ApprovalRequest]:
        if not approval_refs and step_pk is None:
            return []
        rows = self._s.exec(
            select(ApprovalRequest).where(
                col(ApprovalRequest.run_plan_id) == run_plan_id,
                col(ApprovalRequest.status) != ApprovalRequestStatus.APPROVED,
            )
        ).all()
        return [
            row
            for row in rows
            if row.approval_key in approval_refs or row.run_plan_step_id == step_pk
        ]

    def _step_rows(self, run_plan_id: int | None) -> builtins.list[RunPlanStep]:
        if run_plan_id is None:
            return []
        return list(
            self._s.exec(
                select(RunPlanStep)
                .where(col(RunPlanStep.run_plan_id) == run_plan_id)
                .order_by(col(RunPlanStep.position).asc())
            ).all()
        )

    def _approval_rows(self, run_plan_id: int | None) -> builtins.list[ApprovalRequest]:
        if run_plan_id is None:
            return []
        return list(
            self._s.exec(
                select(ApprovalRequest)
                .where(col(ApprovalRequest.run_plan_id) == run_plan_id)
                .order_by(col(ApprovalRequest.id).asc())
            ).all()
        )

    def _step_out(self, step: RunPlanStep, plan: RunPlan | None = None) -> RunPlanStepOut:
        data = RunPlanStepOut.model_validate(step)
        if plan is None:
            plan = self._s.get(RunPlan, step.run_plan_id)
        if plan is not None:
            data.allowed_tools = sorted(
                allowed_tools_for_run_plan_step(
                    plan.grant_snapshot_json,
                    step_id=step.step_id,
                )
            )
            data.action_execution_guidance = _step_action_execution_guidance(
                step=step,
                plan=plan,
                allowed_tools=data.allowed_tools,
            )
        return data

    def _plan_out(self, row: RunPlan) -> RunPlanOut:
        data = RunPlanOut.model_validate(row)
        data.steps = [self._step_out(step, row) for step in self._step_rows(row.id)]
        data.approval_requests = [
            ApprovalRequestOut.model_validate(item) for item in self._approval_rows(row.id)
        ]
        data.consistency_issues = RunPlanLifecycleReconciler(self._s).consistency_issues(row)
        return data


def _step_action_execution_guidance(
    *,
    step: RunPlanStep,
    plan: RunPlan,
    allowed_tools: list[str],
) -> dict[str, Any]:
    action_refs = [ref for ref in step.action_refs_json or [] if isinstance(ref, str) and ref]
    if not action_refs:
        return {}
    next_calls: list[dict[str, Any]] = []
    for action_ref in action_refs[:8]:
        next_calls.extend(
            [
                {
                    "operation": "action.describe",
                    "arguments": {
                        "project_id": plan.project_id,
                        "action_ref": action_ref,
                    },
                },
                {
                    "operation": "executionContext.discover",
                    "arguments": {
                        "project_id": plan.project_id,
                        "action_ref": action_ref,
                        "run_plan_id": plan.id,
                        "run_id": plan.run_id,
                    },
                },
            ]
        )
    guidance: dict[str, Any] = {
        "action_refs": action_refs,
        "preferred_path": [
            "action.describe",
            "executionContext.discover_or_resolve",
            "action.validate",
            "action.execute",
        ],
        "context_rule": (
            "Use context_ref when the step repeats credential, provider scope, output policy, "
            "request budget, or artifact namespace across action calls."
        ),
        "payload_boundary": {
            "input_json": "endpoint payload for this one action call",
            "provider_context_json": "reusable provider/account scope, not endpoint payload",
            "context_ref": "preferred carrier for repeated credential/provider context",
        },
        "next_calls": next_calls,
    }
    if "action.execute" not in set(allowed_tools):
        guidance["warning"] = "step declares action_refs but does not grant action.execute"
    return guidance


__all__ = [
    "RUN_PLAN_CONTROLLER_SKILL",
    "ApprovalRequestOut",
    "RunPlanConsistencyIssueOut",
    "RunPlanConsistencyOut",
    "RunPlanOut",
    "RunPlanReopenOut",
    "RunPlanRepository",
    "RunPlanStartOut",
    "RunPlanStepOut",
    "RunPlanSummaryOut",
]
