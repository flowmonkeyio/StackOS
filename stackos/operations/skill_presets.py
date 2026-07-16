"""Skill preset operation registrations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.spec import (
    OperationExample,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import NotFoundError
from stackos.skill_presets import LoadedSkillPreset, SkillPresetListOut, SkillPresetLoader
from stackos.skill_presets.schema import SkillPresetSpec, SkillProjectAdaptationSpec
from stackos.workflows.template_loader import WorkflowTemplateLoader, WorkflowTemplateSummaryOut
from stackos.workflows.template_schema import WorkflowSkillPresetRequirementSpec

SkillPresetRequirementKind = Literal["required", "recommended", "optional"]


class SkillPresetListInput(MCPInput):
    """List reusable generic skill presets."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"workflow_key": "engineering.tracked-delivery"}},
    )

    project_id: int | None = None
    repo_root: str | None = None
    plugin_slug: str | None = None
    workflow_key: str | None = None
    include_shadowed: bool = False


class SkillPresetDescribeInput(MCPInput):
    """Describe one reusable generic skill preset."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"key": "stackos.sdlc.delivery-orchestrator"}},
    )

    project_id: int | None = None
    key: str
    repo_root: str | None = None
    plugin_slug: str | None = None
    source: str | None = None


class SkillPresetResolveForWorkflowInput(MCPInput):
    """Resolve skill presets for one workflow template."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"workflow_key": "engineering.tracked-delivery"}},
    )

    project_id: int | None = None
    workflow_key: str
    repo_root: str | None = None
    plugin_slug: str | None = None
    source: str | None = None
    include_optional: bool = False


class SkillProjectAdaptationPacket(BaseModel):
    generic_preset: bool = True
    adaptation_required: bool = True
    do_not_use_verbatim: bool = True
    adaptation_status: str = "required"
    instruction: str
    required_agent_action: str
    prompt_assembly_order: list[str]
    required_context_refs: list[dict[str, object]]
    conditional_context_refs: list[dict[str, object]]


class SkillPresetDescribeOut(BaseModel):
    preset: LoadedSkillPreset
    project_adaptation: SkillProjectAdaptationPacket
    setup_guidance: list[str] = Field(default_factory=list)


class ResolvedWorkflowSkillPresetOut(BaseModel):
    requirement: SkillPresetRequirementKind
    purpose: str = ""
    applies_to_steps: list[str] = Field(default_factory=list)
    setup_notes: list[str] = Field(default_factory=list)
    preset: LoadedSkillPreset
    project_adaptation: SkillProjectAdaptationPacket


class UnresolvedWorkflowSkillPresetOut(BaseModel):
    requirement: SkillPresetRequirementKind
    skill_preset_ref: str
    purpose: str = ""
    reason: str


class SkillPresetWorkflowResolutionOut(BaseModel):
    workflow: WorkflowTemplateSummaryOut
    skill_preset_requirements: list[WorkflowSkillPresetRequirementSpec]
    required_skill_presets: list[ResolvedWorkflowSkillPresetOut]
    recommended_skill_presets: list[ResolvedWorkflowSkillPresetOut]
    optional_skill_presets: list[ResolvedWorkflowSkillPresetOut]
    unresolved_skill_preset_requirements: list[UnresolvedWorkflowSkillPresetOut]
    setup_guidance: list[str]


def _adaptation_packet(preset: SkillPresetSpec) -> SkillProjectAdaptationPacket:
    adaptation: SkillProjectAdaptationSpec = preset.project_adaptation
    return SkillProjectAdaptationPacket(
        generic_preset=preset.generic_preset,
        adaptation_required=adaptation.required,
        do_not_use_verbatim=adaptation.do_not_use_verbatim,
        instruction=adaptation.instruction,
        required_agent_action=adaptation.required_agent_action,
        prompt_assembly_order=list(adaptation.prompt_assembly_order),
        required_context_refs=[
            item.model_dump(mode="json", exclude_none=True)
            for item in adaptation.required_context_refs
        ],
        conditional_context_refs=[
            item.model_dump(mode="json", exclude_none=True)
            for item in adaptation.conditional_context_refs
        ],
    )


