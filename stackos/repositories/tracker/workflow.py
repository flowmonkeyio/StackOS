"""Workflow tracker identity helpers."""

from __future__ import annotations

from stackos.db.models import RunPlanStep, TrackerSourceKind, TrackerTicket


def workflow_step_ticket_key(run_plan_id: int, step_id: str) -> str:
    return f"workflow-{run_plan_id}-{step_id}"


def is_workflow_step_mirror_ticket(ticket: TrackerTicket, step: RunPlanStep | None) -> bool:
    if (
        ticket.run_plan_id is None
        or ticket.run_plan_step_id is None
        or step is None
        or step.id is None
    ):
        return False
    return (
        ticket.source_kind == TrackerSourceKind.WORKFLOW
        and ticket.parent_ticket_id is None
        and ticket.run_plan_id == step.run_plan_id
        and ticket.run_plan_step_id == step.id
    )


__all__ = ["is_workflow_step_mirror_ticket", "workflow_step_ticket_key"]
