"""Cost aggregation operation contracts."""

from __future__ import annotations

from typing import Any

from stackos.mcp.tools.cost import (
    CostQueryAllInput,
    CostQueryProjectInput,
    _cost_query_all,
    _cost_query_project,
)
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import OperationExample


def operation_specs():
    return [
        operation_spec(
            name="cost.queryProject",
            summary="Aggregate audited cost for one project and month.",
            input_model=CostQueryProjectInput,
            output_model=dict[str, Any],
            handler=_cost_query_project,
            purpose="Use this to inspect accumulated local run/action cost for one project.",
            examples=(OperationExample(title="Read project cost", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="cost.queryAll",
            summary="Aggregate audited cost across projects for one month.",
            input_model=CostQueryAllInput,
            output_model=dict[str, Any],
            handler=_cost_query_all,
            purpose="Use this for local-admin cost overview across projects.",
            examples=(OperationExample(title="Read all costs", arguments={"month": "2026-05"}),),
            mutating=False,
            grant_policy="local-admin-read",
        ),
    ]


__all__ = ["operation_specs"]
