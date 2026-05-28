"""Run audit operation contracts."""

from __future__ import annotations

from typing import Any

from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.tools.runs import (
    RunAbortInput,
    RunChildrenInput,
    RunCostInput,
    RunFinishInput,
    RunGetInput,
    RunHeartbeatInput,
    RunListInput,
    RunStartInput,
    RunStartOutput,
    RunStepCallListInput,
    RunStepCallRecordInput,
    RunStepInsertInput,
    RunStepListInput,
    _run_abort,
    _run_children,
    _run_cost,
    _run_finish,
    _run_get,
    _run_heartbeat,
    _run_list,
    _run_start,
    _run_step_call_list,
    _run_step_call_record,
    _run_step_insert,
    _run_step_list,
)
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import OperationExample
from stackos.repositories.base import Page
from stackos.repositories.runs import RunOut, RunStepCallOut, RunStepOut


def operation_specs():
    return [
        operation_spec(
            name="run.start",
            summary="Open an audited run and return its run token.",
            input_model=RunStartInput,
            output_model=WriteEnvelope[RunStartOutput],
            handler=_run_start,
            purpose=(
                "Use this for low-level audited execution. Workflow execution normally uses "
                "runPlan.start, which creates and binds the run plan run automatically."
            ),
            when_to_use=("A non-workflow tool flow still needs an audited run boundary.",),
            returns=("A write envelope containing run_id and run_token.",),
            examples=(
                OperationExample(
                    title="Start manual run",
                    arguments={
                        "project_id": 1,
                        "kind": "skill-run",
                        "metadata_json": {"stackos_type": "manual-run"},
                    },
                ),
            ),
            mutating=True,
            grant_policy="direct-run-audit-write",
        ),
        operation_spec(
            name="run.finish",
            summary="Move an audited run to a terminal status.",
            input_model=RunFinishInput,
            output_model=WriteEnvelope[RunOut],
            handler=_run_finish,
            purpose="Use this to close a low-level audited run.",
            examples=(
                OperationExample(title="Finish run", arguments={"run_id": 1, "status": "success"}),
            ),
            mutating=True,
            grant_policy="direct-run-audit-write",
        ),
        operation_spec(
            name="run.heartbeat",
            summary="Refresh heartbeat for an audited run.",
            input_model=RunHeartbeatInput,
            output_model=WriteEnvelope[RunOut | None],
            handler=_run_heartbeat,
            purpose="Use this when a long-running audited process needs liveness.",
            examples=(OperationExample(title="Heartbeat run", arguments={"run_id": 1}),),
            mutating=True,
            grant_policy="direct-run-audit-write",
        ),
        operation_spec(
            name="run.abort",
            summary="Abort an audited run.",
            input_model=RunAbortInput,
            output_model=WriteEnvelope[RunOut],
            handler=_run_abort,
            purpose="Use this to explicitly stop a low-level audited run.",
            examples=(
                OperationExample(title="Abort run", arguments={"run_id": 1, "cascade": True}),
            ),
            mutating=True,
            grant_policy="direct-run-audit-write",
        ),
        operation_spec(
            name="run.list",
            summary="List audited runs.",
            input_model=RunListInput,
            output_model=Page[RunOut],
            handler=_run_list,
            purpose="Use this to inspect recent audited activity.",
            examples=(OperationExample(title="List runs", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="run.get",
            summary="Fetch one audited run.",
            input_model=RunGetInput,
            output_model=RunOut,
            handler=_run_get,
            purpose="Use this to inspect run metadata, status, and project scope.",
            examples=(OperationExample(title="Get run", arguments={"run_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="run.children",
            summary="List direct child runs.",
            input_model=RunChildrenInput,
            output_model=list[RunOut],
            handler=_run_children,
            purpose="Use this to navigate parent/child audited run structure.",
            examples=(OperationExample(title="List child runs", arguments={"parent_run_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="run.cost",
            summary="Sum run costs for one project and month.",
            input_model=RunCostInput,
            output_model=dict[str, Any],
            handler=_run_cost,
            purpose="Use this to inspect audited run spend by kind.",
            examples=(OperationExample(title="Read run cost", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="run.insertStep",
            summary="Insert a low-level run step audit row.",
            input_model=RunStepInsertInput,
            output_model=WriteEnvelope[RunStepOut],
            handler=_run_step_insert,
            purpose="Use this only for low-level audited execution outside run plans.",
            prerequisites=("Prefer runPlan.claimStep/recordStep for workflow execution.",),
            mutating=True,
            grant_policy="direct-run-audit-write",
        ),
        operation_spec(
            name="run.listSteps",
            summary="List low-level run step audit rows.",
            input_model=RunStepListInput,
            output_model=list[RunStepOut],
            handler=_run_step_list,
            purpose="Use this to inspect audit detail for one run.",
            examples=(OperationExample(title="List run steps", arguments={"run_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="run.recordStepCall",
            summary="Record one low-level MCP/tool call audit row.",
            input_model=RunStepCallRecordInput,
            output_model=WriteEnvelope[RunStepCallOut],
            handler=_run_step_call_record,
            purpose="Use this only for low-level audited execution outside run plans.",
            prerequisites=("Prefer run-plan step audit for workflow execution.",),
            mutating=True,
            grant_policy="direct-run-audit-write",
        ),
        operation_spec(
            name="run.listStepCalls",
            summary="List low-level tool-call audit rows for one run step.",
            input_model=RunStepCallListInput,
            output_model=list[RunStepCallOut],
            handler=_run_step_call_list,
            purpose="Use this to inspect recorded tool calls for a run step.",
            examples=(OperationExample(title="List step calls", arguments={"run_step_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
    ]


__all__ = ["operation_specs"]
