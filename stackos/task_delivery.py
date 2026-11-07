"""Dependency-driven task execution; agents supply every work decision."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
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
from stackos.host_mcp.result import AgentSandbox, normalize_usage, validate_agent_result
from stackos.host_mcp.service import agent_review_sandbox, agent_selection_error, run_agent
from stackos.operations.tracker.schemas import TaskDeliveryAgent
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import TrackerRepository
from stackos.repositories.tracker.workflow import workflow_step_ticket_key

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["success", "repair", "blocked", "failed"]},
        "summary": {"type": "string"},
        # Opaque JSON transport supports arbitrary frozen workflow schemas in native
        # harnesses that require closed object properties. Decode before validation.
        "outputs": {
            "type": "string",
            "description": "JSON-encoded object of output values. "
            "For ordinary Git candidate evidence include commit_sha; otherwise use {}.",
        },
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
    "required": ["status", "summary", "outputs", "followups"],
    "additionalProperties": False,
}
ACTOR = "tracker.task-delivery"


def _workflow_scope(repo, project_id, task, run_plan_id, step_id, *, starting=False):
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
    if context["run_plan_status"] != RunPlanStatus.STARTED or (
        starting and context["step_status"] != RunPlanStepStatus.RUNNING
    ):
        raise ValidationError("Automatic delivery requires a running workflow step")
    return context["parent_ticket_key"]


def _git(project_dir: str, *args: str, strip: bool = True) -> str:
    process = subprocess.run(
        ["git", "-C", project_dir, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode:
        reason = process.stderr.strip() or f"{args[0]} exited with status {process.returncode}"
        raise ValidationError(f"Git candidate check failed: {reason}")
    return process.stdout.strip() if strip else process.stdout


def _committed_head(project_dir: str) -> str:
    head = _git(project_dir, "rev-parse", "--verify", "HEAD^{commit}")
    if _git(project_dir, "status", "--porcelain", "--untracked-files=all"):
        raise ValidationError(
            "Delivery requires a clean committed candidate; working tree is dirty"
        )
    return head


def _candidate_commit(project_dir: str, returned_sha: Any, base_sha: str | None) -> str:
    if not isinstance(returned_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{7,64}", returned_sha):
        raise ValidationError("Agent must return a real commit_sha")
    commit_sha = _git(project_dir, "rev-parse", "--verify", f"{returned_sha}^{{commit}}")
    if commit_sha != _committed_head(project_dir):
        raise ValidationError("Agent commit_sha does not match the current committed candidate")
    if base_sha:
        _git(project_dir, "merge-base", "--is-ancestor", base_sha, commit_sha)
    return commit_sha


def _baseline(project_dir: str) -> dict[str, str]:
    directory = Path(project_dir)
    if any((parent / ".git").exists() for parent in (directory, *directory.parents)):
        return {"base_commit_sha": _committed_head(project_dir)}
    return {}


def _worktree_snapshot(project_dir: str) -> dict[str, Any]:
    """Fingerprint staged, unstaged and untracked work without storing contents."""
    root = Path(_git(project_dir, "rev-parse", "--show-toplevel"))
    paths: set[str] = set()
    snapshot: dict[str, Any] = {
        "head_commit_sha": _git(project_dir, "rev-parse", "HEAD"),
    }
    for label, args in (("staged", ("--cached",)), ("unstaged", ())):
        diff = _git(
            str(root), "diff", "--binary", "--no-ext-diff", "--no-textconv", *args, strip=False
        )
        snapshot[label] = sha256(diff.encode()).hexdigest()
        paths.update(
            filter(
                None, _git(str(root), "diff", "--name-only", "-z", *args, strip=False).split("\0")
            )
        )
    snapshot["untracked"] = {}
    for name in filter(
        None,
        _git(str(root), "ls-files", "--others", "--exclude-standard", "-z", strip=False).split(
            "\0"
        ),
    ):
        path = root / name
        contents = str(path.readlink()).encode() if path.is_symlink() else path.read_bytes()
        snapshot["untracked"][name] = {
            "sha256": sha256(contents).hexdigest(),
            "mode": path.lstat().st_mode,
        }
        paths.add(name)
    snapshot["paths"] = sorted(paths)
    return snapshot


def _owned_work_receipt(
    project_dir: str,
    ticket_key: str,
    evidence: dict[str, Any],
    *,
    operator_recovery: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Capture a returned delivery, or an explicitly reviewed legacy recovery."""
    base = evidence.get("candidate", {}).get("base_commit_sha")
    native_session = evidence.get("sessions", {}).get("delivery")
    if not base or not native_session or not native_session.get("thread_id"):
        return None
    snapshot = _worktree_snapshot(project_dir)
    if not snapshot["paths"]:
        return None
    return {
        "ticket_key": ticket_key,
        "base_commit_sha": base,
        "session": deepcopy(native_session),
        "snapshot": snapshot,
        "source": "operator_recovery" if operator_recovery else "delivery_return",
        "captured_at": datetime.now(UTC).isoformat(),
        **({"operator_recovery": deepcopy(operator_recovery)} if operator_recovery else {}),
    }


