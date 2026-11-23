"""Console admission and setup context for the canonical task-delivery runner."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from apscheduler.jobstores.base import JobLookupError
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as ModelValidationError
from sqlalchemy import func
from sqlmodel import col, select

from stackos.artifacts import redact_secret_text
from stackos.db.models import (
    AgentRequestStatus,
    RunPlan,
    TrackerReceiptProjection,
    TrackerTask,
    TrackerTicket,
)
from stackos.host_mcp import service
from stackos.host_mcp.agent_execution import managed_launch_lock
from stackos.repositories.base import ConflictError, ValidationError


class MainAgentPreferences(BaseModel):
    """Launch choices only; instructions and specialist contracts have other owners."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    host_key: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)
    model_reasoning_effort: str | None = Field(default=None, min_length=1)
    provider: str | None = Field(default=None, min_length=1)
    profile: str | None = Field(default=None, min_length=1)


INSTRUCTIONS = """You are the project's setup/update coordinator.
Consume the supplied workflowTemplate.authoringGuide project_setup_method and project guidance.
Use only the selected project and requested scope. Preserve compatible local settings.
Work through this existing core.project-setup workflow; do not start a business workflow.
Return the declared setup_report in outputs. Never call projectSetup.report or mutate your own
workflow lifecycle. The daemon validates your unchanged outcome and records setup evidence.
Preparation returns partial with prepared workflow evidence, an adoption_proposal when needed,
and explicit workflow_checks selecting only missing bounded checks by actual configured role.
For infrastructure_only, preparation returns success after the selected preparation is verified;
there is no adoption or native activation phase.
For a question return blocked with setup_report.status needs_input and a managed_ui question.
Consume recorded answers on resume. Preserve completed evidence and partial edits.
Verification returns success only after current readiness is proved. Record executed checks in
setup_activation_checks. Reuse completed_check_references supplied by the owner. Leave only your
own predeclared executed check references for the owner to bind after this invocation retires.
Never invent native consumption, completed references, source identities or local-admin authority.
"""


def resolve_preferences(
    session,
    project_id,
    preferences=None,
    *,
    inherit_saved=True,
    validate_selection=True,
    fallback_host_key=None,
):
    from stackos.repositories.tracker import TrackerRepository

    defaults = TrackerRepository(session).delivery_defaults(project_id=project_id)
    saved = MainAgentPreferences.model_validate(
        (defaults.get("main_agent") or {}) if inherit_saved else {}
    ).model_dump(exclude_none=True)
    # Omission inherits; explicit null clears a saved selection for this launch.
    explicit = MainAgentPreferences.model_validate(preferences or {}).model_dump(exclude_unset=True)
    saved_host = saved.get("host_key") or defaults.get("host_key") or fallback_host_key
    if explicit.get("host_key") and explicit["host_key"] != saved_host:
        saved = {}
    if "model" in explicit and explicit["model"] != saved.get("model"):
        saved.pop("model_reasoning_effort", None)
    selected = {key: value for key, value in {**saved, **explicit}.items() if value is not None}
    host_key = selected.get("host_key") or defaults.get("host_key") or fallback_host_key
    data_dir = None
    if session is not None:
        runtime_settings = session.info.get("operation_services", {}).get("settings")
        if runtime_settings is not None:
            data_dir = runtime_settings.data_dir
        else:
            # Managed/background sessions have no request services. Settings.db_path
            # binds the daemon's account directory to the database's parent.
            database = session.get_bind().engine.url.database
            if database and database != ":memory:":
                data_dir = Path(database).parent
    if host_key is None:
        # Catalog order is the existing preference order; never substitute an explicit choice.
        host_key = next(
            (
                d.policy.host_key
                for d in service._definitions()
                if d.run_agent
                and d.policy.agent_execution.managed_execution
                and "workspace-write" in d.policy.agent_execution.sandboxes
                and service.inspect_host(d.policy.host_key, data_dir=data_dir).available
            ),
            None,
        )
    if host_key is None:
        raise ValidationError("Install a supported native agent host before starting setup")
    selected["host_key"] = host_key
    # Console preview shares selection/inheritance with launch, while retaining
    # invalid or unavailable saved choices so the operator can repair them.
    if not validate_selection:
        return selected
    host = service.inspect_host(host_key, data_dir=data_dir)
    if host.agent_execution.get("probe_status") == "stale":
        raise ValidationError(
            "Native host or account changed during inspection; refresh before setup"
        )
    error = service.agent_selection_error(
        sandbox="workspace-write", **selected, catalog=host.agent_execution or None
    )
    if error:
        raise ValidationError(error)
    if not host.available or host.agent_execution.get("runnable") in {
        "unavailable",
        "unknown",
        "unsupported",
    }:
        raise ValidationError(f"Selected native host needs setup: {host.message}")
    return selected