def setup_guidance() -> list[str]:
    return [
        "Skill presets are generic main-agent operating contracts, not installed host skills.",
        (
            "Installed host skills remain in workflow skill_requirements, usually "
            "stackos:stackos. Reusable main-agent workflow guidance belongs in "
            "skill_preset_requirements."
        ),
        (
            "Do not use a generic skill preset verbatim. Adapt it to project docs, "
            "workflow extension, local agent setup, tests, release rules, and current "
            "tracker/run-plan state first."
        ),
        (
            "Project-local overrides live under .stackos/skill-presets and shadow "
            "plugin presets the same way agent preset overrides do. They are not "
            "normal host-agent materialization and require an explicit checked-in "
            "project override request before the host agent creates them."
        ),
    ]


def _resolved_skill_preset(
    requirement: WorkflowSkillPresetRequirementSpec,
    preset: LoadedSkillPreset,
) -> ResolvedWorkflowSkillPresetOut:
    return ResolvedWorkflowSkillPresetOut(
        requirement=requirement.requirement,
        purpose=requirement.purpose,
        applies_to_steps=list(requirement.applies_to_steps),
        setup_notes=list(requirement.setup_notes),
        preset=preset,
        project_adaptation=_adaptation_packet(preset.preset),
    )


def resolve_skill_preset_requirements(
    requirements: list[WorkflowSkillPresetRequirementSpec],
    *,
    repo_root: str | None,
    include_optional: bool,
) -> tuple[
    list[ResolvedWorkflowSkillPresetOut],
    list[ResolvedWorkflowSkillPresetOut],
    list[ResolvedWorkflowSkillPresetOut],
    list[UnresolvedWorkflowSkillPresetOut],
]:
    loader = SkillPresetLoader()
    required: list[ResolvedWorkflowSkillPresetOut] = []
    recommended: list[ResolvedWorkflowSkillPresetOut] = []
    optional: list[ResolvedWorkflowSkillPresetOut] = []
    unresolved: list[UnresolvedWorkflowSkillPresetOut] = []
    for requirement in requirements:
        if requirement.requirement == "optional" and not include_optional:
            continue
        try:
            preset = loader.describe_preset(
                key=requirement.skill_preset_ref,
                repo_root=repo_root,
            )
        except NotFoundError as exc:
            unresolved.append(
                UnresolvedWorkflowSkillPresetOut(
                    requirement=requirement.requirement,
                    skill_preset_ref=requirement.skill_preset_ref,
                    purpose=requirement.purpose,
                    reason=str(exc),
                )
            )
            continue
        resolved = _resolved_skill_preset(requirement, preset)
        if requirement.requirement == "required":
            required.append(resolved)
        elif requirement.requirement == "recommended":
            recommended.append(resolved)
        else:
            optional.append(resolved)
    return required, recommended, optional, unresolved


