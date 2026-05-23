"""StackOS project-memory MCP tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field

from stackos.context import (
    ContextPageOut,
    ContextQueryOut,
    ContextRepository,
    ContextSnapshotOut,
    DecisionOut,
    ExperimentObservationOut,
    ExperimentOut,
    LearningOut,
)
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.server import ToolRegistry, ToolSpec
from stackos.mcp.streaming import ProgressEmitter


class ContextQueryInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "sources": ["learnings"]}},
    )

    project_id: int
    sources: list[str] | None = None
    fields: list[str] | None = None
    limit: int | None = Field(default=None, ge=1)
    tags: list[str] | None = None
    domain: str | None = None
    statuses: list[str] | None = None


class ContextTimelineInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int
    event_type: str | None = None
    fields: list[str] | None = None
    limit: int | None = None
    after_id: int | None = None


class ContextSnapshotInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int
    run_id: int | None = None
    name: str | None = None
    query_json: dict[str, Any] | None = None
    selected_sources_json: list[dict[str, Any]] | None = None
    summary_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None


class LearningQueryInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int
    fields: list[str] | None = None
    domain: str | None = None
    status: str | None = None
    review_state: str | None = None
    tags: list[str] | None = None
    limit: int | None = None
    after_id: int | None = None


class LearningCreateInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int
    statement: str
    domain: str | None = None
    confidence: str = "unknown"
    status: str = "active"
    review_state: str = "proposed"
    created_by: str | None = None
    tags: list[str] | None = None
    applies_to_json: dict[str, Any] | None = None
    evidence_json: dict[str, Any] | None = None
    source_snapshot_id: int | None = None
    supersedes_learning_id: int | None = None
    metadata_json: dict[str, Any] | None = None


class LearningUpdateInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"learning_id": 1}})

    project_id: int
    learning_id: int
    statement: str | None = None
    domain: str | None = None
    confidence: str | None = None
    status: str | None = None
    review_state: str | None = None
    tags: list[str] | None = None
    applies_to_json: dict[str, Any] | None = None
    evidence_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None


class ExperimentQueryInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int
    fields: list[str] | None = None
    domain: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    limit: int | None = None
    after_id: int | None = None


class ExperimentCreateInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int
    hypothesis: str
    key: str | None = None
    name: str | None = None
    domain: str | None = None
    status: str = "planned"
    linked_template_ids_json: list[str] | None = None
    linked_run_ids_json: list[int] | None = None
    metric_targets_json: dict[str, Any] | None = None
    decision_policy_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    variants: list[dict[str, Any]] | None = None


class ExperimentObservationInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"experiment_id": 1}})

    project_id: int
    experiment_id: int
    metrics_json: dict[str, Any]
    variant_key: str | None = None
    run_id: int | None = None
    summary: str | None = None
    observed_at: datetime | None = None
    metadata_json: dict[str, Any] | None = None


class ExperimentDecisionInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"experiment_id": 1}})

    project_id: int
    experiment_id: int
    decision: str
    title: str | None = None
    rationale: str | None = None
    status: str = "recorded"
    decided_by: str | None = None
    tags: list[str] | None = None
    evidence_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    run_id: int | None = None
    experiment_status: str | None = None


class DecisionQueryInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int
    fields: list[str] | None = None
    experiment_id: int | None = None
    status: str | None = None
    tags: list[str] | None = None
    limit: int | None = None
    after_id: int | None = None


class DecisionRecordInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int
    decision: str
    title: str | None = None
    rationale: str | None = None
    status: str = "recorded"
    decided_by: str | None = None
    tags: list[str] | None = None
    evidence_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    run_id: int | None = None
    experiment_id: int | None = None


async def _context_query(
    inp: ContextQueryInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ContextQueryOut:
    return ContextRepository(ctx.session).query_context(
        project_id=inp.project_id,
        sources=inp.sources,
        fields=inp.fields,
        limit=inp.limit,
        tags=inp.tags,
        domain=inp.domain,
        statuses=inp.statuses,
    )


async def _context_timeline(
    inp: ContextTimelineInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ContextPageOut:
    return ContextRepository(ctx.session).query_event_context(
        project_id=inp.project_id,
        fields=inp.fields,
        event_type=inp.event_type,
        limit=inp.limit,
        after_id=inp.after_id,
    )


async def _context_snapshot(
    inp: ContextSnapshotInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[ContextSnapshotOut]:
    env = ContextRepository(ctx.session).create_snapshot(
        project_id=inp.project_id,
        run_id=inp.run_id,
        name=inp.name,
        query_json=inp.query_json,
        selected_sources_json=inp.selected_sources_json,
        summary_json=inp.summary_json,
        metadata_json=inp.metadata_json,
    )
    return WriteEnvelope[ContextSnapshotOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def _learning_query(
    inp: LearningQueryInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ContextPageOut:
    return ContextRepository(ctx.session).query_learning_context(
        project_id=inp.project_id,
        fields=inp.fields,
        domain=inp.domain,
        status=inp.status,
        review_state=inp.review_state,
        tags=inp.tags,
        limit=inp.limit,
        after_id=inp.after_id,
    )


async def _learning_create(
    inp: LearningCreateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[LearningOut]:
    env = ContextRepository(ctx.session).create_learning(
        project_id=inp.project_id,
        statement=inp.statement,
        domain=inp.domain,
        confidence=inp.confidence,
        status=inp.status,
        review_state=inp.review_state,
        created_by=inp.created_by,
        tags=inp.tags,
        applies_to_json=inp.applies_to_json,
        evidence_json=inp.evidence_json,
        source_snapshot_id=inp.source_snapshot_id,
        supersedes_learning_id=inp.supersedes_learning_id,
        metadata_json=inp.metadata_json,
    )
    return WriteEnvelope[LearningOut](data=env.data, run_id=ctx.run_id, project_id=env.project_id)


async def _learning_update(
    inp: LearningUpdateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[LearningOut]:
    env = ContextRepository(ctx.session).update_learning(
        project_id=inp.project_id,
        learning_id=inp.learning_id,
        statement=inp.statement,
        domain=inp.domain,
        confidence=inp.confidence,
        status=inp.status,
        review_state=inp.review_state,
        tags=inp.tags,
        applies_to_json=inp.applies_to_json,
        evidence_json=inp.evidence_json,
        metadata_json=inp.metadata_json,
    )
    return WriteEnvelope[LearningOut](data=env.data, run_id=ctx.run_id, project_id=env.project_id)


async def _experiment_query(
    inp: ExperimentQueryInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ContextPageOut:
    return ContextRepository(ctx.session).query_experiment_context(
        project_id=inp.project_id,
        fields=inp.fields,
        domain=inp.domain,
        status=inp.status,
        tags=inp.tags,
        limit=inp.limit,
        after_id=inp.after_id,
    )


async def _experiment_create(
    inp: ExperimentCreateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[ExperimentOut]:
    env = ContextRepository(ctx.session).create_experiment(
        project_id=inp.project_id,
        hypothesis=inp.hypothesis,
        key=inp.key,
        name=inp.name,
        domain=inp.domain,
        status=inp.status,
        linked_template_ids_json=inp.linked_template_ids_json,
        linked_run_ids_json=inp.linked_run_ids_json,
        metric_targets_json=inp.metric_targets_json,
        decision_policy_json=inp.decision_policy_json,
        metadata_json=inp.metadata_json,
        variants=inp.variants,
    )
    return WriteEnvelope[ExperimentOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def _experiment_record_observation(
    inp: ExperimentObservationInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[ExperimentObservationOut]:
    env = ContextRepository(ctx.session).record_observation(
        project_id=inp.project_id,
        experiment_id=inp.experiment_id,
        metrics_json=inp.metrics_json,
        variant_key=inp.variant_key,
        run_id=inp.run_id,
        summary=inp.summary,
        observed_at=inp.observed_at,
        metadata_json=inp.metadata_json,
    )
    return WriteEnvelope[ExperimentObservationOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def _experiment_record_decision(
    inp: ExperimentDecisionInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[DecisionOut]:
    env = ContextRepository(ctx.session).record_experiment_decision(
        project_id=inp.project_id,
        experiment_id=inp.experiment_id,
        decision=inp.decision,
        title=inp.title,
        rationale=inp.rationale,
        status=inp.status,
        decided_by=inp.decided_by,
        tags=inp.tags,
        evidence_json=inp.evidence_json,
        metadata_json=inp.metadata_json,
        run_id=inp.run_id,
        experiment_status=inp.experiment_status,
    )
    return WriteEnvelope[DecisionOut](data=env.data, run_id=ctx.run_id, project_id=env.project_id)


async def _decision_query(
    inp: DecisionQueryInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ContextPageOut:
    return ContextRepository(ctx.session).query_decision_context(
        project_id=inp.project_id,
        fields=inp.fields,
        experiment_id=inp.experiment_id,
        status=inp.status,
        tags=inp.tags,
        limit=inp.limit,
        after_id=inp.after_id,
    )


async def _decision_record(
    inp: DecisionRecordInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[DecisionOut]:
    env = ContextRepository(ctx.session).record_decision(
        project_id=inp.project_id,
        decision=inp.decision,
        title=inp.title,
        rationale=inp.rationale,
        status=inp.status,
        decided_by=inp.decided_by,
        tags=inp.tags,
        evidence_json=inp.evidence_json,
        metadata_json=inp.metadata_json,
        run_id=inp.run_id,
        experiment_id=inp.experiment_id,
    )
    return WriteEnvelope[DecisionOut](data=env.data, run_id=ctx.run_id, project_id=env.project_id)


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            "context.query",
            "Query bounded, projected, sanitized project context.",
            ContextQueryInput,
            ContextQueryOut,
            _context_query,
        )
    )
    registry.register(
        ToolSpec(
            "context.timeline",
            "Read the project memory event timeline.",
            ContextTimelineInput,
            ContextPageOut,
            _context_timeline,
        )
    )
    registry.register(
        ToolSpec(
            "context.snapshot",
            "Internal/admin creation of an immutable context snapshot.",
            ContextSnapshotInput,
            WriteEnvelope[ContextSnapshotOut],
            _context_snapshot,
        )
    )
    registry.register(
        ToolSpec(
            "learning.query",
            "Query project learnings without deciding which are true.",
            LearningQueryInput,
            ContextPageOut,
            _learning_query,
        )
    )
    registry.register(
        ToolSpec(
            "learning.create",
            "Internal/admin record of a supplied project learning.",
            LearningCreateInput,
            WriteEnvelope[LearningOut],
            _learning_create,
        )
    )
    registry.register(
        ToolSpec(
            "learning.update",
            "Internal/admin update of supplied learning status/review data.",
            LearningUpdateInput,
            WriteEnvelope[LearningOut],
            _learning_update,
        )
    )
    registry.register(
        ToolSpec(
            "experiment.query",
            "Query project experiments without deciding winners.",
            ExperimentQueryInput,
            ContextPageOut,
            _experiment_query,
        )
    )
    registry.register(
        ToolSpec(
            "experiment.create",
            "Internal/admin create of a supplied project experiment.",
            ExperimentCreateInput,
            WriteEnvelope[ExperimentOut],
            _experiment_create,
        )
    )
    registry.register(
        ToolSpec(
            "experiment.recordObservation",
            "Internal/admin record of supplied experiment observation data.",
            ExperimentObservationInput,
            WriteEnvelope[ExperimentObservationOut],
            _experiment_record_observation,
        )
    )
    registry.register(
        ToolSpec(
            "experiment.recordDecision",
            "Internal/admin record of an explicit experiment decision.",
            ExperimentDecisionInput,
            WriteEnvelope[DecisionOut],
            _experiment_record_decision,
        )
    )
    registry.register(
        ToolSpec(
            "decision.query",
            "Query explicit project decision records.",
            DecisionQueryInput,
            ContextPageOut,
            _decision_query,
        )
    )
    registry.register(
        ToolSpec(
            "decision.record",
            "Internal/admin record of an explicit decision.",
            DecisionRecordInput,
            WriteEnvelope[DecisionOut],
            _decision_record,
        )
    )


__all__ = ["register"]