def _save(session, row, attempt, *, _commit=True):
    row.metadata_json = {
        **(row.metadata_json or {}),
        "project_setup": attempt.model_dump(mode="json"),
    }
    row.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(row)
    if _commit:
        session.commit()
    else:
        session.flush()


def _load(session, project_id, request_id):
    from stackos.repositories.agent_requests import AgentRequestRepository
    from stackos.repositories.project_setup import SetupAttempt

    row = AgentRequestRepository(session)._fetch(project_id=project_id, request_id=request_id)
    try:
        attempt = SetupAttempt.model_validate((row.metadata_json or {}).get("project_setup"))
    except ModelValidationError as exc:
        raise ConflictError(
            "managed setup request has incompatible metadata; inspect before execution",
            data={
                "project_id": project_id,
                "request_id": request_id,
                "next_action": "inspect_request",
            },
        ) from exc
    if attempt.project_id != project_id or attempt.request_id != request_id or not attempt.managed:
        raise ConflictError("request is not this project's managed setup")
    return row, attempt


def setup_directories(session):
    """Only genuine legacy native ownership remains outside normal task ownership."""
    from stackos.repositories.project_setup import ProjectSetupRepository

    return [item.project_dir for item in ProjectSetupRepository(session).managed_admissions()]


def work_projection(session, attempt):
    """Read actual workflow state; domain readiness is a separate retained fact."""
    from stackos.project_setup_workflow import read_admission

    link = attempt.workflow_admission
    if link is None:
        return None
    read_admission(
        session,
        project_id=attempt.project_id,
        request_id=attempt.request_id,
        run_plan_id=link.run_plan_id,
        task_id=link.task_id,
    )
    plan = session.get(RunPlan, link.run_plan_id)
    task = session.get(TrackerTask, link.task_id)
    state = (task.metadata_json or {}).get("delivery", {})
    busy = state.get("status") in {"queued", "running"} or state.get("slot_reserved", False)
    return {
        "run_plan_id": plan.id,
        "run_id": plan.run_id,
        "task_key": task.key,
        "run_status": plan.status.value,
        "delivery_status": state.get("status"),
        "busy": busy,
        "reason": state.get("reason"),
        "can_continue": not busy and plan.status.value != "completed",
        "can_cancel": plan.status.value not in {"completed", "aborted"},
        "can_answer": not busy
        and bool(attempt.question and attempt.question.answer_state == "pending"),
    }


def attempt_projection(session, attempt):
    """Combine retained domain facts with current work without rewriting either."""
    work = work_projection(session, attempt)
    if not work or (work["run_status"] != "aborted" and work["delivery_status"] != "stopped"):
        return attempt, work
    status = attempt.status
    if work["run_status"] == "aborted":
        status = "cancelled"
    elif status in {"queued", "running"}:
        status = "interrupted"
    possible_edits = attempt.partial_changes_possible
    if not possible_edits:
        # This existing index contains only canonical receipt inputs, including
        # retained invocations. Read one scalar for this exact task, never native
        # result bodies or project-wide setup history. A queued launch is no proof
        # that an invocation actually began.
        invocation = func.json_extract(
            TrackerReceiptProjection.receipt_json, "$.execution.invocation_id"
        )
        latest_invocation = session.exec(
            select(invocation)
            .join(TrackerTicket, TrackerReceiptProjection.ticket_id == TrackerTicket.id)
            .where(
                TrackerTicket.project_id == attempt.project_id,
                TrackerTicket.task_id == attempt.workflow_admission.task_id,
                func.length(invocation) > 0,
            )
            .order_by(
                col(TrackerReceiptProjection.launch_at).desc(),
                col(TrackerReceiptProjection.ticket_id).desc(),
            )
            .limit(1)
        ).first()
        # An accepted report already supplies its effect state; an older
        # invocation must not turn a harmless current question into a warning.
        possible_edits = bool(
            latest_invocation and latest_invocation not in attempt.applied_workflow_results
        )
    return attempt.model_copy(
        update={"status": status, "partial_changes_possible": possible_edits}
    ), work