def _launch_baseline(project_dir, ticket_key, evidence, phase, native_session):
    receipt = evidence.get("owned_work")
    if (
        phase == "delivery"
        and native_session
        and receipt
        and receipt.get("ticket_key") == ticket_key
        and receipt.get("session") == native_session
        and receipt.get("base_commit_sha") == evidence.get("candidate", {}).get("base_commit_sha")
        and receipt.get("snapshot") == _worktree_snapshot(project_dir)
    ):
        return {"base_commit_sha": receipt["base_commit_sha"]}
    # A fresh launch, changed work, another role or another session still needs clean Git.
    return _baseline(project_dir)


def _descendants(tickets, parent_key):
    keys = {parent_key}
    while True:
        expanded = keys | {t.key for t in tickets if t.parent_ticket_key in keys}
        if expanded == keys:
            return [t for t in tickets if t.key in keys and t.key != parent_key]
        keys = expanded


def _workflow_step(plans, state, ticket):
    if state.get("run_plan_id") is None:
        return None
    plan = plans.get(state["run_plan_id"])
    return next(
        (
            step
            for step in plan.steps
            if ticket.key == workflow_step_ticket_key(plan.id, step.step_id)
            and ticket.run_plan_step_id == step.id
            and ticket.parent_ticket_id is None
            and ticket.source_kind == TrackerSourceKind.WORKFLOW
        ),
        None,
    )


def _select_work(repo, plans, project_id, task_key, state):
    """Activate containers through their owner; dispatch only executable work."""
    tickets = repo.get(project_id=project_id, task_key=task_key, include_graph=False).tickets
    for ticket in repo.delivery_ready(project_id=project_id, task_key=task_key):
        step = _workflow_step(plans, state, ticket)
        children = _descendants(tickets, ticket.key)
        if step is not None:
            children = [
                t
                for t in tickets
                if t.key != ticket.key and (t in children or t.run_plan_step_id == step.id)
            ]
            if step.status == RunPlanStepStatus.PENDING:
                if any(
                    s.status == RunPlanStepStatus.RUNNING
                    for s in plans.get(state["run_plan_id"]).steps
                ):
                    continue
                plans.claim_step(
                    run_plan_id=state["run_plan_id"],
                    run_id=state["controller_run_id"],
                    step_id=step.step_id,
                    project_id=project_id,
                    claimed_by=ACTOR,
                )
                return None, None, True
            if step.status != RunPlanStepStatus.RUNNING:
                continue
            if any(child.status != TrackerItemStatus.COMPLETE for child in children):
                continue
            return (
                ticket,
                plans.get_step(
                    state["run_plan_id"], step.step_id, project_id=project_id, full_context=True
                ),
                False,
            )
        if ticket.run_plan_step_id is not None:
            plan = plans.get(ticket.run_plan_id, project_id=project_id)
            bound = next(s for s in plan.steps if s.id == ticket.run_plan_step_id)
            if bound.status != RunPlanStepStatus.RUNNING:
                continue
        if ticket.kind == TrackerTicketKind.GROUP:
            status = (
                "complete"
                if all(c.status == TrackerItemStatus.COMPLETE for c in children)
                else "in-progress"
            )
            if ticket.status != status:
                repo.update_ticket(
                    project_id=project_id,
                    ticket_key=ticket.key,
                    patch_json={"status": status},
                    actor=ACTOR,
                )
                return None, None, True
        elif ticket.status == TrackerItemStatus.NOT_STARTED:
            return ticket, None, False
    return None, None, False


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
    """Copy a known execution receipt without its deferred notes."""
    if isinstance(value, dict):
        return {
            key: deepcopy(item) if key in {"outputs", "summary"} else _prompt_copy(item)
            for key, item in value.items()
            if key != "followups"
        }
    if isinstance(value, list):
        return [_prompt_copy(item) for item in value]
    # Schema errors embed rejected output as Python repr and instance paths;
    # other receipts can contain serialized JSON. Keep diagnostics canonical,
    # but do not replay opaque note payloads through error/outcome/reason strings.
    if isinstance(value, str) and re.search(r"""["']followups\\*["']\s*[:\]]""", value):
        return "Diagnostic containing serialized followups omitted from work prompt."
    return value


