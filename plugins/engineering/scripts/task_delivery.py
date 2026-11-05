"""A task's fixed delivery/review sequence; agents supply every work decision."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.base import BaseScheduler
from sqlmodel import Session

from stackos.db.models import TrackerItemStatus, TrackerSourceKind
from stackos.host_mcp.result import AgentSandbox
from stackos.host_mcp.service import run_agent
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.tracker import TrackerRepository

DELIVERY_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["complete", "blocked"]},
        "summary": {"type": "string"},
    },
    "required": ["status", "summary"],
    "additionalProperties": False,
}
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["approve", "reject"]},
        "summary": {"type": "string"},
    },
    "required": ["decision", "summary"],
    "additionalProperties": False,
}
ACTOR = "engineering.task-delivery"


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
) -> dict[str, Any]:
    repo = TrackerRepository(session)
    task = _task(repo, project_id, task_key)
    if task.source_kind == TrackerSourceKind.WORKFLOW:
        raise ValidationError("Automatic delivery accepts independent prepared tasks only")
    previous = (task.metadata_json or {}).get("delivery", {})
    if previous.get("status") in {"queued", "running"}:
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
    try:
        while True:
            with session_factory() as session:
                repo = TrackerRepository(session)
                task = _task(repo, project_id, task_key)
                state = (task.metadata_json or {})["delivery"]
                ready = repo.next(
                    project_id=project_id,
                    task_key=task_key,
                    pending_only=True,
                    limit=1,
                    include_blocked=False,
                ).tickets
                if not ready:
                    complete = task.status == TrackerItemStatus.COMPLETE
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

            evidence: dict[str, Any] = {}
            phases: tuple[tuple[str, dict[str, Any], AgentSandbox], ...] = (
                ("delivery", DELIVERY_SCHEMA, "workspace-write"),
                ("review", REVIEW_SCHEMA, "read-only"),
            )
            for phase, schema, sandbox in phases:
                agent = state[f"{phase}_agent"]
                result = run_agent(
                    state["host_key"],
                    state["project_dir"],
                    agent["instructions"],
                    json.dumps(
                        {
                            "assignment": "Deliver this ticket."
                            if phase == "delivery"
                            else "Independently review this ticket's actual changes.",
                            "lifecycle": (
                                "Return results only. StackOS owns ticket status and sequencing."
                            ),
                            "brief": brief,
                            "delivery_result": evidence.get("delivery"),
                        }
                    ),
                    schema,
                    sandbox=sandbox,
                )
                evidence[phase] = {"agent": agent["name"], **asdict(result)}
                field, expected = (
                    ("status", "complete") if phase == "delivery" else ("decision", "approve")
                )
                accepted = result.ok and (result.result or {}).get(field) == expected
                with session_factory() as session:
                    repo = TrackerRepository(session)
                    ticket = repo.brief(project_id=project_id, ticket_key=ticket_key).ticket
                    patch: dict[str, Any] = {
                        "completion_evidence_json": {
                            **(ticket.completion_evidence_json or {}),
                            "automatic_delivery": evidence,
                        },
                    }
                    if not accepted:
                        reason = (
                            result.error
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
                repo.update_ticket(
                    project_id=project_id,
                    ticket_key=ticket_key,
                    patch_json={"status": "failed", "outcome": str(error)},
                    actor=ACTOR,
                )
            _update_delivery(repo, project_id, task_key, status="stopped", reason=str(error))