def _started(session, attempt, *, attached=False):
    from stackos.repositories.project_setup import SetupStartOut

    attempt, work = attempt_projection(session, attempt)
    work = work or {}
    return SetupStartOut(
        attempt=attempt,
        attached=attached,
        **{key: work.get(key) for key in ("run_plan_id", "run_id", "task_key")},
        next_action="observe_existing_attempt" if attached else "view_work",
    )


def start_managed(
    *,
    project_id,
    session_factory,
    scheduler,
    mode,
    workflow_keys,
    preferences=None,
    requested_scope="ready_to_use",
):
    from stackos.project_setup_workflow import create_admission
    from stackos.repositories.managed_upgrades import ManagedUpgradeRepository
    from stackos.repositories.project_setup import ProjectSetupRepository
    from stackos.repositories.projects import ProjectRepository
    from stackos.repositories.run_plans import RunPlanRepository
    from stackos.task_delivery import resolve_delivery_settings, start_delivery
    from stackos.workflows.run_plan_schema import run_plan_from_template

    with managed_launch_lock, session_factory() as session:
        ManagedUpgradeRepository(session).require_unfenced()
        repo = ProjectSetupRepository(session)
        current = repo._current_attempt(project_id)
        if current and current.workflow_admission:
            work = work_projection(session, current)
            if work["run_status"] in {"draft", "started"} or work["busy"]:
                return _started(session, current, attached=True)
        if (
            current
            and not current.workflow_admission
            and (
                current.status in {"queued", "running", "needs_input"}
                or (current.managed and current.managed.slot_reserved)
            )
        ):
            raise ConflictError("The previous setup owner must retire before tracked setup")
        settings = resolve_preferences(session, project_id, preferences)
        project = ProjectRepository(session).get(project_id)
        if not project.project_dir:
            raise ValidationError("Select or repair the project's bound folder before setup")
        plans = RunPlanRepository(session)
        title = "Set up project workflows" if mode == "setup" else "Update project workflows"
        concrete = None
        if requested_scope == "infrastructure_only":
            loaded = plans._load_template(
                key="core.project-setup",
                project_id=project_id,
                repo_root=None,
                plugin_slug=None,
                source=None,
            )
            concrete = run_plan_from_template(loaded, title=title).model_dump(mode="json")
            concrete["steps"] = [step for step in concrete["steps"] if step["id"] == "prepare"]
            grants = concrete["grant_snapshot_json"]
            grants["mcp_tool_grants"] = [
                grant
                for grant in grants["mcp_tool_grants"]
                if grant.get("step_id") in {None, "prepare"}
            ]
        plan = plans.create(
            project_id=project_id,
            template_key="core.project-setup",
            title=title,
            run_plan_json=concrete,
            _commit=False,
        ).data
        task = session.exec(
            select(TrackerTask).where(
                TrackerTask.project_id == project_id,
                TrackerTask.key == f"workflow-{plan.id}",
            )
        ).one()
        attempt = create_admission(
            session,
            project_id=project_id,
            run_plan_id=plan.id,
            task_id=task.id,
            mode=mode,
            workflow_keys=workflow_keys,
            preferences=settings,
            requested_scope=requested_scope,
        )
        plan_row = session.get(RunPlan, plan.id)
        plan_row.metadata_json = {
            **(plan_row.metadata_json or {}),
            "project_setup_request_id": attempt.request_id,
        }
        session.add(plan_row)
        resolved = resolve_delivery_settings(
            session,
            project_id=project_id,
            project_dir=project.project_dir,
            host_key=settings["host_key"],
            agents={
                "main_agent": {
                    **settings,
                    "name": "project-setup",
                    "instructions": INSTRUCTIONS,
                }
            },
            role_keys={"main_agent"},
            _setup_bootstrap=(attempt.request_id, plan.id, task.id),
        )
        resolved["project_id"] = project_id
        from stackos.task_delivery import _admission_id, _require_delivery_directory

        _require_delivery_directory(
            session, task, Path(project.project_dir), _admission_id(scheduler)
        )
        started = plans.start(plan.id, project_id=project_id).data
        plans.claim_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            project_id=project_id,
            step_id="prepare",
        )
        start_delivery(
            session=session,
            session_factory=session_factory,
            scheduler=scheduler,
            project_id=project_id,
            task_key=task.key,
            project_dir=project.project_dir,
            run_plan_id=plan.id,
            step_id="prepare",
            controller_run_id=started.run_id,
            agent_assignments={
                f"workflow-{plan.id}-{step.step_id}": "main_agent" for step in plan.steps
            },
            _resolved_settings=resolved,
        )
        return _started(session, attempt)


