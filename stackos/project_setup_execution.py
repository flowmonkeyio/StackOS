"""Request-native setup execution, admitted by task_delivery's shared capacity owner."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path

from apscheduler.jobstores.base import JobLookupError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlmodel import select

from stackos.artifacts import redact_secret_text
from stackos.db.models import AgentRequest, AgentRequestStatus
from stackos.host_mcp import service
from stackos.host_mcp.agent_execution import managed_launch_lock
from stackos.repositories.base import ConflictError, ValidationError


class MainAgentPreferences(BaseModel):
    """Launch choices only; instructions and specialist contracts have other owners."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    host_key: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)
    provider: str | None = Field(default=None, min_length=1)
    profile: str | None = Field(default=None, min_length=1)


RESULT_SCHEMA = {
    "type": "object",
    "properties": {"report_json": {"type": "string"}},
    "required": ["report_json"],
    "additionalProperties": False,
}
INSTRUCTIONS = """You are the project's setup/update coordinator, within the supplied project scope.
Read project instructions and resolve the supplied workflowTemplate.authoringGuide reference.
Consume its complete project_setup_method and authored setup/update prompt. Both managed and
existing-session setup use that same method. Inspect the project, choose applicable workflows
from the operator goal, preserve compatible choices and all applicable source obligations.
This is infrastructure setup: create no business run or tracker task. No rollout to other projects.
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
"""


def resolve_preferences(session, project_id, preferences=None, *, inherit_saved=True):
    from stackos.repositories.tracker import TrackerRepository

    defaults = TrackerRepository(session).delivery_defaults(project_id=project_id)
    saved = MainAgentPreferences.model_validate(
        (defaults.get("main_agent") or {}) if inherit_saved else {}
    ).model_dump(exclude_none=True)
    explicit = MainAgentPreferences.model_validate(preferences or {}).model_dump(exclude_none=True)
    if explicit.get("host_key") and explicit["host_key"] != saved.get("host_key"):
        saved = {}
    selected = {**saved, **explicit}
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
    attempt = SetupAttempt.model_validate((row.metadata_json or {}).get("project_setup"))
    if attempt.project_id != project_id or attempt.request_id != request_id or not attempt.managed:
        raise ConflictError("request is not this project's managed setup")
    return row, attempt


def start_managed(*, project_id, session_factory, scheduler, mode, workflow_keys, preferences=None):
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
        )
        started = repo.start(
            project_id, session_id=None, mode=mode, workflow_keys=workflow_keys, managed=execution
        )
        claims = getattr(scheduler, "project_setup_claims", None)
        if claims is None:
            scheduler.project_setup_claims = claims = {}
        claims[started.attempt.request_id] = started.claim_token
    drain_delivery(session_factory=session_factory, scheduler=scheduler)
    with session_factory() as session:
        _, started.attempt = _load(session, project_id, started.attempt.request_id)
    return started.model_copy(update={"claim_token": None})


def capacity_states(session):
    from stackos.repositories.project_setup import SetupAttempt

    rows = session.exec(
        select(AgentRequest)
        .where(func.json_type(AgentRequest.metadata_json, "$.project_setup.managed") == "object")
        .order_by(AgentRequest.id)
    ).all()
    return [(row, SetupAttempt.model_validate(row.metadata_json["project_setup"])) for row in rows]


def setup_directories(session):
    return [
        attempt.managed.settings["project_dir"]
        for _, attempt in capacity_states(session)
        if attempt.status in {"queued", "running"} or attempt.managed.slot_reserved
    ]


def reserve_setup(session, scheduler):
    """Called only by the shared capacity owner while holding managed_launch_lock."""
    from stackos.task_delivery import _admission_id

    for row, attempt in capacity_states(session):
        managed = attempt.managed
        if (
            attempt.status != "queued"
            or managed.slot_reserved
            or managed.admission_id != _admission_id(scheduler)
        ):
            continue
        token = getattr(scheduler, "project_setup_claims", {}).get(row.id)
        if token is None:
            continue  # Restart never manufactures a new claim or resumes old work.
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
        _save(session, row, attempt)
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
            "workflow_keys": attempt.workflow_keys,
            "guidance_ref": SETUP_GUIDANCE_REF,
            "project_dir": attempt.managed.settings["project_dir"],
            "observed_receipt_revisions": attempt.observed_receipt_revisions,
        }
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
        )
        result = validate_agent_result(result, RESULT_SCHEMA, attempt.runtime)
        with managed_launch_lock, session_factory() as session:
            _, current = _load(session, project_id, request_id)
            if current.managed.cancel_requested:
                return
            if not result.ok:
                raise ValidationError(result.error or "Native setup failed")
            report = SetupReport.model_validate_json(result.result["report_json"])
            if report.status == "running":
                raise ValidationError(
                    "Native setup exited without a final outcome or concrete question"
                )
            if current.status in {"running", "needs_input"}:
                ProjectSetupRepository(session).report(
                    project_id,
                    request_id=request_id,
                    session_id=current.session_id,
                    claim_token=claim_token,
                    expected_revision=current.revision,
                    report=report,
                    _managed=True,
                )
    except Exception as exc:
        with managed_launch_lock, session_factory() as session:
            row, current = _load(session, project_id, request_id)
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
        _request_delivery_drain(session_factory, scheduler)


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
    from stackos.task_delivery import _request_delivery_drain

    with managed_launch_lock, session_factory() as session:
        for row, attempt in capacity_states(session):
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
