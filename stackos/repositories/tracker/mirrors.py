# mypy: disable-error-code=attr-defined
"""Run-plan and agent-request tracker mirroring helpers."""

from __future__ import annotations

from stackos.artifacts import redact_secret_text
from stackos.db.models import (
    AgentRequest,
    RunPlan,
    RunPlanStep,
    RunPlanStepStatus,
    TrackerItemStatus,
    TrackerLinkKind,
    TrackerSourceKind,
    TrackerTask,
    TrackerTicket,
)
from stackos.repositories.base import (
    Envelope,
    NotFoundError,
)
from stackos.repositories.tracker.schema import TrackerMutationOut
from stackos.repositories.tracker.utils import (
    TERMINAL_TICKET_STATUSES,
    _clean_text,
    _slug,
    _utcnow,
)


class TrackerMirrorMixin:
    """Run-plan and agent-request tracker mirroring helpers."""

    def link_run_plan(
        self,
        *,
        project_id: int,
        task_key: str,
        run_plan_id: int,
        actor: str | None = None,
    ) -> Envelope[TrackerMutationOut]:
        tracker = self.ensure_tracker(project_id=project_id)
        plan = self._s.get(RunPlan, run_plan_id)
        if plan is None or plan.project_id != project_id:
            raise NotFoundError(
                "run plan not found in project",
                data={"project_id": project_id, "run_plan_id": run_plan_id},
            )
        task = self._task_by_key(tracker.id, task_key)
        self._add_link(
            tracker,
            task_id=task.id,
            link_kind=TrackerLinkKind.RUN_PLAN,
            run_plan_id=run_plan_id,
            ref=f"run-plan:{run_plan_id}",
            title=plan.title,
        )
        self._record_revision(
            tracker,
            actor=actor,
            change_kind="link",
            entity_kind="task",
            entity_id=task.id,
            entity_key=task.key,
            summary=f"Linked task {task.key} to run plan {run_plan_id}.",
            commit=False,
        )
        self._s.commit()
        return Envelope(
            data=TrackerMutationOut(
                tracker=self._tracker_out(tracker),
                task=self._task_out(task),
                rev=tracker.rev,
            ),
            project_id=project_id,
        )

    def mirror_run_plan_created(
        self,
        *,
        plan: RunPlan,
        steps: list[RunPlanStep],
        created_by: str | None = None,
    ) -> None:
        if plan.id is None:
            return
        tracker = self.ensure_tracker(project_id=plan.project_id)
        task_key = f"workflow-{plan.id}"
        task = self._task_by_key(tracker.id, task_key, missing_ok=True)
        now = _utcnow()
        if task is None:
            task = TrackerTask(
                tracker_id=tracker.id,
                project_id=plan.project_id,
                key=task_key,
                title=_clean_text(plan.title) or task_key,
                goal=_clean_text(plan.goal),
                description=f"Workflow run plan {plan.key}",
                status=TrackerItemStatus.NOT_STARTED,
                priority_key="p1",
                lane_key="planning",
                owner=plan.created_by,
                task_type="workflow",
                order_index=self._next_task_position(tracker.id),
                source_kind=TrackerSourceKind.WORKFLOW,
                source_json={
                    "run_plan_id": plan.id,
                    "run_plan_key": plan.key,
                    "template_key": plan.template_key,
                    "template_version": plan.template_version,
                },
                definition_of_done_json=["All run-plan step tickets reach complete or deferred."],
                constraints_json=[],
                expected_outcomes_json=[],
                context_json=plan.selected_context_json,
                metadata_json={"mirrored_from": "runPlan.create"},
                created_by=created_by,
                created_at=now,
                updated_at=now,
            )
            self._s.add(task)
            self._s.flush()
            self._add_link(
                tracker,
                task_id=task.id,
                link_kind=TrackerLinkKind.RUN_PLAN,
                run_plan_id=plan.id,
                ref=f"run-plan:{plan.id}",
                title=plan.title,
            )
            self._record_revision(
                tracker,
                actor=created_by,
                change_kind="mirror",
                entity_kind="task",
                entity_id=task.id,
                entity_key=task.key,
                summary=f"Mirrored run plan {plan.id} into tracker task {task.key}.",
                after_json=self._task_snapshot(task),
                commit=False,
            )
        step_by_id = {step.step_id: step for step in steps}
        ticket_by_step: dict[str, TrackerTicket] = {}
        for step in sorted(steps, key=lambda item: (item.position, item.id or 0)):
            ticket_key = f"workflow-{plan.id}-{_slug(step.step_id, fallback='step', max_length=80)}"
            ticket = self._ticket_by_key(tracker.id, ticket_key, missing_ok=True)
            if ticket is None:
                ticket = self._create_ticket_row(
                    tracker=tracker,
                    task=task,
                    key=ticket_key,
                    title=step.title,
                    goal=step.purpose,
                    status=self._ticket_status_from_step(step.status),
                    priority_key="p1",
                    lane_key="planning",
                    source_kind=TrackerSourceKind.WORKFLOW,
                    source_json={
                        "run_plan_id": plan.id,
                        "run_plan_key": plan.key,
                        "run_plan_step_id": step.id,
                        "step_id": step.step_id,
                        "template_key": plan.template_key,
                    },
                    definition_of_done_json=step.success_criteria_json or [],
                    constraints_json=step.policy_refs_json or [],
                    expected_changes_json=step.output_refs_json or [],
                    metadata_json={
                        "action_refs": step.action_refs_json or [],
                        "resource_refs": step.resource_refs_json or [],
                    },
                    run_plan_id=plan.id,
                    run_plan_step_id=step.id,
                    created_by=created_by,
                    now=now,
                    order_index=step.position,
                )
                self._add_link(
                    tracker,
                    task_id=task.id,
                    ticket_id=ticket.id,
                    link_kind=TrackerLinkKind.RUN_PLAN_STEP,
                    run_plan_id=plan.id,
                    run_plan_step_id=step.id,
                    ref=f"run-plan-step:{plan.id}:{step.step_id}",
                    title=step.title,
                )
                self._record_revision(
                    tracker,
                    actor=created_by,
                    change_kind="mirror",
                    entity_kind="ticket",
                    entity_id=ticket.id,
                    entity_key=ticket.key,
                    summary=f"Mirrored run-plan step {step.step_id} into ticket {ticket.key}.",
                    after_json=self._ticket_snapshot(ticket),
                    commit=False,
                )
            ticket_by_step[step.step_id] = ticket
        for step in steps:
            ticket = ticket_by_step[step.step_id]
            for dependency_step_id in step.depends_on_json or []:
                dependency_step = step_by_id.get(str(dependency_step_id))
                if dependency_step is None:
                    continue
                dependency_ticket = ticket_by_step.get(dependency_step.step_id)
                if dependency_ticket is not None:
                    self._add_dependency(tracker, ticket, dependency_ticket)
        plan.metadata_json = {
            **(plan.metadata_json or {}),
            "tracker_task_key": task.key,
            "tracker_ticket_keys": [ticket.key for ticket in ticket_by_step.values()],
        }
        plan.updated_at = now
        self._s.add(plan)
        self._sync_task_status(task, now=now)

    def mirror_run_plan_started(self, *, plan: RunPlan) -> None:
        if plan.id is None:
            return
        tracker = self.ensure_tracker(project_id=plan.project_id)
        task = self._task_by_key(tracker.id, f"workflow-{plan.id}", missing_ok=True)
        if task is None:
            return
        now = _utcnow()
        task.status = TrackerItemStatus.IN_PROGRESS
        task.started_at = task.started_at or now
        task.updated_at = now
        tickets = self._ticket_rows_for_run_plan(tracker.id, plan.id)
        for ticket in tickets:
            ticket.run_id = plan.run_id
            ticket.updated_at = now
            self._s.add(ticket)
        self._add_link(
            tracker,
            task_id=task.id,
            link_kind=TrackerLinkKind.RUN,
            run_plan_id=plan.id,
            run_id=plan.run_id,
            ref=f"run:{plan.run_id}",
            title=f"Run for {plan.title}",
        )
        self._record_revision(
            tracker,
            actor="system",
            change_kind="workflow-start",
            entity_kind="task",
            entity_id=task.id,
            entity_key=task.key,
            summary=f"Run plan {plan.id} started.",
            commit=False,
        )

    def mirror_run_plan_aborted(
        self,
        *,
        plan: RunPlan,
        reason: str | None = None,
        actor: str | None = None,
    ) -> None:
        if plan.id is None:
            return
        tracker = self.ensure_tracker(project_id=plan.project_id)
        task = self._task_by_key(tracker.id, f"workflow-{plan.id}", missing_ok=True)
        if task is None:
            return
        now = _utcnow()
        before = self._task_snapshot(task)
        task.status = TrackerItemStatus.DEFERRED
        task.lane_key = "done"
        task.completed_at = now
        task.updated_at = now
        current_evidence = dict(task.completion_evidence_json or {})
        task.completion_evidence_json = {
            **current_evidence,
            "summary": "Run plan aborted before completion.",
            "run_plan_id": plan.id,
            **({"reason": reason} if reason else {}),
        }
        self._s.add(task)

        for ticket in self._ticket_rows_for_run_plan(tracker.id, plan.id):
            if ticket.status in TERMINAL_TICKET_STATUSES:
                continue
            ticket.status = TrackerItemStatus.DEFERRED
            ticket.lane_key = "done"
            ticket.blocker_reason = None
            ticket.completed_at = now
            ticket.updated_at = now
            ticket.outcome = "Run plan aborted before this step completed."
            if reason:
                ticket.outcome = f"{ticket.outcome} Reason: {reason}"
            self._s.add(ticket)

        self._record_revision(
            tracker,
            actor=actor,
            change_kind="workflow-abort",
            entity_kind="task",
            entity_id=task.id,
            entity_key=task.key,
            summary=f"Run plan {plan.id} aborted.",
            before_json=before,
            after_json=self._task_snapshot(task),
            commit=False,
        )

    def mirror_run_plan_step_claimed(self, *, plan: RunPlan, step: RunPlanStep) -> None:
        if plan.id is None:
            return
        tracker = self.ensure_tracker(project_id=plan.project_id)
        ticket = self._ticket_for_step(tracker.id, plan.id, step.step_id)
        if ticket is None:
            return
        now = _utcnow()
        before = self._ticket_snapshot(ticket)
        ticket.status = TrackerItemStatus.IN_PROGRESS
        ticket.assignee = step.claimed_by
        ticket.claimed_at = step.claimed_at or now
        ticket.started_at = ticket.started_at or now
        ticket.run_id = plan.run_id
        ticket.lane_key = "implementation"
        ticket.updated_at = now
        self._s.add(ticket)
        task = self._s.get(TrackerTask, ticket.task_id)
        if task is not None:
            self._sync_task_status(task, now=now)
        self._record_revision(
            tracker,
            actor=step.claimed_by,
            change_kind="workflow-step-claim",
            entity_kind="ticket",
            entity_id=ticket.id,
            entity_key=ticket.key,
            summary=f"Run-plan step {step.step_id} claimed.",
            before_json=before,
            after_json=self._ticket_snapshot(ticket),
            commit=False,
        )

    def mirror_run_plan_step_recorded(self, *, plan: RunPlan, step: RunPlanStep) -> None:
        if plan.id is None:
            return
        tracker = self.ensure_tracker(project_id=plan.project_id)
        ticket = self._ticket_for_step(tracker.id, plan.id, step.step_id)
        if ticket is None:
            return
        now = _utcnow()
        before = self._ticket_snapshot(ticket)
        ticket.status = self._ticket_status_from_step(step.status)
        ticket.outcome = self._step_outcome(step)
        ticket.blocker_reason = step.error if step.status == RunPlanStepStatus.FAILED else None
        ticket.completed_at = now if ticket.status in TERMINAL_TICKET_STATUSES else None
        ticket.lane_key = "done" if ticket.status == TrackerItemStatus.COMPLETE else ticket.lane_key
        ticket.run_id = plan.run_id
        ticket.updated_at = now
        self._s.add(ticket)
        task = self._s.get(TrackerTask, ticket.task_id)
        if task is not None:
            self._sync_task_status(task, now=now)
        self._record_revision(
            tracker,
            actor=step.claimed_by,
            change_kind="workflow-step-record",
            entity_kind="ticket",
            entity_id=ticket.id,
            entity_key=ticket.key,
            summary=f"Run-plan step {step.step_id} recorded as {step.status.value}.",
            before_json=before,
            after_json=self._ticket_snapshot(ticket),
            commit=False,
        )

    def link_agent_request_to_task(
        self,
        *,
        request: AgentRequest,
        task_key: str,
        actor: str | None = None,
    ) -> None:
        tracker = self.ensure_tracker(project_id=request.project_id)
        task = self._task_by_key(tracker.id, task_key, missing_ok=True)
        if task is None:
            return
        self._add_link(
            tracker,
            task_id=task.id,
            link_kind=TrackerLinkKind.AGENT_REQUEST,
            agent_request_id=request.id,
            ref=f"agent-request:{request.id}",
            title=request.title,
        )
        self._record_revision(
            tracker,
            actor=actor,
            change_kind="link",
            entity_kind="task",
            entity_id=task.id,
            entity_key=task.key,
            summary=f"Linked agent request {request.id} to tracker task {task.key}.",
            commit=False,
        )

    def _ticket_status_from_step(self, status: RunPlanStepStatus) -> TrackerItemStatus:
        if status == RunPlanStepStatus.RUNNING:
            return TrackerItemStatus.IN_PROGRESS
        if status == RunPlanStepStatus.SUCCESS:
            return TrackerItemStatus.COMPLETE
        if status == RunPlanStepStatus.SKIPPED:
            return TrackerItemStatus.DEFERRED
        if status == RunPlanStepStatus.FAILED:
            return TrackerItemStatus.IN_PROGRESS
        return TrackerItemStatus.NOT_STARTED

    def _step_outcome(self, step: RunPlanStep) -> str | None:
        if step.error:
            return f"failed: {redact_secret_text(step.error)}"
        if isinstance(step.result_json, dict):
            for key in ("summary", "message", "status"):
                value = step.result_json.get(key)
                if value:
                    return redact_secret_text(str(value))
        return step.status.value


__all__ = [
    "TrackerMirrorMixin",
]
