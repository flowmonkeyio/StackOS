"""Meta lookup for StackOS core enum values."""

from __future__ import annotations

import enum
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from stackos.db.models import (
    ACTION_CALL_STATUS_TRANSITIONS,
    APPROVAL_REQUEST_STATUS_TRANSITIONS,
    RUN_PLAN_STATUS_TRANSITIONS,
    RUN_PLAN_STEP_STATUS_TRANSITIONS,
    RUN_STATUS_TRANSITIONS,
    ActionCallStatus,
    ApprovalRequestStatus,
    PluginSource,
    RunKind,
    RunPlanStatus,
    RunPlanStepStatus,
    RunStatus,
    RunStepStatus,
)


def _values(cls: type[enum.Enum]) -> list[str]:
    return [m.value for m in cls]


def _transitions(transitions: dict[Any, frozenset[Any]]) -> dict[str, list[str]]:
    return {k.value: sorted(v.value for v in vs) for k, vs in transitions.items()}


class EnumLookupResponse(BaseModel):
    """Core enum values and lifecycle transition maps."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "runs_status": ["running", "success", "failed", "aborted"],
                "runs_kind": ["run-plan", "skill-run", "action", "scheduled-job", "maintenance"],
                "allowed_transitions": {
                    "runs": {"running": ["aborted", "failed", "success"]},
                },
            }
        }
    )

    runs_status: list[str]
    runs_kind: list[str]
    run_steps_status: list[str]
    run_plans_status: list[str]
    run_plan_steps_status: list[str]
    approval_requests_status: list[str]
    action_calls_status: list[str]
    plugins_source: list[str]
    allowed_transitions: dict[str, dict[str, list[str]]]


router = APIRouter(prefix="/api/v1", tags=["meta"])


@router.get("/meta/enums", response_model=EnumLookupResponse)
async def get_meta_enums() -> EnumLookupResponse:
    return EnumLookupResponse(
        runs_status=_values(RunStatus),
        runs_kind=_values(RunKind),
        run_steps_status=_values(RunStepStatus),
        run_plans_status=_values(RunPlanStatus),
        run_plan_steps_status=_values(RunPlanStepStatus),
        approval_requests_status=_values(ApprovalRequestStatus),
        action_calls_status=_values(ActionCallStatus),
        plugins_source=_values(PluginSource),
        allowed_transitions={
            "runs": _transitions(RUN_STATUS_TRANSITIONS),
            "run_plans": _transitions(RUN_PLAN_STATUS_TRANSITIONS),
            "run_plan_steps": _transitions(RUN_PLAN_STEP_STATUS_TRANSITIONS),
            "approval_requests": _transitions(APPROVAL_REQUEST_STATUS_TRANSITIONS),
            "action_calls": _transitions(ACTION_CALL_STATUS_TRANSITIONS),
        },
    )


__all__ = ["EnumLookupResponse", "router"]
