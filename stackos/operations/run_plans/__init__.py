"""Run-plan operation compatibility surface."""

from __future__ import annotations

from stackos.operations.run_plans.handlers import (
    run_plan_abort as run_plan_abort,
)
from stackos.operations.run_plans.handlers import (
    run_plan_check_consistency as run_plan_check_consistency,
)
from stackos.operations.run_plans.handlers import (
    run_plan_claim_step,
    run_plan_create,
    run_plan_get,
    run_plan_get_step,
    run_plan_list,
    run_plan_record_step,
    run_plan_recover,
    run_plan_reopen,
    run_plan_start,
    run_plan_update,
    run_plan_validate,
)
from stackos.operations.run_plans.schemas import (
    RunPlanAbortInput as RunPlanAbortInput,
)
from stackos.operations.run_plans.schemas import (
    RunPlanCheckConsistencyInput as RunPlanCheckConsistencyInput,
)
from stackos.operations.run_plans.schemas import (
    RunPlanClaimStepInput,
    RunPlanCreateInput,
    RunPlanGetInput,
    RunPlanGetStepInput,
    RunPlanListInput,
    RunPlanRecordStepInput,
    RunPlanRecoverInput,
    RunPlanReopenInput,
    RunPlanStartInput,
    RunPlanUpdateInput,
    RunPlanValidateInput,
)
from stackos.operations.run_plans.specs import operation_specs

__all__ = [
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
    "operation_specs",
    "run_plan_claim_step",
    "run_plan_create",
    "run_plan_get",
    "run_plan_get_step",
    "run_plan_list",
    "run_plan_record_step",
    "run_plan_recover",
    "run_plan_reopen",
    "run_plan_start",
    "run_plan_update",
    "run_plan_validate",
]
