"""Input schemas for run-plan operations."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict

from stackos.db.models import (
    ApprovalRequestStatus,
    RunPlanStatus,
    RunPlanStepStatus,
)
from stackos.mcp.contract import MCPInput


class RunPlanValidateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"workflow_key": "core.project-memory-review"}},
    )

    project_id: int | None = None
    run_plan_json: dict[str, Any] | None = None
    template_key: str | None = None
    workflow_key: str | None = None
    repo_root: str | None = None
    plugin_slug: str | None = None
    source: str | None = None
    inputs_json: dict[str, Any] | None = None
    selected_context_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    enforce_required_inputs: bool = False


class RunPlanCreateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "workflow_key": "core.project-memory-review",
                "inputs_json": {"goal": "Review recent project memory"},
            }
        },
    )

    project_id: int
    run_plan_json: dict[str, Any] | None = None
    template_key: str | None = None
    workflow_key: str | None = None
    repo_root: str | None = None
    plugin_slug: str | None = None
    source: str | None = None
    key: str | None = None
    title: str | None = None
    inputs_json: dict[str, Any] | None = None
    context_snapshot_id: int | None = None
    selected_context_json: dict[str, Any] | None = None
    created_by: str | None = None
    metadata_json: dict[str, Any] | None = None


class RunPlanStartInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "run_plan_id": 1}},
    )

    project_id: int
    run_plan_id: int


class RunPlanGetInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"run_plan_id": 1}})

    run_plan_id: int
    project_id: int | None = None


class RunPlanGetStepInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"run_plan_id": 1, "step_id": "review"}},
    )

    run_plan_id: int
    step_id: str
    project_id: int | None = None


class RunPlanCheckConsistencyInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"run_plan_id": 1}},
    )

    run_plan_id: int
    project_id: int | None = None


class RunPlanRecoverInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "run_plan_id": 1,
                "step_id": "plan-tickets",
                "step_status": "blocked",
                "reason": "Recover stale daemon/controller failure as a live blocker.",
                "error": "Recoverable controller failure needs review.",
            }
        },
    )

    run_plan_id: int
    project_id: int | None = None
    step_id: str
    step_status: RunPlanStepStatus = RunPlanStepStatus.BLOCKED
    reason: str | None = None
    actor: str | None = None
    result_json: dict[str, Any] | None = None
    error: str | None = None


class RunPlanReopenInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "run_plan_id": 27,
                "reason": (
                    "Admin selected this closed run plan as the canonical in-place recovery target."
                ),
                "actor": "stackos-admin",
            }
        },
    )

    run_plan_id: int
    project_id: int | None = None
    step_id: str | None = None
    reason: str
    actor: str | None = None


class RunPlanListInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int | None = None
    run_id: int | None = None
    status: RunPlanStatus | None = None
    template_key: str | None = None
    workflow_key: str | None = None
    limit: int | None = None
    after_id: int | None = None


class RunPlanUpdateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "run_plan_id": 1,
                "approval_key": "launch-review",
                "approval_status": "approved",
            }
        },
    )

    run_plan_id: int
    project_id: int | None = None
    metadata_json: dict[str, Any] | None = None
    approval_key: str | None = None
    approval_status: ApprovalRequestStatus | None = None
    decided_by: str | None = None
    decision_json: dict[str, Any] | None = None


class RunPlanAbortInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "run_plan_id": 1,
                "reason": "Superseded by a newer support workflow run.",
                "actor": "codex",
            }
        },
    )

    run_plan_id: int
    project_id: int | None = None
    reason: str | None = None
    actor: str | None = None


class RunPlanClaimStepInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"run_plan_id": 1, "step_id": "review"}},
    )

    run_plan_id: int
    project_id: int | None = None
    step_id: str | None = None
    claimed_by: str | None = None


class RunPlanRecordStepInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "run_plan_id": 1,
                "step_id": "review",
                "status": "success",
                "result_json": {"summary": "done"},
            }
        },
    )

    run_plan_id: int
    project_id: int | None = None
    step_id: str
    status: RunPlanStepStatus
    result_json: dict[str, Any] | None = None
    error: str | None = None


__all__ = [
    "RunPlanAbortInput",
    "RunPlanCheckConsistencyInput",
    "RunPlanClaimStepInput",
    "RunPlanCreateInput",
    "RunPlanGetInput",
    "RunPlanGetStepInput",
    "RunPlanListInput",
    "RunPlanRecordStepInput",
    "RunPlanRecoverInput",
    "RunPlanReopenInput",
    "RunPlanStartInput",
    "RunPlanUpdateInput",
    "RunPlanValidateInput",
]
