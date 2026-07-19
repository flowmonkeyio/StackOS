"""Operation registry entry for scoped readiness checks."""

from stackos.operations._helpers import operation_spec
from stackos.operations.spec import OperationExample, OperationSpec

from .action import readiness_check
from .schemas import ReadinessCheckInput, ReadinessCheckOut


def operation_specs() -> list[OperationSpec]:
    return [
        operation_spec(
            name="readiness.check",
            summary="Check scoped workflow or action readiness without broad setup noise.",
            input_model=ReadinessCheckInput,
            output_model=ReadinessCheckOut,
            handler=readiness_check,
            purpose=(
                "Use this when an agent needs to know whether one workflow or action can be "
                "executed now, and exactly which credentials, budgets, connectors, or setup "
                "items are missing for that scope."
            ),
            when_to_use=(
                "Before telling the operator credentials are missing for a selected workflow.",
                "Before broad auth.status when the agent already knows the workflow or action.",
                "Before runPlan.create/start when provider setup might block execution.",
            ),
            prerequisites=(
                "Pass exactly one workflow_key or action_ref/plugin_slug/action_key.",
                "Use workflow readiness for setup; use action readiness for one explicit action.",
                (
                    "Do not treat global auth.status gaps as blockers until readiness.check says "
                    "the selected scope needs them."
                ),
            ),
            returns=(
                (
                    "structurally_ready, context_status, required_providers_ready, and "
                    "execution_ready for the selected scope. There is no ambiguous "
                    "catch-all ready alias."
                ),
                (
                    "Only blocking missing items at the top level, with optional and "
                    "unselected route gaps retained on their action/route details."
                ),
                "Required and optional choose-one route groups when providers are alternatives.",
                (
                    "Workflow responses still allow planning/runPlan.create when provider "
                    "execution setup is incomplete."
                ),
            ),
            examples=(
                OperationExample(
                    title="Check a workflow before setup",
                    arguments={"project_id": 1, "workflow_key": "engineering.tracked-delivery"},
                ),
                OperationExample(
                    title="Check one action",
                    arguments={"project_id": 1, "action_ref": "utils.image.generate"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
            category="setup",
        )
    ]
