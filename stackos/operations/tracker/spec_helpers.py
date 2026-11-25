"""Shared operation spec builders for tracker operations."""

from __future__ import annotations

from typing import Any

from stackos.mcp.contract import (
    MCPInput,
    WriteEnvelope,
)
from stackos.operations.spec import (
    OperationExample,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.tracker import TrackerMutationOut


def _surfaces(name: str, command: str | None = None) -> OperationSurfaces:
    alias_commands = {
        "tracker.status": "tracker status",
        "tracker.get": "tracker get",
        "tracker.next": "tracker next",
        "tracker.brief": "tracker brief",
        "tracker.verify": "tracker verify",
        "tracker.createTask": "tracker create-task",
        "tracker.createTicket": "tracker create-ticket",
        "tracker.patch": "tracker patch",
        "tracker.pick": "tracker pick",
        "tracker.rejectTask": "tracker reject-task",
        "tracker.reopen": "tracker reopen",
        "tracker.updateTask": "ops call tracker.updateTask",
        "tracker.updateTicket": "ops call tracker.updateTicket",
        "tracker.linkRunPlan": "ops call tracker.linkRunPlan",
    }
    return OperationSurfaces(
        mcp=OperationSurface(enabled=True),
        rest=OperationSurface(enabled=True, path=f"/api/v1/operations/{name}/call"),
        cli=OperationSurface(
            enabled=True,
            command=command or alias_commands.get(name) or f"ops call {name}",
        ),
    )


def _read_spec(
    *,
    name: str,
    summary: str,
    input_model: type[MCPInput],
    output_model: Any,
    handler: Any,
    purpose: str,
    examples: tuple[OperationExample, ...] = (),
) -> OperationSpec:
    return OperationSpec(
        name=name,
        summary=summary,
        input_model=input_model,
        output_model=output_model,
        handler=handler,
        surfaces=_surfaces(name),
        purpose=purpose,
        when_to_use=(
            "Use this when an agent needs bounded project work context without "
            "scanning run plans, history, or resources manually.",
        ),
        prerequisites=("Pass the project_id resolved for the current workspace.",),
        returns=("A compact tracker read model with no secrets.",),
        examples=examples,
        mutating=False,
        grant_policy="direct-read",
    )


def _write_spec(
    *,
    name: str,
    summary: str,
    input_model: type[MCPInput],
    handler: Any,
    purpose: str,
    examples: tuple[OperationExample, ...] = (),
    output_model: Any = WriteEnvelope[TrackerMutationOut],
) -> OperationSpec:
    return OperationSpec(
        name=name,
        summary=summary,
        input_model=input_model,
        output_model=output_model,
        handler=handler,
        surfaces=_surfaces(name),
        purpose=purpose,
        when_to_use=(
            "Use this when the agent has decided how tracker state should change.",
            "Do not put secrets in patch_json, metadata_json, context_json, "
            "completion_evidence_json, or references.",
        ),
        prerequisites=("Pass project_id and stable task/ticket keys or ticket ids.",),
        returns=("A WriteEnvelope with compact mutation output and the new tracker revision.",),
        examples=examples,
        mutating=True,
        grant_policy="direct-tracker-write",
    )


__all__ = [
    "_read_spec",
    "_surfaces",
    "_write_spec",
]
