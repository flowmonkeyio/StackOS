"""``cost.*`` tools — per-project + cross-project cost aggregation.

Aggregates ``run_steps.cost_cents`` per ``runs.kind`` for a given month.
M3 returns whatever's accumulated (zeros if no integration calls have
been recorded yet).
"""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict
from sqlmodel import select

from stackos.db.models import Project
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput
from stackos.mcp.server import ToolRegistry
from stackos.mcp.streaming import ProgressEmitter
from stackos.repositories.runs import RunRepository

# ---------------------------------------------------------------------------
# Inputs.
# ---------------------------------------------------------------------------


class CostQueryProjectInput(MCPInput):
    """Sum cost_cents per kind for a project + month."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"example": {"project_id": 1, "month": "2026-05"}}
    )

    project_id: int
    month: str | None = None


class CostQueryAllInput(MCPInput):
    """Sum cost_cents across every project for a month."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"month": "2026-05"}})

    month: str | None = None


# ---------------------------------------------------------------------------
# Handlers.
# ---------------------------------------------------------------------------


async def _cost_query_project(
    inp: CostQueryProjectInput, ctx: MCPContext, _emit: ProgressEmitter
) -> dict[str, Any]:
    return RunRepository(ctx.session).cost(inp.project_id, month=inp.month)


async def _cost_query_all(
    inp: CostQueryAllInput, ctx: MCPContext, _emit: ProgressEmitter
) -> dict[str, Any]:
    """Roll up costs across every project for ``month``.

    Mirrors ``GET /api/v1/cost?month=`` (PLAN.md L778). We iterate each
    project and combine the per-project envelopes; M9 may switch to a
    SQL-side group-by once query volume justifies it, but at M3 the
    project count is small enough that the loop is fine.
    """
    repo = RunRepository(ctx.session)
    projects = ctx.session.exec(select(Project)).all()
    by_project: dict[int, dict[str, Any]] = {}
    grand_total_cents = 0
    aggregated_by_kind: dict[str, int] = {}
    sample_period: dict[str, str] = {}
    for p in projects:
        if p.id is None:  # pragma: no cover — defensive
            continue
        per = repo.cost(p.id, month=inp.month)
        by_project[p.id] = per
        grand_total_cents += int(per["total_cents"])
        for kind, cents in per["by_kind_cents"].items():
            aggregated_by_kind[kind] = aggregated_by_kind.get(kind, 0) + int(cents)
        sample_period = {
            "period_start": per["period_start"],
            "period_end": per["period_end"],
            "month": per["month"],
        }
    return {
        "month": (sample_period.get("month") or inp.month),
        "period_start": sample_period.get("period_start"),
        "period_end": sample_period.get("period_end"),
        "grand_total_cents": grand_total_cents,
        "by_kind_cents": aggregated_by_kind,
        "by_project": by_project,
    }


# ---------------------------------------------------------------------------
# Registration.
# ---------------------------------------------------------------------------


def register(registry: ToolRegistry) -> None:
    """Register cost.* tools."""
    from stackos.operations.adapters.mcp import register_mcp_operation_names

    register_mcp_operation_names(registry, ("cost.queryProject", "cost.queryAll"))


__all__ = ["register"]
