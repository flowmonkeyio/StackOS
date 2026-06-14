"""Resource, artifact, and project-memory operation contracts."""

from __future__ import annotations

from stackos.context import (
    ContextPageOut,
    ContextQueryOut,
    ContextSnapshotOut,
    DecisionOut,
    ExperimentObservationOut,
    ExperimentOut,
    LearningOut,
)
from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.tools.artifacts import (
    ArtifactArchiveInput,
    ArtifactCreateInput,
    ArtifactGetInput,
    ArtifactQueryInput,
    ArtifactSupersedeInput,
    ArtifactUpdateInput,
    _artifact_archive,
    _artifact_create,
    _artifact_get,
    _artifact_query,
    _artifact_supersede,
    _artifact_update,
)
from stackos.mcp.tools.context import (
    ContextQueryInput,
    ContextSnapshotInput,
    ContextTimelineInput,
    DecisionQueryInput,
    DecisionRecordInput,
    ExperimentCreateInput,
    ExperimentDecisionInput,
    ExperimentObservationInput,
    ExperimentQueryInput,
    LearningCreateInput,
    LearningQueryInput,
    LearningUpdateInput,
    _context_query,
    _context_snapshot,
    _context_timeline,
    _decision_query,
    _decision_record,
    _experiment_create,
    _experiment_query,
    _experiment_record_decision,
    _experiment_record_observation,
    _learning_create,
    _learning_query,
    _learning_update,
)
from stackos.mcp.tools.resources import (
    ResourceGetInput,
    ResourceQueryInput,
    ResourceUpsertInput,
    _resource_get,
    _resource_query,
    _resource_upsert,
)
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import OperationExample
from stackos.repositories.base import Page
from stackos.repositories.resources import (
    ArtifactOut,
    ResourceGetOut,
    ResourceQueryOut,
    ResourceRecordOut,
)


def _run_step_prerequisites() -> tuple[str, ...]:
    return (
        "Requires a started run plan, a claimed running step, and an explicit "
        "mcp_tool_grants entry for this tool.",
        (
            "Pass run_token through toolbox.call; the bridge injects it when the "
            "run context is cached."
        ),
    )