def assignment_context(session, project_id, task, ticket, phase, completed_tickets):
    """Expose current setup facts, never additional execution authority."""
    from stackos.project_setup_workflow import load_admission
    from stackos.repositories.project_setup import SETUP_GUIDANCE_REF, ProjectSetupRepository
    from stackos.repositories.tracker import TrackerRepository

    plan_id = ticket.run_plan_id or (task.source_json or {}).get("run_plan_id")
    if plan_id is None:
        return None
    plan = session.get(RunPlan, plan_id)
    request_id = (plan.metadata_json or {}).get("project_setup_request_id") if plan else None
    if not request_id:
        return None
    _, current = load_admission(
        session,
        project_id=project_id,
        request_id=request_id,
        run_plan_id=plan.id,
        task_id=task.id,
    )
    state = ProjectSetupRepository(session).get(project_id)
    return {
        "request_id": request_id,
        "expected_revision": current.revision,
        "mode": current.mode,
        "requested_scope": current.requested_scope,
        "workflow_keys": current.workflow_keys,
        "guidance_ref": SETUP_GUIDANCE_REF,
        "answers": [answer.model_dump(mode="json") for answer in current.answers],
        "workflow_checks": {
            key: [check.model_dump(mode="json") for check in checks]
            for key, checks in current.workflow_checks.items()
        },
        "workflows": [
            item.model_dump(
                mode="json",
                include={
                    "workflow_key",
                    "status",
                    "revision",
                    "requested_scope",
                    "missing_activation_claims",
                    "result_ref",
                    "detail_ref",
                },
            )
            for item in state.workflows
            if item.workflow_key in current.workflow_keys
        ],
        "setup_detail_ref": state.detail_ref,
        "adoption_readback": TrackerRepository(session).delivery_defaults(project_id=project_id),
        "delivery_defaults_digest": state.delivery_defaults_digest,
        "completed_check_references": _completed_checks(session, current, completed_tickets),
    }