async def skill_preset_list(
    inp: SkillPresetListInput,
    _ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> SkillPresetListOut:
    return SkillPresetLoader().list_presets(
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        workflow_key=inp.workflow_key,
        include_shadowed=inp.include_shadowed,
    )


async def skill_preset_describe(
    inp: SkillPresetDescribeInput,
    _ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> SkillPresetDescribeOut:
    preset = SkillPresetLoader().describe_preset(
        key=inp.key,
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        source=inp.source,
    )
    return SkillPresetDescribeOut(
        preset=preset,
        project_adaptation=_adaptation_packet(preset.preset),
        setup_guidance=setup_guidance(),
    )


async def skill_preset_resolve_for_workflow(
    inp: SkillPresetResolveForWorkflowInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> SkillPresetWorkflowResolutionOut:
    workflow = WorkflowTemplateLoader(ctx.session).describe_template(
        key=inp.workflow_key,
        project_id=inp.project_id,
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        source=inp.source,
    )
    required, recommended, optional, unresolved = resolve_skill_preset_requirements(
        list(workflow.spec.skill_preset_requirements),
        repo_root=inp.repo_root,
        include_optional=inp.include_optional,
    )
    return SkillPresetWorkflowResolutionOut(
        workflow=workflow.summary,
        skill_preset_requirements=list(workflow.spec.skill_preset_requirements),
        required_skill_presets=required,
        recommended_skill_presets=recommended,
        optional_skill_presets=optional,
        unresolved_skill_preset_requirements=unresolved,
        setup_guidance=setup_guidance(),
    )


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="skillPreset.list",
            summary="List reusable generic skill presets available to the current StackOS project.",
            input_model=SkillPresetListInput,
            output_model=SkillPresetListOut,
            handler=skill_preset_list,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/skillPreset.list/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call skillPreset.list"),
            ),
            purpose=(
                "Use this when a main agent needs reusable workflow operating guidance "
                "that must be adapted to a project."
            ),
            when_to_use=(
                "A caller is setting up or adapting main-agent workflow guidance.",
                "A caller needs skill presets filtered to one workflow template.",
            ),
            prerequisites=("Treat returned presets as generic project-adapted contracts.",),
            returns=(
                (
                    "Skill preset summaries with type, workflow applicability, source, "
                    "and adaptation-required status."
                ),
            ),
            examples=(
                OperationExample(
                    title="List skill presets for engineering delivery",
                    arguments={"workflow_key": "engineering.tracked-delivery"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="skillPreset.describe",
            summary="Describe one generic skill preset with its project-adaptation contract.",
            input_model=SkillPresetDescribeInput,
            output_model=SkillPresetDescribeOut,
            handler=skill_preset_describe,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/skillPreset.describe/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call skillPreset.describe"),
            ),
            purpose=(
                "Use this before adapting main-agent workflow guidance. The response "
                "includes the generic contract plus explicit project-adaptation instructions."
            ),
            when_to_use=(
                "A caller needs the full operating contract for one skill preset.",
                "A caller needs required project references before adapting the skill preset.",
            ),
            prerequisites=(
                "Read the adaptation packet and project refs before using the contract.",
            ),
            returns=(
                (
                    "The loaded preset, adaptation-required packet, prompt assembly "
                    "order, and setup guidance."
                ),
            ),
            examples=(
                OperationExample(
                    title="Describe SDLC delivery orchestrator",
                    arguments={"key": "stackos.sdlc.delivery-orchestrator"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="skillPreset.resolveForWorkflow",
            summary="Resolve required/recommended skill presets for one workflow.",
            input_model=SkillPresetResolveForWorkflowInput,
            output_model=SkillPresetWorkflowResolutionOut,
            handler=skill_preset_resolve_for_workflow,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/skillPreset.resolveForWorkflow/call",
                ),
                cli=OperationSurface(
                    enabled=True,
                    command="ops call skillPreset.resolveForWorkflow",
                ),
            ),
            purpose=(
                "Use this when a workflow template declares main-agent skill preset "
                "guidance that must be adapted for the active project."
            ),
            when_to_use=(
                "A main agent is deciding which operating skill presets apply to a workflow.",
                "A caller needs unresolved skill preset diagnostics for a workflow.",
            ),
            prerequisites=(
                "A workflow template key is required.",
                "Workflow templates are inert contracts; call runPlan.create before execution.",
            ),
            returns=(
                (
                    "Workflow summary, skill preset requirements, resolved full presets, "
                    "unresolved refs, and setup guidance."
                ),
            ),
            examples=(
                OperationExample(
                    title="Resolve skill presets for engineering delivery",
                    arguments={"workflow_key": "engineering.tracked-delivery"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
            secret_policy="no-secret-output",
        ),
    ]


__all__ = [
    "ResolvedWorkflowSkillPresetOut",
    "SkillPresetDescribeInput",
    "SkillPresetDescribeOut",
    "SkillPresetListInput",
    "SkillPresetResolveForWorkflowInput",
    "SkillPresetWorkflowResolutionOut",
    "UnresolvedWorkflowSkillPresetOut",
    "operation_specs",
    "resolve_skill_preset_requirements",
    "setup_guidance",
    "skill_preset_describe",
    "skill_preset_list",
    "skill_preset_resolve_for_workflow",
]
