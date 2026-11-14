"""Request-native setup execution, admitted by task_delivery's shared capacity owner."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path

from apscheduler.jobstores.base import JobLookupError
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as ModelValidationError
from sqlmodel import select

from stackos.artifacts import redact_secret_text
from stackos.db.models import AgentRequest, AgentRequestAttentionStatus, AgentRequestStatus
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


RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "report_json": {"type": "string"},
        "verification_status": {"type": "string", "enum": ["passed", "failed", "blocked"]},
    },
    "required": ["report_json"],
    "additionalProperties": False,
}
INSTRUCTIONS = """You are the project's setup/update coordinator, within the supplied project scope.
Read project instructions and resolve the supplied workflowTemplate.authoringGuide reference.
Consume its complete project_setup_method and authored setup/update prompt. Both managed and
existing-session setup use that same method. Inspect the project, choose applicable workflows
from the operator goal, preserve compatible choices and all applicable source obligations.
Follow requested_scope: ready_to_use includes canonical adoption and bounded native verification;
infrastructure_only ends at preparation. Create no business run or tracker task for setup proof.
No rollout to other projects or unrelated business actions.
Use projectSetup.get for current receipt revisions. You may report progress and completed workflow
results through projectSetup.report using your workspace.startSession session_id, the supplied
request_id, private claim_token and expected_revision. Keep the token inside this session.
Keep partial edits, failures and activation/reload limitations truthful. For a question use
response_channel managed_ui. Do not wait inside this invocation for an answer.
When the project goal is still unknown, question.workflow_keys must be [] rather than example
workflows. Before workflow work, declare the complete selected set in report.workflow_keys;
selection is then fixed for this attempt. Use current receipt revisions for checked results.
Return report_json containing a JSON-encoded SetupReport (status, progress with message and
evidence_ref, workflow_results, optional question/recovery_condition), as declared by
projectSetup.report. Report checked per-workflow results before long subsequent work so cancellation
preserves them. Final success needs all selected workflow receipts verified. Native failures are
failures; never substitute a different host, model or session. Normal native approvals still apply.
For a linked continuation consume the supplied recorded answers; do not ask them again.
Preserve retained successful outcomes. Work only on remaining workflows and revalidation of changed
sources/evidence, checking current receipts and actual local files before claiming completion.
Partial edits may exist after interruption/cancellation. No rollback is implied.
For ready_to_use preparation, return a partial report with the bounded adoption_proposal when
saved defaults need adoption or post-adoption native verification remains. The managed owner
validates and adopts it after this invocation retires, then links the verification continuation.
Never call configureDelivery or invent local-admin authority. In verification, read the supplied
canonical adoption_readback, reuse current matching proof and perform only missing bounded checks
selected by the workflow method. Record actual consumption separately from saved defaults.
Success still requires complete checked activation evidence; pending proof stays partial/failed.
Only in phase verification, return verification_status passed when the selected missing bounded
native checks actually passed. Return a partial report retaining their observations; this is not
overall setup success. The owner retires that request and supplies its exact completed result ref
to one completion continuation. In completion reuse that evidence, do not execute the same checks
again, and report success only after all current required workflow claims are covered.
"""


def resolve_preferences(
    session, project_id, preferences=None, *, inherit_saved=True, validate_selection=True
):
    from stackos.repositories.tracker import TrackerRepository

    defaults = TrackerRepository(session).delivery_defaults(project_id=project_id)
    saved = MainAgentPreferences.model_validate(
        (defaults.get("main_agent") or {}) if inherit_saved else {}
    ).model_dump(exclude_none=True)
    # Omission inherits; explicit null clears a saved selection for this launch.
    explicit = MainAgentPreferences.model_validate(preferences or {}).model_dump(exclude_unset=True)
    if explicit.get("host_key") and explicit["host_key"] != saved.get("host_key"):
        saved = {}
    if "model" in explicit and explicit["model"] != saved.get("model"):
        saved.pop("model_reasoning_effort", None)
    selected = {key: value for key, value in {**saved, **explicit}.items() if value is not None}
    host_key = selected.get("host_key") or defaults.get("host_key")
    if host_key is None:
        # Catalog order is the existing preference order; never substitute an explicit choice.
        host_key = next(
            (
                d.policy.host_key
                for d in service._definitions()
                if d.run_agent
                and "workspace-write" in d.policy.agent_execution.sandboxes
                and service.inspect_host(d.policy.host_key).available
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
    error = service.agent_selection_error(sandbox="workspace-write", **selected)
    if error:
        raise ValidationError(error)
    host = service.inspect_host(host_key)
    if not host.available:
        raise ValidationError(f"Selected native host needs setup: {host.message}")
    return selected


def _save(session, row, attempt):
    row.metadata_json = {
        **(row.metadata_json or {}),
        "project_setup": attempt.model_dump(mode="json"),
    }
    row.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(row)
    session.commit()


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
    from stackos.repositories.project_setup import ProjectSetupRepository, SetupManagedExecution
    from stackos.repositories.projects import ProjectRepository
    from stackos.task_delivery import _admission_id, drain_delivery

    with managed_launch_lock, session_factory() as session:
        ProjectRepository(session).require_available(project_id, reserve=True)
        repo = ProjectSetupRepository(session)
        state = repo.get(project_id)
        if not state.eligible or state.folder_status != "available":
            raise ValidationError(
                "Select or repair the project's bound folder before setup",
                data={"next_action": state.next_action},
            )
        current = repo._current_attempt(project_id)
        if current and (
            current.status in {"queued", "running", "needs_input"}
            or (current.managed and current.managed.slot_reserved)
        ):
            from stackos.repositories.project_setup import SetupStartOut

            session.commit()
            return SetupStartOut(
                attempt=current, attached=True, next_action="observe_existing_attempt"
            )
        settings = resolve_preferences(session, project_id, preferences)
        project = ProjectRepository(session).get(project_id)
        settings["project_dir"] = project.project_dir
        from stackos.db.models import TrackerTask
        from stackos.task_delivery import _same_directory

        tasks = session.exec(select(TrackerTask)).all()
        if any(
            (delivery_state := (task.metadata_json or {}).get("delivery", {})).get("project_dir")
            and (
                delivery_state.get("status") in {"queued", "running"}
                or delivery_state.get("slot_reserved")
            )
            and _same_directory(Path(settings["project_dir"]), Path(delivery_state["project_dir"]))
            for task in tasks
        ):
            raise ConflictError(
                "The project folder is owned by active task delivery; wait for retirement"
            )
        execution = SetupManagedExecution(
            settings=settings,
            binding_id=state.binding_id,
            admission_id=_admission_id(scheduler),
            job_ref=f"project-setup:{project_id}:{secrets.token_hex(12)}",
            adoption_authorized=requested_scope == "ready_to_use",
        )
        started = repo.start(
            project_id,
            session_id=None,
            mode=mode,
            workflow_keys=workflow_keys,
            managed=execution,
            requested_scope=requested_scope,
        )
        claims = getattr(scheduler, "project_setup_claims", None)
        if claims is None:
            scheduler.project_setup_claims = claims = {}
        claims[started.attempt.request_id] = started.claim_token
    drain_delivery(session_factory=session_factory, scheduler=scheduler)
    with session_factory() as session:
        _, started.attempt = _load(session, project_id, started.attempt.request_id)
    return started.model_copy(update={"claim_token": None})


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
    """Link one recovery turn after retirement or a confirmed pre-launch stop."""
    from stackos.db.models import TrackerTask
    from stackos.repositories.agent_requests import _hash_token
    from stackos.repositories.project_setup import (
        ProjectSetupRepository,
        SetupAnswer,
        SetupManagedExecution,
        SetupStartOut,
        VerifiedSetup,
    )
    from stackos.repositories.projects import ProjectRepository
    from stackos.task_delivery import _admission_id, _same_directory, drain_delivery

    with managed_launch_lock, session_factory() as session:
        # Acquire the same database reservation as start/report, before reading state.
        ProjectRepository(session).require_available(project_id, reserve=True)
        project = ProjectRepository(session).get(project_id)
        row, current = _load(session, project_id, request_id)
        repo = ProjectSetupRepository(session)
        latest = repo._current_attempt(project_id)
        repair = {
            "request_id": request_id,
            "attempt_ref": current.attempt_ref,
            "current_request_id": latest.request_id if latest else None,
            "successor_request_id": current.successor_request_id,
            "next_action": "inspect_setup",
            "revision": current.revision,
            "status": current.status,
        }
        if (
            latest is None
            or latest.request_id != request_id
            or current.successor_request_id is not None
            or current.revision != expected_revision
        ):
            raise ConflictError(
                "setup request/revision changed; inspect its successor", data=repair
            )
        if current.entry_kind != "managed_ui":
            raise ConflictError("answer in the original agent session", data=repair)
        asking = question_id is not None
        if asking:
            question = current.question
            if (
                current.status
                not in {"needs_input", "cancelled", "partial", "failed", "interrupted"}
                or question is None
                or question.answer_state != "pending"
                or question.question_id != question_id
                or question.revision != question_revision
                or question.response_channel != "managed_ui"
                or not isinstance(answer, str)
                or not answer.strip()
                or len(answer) > 8000
            ):
                raise ConflictError(
                    "question changed or was answered; inspect the pending question", data=repair
                )
        elif (
            current.question is not None and current.question.answer_state == "pending"
        ) or current.status not in {"partial", "failed", "interrupted", "cancelled"}:
            raise ConflictError(
                "continue only remaining failed/interrupted work; answer pending questions",
                data=repair,
            )
        native = (row.metadata_json or {}).get("native_execution") or {}
        recorded = row.metadata_json["project_setup"]
        # A queued continuation can be cancelled before its callback starts.
        # It has no new native invocation to retire. Recover only the already
        # retained exact session, proving retirement through its owned lineage.
        if (
            not native
            and current.status in {"cancelled", "failed"}
            and (not current.managed.callback_started or current.managed.preflight_failed)
            and current.managed.resume_thread_id
        ):
            child = current
            seen = {request_id}
            while child.predecessor_request_id is not None:
                parent_id = child.predecessor_request_id
                if parent_id in seen:
                    raise ConflictError("setup continuation lineage is cyclic", data=repair)
                seen.add(parent_id)
                parent_row, parent = _load(session, project_id, parent_id)
                if (
                    parent.successor_request_id != child.request_id
                    or parent.root_request_id != current.root_request_id
                    or parent.attempt_ref != current.attempt_ref
                    or parent.runtime != current.runtime
                    or parent.managed.slot_reserved
                    or service.active_request_execution(project_id, parent_id)
                ):
                    raise ConflictError("setup continuation lineage changed", data=repair)
                native = (parent_row.metadata_json or {}).get("native_execution") or {}
                if native:
                    break
                if parent.managed.callback_started and not parent.managed.preflight_failed:
                    raise ConflictError("prior native retirement is unknown", data=repair)
                child = parent
        identity = native.get("execution") or {}
        thread = identity.get("thread_id")
        # run_setup persists callback_started/possible edits before entering the
        # native executor. Only an untouched, fenced terminal callback can retry
        # without a resume identity. Missing evidence after a possible start is
        # uncertainty, never permission to substitute a new native session.
        never_started = (
            not asking
            and current.status in {"cancelled", "failed"}
            and recorded.get("managed", {}).get("callback_started") is False
            and recorded.get("partial_changes_possible") is False
            and not current.managed.callback_started
            and not current.managed.resume_thread_id
            and not current.thread_id
            and not current.partial_changes_possible
            and not current.reports
            and not current.answers
            and not native
        )
        if (
            current.managed.slot_reserved
            or service.active_request_execution(project_id, request_id)
            or (not never_started and not native.get("finished_at"))
            or native.get("stop_error")
        ):
            raise ConflictError(
                "prior native invocation has not retired; reconcile it before continuing",
                data=repair,
            )
        if not never_started and (
            not isinstance(thread, str)
            or not thread.strip()
            or thread != current.thread_id
            or identity.get("host_key") != current.runtime
            or (
                current.managed.resume_thread_id is not None
                and thread != current.managed.resume_thread_id
            )
        ):
            raise ConflictError(
                "exact supported native session identity is missing; inspect execution evidence",
                data=repair,
            )
        settings = dict(current.managed.settings)
        if (
            settings.get("host_key") != current.runtime
            or project.primary_workspace_binding_id != current.managed.binding_id
            or project.project_dir != settings.get("project_dir")
        ):
            raise ConflictError(
                "setup folder/binding or frozen host changed; repair before continuing", data=repair
            )
        state = repo.get(project_id)
        if state.folder_status != "available":
            raise ConflictError("repair the selected project folder before continuing", data=repair)
        # Validate the frozen selection; never inherit newer preferences or choose a fallback.
        resolve_preferences(
            session,
            project_id,
            {key: value for key, value in settings.items() if key != "project_dir"},
            inherit_saved=False,
        )
        if any(
            (delivery := (task.metadata_json or {}).get("delivery", {})).get("project_dir")
            and (delivery.get("status") in {"queued", "running"} or delivery.get("slot_reserved"))
            and _same_directory(Path(settings["project_dir"]), Path(delivery["project_dir"]))
            for task in session.exec(select(TrackerTask)).all()
        ):
            raise ConflictError(
                "project folder is owned by active task delivery; wait for retirement", data=repair
            )
        remaining, revalidate = [], []
        workflow_states = {item.workflow_key: item for item in state.workflows}
        for key in current.workflow_keys:
            outcome = current.outcomes.get(key)
            receipt = repo._row(project_id, key)
            if (
                outcome is None
                or outcome.status != "success"
                or (
                    current.requested_scope == "ready_to_use"
                    and outcome.activation_status != "verified"
                )
            ):
                remaining.append(key)
            elif (
                receipt is None
                or receipt.revision != outcome.revision
                or not receipt.successful_json
            ):
                revalidate.append(key)
            else:
                proof = VerifiedSetup.model_validate(receipt.successful_json["verification"])
                observed = workflow_states.get(key)
                if (
                    observed is None
                    or observed.status == "source_unavailable"
                    or repo._identity(observed.available_sources) != repo._identity(proof.sources)
                ):
                    revalidate.append(key)
                elif current.requested_scope == "ready_to_use" and proof.activation_proof:
                    try:
                        repo._validate_activation(project_id, proof)
                    except (ConflictError, ValidationError):
                        revalidate.append(key)
            current.observed_receipt_revisions[key] = receipt.revision if receipt else 0
        now = datetime.now(UTC).replace(tzinfo=None)
        token = secrets.token_urlsafe(32)
        successor = AgentRequest(
            project_id=project_id,
            request_key=f"project-setup:{secrets.token_hex(16)}",
            title=row.title,
            source_kind="project_setup",
            status=AgentRequestStatus.CLAIMED,
            attention_status=AgentRequestAttentionStatus.READ,
            claimed_by="project-setup-continuation",
            claim_token_hash=_hash_token(token),
            claimed_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(successor)
        session.flush()
        if asking:
            current.question.answer_state = "answered"
            current.answers.append(
                SetupAnswer(
                    question_id=question_id,
                    question_revision=question_revision,
                    question_request_id=request_id,
                    answer=answer,
                    answered_at=now,
                )
            )
        current.successor_request_id = successor.id
        current.revision += 1
        next_attempt = current.model_copy(
            deep=True,
            update={
                "request_id": successor.id,
                "predecessor_request_id": request_id,
                "successor_request_id": None,
                "status": "queued",
                "question": None,
                "session_id": None,
                "remaining_workflow_keys": remaining,
                "revalidate_workflow_keys": revalidate,
                # Native failure/cancellation cannot establish unchanged files.
                "partial_changes_possible": not never_started,
                "managed": SetupManagedExecution(
                    settings=settings,
                    binding_id=current.managed.binding_id,
                    admission_id=_admission_id(scheduler),
                    job_ref=f"project-setup:{project_id}:{secrets.token_hex(12)}",
                    resume_thread_id=thread,
                    adoption_authorized=current.managed.adoption_authorized,
                    phase=current.managed.phase,
                    adopted_delivery_digest=current.managed.adopted_delivery_digest,
                    verification_request_id=current.managed.verification_request_id,
                ),
            },
        )
        if any(
            len(item.model_dump_json().encode()) > 2_000_000 for item in (current, next_attempt)
        ):
            raise ValidationError(
                "setup attempt exceeds 2 MB; retain detailed evidence by reference"
            )
        row.claim_token_hash = None
        row.status = (
            AgentRequestStatus.RESOLVED
            if asking or current.managed.verification_request_id == request_id
            else AgentRequestStatus.FAILED
        )
        row.completed_at = row.updated_at = now
        row.metadata_json = {**row.metadata_json, "project_setup": current.model_dump(mode="json")}
        successor.metadata_json = {"project_setup": next_attempt.model_dump(mode="json")}
        session.add(row)
        session.add(successor)
        session.commit()
        claims = getattr(scheduler, "project_setup_claims", None)
        if claims is None:
            scheduler.project_setup_claims = claims = {}
        claims[successor.id] = token
        successor_id = successor.id
    drain_delivery(session_factory=session_factory, scheduler=scheduler)
    with session_factory() as session:
        _, next_attempt = _load(session, project_id, successor_id)
    return SetupStartOut(attempt=next_attempt, attached=False, next_action="observe_managed_setup")


def setup_directories(session):
    from stackos.repositories.project_setup import ProjectSetupRepository

    return [state.project_dir for state in ProjectSetupRepository(session).managed_admissions()]


def reserve_setup(session, scheduler):
    """Called only by the shared capacity owner while holding managed_launch_lock."""
    from stackos.repositories.project_setup import ProjectSetupRepository
    from stackos.task_delivery import _admission_id

    for state in ProjectSetupRepository(session).managed_admissions(
        admission_id=_admission_id(scheduler)
    ):
        token = getattr(scheduler, "project_setup_claims", {}).get(state.request_id)
        if token is None:
            continue  # Restart never manufactures a new claim or resumes old work.
        row, attempt = _load(session, state.project_id, state.request_id)
        managed = attempt.managed
        managed.slot_reserved = True
        _save(session, row, attempt)
        return (
            row.project_id,
            None,
            {
                "request_id": row.id,
                "job_ref": managed.job_ref,
                "claim_token": token,
            },
        )


def schedule_setup(*, project_id, state, session_factory, scheduler):
    try:
        scheduler.add_job(
            run_setup,
            trigger="date",
            id=state["job_ref"],
            jobstore="memory",
            executor="task-delivery",
            kwargs={
                "project_id": project_id,
                "request_id": state["request_id"],
                "claim_token": state["claim_token"],
                "session_factory": session_factory,
                "scheduler": scheduler,
            },
        )
    except Exception as exc:
        with managed_launch_lock, session_factory() as session:
            row, attempt = _load(session, project_id, state["request_id"])
            attempt.managed.cancel_requested = True
            attempt.managed.diagnostic = redact_secret_text(str(exc))
            if not attempt.managed.callback_started:
                attempt.status = "failed"
                attempt.managed.slot_reserved = False
                row.status = AgentRequestStatus.FAILED
                row.claim_token_hash = None
                getattr(scheduler, "project_setup_claims", {}).pop(row.id, None)
            _save(session, row, attempt)
            calls = service.cancel_request_invocations(project_id, state["request_id"])
        for call in calls:
            call.process_control.stop()
        raise


def _adopt_managed(session, project_id, request_id, claim_token):
    """Consume a bounded proposal under the original authenticated setup ownership."""
    from stackos.operations.tracker.schemas import TaskDeliveryAgentOverride
    from stackos.repositories.agent_requests import AgentRequestRepository
    from stackos.repositories.project_setup import ProjectSetupRepository, VerifiedSetup, _digest
    from stackos.repositories.projects import ProjectRepository
    from stackos.repositories.tracker import TrackerRepository
    from stackos.repositories.tracker.setup import _delivery_host, _delivery_role_defaults

    ProjectRepository(session).require_available(project_id, reserve=True)
    project = ProjectRepository(session).get(project_id)
    row, current = _load(session, project_id, request_id)
    AgentRequestRepository(session)._verify_claim(row, claim_token, _setup_owner=True)
    repo = ProjectSetupRepository(session)
    latest = repo._current_attempt(project_id)
    native = (row.metadata_json or {}).get("native_execution") or {}
    if (
        latest is None
        or latest.request_id != request_id
        or current.entry_kind != "managed_ui"
        or current.status != "partial"
        or current.requested_scope != "ready_to_use"
        or not current.managed.adoption_authorized
        or current.managed.phase != "preparation"
        or current.managed.cancel_requested
        or not current.managed.slot_reserved
        or not current.managed.callback_started
        or not native.get("finished_at")
        or native.get("stop_error")
        or service.active_request_execution(project_id, request_id)
        or project.primary_workspace_binding_id != current.managed.binding_id
        or project.project_dir != current.managed.settings.get("project_dir")
        or current.runtime != current.managed.settings.get("host_key")
    ):
        raise ConflictError("Managed adoption requires current authorized, retired preparation")
    proposal = current.adoption_proposal
    if proposal is None:
        raise ValidationError("Managed adoption needs a current bounded proposal")
    repo._validate_adoption_proposal(project_id, proposal)
    for key, outcome in current.outcomes.items():
        receipt = repo._row(project_id, key)
        if receipt is None or receipt.revision != outcome.revision:
            raise ConflictError("Preparation receipt changed before adoption")
        if outcome.status == "success":
            proof = VerifiedSetup.model_validate(receipt.successful_json["verification"])
            sources = repo.inspect_sources(
                project_id, key, selected_optional_presets=proof.selected_optional_presets
            )
            if repo._identity(sources) != repo._identity(proof.sources):
                raise ConflictError("Preparation sources changed before adoption")
    tracker = TrackerRepository(session)
    defaults = tracker.delivery_defaults(project_id=project_id)
    sources, inline = {}, {}
    for role, selection in proposal.roles.items():
        before = _delivery_role_defaults(defaults, role)
        if before or defaults.get("host_key") is not None:
            # Inspect the saved basis before proposal overrides. New roles in
            # blank defaults may choose a host; configured roles retain their
            # canonical inheritance, independently of the setup agent runtime.
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
    arguments = dict(
        project_id=project_id,
        project_dir=proposal.project_dir,
        sources=sources,
        inline_roles=inline,
    )
    preview = tracker.adopt_delivery_sources(**arguments, preview=True)
    if any(
        preview["adoption"]["agents"][role]["host_key"] != selection.host_key
        for role, selection in proposal.roles.items()
    ):
        raise ConflictError("Adoption cannot change the intended native host")
    tracker.adopt_delivery_sources(**arguments, preview=False, _commit=False)
    current.managed.phase = "verification"
    current.managed.adopted_delivery_digest = _digest(
        tracker.delivery_defaults(project_id=project_id)
    )
    # Adoption and its receipt commit together. A retry cannot repeat the write.
    _save(session, row, current)


def run_setup(*, project_id, request_id, claim_token, session_factory, scheduler):
    from stackos.host_mcp.result import validate_agent_result
    from stackos.repositories.agent_requests import AgentRequestExecution
    from stackos.repositories.project_setup import (
        SETUP_GUIDANCE_REF,
        ProjectSetupRepository,
        SetupReport,
    )
    from stackos.repositories.projects import ProjectRepository
    from stackos.task_delivery import _request_delivery_drain

    continue_verification = False
    native_entered = False

    def allowed():
        with session_factory() as session:
            _row, current = _load(session, project_id, request_id)
            project = ProjectRepository(session).get(project_id)
            return (
                not current.managed.cancel_requested
                and current.managed.slot_reserved
                and project.primary_workspace_binding_id == current.managed.binding_id
                and project.project_dir == current.managed.settings["project_dir"]
            )

    with managed_launch_lock, session_factory() as session:
        row, attempt = _load(session, project_id, request_id)
        if (
            attempt.managed.cancel_requested
            or not attempt.managed.slot_reserved
            or attempt.managed.callback_started
        ):
            return
        attempt.managed.callback_started = True
        attempt.status = "running"
        attempt.partial_changes_possible = True
        _save(session, row, attempt)
        previous_usage = None
        if attempt.predecessor_request_id is not None:
            previous, _ = _load(session, project_id, attempt.predecessor_request_id)
            previous_usage = (previous.metadata_json or {}).get("native_execution", {}).get("usage")
    try:
        if not allowed():
            raise ValidationError(
                "Project folder/binding changed before native launch; repair and retry"
            )
        context = {
            "project_id": project_id,
            "request_id": request_id,
            "attempt_ref": attempt.attempt_ref,
            "expected_revision": attempt.revision,
            "mode": attempt.mode,
            "requested_scope": attempt.requested_scope,
            "phase": attempt.managed.phase,
            "workflow_keys": attempt.workflow_keys,
            "guidance_ref": SETUP_GUIDANCE_REF,
            "project_dir": attempt.managed.settings["project_dir"],
            "observed_receipt_revisions": attempt.observed_receipt_revisions,
            "predecessor_request_id": attempt.predecessor_request_id,
            "answers": [answer.model_dump(mode="json") for answer in attempt.answers],
            "retained_outcomes": {
                key: value.model_dump(mode="json") for key, value in attempt.outcomes.items()
            },
            "remaining_workflow_keys": attempt.remaining_workflow_keys,
            "revalidate_workflow_keys": attempt.revalidate_workflow_keys,
            "partial_changes_possible": attempt.partial_changes_possible,
            "recovery_condition": attempt.recovery_condition,
        }
        if attempt.managed.phase != "preparation":
            from stackos.repositories.project_setup import _digest
            from stackos.repositories.tracker import TrackerRepository
            from stackos.task_delivery import resolve_delivery_settings

            with session_factory() as session:
                defaults = TrackerRepository(session).delivery_defaults(project_id=project_id)
                if _digest(defaults) != attempt.managed.adopted_delivery_digest:
                    raise ConflictError(
                        "Saved defaults changed; refresh setup adoption before verification"
                    )
                context["adoption_readback"] = resolve_delivery_settings(
                    session,
                    project_id=project_id,
                    project_dir=attempt.managed.settings["project_dir"],
                    role_keys=set(attempt.adoption_proposal.roles),
                )
                context["adopted_delivery_digest"] = attempt.managed.adopted_delivery_digest
                for role, selection in attempt.adoption_proposal.roles.items():
                    if selection.source_ref and (
                        context["adoption_readback"]["agents"][role]["source_digest"]
                        != selection.expected_digest
                        or context["adoption_readback"]["agents"][role]["source_ref"]
                        != selection.source_ref.model_dump()
                    ):
                        raise ConflictError("Adopted source changed before native verification")
                if attempt.managed.verification_request_id is not None:
                    verified, _ = _load(
                        session, project_id, attempt.managed.verification_request_id
                    )
                    native = verified.metadata_json["native_execution"]
                    context["completed_verification"] = {
                        "reference": {
                            "kind": "native_request",
                            "request_id": verified.id,
                            "invocation_id": native["execution"]["invocation_id"],
                            "outcome_digest": _digest(native["result"]),
                        },
                        "result": native["result"],
                    }
        native_entered = True
        result = service.run_agent(
            **attempt.managed.settings,
            instructions=INSTRUCTIONS,
            prompt=json.dumps({**context, "claim_token": claim_token}),
            output_schema=RESULT_SCHEMA,
            launch_allowed=allowed,
            execution_scope=service.AgentExecutionScope(
                project_id, request_id=request_id, agent="project-setup"
            ),
            request_execution=AgentRequestExecution(
                project_id,
                request_id,
                claim_token,
                session_factory,
                context,
                setup_owner=True,
            ),
            resume_thread_id=attempt.managed.resume_thread_id,
            previous_usage=previous_usage,
        )
        result = validate_agent_result(result, RESULT_SCHEMA, attempt.runtime)
        with managed_launch_lock, session_factory() as session:
            _, current = _load(session, project_id, request_id)
            if current.managed.cancel_requested:
                return
            if not result.ok:
                raise ValidationError(result.error or "Native setup failed")
            report = SetupReport.model_validate_json(result.result["report_json"])
            if report.status == "success" and result.result.get("verification_status") in {
                "failed",
                "blocked",
            }:
                raise ValidationError("Failed or blocked native verification cannot complete setup")
            if report.status == "running":
                raise ValidationError(
                    "Native setup exited without a final outcome or concrete question"
                )
            ProjectSetupRepository(session).report(
                project_id,
                request_id=request_id,
                session_id=current.session_id,
                claim_token=claim_token,
                expected_revision=current.revision,
                report=report,
                _managed=True,
                _native_final=True,
            )
            _, current = _load(session, project_id, request_id)
            if (
                current.status == "partial"
                and current.requested_scope == "ready_to_use"
                and current.managed.phase == "preparation"
                and current.adoption_proposal is not None
            ):
                _adopt_managed(session, project_id, request_id, claim_token)
                continue_verification = True
            elif (
                current.status == "partial"
                and current.managed.phase == "verification"
                and result.result.get("verification_status") == "passed"
            ):
                # The native result is already durably retired. Only its bounded
                # check succeeded; final receipt validation needs a new claim.
                row, current = _load(session, project_id, request_id)
                current.managed.phase = "completion"
                current.managed.verification_request_id = request_id
                _save(session, row, current)
                continue_verification = True
    except Exception as exc:
        with managed_launch_lock, session_factory() as session:
            row, current = _load(session, project_id, request_id)
            current.managed.preflight_failed = not native_entered
            current.managed.diagnostic = redact_secret_text(str(exc))[:2000]
            current.status = (
                "interrupted"
                if (row.metadata_json or {}).get("native_execution", {}).get("finished_at") is None
                and (row.metadata_json or {}).get("native_execution")
                else "failed"
            )
            _save(session, row, current)
    finally:
        with managed_launch_lock, session_factory() as session:
            row, current = _load(session, project_id, request_id)
            native = (row.metadata_json or {}).get("native_execution")
            quiescent = not native or bool(
                native.get("finished_at") and not native.get("stop_error")
            )
            if quiescent:
                current.managed.slot_reserved = False
                if current.managed.cancel_requested:
                    current.status = "cancelled"
                if current.status not in {"needs_input", "running", "queued"}:
                    row.status = (
                        AgentRequestStatus.RESOLVED
                        if current.status == "success"
                        or current.managed.verification_request_id == request_id
                        else AgentRequestStatus.FAILED
                    )
                    row.completed_at = datetime.now(UTC).replace(tzinfo=None)
                    row.claim_token_hash = None
                getattr(scheduler, "project_setup_claims", {}).pop(request_id, None)
            else:
                current.managed.diagnostic = (
                    "Native exit remains unconfirmed; inspect this invocation before recovery"
                )
            if native:
                current.thread_id = native.get("execution", {}).get("thread_id")
            _save(session, row, current)
        if continue_verification and quiescent and not current.managed.cancel_requested:
            try:
                continue_managed(
                    project_id,
                    request_id,
                    expected_revision=current.revision,
                    session_factory=session_factory,
                    scheduler=scheduler,
                )
            except Exception as exc:
                with managed_launch_lock, session_factory() as session:
                    row, current = _load(session, project_id, request_id)
                    current.managed.diagnostic = redact_secret_text(str(exc))[:2000]
                    _save(session, row, current)
        _request_delivery_drain(session_factory, scheduler)


def cancel_setup(
    project_id,
    request_id,
    *,
    expected_revision,
    external_work_stopped,
    session_factory,
    scheduler,
):
    from stackos.repositories.agent_requests import AgentRequestRepository
    from stackos.repositories.project_setup import ProjectSetupRepository, SetupAttempt

    with session_factory() as session:
        row = AgentRequestRepository(session)._fetch(project_id=project_id, request_id=request_id)
        attempt = SetupAttempt.model_validate((row.metadata_json or {}).get("project_setup"))
        if attempt.entry_kind == "existing_agent":
            return ProjectSetupRepository(session).cancel_existing(
                project_id,
                request_id=request_id,
                expected_revision=expected_revision,
                external_work_stopped=external_work_stopped,
            )
    return cancel_managed(
        project_id, request_id, session_factory=session_factory, scheduler=scheduler
    )


def cancel_managed(project_id, request_id, *, session_factory, scheduler):
    from stackos.task_delivery import _admission_id, _request_delivery_drain

    with managed_launch_lock, session_factory() as session:
        row, attempt = _load(session, project_id, request_id)
        if (
            attempt.status in {"success", "partial", "failed", "cancelled"}
            and not attempt.managed.slot_reserved
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
        call.process_control.stop()
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


def missed_setup(event, *, session_factory, scheduler):
    """A missed callback has no native process and cannot retire itself."""
    from stackos.repositories.project_setup import ProjectSetupRepository
    from stackos.task_delivery import _request_delivery_drain

    with managed_launch_lock, session_factory() as session:
        for state in ProjectSetupRepository(session).managed_admissions(job_ref=event.job_id):
            row, attempt = _load(session, state.project_id, state.request_id)
            if (
                attempt.managed.job_ref == event.job_id
                and not attempt.managed.callback_started
                and attempt.status == "queued"
                and not attempt.managed.cancel_requested
            ):
                attempt.status = "failed"
                attempt.managed.diagnostic = "Setup job missed its launch window; retry setup"
                attempt.managed.slot_reserved = False
                row.status = AgentRequestStatus.FAILED
                row.claim_token_hash = None
                getattr(scheduler, "project_setup_claims", {}).pop(row.id, None)
                _save(session, row, attempt)
    _request_delivery_drain(session_factory, scheduler)
