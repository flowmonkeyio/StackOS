"""``run.*`` tools.

Exposes the audit-trail layer (``RunRepository`` + ``RunStepRepository``
+ ``RunStepCallRepository``).
"""

from __future__ import annotations

import secrets
from typing import Any

from pydantic import BaseModel, ConfigDict

from content_stack.db.models import RunKind, RunStatus
from content_stack.mcp.context import MCPContext
from content_stack.mcp.contract import MCPInput, WriteEnvelope
from content_stack.mcp.permissions import INVALID_SKILL, SYSTEM_SKILL, TEST_SKILL
from content_stack.mcp.server import ToolRegistry, ToolSpec
from content_stack.mcp.streaming import ProgressEmitter
from content_stack.repositories.base import Page, ValidationError
from content_stack.repositories.runs import (
    RunOut,
    RunRepository,
    RunStepCallOut,
    RunStepCallRepository,
    RunStepOut,
    RunStepRepository,
)

# ---------------------------------------------------------------------------
# run.* inputs.
# ---------------------------------------------------------------------------


class RunStartInput(MCPInput):
    """Insert a fresh run + return ``(run_id, run_token)``.

    The ``run_token`` is the run's ``client_session_id`` — every
    subsequent tool call within this run echoes the token so the server
    correlates calls to the run for audit + tool-grant enforcement
    (audit B-10).
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "kind": "skill-run",
                "metadata_json": {"stackos_type": "manual-run"},
            }
        },
    )

    project_id: int | None = None
    kind: RunKind
    parent_run_id: int | None = None
    skill_name: str | None = None
    metadata_json: dict[str, Any] | None = None


class RunStartOutput(BaseModel):
    """Wire shape for ``run.start`` — exposes ``run_token`` so the caller
    can echo it on subsequent calls."""

    run: RunOut
    run_token: str
    run_id: int


class RunFinishInput(MCPInput):
    """Move a run to a terminal status."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"example": {"run_id": 1, "status": "success"}}
    )

    run_id: int
    status: RunStatus
    error: str | None = None
    metadata_json: dict[str, Any] | None = None


class RunHeartbeatInput(MCPInput):
    """Update heartbeat_at to now."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"run_id": 1}})

    run_id: int


class RunAbortInput(MCPInput):
    """Abort a run, optionally cascading to children."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"example": {"run_id": 1, "cascade": True}}
    )

    run_id: int
    cascade: bool = False


class RunListInput(MCPInput):
    """List runs with filters."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int | None = None
    kind: RunKind | None = None
    status: RunStatus | None = None
    parent_run_id: int | None = None
    limit: int | None = None
    after_id: int | None = None


class RunGetInput(MCPInput):
    """Fetch one run by id."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"run_id": 1}})

    run_id: int


class RunChildrenInput(MCPInput):
    """List direct children of a parent run."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"parent_run_id": 1}})

    parent_run_id: int


class RunCostInput(MCPInput):
    """Sum cost_cents per run kind for a project + month."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"example": {"project_id": 1, "month": "2026-05"}}
    )

    project_id: int
    month: str | None = None


# ---------------------------------------------------------------------------
# run.* handlers.
# ---------------------------------------------------------------------------


def _generate_run_token() -> str:
    """Return a 32-byte URL-safe token used as ``runs.client_session_id``."""
    return secrets.token_urlsafe(32)


