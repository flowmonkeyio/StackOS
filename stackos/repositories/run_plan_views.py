"""Output contracts and pure view composition for run plans."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stackos.db.models import (
    ApprovalRequestStatus,
    RunPlan,
    RunPlanStatus,
    RunPlanStep,
    RunPlanStepStatus,
)
from stackos.repositories.run_plan_lifecycle import RunPlanConsistencyIssueOut
from stackos.repositories.runs import RunOut


def jsonable(value: Any) -> Any:
    """Return the same JSON-safe copy used by run-plan persistence and views."""

    return json.loads(json.dumps(value, default=str))


class RunPlanStepHandoffOut(BaseModel):
    """Bounded result context passed from one direct dependency."""

    step_id: str
    title: str
    status: RunPlanStepStatus
    output_refs_json: list[str] = Field(default_factory=list)
    result_json: dict[str, Any] | None = None
    truncated: bool = False


class RunPlanStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_plan_id: int
    step_id: str
    title: str
    purpose: str
    position: int
    status: RunPlanStepStatus
    depends_on_json: list[str]
    input_refs_json: list[str]
    context_refs_json: list[str]
    action_refs_json: list[str]
    resource_refs_json: list[str]
    policy_refs_json: list[str]
    approval_refs_json: list[str]
    output_refs_json: list[str]
    instructions_json: list[str]
    success_criteria_json: list[str]
    action_payloads_json: list[dict[str, Any]] | None
    expected_outputs_json: dict[str, Any] | None
    input_values_json: dict[str, Any] = Field(default_factory=dict)
    step_context_json: dict[str, Any] | None = None
    input_context_truncated: bool = False
    result_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    direct_dependency_handoffs: list[RunPlanStepHandoffOut] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    action_execution_guidance: dict[str, Any] = Field(default_factory=dict)
    error: str | None
    claimed_by: str | None
    claimed_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApprovalRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    run_plan_id: int
    run_plan_step_id: int | None
    approval_key: str
    title: str
    description: str
    required_when: str
    approver: str | None
    status: ApprovalRequestStatus
    requested_by: str | None
    decided_by: str | None
    requested_at: datetime
    decided_at: datetime | None
    decision_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class RunPlanSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    run_id: int | None
    template_id: int | None
    template_version_id: int | None
    context_snapshot_id: int | None
    key: str
    title: str
    goal: str
    status: RunPlanStatus
    template_key: str | None
    template_version: str | None
    template_source: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class RunPlanOut(RunPlanSummaryOut):
    template_origin_path: str | None
    template_snapshot_json: dict[str, Any] | None
    inputs_json: dict[str, Any]
    selected_context_json: dict[str, Any] | None
    context_filters_json: dict[str, Any] | None
    grant_snapshot_json: dict[str, Any] | None
    budget_snapshot_json: dict[str, Any] | None
    policy_snapshot_json: dict[str, Any] | None
    output_contract_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    steps: list[RunPlanStepOut] = Field(default_factory=list)
    approval_requests: list[ApprovalRequestOut] = Field(default_factory=list)
    consistency_issues: list[RunPlanConsistencyIssueOut] = Field(default_factory=list)


class RunPlanStartOut(BaseModel):
    plan: RunPlanOut
    run: RunOut
    run_token: str
    run_id: int


class RunPlanReopenOut(BaseModel):
    plan: RunPlanOut
    run: RunOut
    run_token: str
    run_id: int
    reopened_step_id: str
    reset_step_ids: list[str]
    next_operations: list[str] = Field(default_factory=list)


def direct_dependency_handoffs(
    step: RunPlanStep,
    *,
    all_steps: list[RunPlanStep],
) -> list[RunPlanStepHandoffOut]:
    by_step_id = {item.step_id: item for item in all_steps}
    handoffs: list[RunPlanStepHandoffOut] = []
    for dependency_id in (step.depends_on_json or [])[:12]:
        dependency = by_step_id.get(dependency_id)
        if dependency is None:
            continue
        result, truncated = bounded_handoff_result(
            dependency.result_json,
            run_plan_id=dependency.run_plan_id,
            step_id=dependency.step_id,
        )
        handoffs.append(
            RunPlanStepHandoffOut(
                step_id=dependency.step_id,
                title=dependency.title,
                status=dependency.status,
                output_refs_json=list(dependency.output_refs_json or []),
                result_json=result,
                truncated=truncated,
            )
        )
    return handoffs


def bounded_handoff_result(
    result_json: dict[str, Any] | None,
    *,
    run_plan_id: int,
    step_id: str,
    max_bytes: int = 4096,
) -> tuple[dict[str, Any] | None, bool]:
    if result_json is None:
        return None, False
    copied = jsonable(result_json)
    if len(json.dumps(copied, sort_keys=True).encode("utf-8")) <= max_bytes:
        return copied, False
    summary = copied.get("summary")
    bounded: dict[str, Any] = {
        "available_keys": sorted(str(key) for key in copied),
        "handoff_truncated": True,
        "recovery": (
            "Call runPlan.getStep with "
            f"run_plan_id={run_plan_id}, step_id={step_id!r}, and response_mode=raw "
            "for the complete prior result."
        ),
    }
    if isinstance(summary, str) and summary.strip():
        bounded["summary"] = summary[:1200]
    return bounded, True


def bounded_step_payload(
    value: dict[str, Any] | None,
    *,
    recovery: str,
    max_bytes: int = 4096,
) -> tuple[dict[str, Any] | None, bool]:
    if not value:
        return None, False
    copied = jsonable(value)
    if len(json.dumps(copied, sort_keys=True).encode("utf-8")) <= max_bytes:
        return copied, False
    return (
        {
            "available_keys": sorted(str(key) for key in copied),
            "payload_truncated": True,
            "recovery": recovery,
        },
        True,
    )


def step_action_execution_guidance(
    *,
    step: RunPlanStep,
    plan: RunPlan,
    allowed_tools: list[str],
) -> dict[str, Any]:
    action_refs = [ref for ref in step.action_refs_json or [] if isinstance(ref, str) and ref]
    if not action_refs:
        return {}
    next_calls: list[dict[str, Any]] = []
    for action_ref in action_refs[:8]:
        next_calls.extend(
            [
                {
                    "operation": "action.describe",
                    "arguments": {
                        "project_id": plan.project_id,
                        "action_ref": action_ref,
                    },
                },
                {
                    "operation": "executionContext.discover",
                    "arguments": {
                        "project_id": plan.project_id,
                        "action_ref": action_ref,
                        "run_plan_id": plan.id,
                        "run_id": plan.run_id,
                    },
                },
            ]
        )
    guidance: dict[str, Any] = {
        "action_refs": action_refs,
        "preferred_path": [
            "action.describe",
            "executionContext.discover_or_resolve",
            "action.validate",
            "action.execute",
        ],
        "context_rule": (
            "Use context_ref when the step repeats credential, provider scope, output policy, "
            "request budget, or artifact namespace across action calls."
        ),
        "payload_boundary": {
            "input_json": "endpoint payload for this one action call",
            "provider_context_json": "reusable provider/account scope, not endpoint payload",
            "context_ref": "preferred carrier for repeated credential/provider context",
        },
        "next_calls": next_calls,
    }
    if "action.execute" not in set(allowed_tools):
        guidance["warning"] = "step declares action_refs but does not grant action.execute"
    return guidance


__all__ = [
    "ApprovalRequestOut",
    "RunPlanOut",
    "RunPlanReopenOut",
    "RunPlanStartOut",
    "RunPlanStepHandoffOut",
    "RunPlanStepOut",
    "RunPlanSummaryOut",
    "bounded_step_payload",
    "direct_dependency_handoffs",
    "jsonable",
    "step_action_execution_guidance",
]
