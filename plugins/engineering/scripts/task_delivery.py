"""A task's fixed delivery/review sequence; agents supply every work decision."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.base import BaseScheduler
from pydantic import ValidationError as SchemaValidationError
from sqlmodel import Session

from stackos.db.models import (
    RunPlanStatus,
    RunPlanStepStatus,
    TrackerItemStatus,
    TrackerSourceKind,
    TrackerTicketKind,
)
from stackos.host_mcp.result import AgentSandbox, validate_agent_result
from stackos.host_mcp.service import agent_review_sandbox, agent_selection_error, run_agent
from stackos.operations.tracker.schemas import TaskDeliveryAgent
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.plugins import PluginRepository
from stackos.repositories.tracker import TrackerRepository

DELIVERY_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["complete", "blocked"]},
        "summary": {"type": "string"},
        "commit_sha": {"type": "string"},
    },
    "required": ["status", "summary", "commit_sha"],
    "additionalProperties": False,
}
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["approve", "reject", "blocked"]},
        "summary": {"type": "string"},
        "commit_sha": {"type": "string"},
        "followups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string", "minLength": 1}},
                "required": ["text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["decision", "summary", "commit_sha", "followups"],
    "additionalProperties": False,
}
ACTOR = "engineering.task-delivery"


def _workflow_scope(repo, project_id, task, run_plan_id, step_id):
    if (run_plan_id is None) != (step_id is None):
        raise ValidationError("run_plan_id and step_id must be provided together")
    if run_plan_id is None:
        if task.source_kind == TrackerSourceKind.WORKFLOW:
            raise ValidationError("Workflow delivery requires run_plan_id and step_id")
        return None
    context = repo.workflow_step_ticket_context(
        project_id=project_id,
        run_plan_id=run_plan_id,
        step_id=step_id,
    )
    if context["task_key"] != task.key:
        raise ValidationError("task_key must match the selected workflow task")
    if (
        context["run_plan_status"] != RunPlanStatus.STARTED
        or context["step_status"] != RunPlanStepStatus.RUNNING
    ):
        raise ValidationError("Automatic delivery requires a running workflow step")
    return context["parent_ticket_key"]


def _git(project_dir: str, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", project_dir, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode:
        reason = process.stderr.strip() or f"{args[0]} exited with status {process.returncode}"
        raise ValidationError(f"Git candidate check failed: {reason}")
    return process.stdout.strip()


def _committed_head(project_dir: str) -> str:
    head = _git(project_dir, "rev-parse", "--verify", "HEAD^{commit}")
    if _git(project_dir, "status", "--porcelain", "--untracked-files=all"):
        raise ValidationError(
            "Delivery requires a clean committed candidate; working tree is dirty"
        )
    return head


def _candidate_commit(project_dir: str, returned_sha: Any, base_sha: str) -> str:
    if not isinstance(returned_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{7,64}", returned_sha):
        raise ValidationError("Agent must return a real commit_sha")
    commit_sha = _git(project_dir, "rev-parse", "--verify", f"{returned_sha}^{{commit}}")
    if commit_sha == base_sha:
        raise ValidationError("Agent returned a stale commit_sha; a new ticket commit is required")
    if commit_sha != _committed_head(project_dir):
        raise ValidationError("Agent commit_sha does not match the current committed candidate")
    _git(project_dir, "merge-base", "--is-ancestor", base_sha, commit_sha)
    return commit_sha


def _task(repo: TrackerRepository, project_id: int, task_key: str):
    tasks = repo.get(project_id=project_id, task_key=task_key, include_graph=False).tasks
    if not tasks:
        raise NotFoundError("tracker task not found", data={"task_key": task_key})
    return tasks[0]


def _update_delivery(repo, project_id, task_key, **changes) -> dict[str, Any]:
    task = _task(repo, project_id, task_key)
    metadata = dict(task.metadata_json or {})
    state = {**metadata.get("delivery", {}), **changes, "updated_at": datetime.now(UTC).isoformat()}
    metadata["delivery"] = state
    repo.update_task(
        project_id=project_id,
        task_key=task_key,
        patch_json={"metadata_json": metadata},
        actor=ACTOR,
    )
    return state


def _receipt(task_key: str, state: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_key": task_key,
        "status": state["status"],
        "ticket_key": state.get("ticket_key"),
        "job_ref": state["job_ref"],
        "run_plan_id": state.get("run_plan_id"),
        "step_id": state.get("step_id"),
        "recommended_tool": "tracker.get",
        "message": "Inspect this task with tracker.get, task_key and response_mode=raw.",
    }


def _prompt_copy(value: Any) -> Any:
    """Leave deferred notes canonical, but exclude them from every nested work receipt."""
    if isinstance(value, dict):
        return {key: _prompt_copy(item) for key, item in value.items() if key != "followups"}
    if isinstance(value, list):
        return [_prompt_copy(item) for item in value]
    # Schema errors embed rejected output as Python repr and instance paths;
    # other receipts can contain serialized JSON. Keep diagnostics canonical,
    # but do not replay opaque note payloads through error/outcome/reason strings.
    if isinstance(value, str) and re.search(r"""["']followups\\*["']\s*[:\]]""", value):
        return "Diagnostic containing serialized followups omitted from work prompt."
    return value


def _archive_attempt(evidence: dict[str, Any]) -> None:
    if "delivery" in evidence or "review" in evidence:
        evidence.setdefault("attempts", []).append(
            {
                key: deepcopy(evidence[key])
                for key in ("attempt", "candidate", "delivery", "review")
                if key in evidence
            }
        )
    evidence.pop("delivery", None)
    evidence.pop("review", None)
    evidence["attempt"] = len(evidence.setdefault("attempts", [])) + 1


def _save_evidence(repo, project_id, ticket_key, evidence, **patch) -> None:
    ticket = repo.brief(project_id=project_id, ticket_key=ticket_key).ticket
    repo.update_ticket(
        project_id=project_id,
        ticket_key=ticket_key,
        patch_json={
            "completion_evidence_json": {
                **(ticket.completion_evidence_json or {}),
                "automatic_delivery": evidence,
            },
            **patch,
        },
        actor=ACTOR,
    )


def start_delivery(
    *,
    session: Session,
    session_factory: Callable[[], Session],
    scheduler: BaseScheduler,
    project_id: int,
    task_key: str,
    project_dir: str,
    host_key: str | None = None,
    delivery_agent: dict[str, Any] | None = None,
    review_agent: dict[str, Any] | None = None,
    run_plan_id: int | None = None,
    step_id: str | None = None,
) -> dict[str, Any]:
    repo = TrackerRepository(session)
    task = _task(repo, project_id, task_key)
    _workflow_scope(repo, project_id, task, run_plan_id, step_id)
    previous = (task.metadata_json or {}).get("delivery", {})
    if previous.get("status") in {"queued", "running"}:
        if (previous.get("run_plan_id"), previous.get("step_id")) != (run_plan_id, step_id):
            raise ValidationError("Task already has automatic delivery for a different scope")
        return _receipt(task_key, previous)
    if task.status not in {TrackerItemStatus.NOT_STARTED, TrackerItemStatus.IN_PROGRESS}:
        raise ValidationError("Task must be open before starting delivery")
    directory = Path(project_dir).expanduser().resolve()
    if not directory.is_dir():
        raise ValidationError("project_dir must be an existing working directory")
    defaults = (
        PluginRepository(session)
        .get_project_config(project_id=project_id, plugin_slug="engineering")
        .get("delivery", {})
    )
    if not isinstance(defaults, dict):
        raise ValidationError("Engineering delivery defaults must be an object")
    host_key = host_key or defaults.get("host_key") or "codex"
    if not isinstance(host_key, str):
        raise ValidationError("Engineering delivery host_key must be a string")
    resolved = {}
    for role, override in (("delivery_agent", delivery_agent), ("review_agent", review_agent)):
        default_agent = defaults.get(role, {})
        if not isinstance(default_agent, dict):
            raise ValidationError(f"Engineering delivery {role} must be an object")
        try:
            agent = TaskDeliveryAgent.model_validate(
                {**default_agent, **(override or {})}
            ).model_dump(exclude_none=True)
        except SchemaValidationError as schema_error:
            raise ValidationError(f"Resolved {role} is invalid: {schema_error}") from schema_error
        agent["host_key"] = agent.get("host_key") or host_key
        sandbox: AgentSandbox = (
            agent_review_sandbox(agent["host_key"]) if role == "review_agent" else "workspace-write"
        )
        error = agent_selection_error(
            agent["host_key"],
            sandbox=sandbox,
            model=agent.get("model"),
            provider=agent.get("provider"),
            profile=agent.get("profile"),
        )
        if error:
            raise ValidationError(error)
        resolved[role] = agent
    state = _update_delivery(
        repo,
        project_id,
        task_key,
        status="queued",
        ticket_key=None,
        reason=None,
        project_dir=str(directory),
        host_key=host_key,
        delivery_agent=resolved["delivery_agent"],
        review_agent=resolved["review_agent"],
        run_plan_id=run_plan_id,
        step_id=step_id,
        job_ref=f"engineering-delivery:{project_id}:{task.id}",
    )
    try:
        scheduler.add_job(
            run_delivery,
            trigger="date",
            id=state["job_ref"],
            jobstore="memory",
            executor="long",
            kwargs={
                "session_factory": session_factory,
                "project_id": project_id,
                "task_key": task_key,
            },
        )
    except Exception as error:
        _update_delivery(repo, project_id, task_key, status="stopped", reason=str(error))
        raise
    return _receipt(task_key, state)


def run_delivery(*, session_factory: Callable[[], Session], project_id: int, task_key: str) -> None:
    ticket_key = None
    evidence: dict[str, Any] = {}
    try:
        while True:
            with session_factory() as session:
                repo = TrackerRepository(session)
                task = _task(repo, project_id, task_key)
                state = (task.metadata_json or {})["delivery"]
                parent_key = _workflow_scope(
                    repo, project_id, task, state.get("run_plan_id"), state.get("step_id")
                )
                ready = repo.next(
                    project_id=project_id,
                    task_key=task_key,
                    pending_only=True,
                    limit=1,
                    include_blocked=False,
                    parent_ticket_key=parent_key,
                ).tickets
                if not ready:
                    complete = task.status == TrackerItemStatus.COMPLETE
                    if parent_key is not None:
                        children = [
                            ticket
                            for ticket in repo.get(
                                project_id=project_id, task_key=task_key, include_graph=False
                            ).tickets
                            if ticket.parent_ticket_key == parent_key
                            and ticket.kind != TrackerTicketKind.GROUP
                        ]
                        complete = bool(children) and all(
                            ticket.status == TrackerItemStatus.COMPLETE for ticket in children
                        )
                    _update_delivery(
                        repo,
                        project_id,
                        task_key,
                        status="complete" if complete else "stopped",
                        ticket_key=None,
                        reason=None if complete else "No pending ticket is ready for delivery.",
                    )
                    return
                ticket_key = ready[0].key
                evidence = deepcopy(
                    (ready[0].completion_evidence_json or {}).get("automatic_delivery", {})
                )
                _archive_attempt(evidence)
                evidence["candidate"] = {"base_commit_sha": _committed_head(state["project_dir"])}
                repo.update_ticket(
                    project_id=project_id,
                    ticket_key=ticket_key,
                    patch_json={
                        "status": "in-progress",
                        "assignee": state["delivery_agent"]["name"],
                    },
                    actor=ACTOR,
                )
                _update_delivery(
                    repo, project_id, task_key, status="running", ticket_key=ticket_key
                )

            phase = "delivery"
            while True:
                schema = DELIVERY_SCHEMA if phase == "delivery" else REVIEW_SCHEMA
                agent = state[f"{phase}_agent"]
                host_key = agent.get("host_key") or state["host_key"]
                sandbox: AgentSandbox = (
                    "workspace-write" if phase == "delivery" else agent_review_sandbox(host_key)
                )
                selection = {
                    key: agent[key]
                    for key in ("model", "provider", "profile")
                    if agent.get(key) is not None
                }
                identity = {
                    "agent": agent["name"],
                    "host_key": host_key,
                    "project_dir": state["project_dir"],
                    **selection,
                }
                sessions = evidence.setdefault("sessions", {})
                native_session = sessions.get(phase)
                resume_thread_id = None
                if native_session is not None:
                    if {
                        key: value for key, value in native_session.items() if key != "thread_id"
                    } != identity:
                        raise ValidationError(
                            "Ticket role session selection changed; cannot resume"
                        )
                    resume_thread_id = native_session["thread_id"]
                elif any(phase in attempt for attempt in evidence["attempts"]):
                    raise ValidationError(f"Cannot resume {phase}: missing native session ID")
                launch = {
                    **identity,
                    "sandbox": sandbox,
                    "started_at": datetime.now(UTC).isoformat(),
                    **({"resume_thread_id": resume_thread_id} if resume_thread_id else {}),
                }
                evidence[phase] = {"agent": agent["name"], "launch": launch}
                with session_factory() as session:
                    repo = TrackerRepository(session)
                    task = _task(repo, project_id, task_key)
                    _workflow_scope(
                        repo, project_id, task, state.get("run_plan_id"), state.get("step_id")
                    )
                    _save_evidence(repo, project_id, ticket_key, evidence)
                    brief = repo.brief(project_id=project_id, ticket_key=ticket_key).model_dump(
                        mode="json"
                    )
                    brief["task"]["metadata_json"] = {
                        key: value
                        for key, value in (brief["task"]["metadata_json"] or {}).items()
                        if key != "delivery"
                    }
                prior_review = next(
                    (
                        attempt["review"]
                        for attempt in reversed(evidence["attempts"])
                        if "review" in attempt
                    ),
                    None,
                )
                result = run_agent(
                    host_key,
                    state["project_dir"],
                    agent["instructions"],
                    json.dumps(
                        _prompt_copy(
                            {
                                "assignment": (
                                    "Deliver and commit only this ticket, "
                                    "then return its commit_sha. "
                                    "Use normal approval review for authorized .git/.codex writes; "
                                    "a denial stops this task. Never bypass it. "
                                    "When review_result is present, repair only "
                                    "its scoped findings, run "
                                    "focused checks and create a new descendant commit."
                                )
                                if phase == "delivery"
                                else (
                                    "Independently review this exact committed candidate. "
                                    "Execute assigned specs and assess "
                                    "relevant business regressions, "
                                    "architecture, wiring and shared ownership. "
                                    "Return the reviewed commit_sha. "
                                    "Do not change files or commits. "
                                    "Reject only for scoped repairs; "
                                    "put unrelated findings in followups. "
                                    "If a required check cannot run, return blocked. "
                                    "Keep commands, results and scoped repairs in summary."
                                ),
                                "lifecycle": (
                                    "Return results only. "
                                    "StackOS owns ticket status and sequencing."
                                ),
                                "brief": brief,
                                "delivery_result": evidence.get("delivery"),
                                "review_result": prior_review,
                                "candidate": evidence["candidate"],
                            }
                        )
                    ),
                    schema,
                    sandbox=sandbox,
                    **selection,
                    **({"resume_thread_id": resume_thread_id} if resume_thread_id else {}),
                )
                # The shared schema owner also protects controlled/injected harnesses.
                result = validate_agent_result(result, schema, host_key)
                evidence[phase] = {"agent": agent["name"], "launch": launch, **asdict(result)}
                value = result.result or {}
                decision = value.get("status" if phase == "delivery" else "decision")
                validation_error = None
                valid = result.ok
                if valid:
                    try:
                        thread_id = result.thread_id
                        if not isinstance(thread_id, str) or not thread_id.strip():
                            raise ValidationError("Agent must return a native session ID")
                        if resume_thread_id is not None and thread_id != resume_thread_id:
                            raise ValidationError("Native session resume identity mismatch")
                        other_session = sessions.get(
                            "review" if phase == "delivery" else "delivery"
                        )
                        if (
                            other_session
                            and host_key == other_session["host_key"]
                            and thread_id == other_session["thread_id"]
                        ):
                            raise ValidationError(
                                "Delivery and review require distinct native sessions"
                            )
                        sessions[phase] = {**identity, "thread_id": thread_id}
                        if decision != "blocked":
                            sha = _candidate_commit(
                                state["project_dir"],
                                value.get("commit_sha"),
                                evidence["candidate"]["base_commit_sha"],
                            )
                            if phase == "delivery":
                                evidence["candidate"]["commit_sha"] = sha
                            elif sha != evidence["candidate"]["commit_sha"]:
                                raise ValidationError(
                                    "Review commit_sha differs from the delivery candidate"
                                )
                    except (ValidationError, OSError) as error:
                        validation_error = str(error)
                        evidence[phase]["validation_error"] = validation_error
                        valid = False
                # Error receipts can still identify the exact native session for explicit requeue.
                elif native_session is None and result.thread_id:
                    sessions[phase] = {**identity, "thread_id": result.thread_id}

                repair = (
                    valid
                    and phase == "review"
                    and decision == "reject"
                    and not evidence.get("repair_pass_used", False)
                )
                accepted = valid and decision == ("complete" if phase == "delivery" else "approve")
                if repair:
                    evidence["repair_pass_used"] = True
                with session_factory() as session:
                    repo = TrackerRepository(session)
                    task = _task(repo, project_id, task_key)
                    _workflow_scope(
                        repo, project_id, task, state.get("run_plan_id"), state.get("step_id")
                    )
                    if valid and phase == "review":
                        context = deepcopy(task.context_json or {})
                        notes = context.setdefault("followups", [])
                        notes.extend(
                            {
                                "text": note["text"],
                                "source_ticket_key": ticket_key,
                                "source_attempt": evidence["attempt"],
                                "created_at": datetime.now(UTC).isoformat(),
                            }
                            for note in value["followups"]
                        )
                        repo.update_task(
                            project_id=project_id,
                            task_key=task_key,
                            patch_json={"context_json": context},
                            actor=ACTOR,
                        )
                    patch: dict[str, Any] = {}
                    if not accepted and not repair:
                        reason = (
                            validation_error
                            or result.error
                            or value.get("summary")
                            or f"{phase} failed"
                        )
                        patch.update(status="failed", outcome=reason)
                    elif accepted and phase == "review":
                        patch.update(status="complete", outcome=value["summary"])
                    _save_evidence(repo, project_id, ticket_key, evidence, **patch)
                    if not accepted and not repair:
                        _update_delivery(
                            repo, project_id, task_key, status="stopped", reason=reason
                        )
                        return
                if repair:
                    rejected_sha = evidence["candidate"]["commit_sha"]
                    _archive_attempt(evidence)
                    evidence["candidate"] = {"base_commit_sha": rejected_sha}
                    phase = "delivery"
                elif phase == "delivery":
                    phase = "review"
                else:
                    break
            ticket_key = None
    except Exception as error:
        with session_factory() as session:
            repo = TrackerRepository(session)
            if ticket_key is not None:
                _save_evidence(
                    repo, project_id, ticket_key, evidence, status="failed", outcome=str(error)
                )
            _update_delivery(repo, project_id, task_key, status="stopped", reason=str(error))