async def _run_start(
    inp: RunStartInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[RunStartOutput]:
    """Open a new run row + return a fresh ``run_token``.

    The run's ``client_session_id`` IS the token — we mint a
    cryptographically-random URL-safe value here so every run has a
    distinct correlation handle. The ``metadata_json`` carries the
    skill name when caller specifies one, so ``permissions.resolve_run_token``
    can resolve the token back to a skill on subsequent calls.
    """
    token = _generate_run_token()
    metadata = dict(inp.metadata_json or {})
    if inp.skill_name is not None:
        if inp.skill_name in {SYSTEM_SKILL, TEST_SKILL, INVALID_SKILL}:
            raise ValidationError(
                "reserved skill_name cannot be bound to a run",
                data={"skill_name": inp.skill_name},
            )
        metadata["skill_name"] = inp.skill_name
    env = RunRepository(ctx.session).start(
        project_id=inp.project_id,
        kind=inp.kind,
        parent_run_id=inp.parent_run_id,
        client_session_id=token,
        metadata_json=metadata if metadata else None,
    )
    assert env.data.id is not None
    payload = RunStartOutput(run=env.data, run_token=token, run_id=env.data.id)
    return WriteEnvelope[RunStartOutput](
        data=payload, run_id=env.data.id, project_id=env.project_id
    )


async def _run_finish(
    inp: RunFinishInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[RunOut]:
    env = RunRepository(ctx.session).finish(
        inp.run_id, status=inp.status, error=inp.error, metadata_json=inp.metadata_json
    )
    return WriteEnvelope[RunOut](data=env.data, run_id=env.run_id, project_id=env.project_id)


async def _run_heartbeat(
    inp: RunHeartbeatInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[RunOut | None]:
    env = RunRepository(ctx.session).heartbeat(inp.run_id)
    return WriteEnvelope[RunOut | None](data=env.data, run_id=env.run_id, project_id=env.project_id)


async def _run_abort(
    inp: RunAbortInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[RunOut]:
    env = RunRepository(ctx.session).abort(inp.run_id, cascade=inp.cascade)
    return WriteEnvelope[RunOut](data=env.data, run_id=env.run_id, project_id=env.project_id)


async def _run_list(inp: RunListInput, ctx: MCPContext, _emit: ProgressEmitter) -> Page[RunOut]:
    return RunRepository(ctx.session).list(
        project_id=inp.project_id,
        kind=inp.kind,
        status=inp.status,
        parent_run_id=inp.parent_run_id,
        limit=inp.limit,
        after_id=inp.after_id,
    )


async def _run_get(inp: RunGetInput, ctx: MCPContext, _emit: ProgressEmitter) -> RunOut:
    return RunRepository(ctx.session).get(inp.run_id)


async def _run_children(
    inp: RunChildrenInput, ctx: MCPContext, _emit: ProgressEmitter
) -> list[RunOut]:
    return RunRepository(ctx.session).children(inp.parent_run_id)


async def _run_cost(inp: RunCostInput, ctx: MCPContext, _emit: ProgressEmitter) -> dict[str, Any]:
    return RunRepository(ctx.session).cost(inp.project_id, month=inp.month)


# ---------------------------------------------------------------------------
# Run-step audit-grain tools.
# ---------------------------------------------------------------------------


class RunStepInsertInput(MCPInput):
    """Insert a ``run_steps`` row for run-plan or skill-level audit detail."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"run_id": 1, "step_index": 0, "skill_name": "outline"}},
    )

    run_id: int
    step_index: int
    skill_name: str
    input_snapshot_json: dict[str, Any] | None = None


class RunStepListInput(MCPInput):
    """List per-skill steps for a run."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"run_id": 1}})

    run_id: int


class RunStepCallRecordInput(MCPInput):
    """Record a per-MCP-tool call inside a run step."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"run_step_id": 1, "mcp_tool": "resource.get"}},
    )

    run_step_id: int
    mcp_tool: str
    request_json: dict[str, Any] | None = None
    response_json: dict[str, Any] | None = None
    duration_ms: int | None = None
    error: str | None = None
    cost_cents: int = 0


class RunStepCallListInput(MCPInput):
    """List recorded MCP-tool calls for a run step."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"run_step_id": 1}})

    run_step_id: int


async def _run_step_insert(
    inp: RunStepInsertInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[RunStepOut]:
    env = RunStepRepository(ctx.session).insert_step(
        run_id=inp.run_id,
        step_index=inp.step_index,
        skill_name=inp.skill_name,
        input_snapshot_json=inp.input_snapshot_json,
    )
    return WriteEnvelope[RunStepOut](data=env.data, run_id=env.run_id, project_id=env.project_id)


async def _run_step_list(
    inp: RunStepListInput, ctx: MCPContext, _emit: ProgressEmitter
) -> list[RunStepOut]:
    return RunStepRepository(ctx.session).list_steps(inp.run_id)


async def _run_step_call_record(
    inp: RunStepCallRecordInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[RunStepCallOut]:
    env = RunStepCallRepository(ctx.session).record_call(
        run_step_id=inp.run_step_id,
        mcp_tool=inp.mcp_tool,
        request_json=inp.request_json,
        response_json=inp.response_json,
        duration_ms=inp.duration_ms,
        error=inp.error,
        cost_cents=inp.cost_cents,
    )
    return WriteEnvelope[RunStepCallOut](
        data=env.data, run_id=env.run_id, project_id=env.project_id
    )


async def _run_step_call_list(
    inp: RunStepCallListInput, ctx: MCPContext, _emit: ProgressEmitter
) -> list[RunStepCallOut]:
    return RunStepCallRepository(ctx.session).list(inp.run_step_id)


# ---------------------------------------------------------------------------
# Registration.
# ---------------------------------------------------------------------------


def register(registry: ToolRegistry) -> None:
    """Register every run.* tool."""
    # run.*
    registry.register(
        ToolSpec(
            "run.start",
            "Open a run; returns (run_id, run_token).",
            RunStartInput,
            WriteEnvelope[RunStartOutput],
            _run_start,
        )
    )
    registry.register(
        ToolSpec(
            "run.finish",
            "Move a run to a terminal status.",
            RunFinishInput,
            WriteEnvelope[RunOut],
            _run_finish,
        )
    )
    registry.register(
        ToolSpec(
            "run.heartbeat",
            "Update heartbeat_at to now.",
            RunHeartbeatInput,
            WriteEnvelope[RunOut | None],
            _run_heartbeat,
        )
    )
    registry.register(
        ToolSpec(
            "run.abort",
            "Abort a run (optionally cascading to children).",
            RunAbortInput,
            WriteEnvelope[RunOut],
            _run_abort,
        )
    )
    registry.register(
        ToolSpec("run.list", "List runs with filters.", RunListInput, Page[RunOut], _run_list)
    )
    registry.register(ToolSpec("run.get", "Fetch one run by id.", RunGetInput, RunOut, _run_get))
    registry.register(
        ToolSpec(
            "run.children",
            "List direct children of a parent run.",
            RunChildrenInput,
            list[RunOut],
            _run_children,
        )
    )
    registry.register(
        ToolSpec(
            "run.cost",
            "Sum cost_cents per run kind for a project + month.",
            RunCostInput,
            dict[str, Any],
            _run_cost,
        )
    )
    # Run-step audit grain.
    registry.register(
        ToolSpec(
            "run.insertStep",
            "Insert a per-skill run_step row.",
            RunStepInsertInput,
            WriteEnvelope[RunStepOut],
            _run_step_insert,
        )
    )
    registry.register(
        ToolSpec(
            "run.listSteps",
            "List per-skill steps for a run.",
            RunStepListInput,
            list[RunStepOut],
            _run_step_list,
        )
    )
    registry.register(
        ToolSpec(
            "run.recordStepCall",
            "Record a per-MCP-tool call inside a run step.",
            RunStepCallRecordInput,
            WriteEnvelope[RunStepCallOut],
            _run_step_call_record,
        )
    )
    registry.register(
        ToolSpec(
            "run.listStepCalls",
            "List recorded MCP-tool calls for a run step.",
            RunStepCallListInput,
            list[RunStepCallOut],
            _run_step_call_list,
        )
    )


__all__ = ["register"]
