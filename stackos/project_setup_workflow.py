"""Setup domain effects composed by the ordinary workflow delivery owner.

These helpers neither launch agents nor commit. The caller stages native evidence,
applies these effects and records the canonical step in one transaction.
"""

from __future__ import annotations

import json
import secrets
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Literal

from sqlmodel import Session

from stackos.db.models import (
    AgentRequest,
    AgentRequestAttentionStatus,
    AgentRequestStatus,
    RunPlan,
    RunPlanStep,
    TrackerTask,
    TrackerTicket,
)
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.project_setup import (
    SETUP_NAMESPACE,
    ProjectSetupRepository,
    SetupActivationCheck,
    SetupAdoptionProposal,
    SetupAttempt,
    SetupProofReference,
    SetupReport,
    SetupScope,
    SetupWorkflowAdmission,
    VerifiedSetup,
    _digest,
)
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.tracker import TrackerRepository


def create_admission(
    session: Session,
    *,
    project_id: int,
    run_plan_id: int,
    task_id: int,
    mode: Literal["setup", "update"],
    workflow_keys: list[str],
    preferences: dict[str, Any],
    requested_scope: SetupScope = "ready_to_use",
) -> SetupAttempt:
    """Called only by authenticated console admission, never from agent metadata."""
    ProjectRepository(session).require_available(project_id, reserve=True)
    project = ProjectRepository(session).get(project_id)
    repo = ProjectSetupRepository(session)
    current = repo._current_attempt(project_id)
    if current and current.workflow_admission:
        admission = current.workflow_admission
        if (admission.run_plan_id, admission.task_id) == (run_plan_id, task_id):
            return load_admission(
                session,
                project_id=project_id,
                request_id=current.request_id,
                run_plan_id=run_plan_id,
                task_id=task_id,
            )[1]
        read_admission(
            session,
            project_id=project_id,
            request_id=current.request_id,
            run_plan_id=admission.run_plan_id,
            task_id=admission.task_id,
        )
        previous_plan = session.get(RunPlan, admission.run_plan_id)
        previous_task = session.get(TrackerTask, admission.task_id)
        assert previous_plan is not None and previous_task is not None
        if previous_plan.status.value in {"draft", "started"} or (
            (previous_task.metadata_json or {}).get("delivery", {}).get("slot_reserved")
        ):
            raise ConflictError("the previous setup workflow still owns execution")
    elif current and (
        current.status in {"queued", "running", "needs_input"}
        or (current.managed is not None and current.managed.slot_reserved)
    ):
        raise ConflictError("the previous setup owner must retire before tracked admission")
    plan, task = session.get(RunPlan, run_plan_id), session.get(TrackerTask, task_id)
    if (
        plan is None
        or plan.project_id != project_id
        or task is None
        or task.project_id != project_id
        or task.key != f"workflow-{plan.id}"
        or (task.source_json or {}).get("run_plan_id") != plan.id
        or project.primary_workspace_binding_id is None
        or not project.project_dir
    ):
        raise ConflictError("setup admission requires the exact project workflow and task")
    if (
        len(workflow_keys) > 100
        or len(workflow_keys) != len(set(workflow_keys))
        or any(not key.strip() or len(key) > 200 for key in workflow_keys)
        or not preferences.get("host_key")
    ):
        raise ValidationError(
            "setup admission needs bounded workflows and resolved Main preferences"
        )
    now = datetime.now(UTC).replace(tzinfo=None)
    row = AgentRequest(
        project_id=project_id,
        run_plan_id=run_plan_id,
        request_key=f"project-setup:{secrets.token_hex(16)}",
        title="Project setup" if mode == "setup" else "Project workflow update",
        source_kind=SETUP_NAMESPACE,
        status=AgentRequestStatus.RUN_CREATED,
        attention_status=AgentRequestAttentionStatus.READ,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    assert row.id is not None
    current = SetupAttempt(
        project_id=project_id,
        attempt_ref=f"agent-request:{row.id}",
        root_request_id=row.id,
        request_id=row.id,
        mode=mode,
        requested_scope=requested_scope,
        entry_kind="managed_ui",
        session_id=None,
        runtime=preferences["host_key"],
        workflow_keys=workflow_keys,
        observed_receipt_revisions={
            item.workflow_key: item.revision for item in repo.get(project_id).workflows
        },
        workflow_admission=SetupWorkflowAdmission(
            run_plan_id=run_plan_id,
            task_id=task_id,
            binding_id=project.primary_workspace_binding_id,
            project_dir=project.project_dir,
            # Full instructions and native snapshots belong to task delivery.
            preferences={
                key: value
                for key, value in preferences.items()
                if key
                in {
                    "host_key",
                    "model",
                    "model_reasoning_effort",
                    "provider",
                    "profile",
                    "native_settings",
                    "timeout_seconds",
                }
            },
        ),
    )
    for key in workflow_keys:
        current.observed_receipt_revisions.setdefault(key, 0)
    _save(session, row, current)
    return current


def read_admission(
    session: Session,
    *,
    project_id: int,
    request_id: int,
    run_plan_id: int,
    task_id: int,
) -> tuple[AgentRequest, SetupAttempt]:
    """Validate immutable linkage, including historical completed admissions."""
    row = session.get(AgentRequest, request_id, populate_existing=True)
    plan = session.get(RunPlan, run_plan_id, populate_existing=True)
    task = session.get(TrackerTask, task_id, populate_existing=True)
    if row is None or row.project_id != project_id or row.source_kind != SETUP_NAMESPACE:
        raise ConflictError("setup admission is not owned by this project")
    current = SetupAttempt.model_validate((row.metadata_json or {}).get(SETUP_NAMESPACE))
    admission = current.workflow_admission
    if (
        admission is None
        or current.entry_kind != "managed_ui"
        or current.project_id != project_id
        or current.request_id != request_id
        or row.run_plan_id != run_plan_id
        or admission.run_plan_id != run_plan_id
        or admission.task_id != task_id
        or plan is None
        or plan.project_id != project_id
        or task is None
        or task.project_id != project_id
        or task.key != f"workflow-{run_plan_id}"
        or (task.source_json or {}).get("run_plan_id") != run_plan_id
        or row.status != AgentRequestStatus.RUN_CREATED
    ):
        raise ConflictError("setup admission has a stale or foreign workflow binding")
    return row, current


def load_admission(
    session: Session,
    *,
    project_id: int,
    request_id: int,
    run_plan_id: int,
    task_id: int,
) -> tuple[AgentRequest, SetupAttempt]:
    """Mutation additionally requires current setup and current workspace ownership."""
    session.flush()
    row, current = read_admission(
        session,
        project_id=project_id,
        request_id=request_id,
        run_plan_id=run_plan_id,
        task_id=task_id,
    )
    ProjectRepository(session).require_available(project_id, reserve=True)
    project = ProjectRepository(session).get(project_id)
    latest = ProjectSetupRepository(session)._current_attempt(project_id)
    admission = current.workflow_admission
    assert admission is not None
    if (
        latest is None
        or latest.request_id != request_id
        or project.primary_workspace_binding_id != admission.binding_id
        or project.project_dir != admission.project_dir
    ):
        raise ConflictError("setup admission is no longer the current project owner")
    return row, current


def _save(session: Session, row: AgentRequest, current: SetupAttempt) -> None:
    if len(current.model_dump_json().encode()) > 2_000_000:
        raise ValidationError("setup attempt exceeds 2 MB; retain detailed evidence by reference")
    row.metadata_json = {
        **(row.metadata_json or {}),
        SETUP_NAMESPACE: current.model_dump(mode="json"),
    }
    row.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(row)
    session.flush()


def adopt_proposal(session: Session, project_id: int, proposal: SetupAdoptionProposal) -> str:
    """Preserve the existing settings owner and its expected-source/default checks."""
    from stackos.operations.tracker.schemas import TaskDeliveryAgentOverride
    from stackos.repositories.tracker.setup import _delivery_host, _delivery_role_defaults

    ProjectSetupRepository(session)._validate_adoption_proposal(project_id, proposal)
    tracker = TrackerRepository(session)
    defaults = tracker.delivery_defaults(project_id=project_id)
    sources, inline = {}, {}
    for role, selection in proposal.roles.items():
        before = _delivery_role_defaults(defaults, role)
        if before or defaults.get("host_key") is not None:
            saved_host = TaskDeliveryAgentOverride(host_key=before.get("host_key")).host_key
            if _delivery_host(defaults, role_host=saved_host) != selection.host_key:
                raise ConflictError("Adoption cannot change the intended native host")
        if selection.source_ref:
            sources[role] = {
                "source_ref": selection.source_ref.model_dump(),
                "expected_digest": selection.expected_digest,
            }
        else:
            inline[role] = {
                "host_key": selection.host_key,
                "name": selection.name,
                "instructions": selection.instructions,
            }
    arguments: dict[str, Any] = dict(
        project_id=project_id,
        project_dir=proposal.project_dir,
        sources=sources,
        inline_roles=inline,
        _execution_requirements={
            role: selection.execution_requirements.model_dump()
            for role, selection in proposal.roles.items()
            if selection.execution_requirements is not None
        },
    )
    preview = tracker.adopt_delivery_sources(**arguments, preview=True)
    if any(
        preview["adoption"]["agents"][role]["host_key"] != selection.host_key
        for role, selection in proposal.roles.items()
    ):
        raise ConflictError("Adoption cannot change the intended native host")
    tracker.adopt_delivery_sources(**arguments, preview=False, _commit=False)
    return _digest(tracker.delivery_defaults(project_id=project_id))


def accept_result(
    session: Session,
    *,
    project_id: int,
    request_id: int,
    run_plan_id: int,
    task_id: int,
    ticket_id: int,
    attempt: int,
    phase: Literal["delivery", "review"],
    invocation_id: str,
    handoff_id: str,
    expected_revision: int,
) -> SetupAttempt:
    """Stage only the domain effects of the exact canonical native outcome.

    The result comes from the persisted receipt, not a caller-supplied report.
    A successful verifier may leave its own check references pending in that
    report. Only previously selected slots receive server-derived references,
    before the ordinary strict report validation runs.
    """
    from stackos.host_mcp.service import active_execution
    from stackos.task_delivery import _delivery_active

    row, current = load_admission(
        session,
        project_id=project_id,
        request_id=request_id,
        run_plan_id=run_plan_id,
        task_id=task_id,
    )
    repo = ProjectSetupRepository(session)
    task = session.get(TrackerTask, task_id)
    ticket = session.get(TrackerTicket, ticket_id)
    if (
        task is None
        or ticket is None
        or ticket.task_id != task_id
        or ticket.project_id != project_id
    ):
        raise ConflictError("setup outcome has a foreign task or ticket")
    automatic = (ticket.completion_evidence_json or {}).get("automatic_delivery", {})
    receipt = automatic.get(phase, {})
    native = receipt.get("result") or {}
    reference = SetupProofReference(
        kind="task_phase",
        task_id=task_id,
        ticket_id=ticket_id,
        attempt=attempt,
        phase=phase,
        invocation_id=invocation_id,
        outcome_digest=_digest(native),
    )
    outcome = repo._task_phase_outcome(
        project_id, reference, "passed" if native.get("status") == "success" else "failed"
    )
    if automatic.get("attempt") != attempt or outcome["receipt"] != receipt:
        raise ConflictError("setup effects require the current invocation receipt")
    identity = _digest(reference.model_dump(mode="json"))
    applied = current.applied_workflow_results.get(invocation_id)
    if applied:
        if applied != identity:
            raise ConflictError("setup invocation was already accepted with a different outcome")
        return current
    step = session.get(RunPlanStep, ticket.run_plan_step_id)
    plan = session.get(RunPlan, run_plan_id)
    retained = outcome["sessions"].get(phase, {})
    selected_role = receipt.get("selected_role")
    agent = retained.get("agent_snapshot")
    if (
        plan is None
        or plan.status.value != "started"
        or ticket.run_plan_id != run_plan_id
        or step is None
        or step.run_plan_id != run_plan_id
        or step.status.value != "running"
        or step.step_id not in {"prepare", "activate-verify"}
        or not _delivery_active(task, handoff_id)
        or active_execution(project_id, task.key, ticket.key) is not None
        or current.revision != expected_revision
        or current.status in {"cancelled", "interrupted", "failed"}
        or (current.question is not None and current.question.answer_state == "pending")
        or receipt.get("validated_outcome") not in {"success", "blocked"}
        or native.get("status") != receipt.get("validated_outcome")
        or receipt.get("exit_code") != 0
        or any(receipt.get(key) for key in ("error", "validation_error", "cancelled", "superseded"))
        or not selected_role
        or not isinstance(agent, dict)
        or retained.get("thread_id") != receipt.get("thread_id")
        or agent.get("name") != receipt.get("agent")
        or agent.get("host_key") != receipt.get("launch", {}).get("host_key")
    ):
        raise ConflictError("setup outcome is not the current retired workflow invocation")
    try:
        outputs = json.loads(native["outputs"])
        report_data = deepcopy(outputs["setup_report"])
        if not isinstance(report_data, dict):
            raise ValueError("setup_report must be an object")
    except (KeyError, TypeError, ValueError) as error:
        raise ValidationError("setup outcome requires its structured setup_report") from error
    if step.step_id == "activate-verify" and native["status"] == "success":
        _complete_checks(session, repo, current, reference, receipt, agent, report_data, outputs)
    report = SetupReport.model_validate(report_data)
    if native["status"] == "blocked":
        if report.status != "needs_input" or report.adoption_proposal or report.workflow_checks:
            raise ValidationError(
                "blocked setup may retain its question, not adoption or new checks"
            )
    elif report.status not in {"success", "partial"}:
        raise ValidationError("successful setup work requires a preparation or completion report")
    _select_workflows(current, report)
    if step.step_id == "prepare":
        if current.adopted_delivery_digest is not None:
            raise ConflictError("accepted setup preparation cannot be adopted again")
    else:
        if report.workflow_checks and report.workflow_checks != current.workflow_checks:
            raise ConflictError("verification cannot change the accepted check selection")
        if report.adoption_proposal:
            if report.adoption_proposal != current.adoption_proposal:
                raise ConflictError("verification cannot replace the adopted proposal")
            report.adoption_proposal = None
        if native["status"] == "success" and report.status != "success":
            raise ValidationError("setup verification must finish the selected readiness contract")
    if report.adoption_proposal and current.requested_scope != "ready_to_use":
        raise ConflictError("infrastructure-only setup cannot adopt execution defaults")
    current = repo._apply_report(
        project_id, row, current, report, report_digest=_digest(report.model_dump(mode="json"))
    )
    if step.step_id == "prepare" and native["status"] == "success":
        current.workflow_checks = report.workflow_checks
        if report.adoption_proposal:
            current.adopted_delivery_digest = adopt_proposal(
                session, project_id, report.adoption_proposal
            )
        else:
            current.adopted_delivery_digest = _digest(
                TrackerRepository(session).delivery_defaults(project_id=project_id)
            )
        _validate_check_selection(repo, current, report)
    current.applied_workflow_results[invocation_id] = identity
    _save(session, row, current)
    return current


def _select_workflows(current: SetupAttempt, report: SetupReport) -> None:
    selected = report.workflow_keys
    if not current.workflow_keys:
        current.workflow_keys = (
            selected
            if selected is not None
            else list(
                dict.fromkeys(
                    [item.workflow_key for item in report.workflow_results]
                    + (report.question.workflow_keys if report.question else [])
                )
            )
        )
        for key in current.workflow_keys:
            current.observed_receipt_revisions.setdefault(key, 0)
    elif selected is not None and set(selected) != set(current.workflow_keys):
        raise ConflictError("managed workflow selection is already fixed for this attempt")


def _validate_check_selection(
    repo: ProjectSetupRepository,
    current: SetupAttempt,
    report: SetupReport,
) -> None:
    by_workflow = {item.workflow_key: item.result.verification for item in report.workflow_results}
    if not set(report.workflow_checks) <= set(current.workflow_keys):
        raise ValidationError("selected checks must belong to the admitted workflows")
    for key in current.workflow_keys:
        requests = report.workflow_checks.get(key, [])
        proof = by_workflow.get(key)
        if proof is None:
            stored = repo._row(current.project_id, key)
            raw_proof = (stored.latest_result_json or {}).get("verification") if stored else None
            proof = VerifiedSetup.model_validate(raw_proof) if raw_proof else None
        if proof is None:
            raise ValidationError("preparation requires each selected workflow's evidence")
        if current.requested_scope == "infrastructure_only":
            if requests:
                raise ValidationError("infrastructure-only setup cannot select native activation")
            continue
        if proof.activation_proof is None:
            raise ValidationError(
                "preparation requires explicit covered and missing activation claims"
            )
        roles = repo.activation_role_claims(current.project_id, proof.sources)
        gaps = set(proof.activation_proof.gaps())
        selected: set[str] = set()
        for request in requests:
            if not set(request.claims) <= gaps or any(
                roles[claim] != request.role for claim in request.claims if claim in roles
            ):
                raise ValidationError(
                    "selected check does not cover the declared missing role claims"
                )
            selected.update(request.claims)
        if (gaps & roles.keys()) - selected:
            raise ValidationError("missing specialist claims need their bounded check selection")


def _complete_checks(
    session: Session,
    repo: ProjectSetupRepository,
    current: SetupAttempt,
    reference: SetupProofReference,
    receipt: dict[str, Any],
    agent: dict[str, Any],
    report: dict[str, Any],
    outputs: dict[str, Any],
) -> None:
    project_id = current.project_id
    if current.adopted_delivery_digest != _digest(
        TrackerRepository(session).delivery_defaults(project_id=project_id)
    ):
        raise ConflictError("saved setup defaults changed before verification")
    observations = outputs.get("setup_activation_checks", [])
    if not isinstance(observations, list) or len(observations) > 100:
        raise ValidationError("setup activation observations must be a bounded list")
    for item in report.get("workflow_results", []):
        requests = current.workflow_checks.get(item.get("workflow_key"), [])
        matching = [request for request in requests if request.role == receipt.get("selected_role")]
        if not matching:
            continue
        result = item.get("result", {})
        proof_data = result.get("verification")
        if not isinstance(proof_data, dict) or not isinstance(
            proof_data.get("activation_proof"), dict
        ):
            raise ValidationError("verification must retain its selected workflow evidence")
        proof_data = deepcopy(proof_data)
        requested_status = proof_data["activation_proof"]["status"]
        proof_data["activation_proof"]["status"] = "pending"
        proof = VerifiedSetup.model_validate(proof_data)
        activation = proof.activation_proof
        assert activation is not None
        stored = repo._row(project_id, item["workflow_key"])
        if (
            stored is None
            or stored.revision != current.observed_receipt_revisions[item["workflow_key"]]
        ):
            raise ConflictError("preparation evidence changed before verification")
        prepared = VerifiedSetup.model_validate(
            (stored.latest_result_json or {}).get("verification")
        )
        roles = repo.activation_role_claims(project_id, proof.sources)
        covered: set[str] = set()
        for request in matching:
            if not any(
                isinstance(observation, dict)
                and isinstance(observation.get("claims"), list)
                and all(isinstance(claim, str) for claim in observation["claims"])
                and set(request.claims) <= set(observation.get("claims", []))
                and observation.get("status") == "passed"
                and observation.get("environment_ref") == request.environment_ref
                and observation.get("evidence_ref") == request.evidence_ref
                for observation in observations
            ):
                raise ValidationError("selected check lacks its native bounded-check observation")
            basis = repo.activation_basis(
                project_id, proof, environment_ref=request.environment_ref
            )
            if basis != repo.activation_basis(
                project_id, prepared, environment_ref=request.environment_ref
            ):
                raise ConflictError("verification changed the prepared source or adaptation basis")
            activation.checks.append(
                SetupActivationCheck(
                    claims=request.claims,
                    origin="executed",
                    status="passed",
                    basis=basis,
                    reference=reference,
                    evidence_ref=request.evidence_ref,
                    execution=repo.execution_binding(project_id, request.role, agent)
                    if any(claim in roles for claim in request.claims)
                    else None,
                )
            )
            covered.update(request.claims)
        activation.missing_claims = [
            claim for claim in activation.missing_claims if claim not in covered
        ]
        # The agent chose the terminal claim. Strict validation below still
        # rejects every missing claim, stale basis, wrong role or failed outcome.
        activation.status = requested_status
        result["verification"] = proof.model_dump(mode="json")