def _completed_checks(session, current, completed_tickets):
    """Derive references from accepted child receipts, without another proof store."""
    from stackos.repositories.project_setup import (
        ProjectSetupRepository,
        SetupActivationCheck,
        SetupProofReference,
        VerifiedSetup,
        _digest,
    )

    repo = ProjectSetupRepository(session)
    checks = []
    for ticket in completed_tickets:
        if (
            ticket.get("parent_ticket_key")
            != f"workflow-{current.workflow_admission.run_plan_id}-activate-verify"
        ):
            continue
        automatic = (ticket.get("completion_evidence_json") or {}).get("automatic_delivery", {})
        for phase in ("delivery", "review"):
            receipt = automatic.get(phase, {})
            if receipt.get("validated_outcome") != "success":
                continue
            native = receipt.get("result", {})
            outputs = json.loads(native["outputs"])
            observations = outputs.get("setup_activation_checks", [])
            reference = SetupProofReference(
                kind="task_phase",
                task_id=ticket["task_id"],
                ticket_id=ticket["id"],
                attempt=automatic["attempt"],
                phase=phase,
                invocation_id=receipt["execution"]["invocation_id"],
                outcome_digest=_digest(native),
            )
            outcome = repo._task_phase_outcome(current.project_id, reference, "passed")
            agent = outcome["sessions"][phase]["agent_snapshot"]
            for workflow, requests in current.workflow_checks.items():
                for request in requests:
                    if request.role != receipt.get("selected_role") or not any(
                        isinstance(item, dict)
                        and item.get("status") == "passed"
                        and item.get("environment_ref") == request.environment_ref
                        and item.get("evidence_ref") == request.evidence_ref
                        and isinstance(item.get("claims"), list)
                        and set(request.claims) <= set(item["claims"])
                        for item in observations
                    ):
                        continue
                    stored = repo._row(current.project_id, workflow)
                    proof = VerifiedSetup.model_validate(stored.latest_result_json["verification"])
                    roles = repo.activation_role_claims(current.project_id, proof.sources)
                    check = SetupActivationCheck(
                        claims=request.claims,
                        origin="executed",
                        status="passed",
                        basis=repo.activation_basis(
                            current.project_id, proof, environment_ref=request.environment_ref
                        ),
                        reference=reference,
                        evidence_ref=request.evidence_ref,
                        execution=repo.execution_binding(current.project_id, request.role, agent)
                        if any(claim in roles for claim in request.claims)
                        else None,
                    )
                    checks.append(
                        {"workflow_key": workflow, "check": check.model_dump(mode="json")}
                    )
    return checks


def bind_verification(session, project_id, task, attempt):
    """Resolve newly adopted settings once, before the pending verifier can launch."""
    from stackos.repositories.tracker import TrackerRepository
    from stackos.repositories.tracker.workflow import workflow_step_ticket_key
    from stackos.task_delivery import resolve_delivery_settings

    task = session.get(TrackerTask, task.id)
    state = deepcopy((task.metadata_json or {})["delivery"])
    role_keys = {check.role for checks in attempt.workflow_checks.values() for check in checks}
    role = next(iter(role_keys), "main_agent") if len(role_keys) <= 1 else "main_agent"
    key = workflow_step_ticket_key(attempt.workflow_admission.run_plan_id, "activate-verify")
    repo = TrackerRepository(session)
    verifier = repo.brief(project_id=project_id, ticket_key=key).ticket
    if (verifier.completion_evidence_json or {}).get("automatic_delivery"):
        raise ConflictError("Setup verification already has frozen native evidence")
    selected = role_keys - {"main_agent"}
    if len(role_keys) > 1:
        selected.add("review_agent")
    if selected:
        resolved = resolve_delivery_settings(
            session,
            project_id=project_id,
            project_dir=state["project_dir"],
            role_keys=selected,
            mcp_config_ref=state.get("mcp_config_ref"),
        )
        state["agents"] = {**state["agents"], **resolved["agents"]}
        state["mcp_route_identities"] = {
            **state.get("mcp_route_identities", {}),
            **resolved.get("mcp_route_identities", {}),
        }
    state["agent_assignments"] = {**state.get("agent_assignments", {}), key: role}
    if len(role_keys) > 1:
        from stackos.task_delivery import _validate_ticket_execution

        # The agent's explicit bounded check requests are the work definition.
        # Ordinary children use the existing independent review and activation edge.
        for index, selected_role in enumerate(sorted(role_keys - {"review_agent"})):
            worker, reviewer = state["agents"][selected_role], state["agents"]["review_agent"]
            if (worker["host_key"], worker["name"]) == (reviewer["host_key"], reviewer["name"]):
                raise ValidationError("Setup check delivery and review must be independent")
            _validate_ticket_execution(state["agents"], selected_role, {})
            child_key = f"{task.key}-check-{selected_role}"
            requests = [
                {"workflow_key": workflow, **check.model_dump(mode="json")}
                for workflow, checks in attempt.workflow_checks.items()
                for check in checks
                if check.role == selected_role or (index == 0 and check.role == "review_agent")
            ]
            repo.create_ticket(
                project_id=project_id,
                task_key=task.key,
                key=child_key,
                title=f"Verify selected {selected_role} setup checks",
                goal="Execute only the selected bounded readiness checks under the actual role.",
                parent_ticket_key=key,
                dependency_keys=[key],
                run_plan_id=attempt.workflow_admission.run_plan_id,
                run_plan_step_id=verifier.run_plan_step_id,
                definition_of_done_json=[
                    "Return setup_activation_checks for the assigned role with exact claims, "
                    "status, environment_ref and evidence_ref.",
                    "The distinct reviewer performs only its selected missing checks "
                    "and reviews the delivered proof.",
                ],
                context_json={"setup_checks": requests},
                commit=False,
            )
            state["agent_assignments"][child_key] = selected_role
    task.metadata_json = {**(task.metadata_json or {}), "delivery": state}
    session.add(task)
    session.flush()


