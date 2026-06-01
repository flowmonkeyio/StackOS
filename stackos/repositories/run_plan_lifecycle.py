"""Shared run-plan lifecycle reconciliation helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from stackos.db.models import (
    APPROVAL_REQUEST_STATUS_TRANSITIONS,
    RUN_PLAN_STATUS_TRANSITIONS,
    RUN_PLAN_STEP_STATUS_TRANSITIONS,
    RUN_STATUS_TRANSITIONS,
    ApprovalRequest,
    ApprovalRequestStatus,
    Run,
    RunPlan,
    RunPlanStatus,
    RunPlanStep,
    RunPlanStepStatus,
    RunStatus,
    TrackerItemStatus,
    TrackerTicket,
)
from stackos.repositories.base import ConflictError, validate_transition
from stackos.repositories.run_plan_state import (
    PLAN_TO_RUN_TERMINAL_STATUS,
    TERMINAL_PLAN_STATUSES,
    TERMINAL_RUN_STATUSES,
    TERMINAL_STEP_STATUSES,
)
from stackos.repositories.tracker import TrackerRepository

STALE_RUN_ERROR = "daemon-restart-orphan"


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


class RunPlanConsistencyIssueOut(BaseModel):
    code: str
    severity: Literal["warning", "error"]
    message: str
    run_plan_id: int
    run_id: int | None = None
    step_id: str | None = None
    ticket_key: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class RunPlanConsistencyOut(BaseModel):
    run_plan_id: int
    project_id: int
    status: RunPlanStatus
    issue_count: int
    repairable: bool
    issues: list[RunPlanConsistencyIssueOut] = Field(default_factory=list)
    next_operations: list[str] = Field(default_factory=list)


class RunPlanLifecycleReconciler:
    """Owns cross-model run/run-plan/tracker lifecycle invariants."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def validate_direct_run_finish(self, run: Run, requested_status: RunStatus) -> None:
        plan = self._plan_for_run(run)
        if plan is None:
            return
        if plan.status not in TERMINAL_PLAN_STATUSES:
            raise ConflictError(
                "run-plan audit runs cannot be finished while the run plan is live; "
                "use runPlan.recordStep or runPlan.abort",
                data={
                    "run_id": run.id,
                    "run_status": run.status.value,
                    "run_plan_id": plan.id,
                    "run_plan_status": plan.status.value,
                    "next_operations": ["runPlan.get", "runPlan.recordStep", "runPlan.abort"],
                },
            )
        expected_status = PLAN_TO_RUN_TERMINAL_STATUS[plan.status]
        if requested_status != expected_status:
            raise ConflictError(
                "run-plan audit run terminal status must match the linked run plan",
                data={
                    "run_id": run.id,
                    "run_plan_id": plan.id,
                    "run_plan_status": plan.status.value,
                    "requested_run_status": requested_status.value,
                    "expected_run_status": expected_status.value,
                },
            )

    def complete_plan(
        self,
        plan: RunPlan,
        *,
        run_status: RunStatus,
        error: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        status_by_run_status = {
            RunStatus.SUCCESS: RunPlanStatus.COMPLETED,
            RunStatus.FAILED: RunPlanStatus.FAILED,
        }
        plan_status = status_by_run_status.get(run_status)
        if plan_status is None:
            raise ValueError("complete_plan supports only success or failed run statuses")
        if plan.status == plan_status:
            return False
        if plan.status in TERMINAL_PLAN_STATUSES:
            return False
        validate_transition(
            plan.status,
            plan_status,
            RUN_PLAN_STATUS_TRANSITIONS,
            label="run_plan.status",
        )
        now = now or _utcnow()
        plan.status = plan_status
        plan.completed_at = now
        plan.updated_at = now
        self._s.add(plan)
        self.finish_linked_run(plan, status=run_status, error=error)
        TrackerRepository(self._s).mirror_run_plan_status(plan=plan)
        self._s.flush()
        return True

    def abort_plan(
        self,
        plan: RunPlan,
        *,
        reason: str | None = None,
        actor: str | None = None,
        linked_run_error: str | None = None,
    ) -> bool:
        if plan.id is None:
            return False
        if plan.status == RunPlanStatus.ABORTED:
            return False
        if plan.status in {RunPlanStatus.COMPLETED, RunPlanStatus.FAILED}:
            return False

        validate_transition(
            plan.status,
            RunPlanStatus.ABORTED,
            RUN_PLAN_STATUS_TRANSITIONS,
            label="run_plan.status",
        )
        now = _utcnow()
        for step in self._step_rows(plan.id):
            if step.status in {
                RunPlanStepStatus.PENDING,
                RunPlanStepStatus.BLOCKED,
                RunPlanStepStatus.RUNNING,
            }:
                validate_transition(
                    step.status,
                    RunPlanStepStatus.SKIPPED,
                    RUN_PLAN_STEP_STATUS_TRANSITIONS,
                    label="run_plan_step.status",
                )
                was_running = step.status == RunPlanStepStatus.RUNNING
                step.status = RunPlanStepStatus.SKIPPED
                step.result_json = {
                    "summary": (
                        "Run plan aborted while this step was running."
                        if was_running
                        else "Run plan aborted before this step executed."
                    ),
                    **({"reason": reason} if reason else {}),
                }
                step.error = None
                step.completed_at = now
                step.updated_at = now
                self._s.add(step)

        for approval in self._pending_approvals(plan.id):
            validate_transition(
                approval.status,
                ApprovalRequestStatus.CANCELLED,
                APPROVAL_REQUEST_STATUS_TRANSITIONS,
                label="approval_request.status",
            )
            approval.status = ApprovalRequestStatus.CANCELLED
            approval.decided_at = now
            approval.decided_by = actor
            approval.decision_json = {
                "summary": "Run plan aborted before this approval was decided.",
                **({"reason": reason} if reason else {}),
            }
            approval.updated_at = now
            self._s.add(approval)

        plan.status = RunPlanStatus.ABORTED
        plan.completed_at = now
        plan.updated_at = now
        plan.metadata_json = {
            **(plan.metadata_json or {}),
            "aborted_at": now.isoformat(),
            **({"aborted_by": actor} if actor else {}),
            **({"abort_reason": reason} if reason else {}),
            **({"linked_run_error": linked_run_error} if linked_run_error else {}),
        }
        self._s.add(plan)
        self.finish_linked_run(plan, status=RunStatus.ABORTED, error=linked_run_error)
        TrackerRepository(self._s).mirror_run_plan_aborted(
            plan=plan,
            reason=reason,
            actor=actor,
        )
        self._s.flush()
        return True

    def reconcile_aborted_run_plan(
        self,
        run: Run,
        *,
        reason: str = STALE_RUN_ERROR,
        actor: str | None = "system",
    ) -> bool:
        plan = self._plan_for_run(run)
        if plan is None:
            return False
        return self.abort_plan(
            plan,
            reason=reason,
            actor=actor,
            linked_run_error=run.error or reason,
        )

    def reconcile_orphaned_run_plans(
        self,
        *,
        project_id: int | None = None,
        reason: str = STALE_RUN_ERROR,
        actor: str | None = "system",
    ) -> int:
        stmt = select(Run).where(
            Run.status == RunStatus.ABORTED,
            Run.error == reason,
        )
        if project_id is not None:
            stmt = stmt.where(Run.project_id == project_id)
        count = 0
        for run in self._s.exec(stmt).all():
            if self.reconcile_aborted_run_plan(run, reason=reason, actor=actor):
                count += 1
            if run.id is not None:
                count += self.cascade_aborted_children(
                    run.id,
                    reason="parent-aborted",
                    actor=actor,
                )
        return count

    def cascade_aborted_children(
        self,
        root_run_id: int,
        *,
        reason: str = "parent-aborted",
        actor: str | None = "system",
    ) -> int:
        count = 0
        frontier = [root_run_id]
        while frontier:
            current = frontier.pop()
            children = self._s.exec(
                select(Run).where(
                    col(Run.parent_run_id) == current,
                    col(Run.status) == RunStatus.RUNNING,
                )
            ).all()
            for child in children:
                child.status = RunStatus.ABORTED
                child.error = child.error or reason
                child.ended_at = _utcnow()
                self._s.add(child)
                if self.reconcile_aborted_run_plan(child, reason=child.error, actor=actor):
                    count += 1
                if child.id is not None:
                    frontier.append(child.id)
        return count

    def should_preserve_live_run_plan(self, run: Run) -> bool:
        plan = self._plan_for_run(run)
        return plan is not None and plan.id is not None and plan.status == RunPlanStatus.STARTED

    def check_plan(self, plan: RunPlan) -> RunPlanConsistencyOut:
        issues = self.consistency_issues(plan)
        return RunPlanConsistencyOut(
            run_plan_id=int(plan.id or 0),
            project_id=plan.project_id,
            status=plan.status,
            issue_count=len(issues),
            repairable=any(issue.data.get("repairable") is True for issue in issues),
            issues=issues,
            next_operations=["runPlan.checkConsistency"] if issues else [],
        )

    def consistency_issues(self, plan: RunPlan) -> list[RunPlanConsistencyIssueOut]:
        if plan.id is None:
            return []
        issues: list[RunPlanConsistencyIssueOut] = []
        run = self._s.get(Run, plan.run_id) if plan.run_id is not None else None
        if run is not None and run.status in TERMINAL_RUN_STATUSES:
            if plan.status not in TERMINAL_PLAN_STATUSES:
                issues.append(
                    RunPlanConsistencyIssueOut(
                        code="terminal-run-live-plan",
                        severity="error",
                        message="Linked audit run is terminal while run plan is still live.",
                        run_plan_id=plan.id,
                        run_id=run.id,
                        data={
                            "run_status": run.status.value,
                            "run_error": run.error,
                            "run_plan_status": plan.status.value,
                            "repairable": run.status == RunStatus.ABORTED,
                            "next_operations": ["runPlan.checkConsistency"],
                        },
                    )
                )
            elif plan.status == RunPlanStatus.COMPLETED and run.status != RunStatus.SUCCESS:
                issues.append(
                    RunPlanConsistencyIssueOut(
                        code="terminal-run-terminal-plan-mismatch",
                        severity="error",
                        message="Run plan completed but linked audit run is not successful.",
                        run_plan_id=plan.id,
                        run_id=run.id,
                        data={
                            "run_status": run.status.value,
                            "run_error": run.error,
                            "run_plan_status": plan.status.value,
                        },
                    )
                )

        steps = self._step_rows(plan.id)
        if (
            plan.status == RunPlanStatus.STARTED
            and steps
            and all(step.status == RunPlanStepStatus.PENDING for step in steps)
            and self._has_tracker_progress(plan)
        ):
            issues.append(
                RunPlanConsistencyIssueOut(
                    code="tracker-progress-while-steps-pending",
                    severity="error",
                    message="Tracker shows workflow progress while all run-plan steps are pending.",
                    run_plan_id=plan.id,
                    run_id=plan.run_id,
                )
            )
        issues.extend(self._tracker_step_issues(plan, steps))
        return issues

    def finish_linked_run(
        self,
        plan: RunPlan,
        *,
        status: RunStatus,
        error: str | None = None,
    ) -> None:
        if plan.run_id is None:
            return
        run = self._s.get(Run, plan.run_id)
        if run is None:
            return
        if run.status == RunStatus.RUNNING:
            validate_transition(
                run.status,
                status,
                RUN_STATUS_TRANSITIONS,
                label="run.status",
            )
            run.status = status
            run.ended_at = _utcnow()
        if error is not None and not run.error:
            run.error = error
        metadata = dict(run.metadata_json or {})
        metadata.update({"run_plan_id": plan.id, "stackos_type": "run-plan"})
        run.metadata_json = metadata
        self._s.add(run)

    def _plan_for_run(self, run: Run) -> RunPlan | None:
        if run.id is not None:
            plan = self._s.exec(select(RunPlan).where(RunPlan.run_id == run.id)).first()
            if plan is not None:
                return plan
        metadata = run.metadata_json or {}
        run_plan_id = metadata.get("run_plan_id")
        if isinstance(run_plan_id, int):
            return self._s.get(RunPlan, run_plan_id)
        return None

    def plan_for_run(self, run: Run) -> RunPlan | None:
        return self._plan_for_run(run)

    def _step_rows(self, run_plan_id: int) -> list[RunPlanStep]:
        return list(
            self._s.exec(
                select(RunPlanStep)
                .where(col(RunPlanStep.run_plan_id) == run_plan_id)
                .order_by(col(RunPlanStep.position).asc())
            ).all()
        )

    def _pending_approvals(self, run_plan_id: int) -> list[ApprovalRequest]:
        return list(
            self._s.exec(
                select(ApprovalRequest).where(
                    ApprovalRequest.run_plan_id == run_plan_id,
                    ApprovalRequest.status == ApprovalRequestStatus.PENDING,
                )
            ).all()
        )

    def _has_tracker_progress(self, plan: RunPlan) -> bool:
        if plan.id is None:
            return False
        return (
            self._s.exec(
                select(TrackerTicket).where(
                    TrackerTicket.run_plan_id == plan.id,
                    TrackerTicket.status.in_(  # type: ignore[attr-defined]
                        [TrackerItemStatus.IN_PROGRESS, TrackerItemStatus.COMPLETE]
                    ),
                )
            ).first()
            is not None
        )

    def _tracker_step_issues(
        self,
        plan: RunPlan,
        steps: list[RunPlanStep],
    ) -> list[RunPlanConsistencyIssueOut]:
        if plan.id is None:
            return []
        by_id = {step.id: step for step in steps if step.id is not None}
        issues: list[RunPlanConsistencyIssueOut] = []
        tickets = self._s.exec(
            select(TrackerTicket).where(TrackerTicket.run_plan_id == plan.id)
        ).all()
        for ticket in tickets:
            if ticket.run_plan_step_id is None:
                continue
            step = by_id.get(ticket.run_plan_step_id)
            if step is None:
                continue
            if (
                ticket.status == TrackerItemStatus.COMPLETE
                and step.status not in TERMINAL_STEP_STATUSES
            ):
                issues.append(
                    RunPlanConsistencyIssueOut(
                        code="tracker-complete-step-not-terminal",
                        severity="error",
                        message=(
                            "Tracker ticket is complete while its canonical run-plan step "
                            "is not terminal."
                        ),
                        run_plan_id=plan.id,
                        run_id=plan.run_id,
                        step_id=step.step_id,
                        ticket_key=ticket.key,
                        data={
                            "ticket_status": ticket.status.value,
                            "step_status": step.status.value,
                        },
                    )
                )
            if (
                ticket.status == TrackerItemStatus.IN_PROGRESS
                and plan.status in TERMINAL_PLAN_STATUSES
            ):
                issues.append(
                    RunPlanConsistencyIssueOut(
                        code="tracker-active-terminal-plan",
                        severity="error",
                        message="Tracker ticket is active while its run plan is terminal.",
                        run_plan_id=plan.id,
                        run_id=plan.run_id,
                        step_id=step.step_id,
                        ticket_key=ticket.key,
                        data={
                            "ticket_status": ticket.status.value,
                            "run_plan_status": plan.status.value,
                        },
                    )
                )
        return issues


__all__ = [
    "STALE_RUN_ERROR",
    "RunPlanConsistencyIssueOut",
    "RunPlanConsistencyOut",
    "RunPlanLifecycleReconciler",
]