def operation_specs():
    return [
        operation_spec(
            name="resource.get",
            summary="Fetch a generic resource schema or resource record.",
            input_model=ResourceGetInput,
            output_model=ResourceGetOut,
            handler=_resource_get,
            purpose="Use this to inspect resource schemas or one bounded project record.",
            examples=(
                OperationExample(
                    title="Get resource schema",
                    arguments={
                        "project_id": 1,
                        "plugin_slug": "engineering",
                        "resource_key": "engineering-evidence",
                    },
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="resource.query",
            summary="Query resource schemas and bounded project records.",
            input_model=ResourceQueryInput,
            output_model=ResourceQueryOut,
            handler=_resource_query,
            purpose="Use this to read project evidence/data records without writing new state.",
            examples=(
                OperationExample(
                    title="Query engineering evidence",
                    arguments={"project_id": 1, "plugin_slug": "engineering"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="resource.upsert",
            summary="Create or update one generic project resource record.",
            input_model=ResourceUpsertInput,
            output_model=WriteEnvelope[ResourceRecordOut],
            handler=_resource_upsert,
            purpose="Use this inside a workflow step to persist explicit evidence or output data.",
            prerequisites=_run_step_prerequisites(),
            examples=(
                OperationExample(
                    title="Write evidence record",
                    arguments={
                        "project_id": 1,
                        "plugin_slug": "engineering",
                        "resource_key": "engineering-evidence",
                        "data_json": {"summary": "Tests passed"},
                    },
                ),
            ),
            mutating=True,
            grant_policy="run-plan-step-grant",
        ),
        operation_spec(
            name="artifact.get",
            summary="Fetch one generic artifact reference.",
            input_model=ArtifactGetInput,
            output_model=ArtifactOut,
            handler=_artifact_get,
            purpose="Use this to inspect an artifact pointer without reading secret data.",
            examples=(OperationExample(title="Get artifact", arguments={"artifact_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="artifact.query",
            summary="Query bounded artifact references.",
            input_model=ArtifactQueryInput,
            output_model=Page[ArtifactOut],
            handler=_artifact_query,
            purpose="Use this to list project artifacts linked to resources or workflow runs.",
            examples=(OperationExample(title="Query artifacts", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="artifact.create",
            summary="Create one intentional durable artifact reference.",
            input_model=ArtifactCreateInput,
            output_model=WriteEnvelope[ArtifactOut],
            handler=_artifact_create,
            purpose=(
                "Use this inside a workflow step only for intentional durable records: "
                "approved outputs, final packets, evidence, operator-approved drafts, "
                "or workflow outputs that should be preserved. Do not use artifacts as "
                "a default scratchpad for normal iteration."
            ),
            prerequisites=_run_step_prerequisites(),
            when_to_use=(
                "The artifact should remain queryable after the run.",
                "The workflow, operator, or test design explicitly asks for a durable artifact.",
                (
                    "The payload is approved, final, durable evidence, or an intentionally "
                    "preserved draft."
                ),
            ),
            examples=(
                OperationExample(
                    title="Record artifact",
                    arguments={
                        "project_id": 1,
                        "plugin_slug": "engineering",
                        "kind": "report",
                        "uri": "stackos://artifact/report.md",
                        "status": "approved",
                    },
                ),
            ),
            mutating=True,
            grant_policy="run-plan-step-grant",
        ),
        operation_spec(
            name="artifact.update",
            summary="Update one existing durable artifact reference.",
            input_model=ArtifactUpdateInput,
            output_model=WriteEnvelope[ArtifactOut],
            handler=_artifact_update,
            purpose=(
                "Use this when an intentionally durable artifact needs refinement, "
                "metadata correction, status change, or provenance update. Prefer updating "
                "the existing artifact over creating multiple draft artifacts for one output."
            ),
            prerequisites=_run_step_prerequisites(),
            examples=(
                OperationExample(
                    title="Mark artifact approved",
                    arguments={
                        "project_id": 1,
                        "artifact_id": 12,
                        "status": "approved",
                        "metadata_patch_json": {"review": {"approved_by": "operator"}},
                    },
                ),
            ),
            mutating=True,
            grant_policy="run-plan-step-grant",
        ),
        operation_spec(
            name="artifact.archive",
            summary="Archive one artifact without hard deleting audit history.",
            input_model=ArtifactArchiveInput,
            output_model=WriteEnvelope[ArtifactOut],
            handler=_artifact_archive,
            purpose=(
                "Use this to clean up accidental artifact clutter or no-longer-current "
                "drafts while preserving audit lineage."
            ),
            prerequisites=_run_step_prerequisites(),
            examples=(
                OperationExample(
                    title="Archive accidental draft",
                    arguments={
                        "project_id": 1,
                        "artifact_id": 12,
                        "reason": "Created during iteration and not approved for preservation.",
                    },
                ),
            ),
            mutating=True,
            grant_policy="run-plan-step-grant",
        ),
        operation_spec(
            name="artifact.supersede",
            summary="Mark one artifact superseded by a replacement artifact.",
            input_model=ArtifactSupersedeInput,
            output_model=WriteEnvelope[ArtifactOut],
            handler=_artifact_supersede,
            purpose=(
                "Use this when a durable draft or output is replaced by a newer durable "
                "artifact and both lineage and cleanup matter."
            ),
            prerequisites=_run_step_prerequisites(),
            examples=(
                OperationExample(
                    title="Supersede old publication packet",
                    arguments={
                        "project_id": 1,
                        "artifact_id": 12,
                        "replacement_artifact_id": 18,
                        "reason": "Operator approved the refined packet.",
                    },
                ),
            ),
            mutating=True,
            grant_policy="run-plan-step-grant",
        ),
        operation_spec(
            name="context.query",
            summary="Query projected, sanitized project memory context.",
            input_model=ContextQueryInput,
            output_model=ContextQueryOut,
            handler=_context_query,
            purpose=(
                "Use this to read bounded project memory. Direct calls are field-filtered; "
                "workflow steps can request explicit source/field grants."
            ),
            examples=(
                OperationExample(
                    title="Read decisions and learnings",
                    arguments={
                        "project_id": 1,
                        "sources": ["decisions", "learnings"],
                        "fields": ["title", "summary", "status"],
                    },
                ),
            ),
            mutating=False,
            grant_policy="direct-read-or-run-plan-step-grant",
        ),
        operation_spec(
            name="context.timeline",
            summary="Read the project memory event timeline.",
            input_model=ContextTimelineInput,
            output_model=ContextPageOut,
            handler=_context_timeline,
            purpose="Use this to inspect chronological project memory events.",
            examples=(OperationExample(title="Read timeline", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="context.snapshot",
            summary="Create an immutable context snapshot.",
            input_model=ContextSnapshotInput,
            output_model=WriteEnvelope[ContextSnapshotOut],
            handler=_context_snapshot,
            purpose="Use this inside a workflow step to preserve selected context evidence.",
            prerequisites=_run_step_prerequisites(),
            mutating=True,
            grant_policy="run-plan-step-grant",
        ),
        operation_spec(
            name="learning.query",
            summary="Query project learnings without deciding which are true.",
            input_model=LearningQueryInput,
            output_model=ContextPageOut,
            handler=_learning_query,
            purpose="Use this to read recorded project learnings.",
            examples=(OperationExample(title="Read learnings", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="learning.create",
            summary="Record one supplied project learning.",
            input_model=LearningCreateInput,
            output_model=WriteEnvelope[LearningOut],
            handler=_learning_create,
            purpose="Use this inside a workflow step after the agent has explicit evidence.",
            prerequisites=_run_step_prerequisites(),
            mutating=True,
            grant_policy="run-plan-step-grant",
        ),
        operation_spec(
            name="learning.update",
            summary="Update one supplied project learning.",
            input_model=LearningUpdateInput,
            output_model=WriteEnvelope[LearningOut],
            handler=_learning_update,
            purpose="Use this inside a workflow step to update learning status or review data.",
            prerequisites=_run_step_prerequisites(),
            mutating=True,
            grant_policy="run-plan-step-grant",
        ),
        operation_spec(
            name="experiment.query",
            summary="Query project experiments without deciding winners.",
            input_model=ExperimentQueryInput,
            output_model=ContextPageOut,
            handler=_experiment_query,
            purpose="Use this to read recorded experiments and statuses.",
            examples=(OperationExample(title="Read experiments", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="experiment.create",
            summary="Create one supplied project experiment.",
            input_model=ExperimentCreateInput,
            output_model=WriteEnvelope[ExperimentOut],
            handler=_experiment_create,
            purpose=(
                "Use this inside a workflow step when the workflow explicitly records experiments."
            ),
            prerequisites=_run_step_prerequisites(),
            mutating=True,
            grant_policy="run-plan-step-grant",
        ),
        operation_spec(
            name="experiment.recordObservation",
            summary="Record one supplied experiment observation.",
            input_model=ExperimentObservationInput,
            output_model=WriteEnvelope[ExperimentObservationOut],
            handler=_experiment_record_observation,
            purpose="Use this inside a workflow step after an observation is produced.",
            prerequisites=_run_step_prerequisites(),
            mutating=True,
            grant_policy="run-plan-step-grant",
        ),
        operation_spec(
            name="experiment.recordDecision",
            summary="Record an explicit experiment decision.",
            input_model=ExperimentDecisionInput,
            output_model=WriteEnvelope[DecisionOut],
            handler=_experiment_record_decision,
            purpose="Use this inside a workflow step when a decision is explicitly made.",
            prerequisites=_run_step_prerequisites(),
            mutating=True,
            grant_policy="run-plan-step-grant",
        ),
        operation_spec(
            name="decision.query",
            summary="Query recorded project decisions.",
            input_model=DecisionQueryInput,
            output_model=ContextPageOut,
            handler=_decision_query,
            purpose="Use this to inspect explicit project decisions and evidence.",
            examples=(OperationExample(title="Read decisions", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="decision.record",
            summary="Record one explicit project decision.",
            input_model=DecisionRecordInput,
            output_model=WriteEnvelope[DecisionOut],
            handler=_decision_record,
            purpose=(
                "Use this inside a workflow step to persist an explicit decision with evidence."
            ),
            prerequisites=_run_step_prerequisites(),
            mutating=True,
            grant_policy="run-plan-step-grant",
        ),
    ]


__all__ = ["operation_specs"]