def continue_managed(
    project_id,
    request_id,
    *,
    expected_revision,
    session_factory,
    scheduler,
    question_id=None,
    question_revision=None,
    answer=None,
):
    from stackos.project_setup_workflow import load_admission
    from stackos.repositories.agent_requests import AgentRequestRepository
    from stackos.repositories.project_setup import SetupAnswer, SetupAttempt
    from stackos.repositories.run_plans import RunPlanRepository
    from stackos.task_delivery import start_delivery

    with managed_launch_lock, session_factory() as session:
        row = AgentRequestRepository(session)._fetch(project_id=project_id, request_id=request_id)
        current = SetupAttempt.model_validate((row.metadata_json or {}).get("project_setup"))
        link = current.workflow_admission
        if link is None:
            raise ConflictError("Historical native setup cannot be resumed by tracked delivery")
        row, current = load_admission(
            session,
            project_id=project_id,
            request_id=request_id,
            run_plan_id=link.run_plan_id,
            task_id=link.task_id,
        )
        work = work_projection(session, current)
        if current.revision != expected_revision or not work["can_continue"]:
            raise ConflictError("Setup work changed or has not retired; refresh before continuing")
        if current.question and current.question.answer_state == "pending":
            if (
                (question_id, question_revision)
                != (
                    current.question.question_id,
                    current.question.revision,
                )
                or not answer
                or not answer.strip()
            ):
                raise ConflictError("Answer the current setup question before continuing")
            current.answers.append(
                SetupAnswer(
                    question_id=question_id,
                    question_revision=question_revision,
                    question_request_id=request_id,
                    answer=answer,
                    answered_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
            current.question.answer_state = "answered"
        elif answer is not None:
            raise ConflictError("There is no pending setup question to answer")
        current.status = "queued"
        current.revision += 1
        _save(session, row, current, _commit=False)
        plans = RunPlanRepository(session)
        plan = plans.get(link.run_plan_id, project_id=project_id)
        step = next((item for item in plan.steps if item.status.value != "success"), None)
        if step is None:
            raise ConflictError("Setup workflow has no remaining work")
        plans.reopen(
            run_plan_id=plan.id,
            project_id=project_id,
            step_id=step.step_id,
            reason="Continue remaining project setup work",
            actor="project-setup",
        )
        plans.claim_step(
            run_plan_id=plan.id,
            run_id=plan.run_id,
            project_id=project_id,
            step_id=step.step_id,
        )
        start_delivery(
            session=session,
            session_factory=session_factory,
            scheduler=scheduler,
            project_id=project_id,
            task_key=work["task_key"],
            project_dir=link.project_dir,
            run_plan_id=plan.id,
            step_id=step.step_id,
            controller_run_id=plan.run_id,
        )
        return _started(session, current, attached=True)


def cancel_setup(
    project_id,
    request_id,
    *,
    expected_revision,
    external_work_stopped,
    session_factory,
    scheduler,
):
    from stackos.project_setup_workflow import load_admission, read_admission
    from stackos.repositories.agent_requests import AgentRequestRepository
    from stackos.repositories.project_setup import ProjectSetupRepository, SetupAttempt
    from stackos.task_delivery import abort_run_plan

    with session_factory() as session:
        row = AgentRequestRepository(session)._fetch(project_id=project_id, request_id=request_id)
        current = SetupAttempt.model_validate((row.metadata_json or {}).get("project_setup"))
        if current.entry_kind == "existing_agent":
            return ProjectSetupRepository(session).cancel_existing(
                project_id,
                request_id=request_id,
                expected_revision=expected_revision,
                external_work_stopped=external_work_stopped,
            )
        link = current.workflow_admission
        if link:
            row, current = load_admission(
                session,
                project_id=project_id,
                request_id=request_id,
                run_plan_id=link.run_plan_id,
                task_id=link.task_id,
            )
            if expected_revision is not None and current.revision != expected_revision:
                raise ConflictError("Setup changed before cancellation; refresh")
            # The canonical abort releases its lock before waiting for native
            # retirement. Do not retain this adapter's transaction across that wait.
            session.commit()
            abort_run_plan(
                session,
                run_plan_id=link.run_plan_id,
                project_id=project_id,
                reason="Project setup cancelled",
                actor="project-setup",
                scheduler=scheduler,
            )
            with managed_launch_lock:
                row, current = read_admission(
                    session,
                    project_id=project_id,
                    request_id=request_id,
                    run_plan_id=link.run_plan_id,
                    task_id=link.task_id,
                )
                return attempt_projection(session, current)[0]
    return _cancel_legacy(
        project_id, request_id, session_factory=session_factory, scheduler=scheduler
    )


def _cancel_legacy(project_id, request_id, *, session_factory, scheduler):
    from stackos.task_delivery import _admission_id, _request_delivery_drain

    with managed_launch_lock, session_factory() as session:
        row, attempt = _load(session, project_id, request_id)
        if (
            attempt.status in {"success", "partial", "failed", "cancelled"}
            and not attempt.managed.slot_reserved
            and service.active_request_execution(project_id, request_id, include_handoff=True)
            is None
        ):
            return attempt
        attempt.managed.cancel_requested = True
        calls = service.cancel_request_invocations(project_id, request_id)
        native = (row.metadata_json or {}).get("native_execution")
        # Queued callbacks are fenced before admission. Running/restarted invocations
        # retain their slot until their owning callback observes actual retirement.
        if (not attempt.managed.callback_started and not native) or (
            native and native.get("finished_at") and not native.get("stop_error") and not calls
        ):
            attempt.status = "cancelled"
            attempt.managed.slot_reserved = False
            row.status = AgentRequestStatus.FAILED
            row.claim_token_hash = None
            row.completed_at = datetime.now(UTC).replace(tzinfo=None)
            getattr(scheduler, "project_setup_claims", {}).pop(request_id, None)
        else:
            attempt.managed.diagnostic = (
                "Cancellation requested; waiting for native retirement"
                if attempt.managed.admission_id == _admission_id(scheduler)
                else "Earlier daemon invocation needs native retirement reconciliation"
            )
        _save(session, row, attempt)
    for call in calls:
        service.retire_invocation(call)
    # Scheduler availability must not prevent native cancellation. Keep scheduler
    # I/O outside the launch lock; persisted cancellation already fences callbacks.
    try:
        scheduler.remove_job(attempt.managed.job_ref, jobstore="memory")
    except JobLookupError:
        pass
    except Exception as exc:
        with managed_launch_lock, session_factory() as session:
            row, current = _load(session, project_id, request_id)
            current.managed.diagnostic = (
                "Cancellation fenced the invocation; queue removal needs repair: "
                + redact_secret_text(str(exc))[:1500]
            )
            _save(session, row, current)
    _request_delivery_drain(session_factory, scheduler)
    with session_factory() as session:
        return _load(session, project_id, request_id)[1]