def _work_prompt_copy(value: dict[str, Any]) -> dict[str, Any]:
    """Filter owned note locations while preserving frozen contracts and domain data."""
    packet = deepcopy(value)
    for key in ("delivery_result", "review_result"):
        packet[key] = _prompt_copy(packet.get(key))
    brief = packet.get("brief", {})
    context = (brief.get("task") or {}).get("context_json")
    if isinstance(context, dict):
        context.pop("followups", None)
    tickets = [
        brief.get("ticket"),
        *brief.get("dependencies", []),
        *brief.get("dependents", []),
        *packet.get("accepted_receipts", []),
    ]
    for ticket in tickets:
        if not isinstance(ticket, dict):
            continue
        evidence = ticket.get("completion_evidence_json") or {}
        automatic = evidence.get("automatic_delivery") or {}
        diagnostics: list[str | None] = []
        for attempt in [automatic, *automatic.get("attempts", [])]:
            for role in ("delivery", "review"):
                receipt = attempt.get(role) or {}
                diagnostics.extend(receipt.get(key) for key in ("error", "validation_error"))
        for key in ("automatic_delivery", "prior_review"):
            if key in evidence:
                evidence[key] = _prompt_copy(evidence[key])
        if ticket.get("outcome") and ticket["outcome"] in diagnostics:
            ticket["outcome"] = _prompt_copy(ticket["outcome"])
    return packet


