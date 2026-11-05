"""A task's fixed delivery/review sequence; agents supply every work decision."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.base import BaseScheduler
from sqlmodel import Session

from stackos.db.models import (
    RunPlanStatus,
    RunPlanStepStatus,
    TrackerItemStatus,
    TrackerSourceKind,
    TrackerTicketKind,
)
from stackos.host_mcp.result import AgentSandbox
from stackos.host_mcp.service import run_agent
from stackos.repositories.base import NotFoundError, ValidationError
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
        "decision": {"type": "string", "enum": ["approve", "reject"]},
        "summary": {"type": "string"},
        "commit_sha": {"type": "string"},
    },
    "required": ["decision", "summary", "commit_sha"],
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


def start_delivery(
    *,
    session: Session,
    session_factory: Callable[[], Session],
    scheduler: BaseScheduler,
    project_id: int,
    task_key: str,
    project_dir: str,
    host_key: str,
    delivery_agent: dict[str, str],
    review_agent: dict[str, str],
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
    state = _update_delivery(
        repo,
        project_id,
        task_key,
        status="queued",
        ticket_key=None,
        reason=None,
        project_dir=str(directory),
        host_key=host_key,
        delivery_agent=delivery_agent,
        review_agent=review_agent,
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
                    repo,
                    project_id,
                    task,
                    state.get("run_plan_id"),
                    state.get("step_id"),
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
                evidence = {}
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
                brief = repo.brief(project_id=project_id, ticket_key=ticket_key).model_dump(
                    mode="json"
                )
                # The canonical brief supplies work context; task control instructions
                # are supplied separately to the selected harness, once per agent.
                brief["task"]["metadata_json"] = {
                    key: value
                    for key, value in (brief["task"]["metadata_json"] or {}).items()
                    if key != "delivery"
                }

            phases: tuple[tuple[str, dict[str, Any], AgentSandbox], ...] = (
                ("delivery", DELIVERY_SCHEMA, "workspace-write"),
                ("review", REVIEW_SCHEMA, "read-only"),
            )
            for phase, schema, sandbox in phases:
                agent = state[f"{phase}_agent"]
                launch = {
                    "host_key": state["host_key"],
                    "project_dir": state["project_dir"],
                    "sandbox": sandbox,
                    "started_at": datetime.now(UTC).isoformat(),
                }
                evidence[phase] = {"agent": agent["name"], "launch": launch}
                with session_factory() as session:
                    repo = TrackerRepository(session)
                    _workflow_scope(
                        repo,
                        project_id,
                        _task(repo, project_id, task_key),
                        state.get("run_plan_id"),
                        state.get("step_id"),
                    )
                    ticket = repo.brief(project_id=project_id, ticket_key=ticket_key).ticket
                    repo.update_ticket(
                        project_id=project_id,
                        ticket_key=ticket_key,
                        patch_json={
                            "completion_evidence_json": {
                                **(ticket.completion_evidence_json or {}),
                                "automatic_delivery": evidence,
                            }
                        },
                        actor=ACTOR,
                    )
                result = run_agent(
                    state["host_key"],
                    state["project_dir"],
                    agent["instructions"],
                    json.dumps(
                        {
                            "assignment": (
                                "Deliver and commit only this ticket, then return its commit_sha. "
                                "Use normal approval review for authorized .git/.codex writes; "
                                "a denial stops this task. Never bypass it."
                            )
                            if phase == "delivery"
                            else (
                                "Independently review this exact committed candidate. "
                                "Return the reviewed commit_sha. Do not change files or commits."
                            ),
                            "lifecycle": (
                                "Return results only. StackOS owns ticket status and sequencing."
                            ),
                            "brief": brief,
                            "delivery_result": evidence.get("delivery"),
                            "candidate": evidence["candidate"],
                        }
                    ),
                    schema,
                    sandbox=sandbox,
                )
                evidence[phase] = {"agent": agent["name"], "launch": launch, **asdict(result)}
                field, expected = (
                    ("status", "complete") if phase == "delivery" else ("decision", "approve")
                )
                accepted = result.ok and (result.result or {}).get(field) == expected
                validation_error = None
                if accepted:
                    try:
                        sha = _candidate_commit(
                            state["project_dir"],
                            (result.result or {}).get("commit_sha"),
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
                        accepted = False
                with session_factory() as session:
                    repo = TrackerRepository(session)
                    ticket = repo.brief(project_id=project_id, ticket_key=ticket_key).ticket
                    _workflow_scope(
                        repo,
                        project_id,
                        _task(repo, project_id, task_key),
                        state.get("run_plan_id"),
                        state.get("step_id"),
                    )
                    patch: dict[str, Any] = {
                        "completion_evidence_json": {
                            **(ticket.completion_evidence_json or {}),
                            "automatic_delivery": evidence,
                        },
                    }
                    if not accepted:
                        reason = (
                            validation_error
                            or result.error
                            or (result.result or {}).get("summary")
                            or f"{phase} failed"
                        )
                        patch.update(status="failed", outcome=reason)
                    elif phase == "review":
                        patch.update(
                            status="complete", outcome=(result.result or {}).get("summary")
                        )
                    repo.update_ticket(
                        project_id=project_id,
                        ticket_key=ticket_key,
                        patch_json=patch,
                        actor=ACTOR,
                    )
                    if not accepted:
                        _update_delivery(
                            repo, project_id, task_key, status="stopped", reason=reason
                        )
                        return
            ticket_key = None
    except Exception as error:
        with session_factory() as session:
            repo = TrackerRepository(session)
            if ticket_key is not None:
                ticket = repo.brief(project_id=project_id, ticket_key=ticket_key).ticket
                repo.update_ticket(
                    project_id=project_id,
                    ticket_key=ticket_key,
                    patch_json={
                        "status": "failed",
                        "outcome": str(error),
                        "completion_evidence_json": {
                            **(ticket.completion_evidence_json or {}),
                            "automatic_delivery": evidence,
                        },
                    },
                    actor=ACTOR,
                )
            _update_delivery(repo, project_id, task_key, status="stopped", reason=str(error))