def _archive_attempt(evidence: dict[str, Any]) -> None:
    if "delivery" in evidence or "review" in evidence:
        evidence.setdefault("attempts", []).append(
            {
                key: deepcopy(evidence[key])
                for key in ("attempt", "candidate", "delivery", "review", "owned_work")
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
    agent_assignments: Mapping[str, str] | None = None,
    controller_run_id: int | None = None,
) -> dict[str, Any]:
    repo = TrackerRepository(session)
    task = _task(repo, project_id, task_key)
    _workflow_scope(repo, project_id, task, run_plan_id, step_id)
    plan = (
        RunPlanRepository(session).get(run_plan_id, project_id=project_id) if run_plan_id else None
    )
    if plan and controller_run_id is not None and controller_run_id != plan.run_id:
        raise ValidationError("Delivery controller run does not match the workflow plan")
    if plan:
        RunPlanRepository(session).validate_delivery_controller(
            run_plan_id=plan.id, project_id=project_id, run_id=plan.run_id
        )
    previous = (task.metadata_json or {}).get("delivery", {})
    if previous.get("status") in {"queued", "running"}:
        if (previous.get("run_plan_id"), previous.get("step_id")) != (run_plan_id, step_id):
            raise ValidationError("Task already has automatic delivery for a different scope")
        return _receipt(task_key, previous)
    _workflow_scope(repo, project_id, task, run_plan_id, step_id, starting=True)
    if task.status not in {TrackerItemStatus.NOT_STARTED, TrackerItemStatus.IN_PROGRESS}:
        raise ValidationError("Task must be open before starting delivery")
    directory = Path(project_dir).expanduser().resolve()
    if not directory.is_dir():
        raise ValidationError("project_dir must be an existing working directory")
    defaults = repo.delivery_defaults(project_id=project_id)
    if not isinstance(defaults, dict):
        raise ValidationError("Task delivery defaults must be an object")
    host_key = host_key or defaults.get("host_key") or "codex"
    if not isinstance(host_key, str):
        raise ValidationError("Task delivery host_key must be a string")
    resolved = {}
    for role, override in (("delivery_agent", delivery_agent), ("review_agent", review_agent)):
        default_agent = defaults.get(role, {})
        if not isinstance(default_agent, dict):
            raise ValidationError(f"Task delivery {role} must be an object")
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
    assignments = {}
    if plan:
        for step in plan.steps:
            role = (step.metadata_json or {}).get("agent_assignment", "delivery_agent")
            assignments[workflow_step_ticket_key(plan.id, step.step_id)] = role
    assignments.update(agent_assignments or {})
    keys = {
        t.key
        for t in repo.get(project_id=project_id, task_key=task_key, include_graph=False).tickets
    }
    if any(key not in keys or role not in resolved for key, role in assignments.items()):
        raise ValidationError("agent_assignments must map task ticket keys to existing roles")
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
        agent_assignments=assignments,
        controller_run_id=plan.run_id if plan else None,
        job_ref=f"task-delivery:{project_id}:{task.id}",
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
                snapshot = repo.get(project_id=project_id, task_key=task_key, include_graph=False)
                if snapshot.tickets and all(
                    t.status == TrackerItemStatus.COMPLETE for t in snapshot.tickets
                ):
                    _update_delivery(
                        repo, project_id, task_key, status="complete", ticket_key=None, reason=None
                    )
                    return
                _workflow_scope(
                    repo, project_id, task, state.get("run_plan_id"), state.get("step_id")
                )
                plans = RunPlanRepository(session)
                if state.get("run_plan_id"):
                    plans.validate_delivery_controller(
                        run_plan_id=state["run_plan_id"],
                        project_id=project_id,
                        run_id=state["controller_run_id"],
                    )
                selected, workflow_step, progressed = _select_work(
                    repo, plans, project_id, task_key, state
                )
                if progressed:
                    continue
                if selected is None:
                    _update_delivery(
                        repo,
                        project_id,
                        task_key,
                        status="stopped",
                        ticket_key=None,
                        reason="No pending ticket is ready for delivery.",
                    )
                    return
                ticket_key = selected.key
                evidence = deepcopy(
                    (selected.completion_evidence_json or {}).get("automatic_delivery", {})
                )
                _archive_attempt(evidence)
                baseline_pending = True
                role = state["agent_assignments"].get(ticket_key, "delivery_agent")
                if workflow_step is None:
                    repo.update_ticket(
                        project_id=project_id,
                        ticket_key=ticket_key,
                        patch_json={
                            "status": "in-progress",
                            "assignee": state[role]["name"],
                        },
                        actor=ACTOR,
                    )
                _update_delivery(
                    repo, project_id, task_key, status="running", ticket_key=ticket_key
                )

            phase = role.removesuffix("_agent") if workflow_step is not None else "delivery"
            while True:
                schema = RESULT_SCHEMA
                selected_role = role if phase == "delivery" else "review_agent"
                agent = state[selected_role]
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
                if baseline_pending:
                    evidence["candidate"] = _launch_baseline(
                        state["project_dir"], ticket_key, evidence, phase, native_session
                    )
                    baseline_pending = False
                launch = {
                    **identity,
                    "sandbox": sandbox,
                    "started_at": datetime.now(UTC).isoformat(),
                    **({"resume_thread_id": resume_thread_id} if resume_thread_id else {}),
                }
                evidence[phase] = {
                    "agent": agent["name"],
                    "selected_role": selected_role,
                    "launch": launch,
                }
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
                    accepted_receipts = [
                        t.model_dump(mode="json")
                        for t in repo.get(
                            project_id=project_id, task_key=task_key, include_graph=False
                        ).tickets
                        if t.status == TrackerItemStatus.COMPLETE
                    ]
                prior_review = next(
                    (
                        attempt["review"]
                        for attempt in reversed(evidence["attempts"])
                        if "review" in attempt
                    ),
                    None,
                )
                # Earlier approvals describe historical candidates. Current ticket
                # context is authoritative when an agent reopens accepted work.
                if prior_review and prior_review.get("validated_outcome") != "repair":
                    prior_review = None
                result = run_agent(
                    host_key,
                    state["project_dir"],
                    agent["instructions"],
                    json.dumps(
                        _work_prompt_copy(
                            {
                                "assignment": (
                                    "Execute the remaining frozen workflow_step contract. "
                                    "Use accepted receipts and current operator inputs. "
                                    "Return declared outputs and explicit disposition. "
                                    "Do not edit source. Use only explicitly assigned existing "
                                    "tracker correction/reopen operations; StackOS owns "
                                    "claim, record and dispatch."
                                )
                                if workflow_step is not None
                                else (
                                    "Execute only this ticket. Commit source changes/repairs when "
                                    "required by project guidance; observation and non-Git work "
                                    "need no fabricated commit. Return optional commit_sha "
                                    "inside outputs. "
                                    "Use normal approval review for authorized .git/.codex writes; "
                                    "a denial stops that write and its dependent work. Never bypass "
                                    "the denial; continue unaffected checks and authorized "
                                    "alternatives. Missing required proof remains blocked. "
                                    "When review_result is present, repair only "
                                    "its scoped findings, run "
                                    "focused checks and create a new descendant commit."
                                )
                                if phase == "delivery"
                                else (
                                    "Independently review this exact candidate and ticket outputs. "
                                    "Execute assigned specs and assess "
                                    "relevant business regressions, "
                                    "architecture, wiring and shared ownership. "
                                    "Return supplied commit_sha in outputs unchanged. "
                                    "Do not change files or commits. "
                                    "Reject only for scoped repairs; "
                                    "put unrelated findings in followups. "
                                    "Report scoped rejection as repair. "
                                    "If a required check cannot run, return blocked. "
                                    "Keep commands, results and scoped repairs in summary."
                                ),
                                "result_contract": (
                                    "Return status success/repair/blocked/failed explicitly. "
                                    "Map domain conclusions to that disposition explicitly. "
                                    "outputs is a JSON-encoded object. followups are unrelated "
                                    "nonblocking text notes. Mirror repair without persisted "
                                    "reopened dependency work stops this task."
                                ),
                                "phase": phase,
                                "lifecycle": (
                                    "Return results only. "
                                    "StackOS owns ticket status and sequencing."
                                ),
                                "brief": brief,
                                "delivery_result": evidence.get("delivery"),
                                "review_result": prior_review,
                                "candidate": evidence["candidate"],
                                "workflow_step": workflow_step.model_dump(mode="json")
                                if workflow_step
                                else None,
                                "accepted_receipts": accepted_receipts,
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
                previous = next(
                    (
                        attempt[phase].get("usage")
                        for attempt in reversed(evidence["attempts"])
                        if phase in attempt
                    ),
                    None,
                )
                evidence[phase] = {
                    "agent": agent["name"],
                    "selected_role": selected_role,
                    "launch": launch,
                    **asdict(result),
                    "usage": normalize_usage(
                        result.usage,
                        session_id=result.thread_id,
                        resumed=resume_thread_id is not None,
                        previous=previous,
                    ),
                    "validated_outcome": None,
                }
                value = result.result or {}
                decision = value.get("status")
                outputs = {}
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
                        outputs = json.loads(value["outputs"])
                        if not isinstance(outputs, dict):
                            raise ValidationError("outputs must encode a JSON object")
                        if decision in {"success", "repair"} and evidence["candidate"].get(
                            "base_commit_sha"
                        ):
                            _committed_head(state["project_dir"])
                        returned_sha = outputs.get("commit_sha")
                        previous_candidate: dict[str, Any] = next(
                            (
                                attempt.get("candidate", {})
                                for attempt in reversed(evidence["attempts"])
                                if attempt.get("review", {}).get("validated_outcome") == "success"
                            ),
                            {},
                        )
                        changed_candidate = bool(
                            workflow_step is None
                            and previous_candidate.get("commit_sha")
                            and previous_candidate.get("base_commit_sha")
                            and previous_candidate.get("commit_sha")
                            != previous_candidate.get("base_commit_sha")
                        )
                        if decision in {"success", "repair"} and (
                            "commit_sha" in outputs
                            or evidence["candidate"].get("commit_sha")
                            or changed_candidate
                        ):
                            sha = _candidate_commit(
                                state["project_dir"],
                                returned_sha,
                                evidence["candidate"].get("base_commit_sha"),
                            )
                            if workflow_step is not None or phase == "delivery":
                                if (
                                    workflow_step is None
                                    and (evidence.get("repair_pass_used") or changed_candidate)
                                    and sha == evidence["candidate"].get("base_commit_sha")
                                ):
                                    raise ValidationError("Repair requires a new descendant commit")
                                evidence["candidate"]["commit_sha"] = sha
                            elif sha != evidence["candidate"].get("commit_sha"):
                                raise ValidationError(
                                    "Review commit_sha differs from the delivery candidate"
                                )
                    except (ValidationError, OSError, ValueError) as error:
                        validation_error = str(error)
                        evidence[phase]["validation_error"] = validation_error
                        valid = False
                # Error receipts can still identify the exact native session for explicit requeue.
                elif native_session is None and result.thread_id:
                    sessions[phase] = {**identity, "thread_id": result.thread_id}

                if (
                    phase == "delivery"
                    and result.thread_id
                    and sessions.get("delivery") == {**identity, "thread_id": result.thread_id}
                ):
                    receipt = _owned_work_receipt(state["project_dir"], ticket_key, evidence)
                    if receipt:
                        evidence["owned_work"] = receipt
                    else:
                        evidence.pop("owned_work", None)

                if workflow_step is None and valid:
                    evidence[phase]["validated_outcome"] = decision

                repair = (
                    valid
                    and workflow_step is None
                    and phase == "review"
                    and decision == "repair"
                    and not evidence.get("repair_pass_used", False)
                )
                accepted = valid and decision == "success"
                if repair:
                    evidence["repair_pass_used"] = True
                with session_factory() as session:
                    repo = TrackerRepository(session)
                    task = _task(repo, project_id, task_key)
                    _workflow_scope(
                        repo, project_id, task, state.get("run_plan_id"), state.get("step_id")
                    )
                    if valid:
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
                    if workflow_step is not None:
                        plans = RunPlanRepository(session)
                        current_step = plans.get_step(
                            state["run_plan_id"], workflow_step.step_id, project_id=project_id
                        )
                        if current_step.status != RunPlanStepStatus.RUNNING:
                            # The agent may have rewound this occurrence through the
                            # canonical owner. Its return is evidence, not acceptance
                            # of the newly pending occurrence (even for success).
                            evidence[phase]["superseded"] = True
                            _save_evidence(repo, project_id, ticket_key, evidence)
                            previously_accepted = {t["key"] for t in accepted_receipts}
                            # Requeued children may still await activation by their
                            # pending parent. Detect persisted progress here; the next
                            # selection applies dependency readiness and claims it.
                            if (
                                valid
                                and decision in {"success", "repair"}
                                and current_step.status == RunPlanStepStatus.PENDING
                                and any(
                                    t.key in previously_accepted
                                    and t.status == TrackerItemStatus.NOT_STARTED
                                    for t in repo.get(
                                        project_id=project_id,
                                        task_key=task_key,
                                        include_graph=False,
                                    ).tickets
                                )
                            ):
                                break
                            _update_delivery(
                                repo,
                                project_id,
                                task_key,
                                status="stopped",
                                reason=validation_error
                                or result.error
                                or (
                                    value.get("summary")
                                    if decision in {"blocked", "failed"}
                                    else None
                                )
                                or "Workflow occurrence changed without valid "
                                "reopened dependency work",
                            )
                            return
                        # Only the run-plan owner can validate outputs and advance mirrors.
                        _save_evidence(repo, project_id, ticket_key, evidence)
                        status = (
                            RunPlanStepStatus.SUCCESS
                            if accepted
                            else RunPlanStepStatus.BLOCKED
                            if decision in {"repair", "blocked"}
                            else RunPlanStepStatus.FAILED
                        )
                        plans.record_step(
                            run_plan_id=state["run_plan_id"],
                            run_id=state["controller_run_id"],
                            project_id=project_id,
                            step_id=workflow_step.step_id,
                            status=status,
                            result_json=outputs,
                            error=None
                            if accepted
                            else (validation_error or result.error or value.get("summary")),
                        )
                        if valid:
                            evidence[phase]["validated_outcome"] = decision
                            _save_evidence(repo, project_id, ticket_key, evidence)
                    else:
                        _save_evidence(repo, project_id, ticket_key, evidence, **patch)
                    if not accepted and not repair:
                        _update_delivery(
                            repo, project_id, task_key, status="stopped", reason=reason
                        )
                        return
                if repair:
                    rejected_sha = evidence["candidate"].get("commit_sha")
                    _archive_attempt(evidence)
                    evidence["candidate"] = (
                        {"base_commit_sha": rejected_sha} if rejected_sha else {}
                    )
                    phase = "delivery"
                elif phase == "delivery" and workflow_step is None:
                    phase = "review"
                else:
                    break
            ticket_key = None
    except Exception as error:
        with session_factory() as session:
            repo = TrackerRepository(session)
            if ticket_key is not None:
                plans = RunPlanRepository(session)
                ticket = repo.brief(project_id=project_id, ticket_key=ticket_key).ticket
                step = _workflow_step(plans, state, ticket)
                _save_evidence(
                    repo,
                    project_id,
                    ticket_key,
                    evidence,
                    **({} if step else {"status": "failed", "outcome": str(error)}),
                )
                if step and step.status == RunPlanStepStatus.RUNNING:
                    plans.record_step(
                        run_plan_id=state["run_plan_id"],
                        run_id=state["controller_run_id"],
                        project_id=project_id,
                        step_id=step.step_id,
                        status=RunPlanStepStatus.BLOCKED,
                        error=str(error),
                    )
            _update_delivery(repo, project_id, task_key, status="stopped", reason=str(error))
