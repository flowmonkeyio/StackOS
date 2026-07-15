"""Agent preset operation registrations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from stackos.agents import AgentPresetListOut, AgentPresetLoader, LoadedAgentPreset
from stackos.agents.schema import AgentPresetSpec, AgentProjectAdaptationSpec
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.skill_presets import (
    ResolvedWorkflowSkillPresetOut,
    UnresolvedWorkflowSkillPresetOut,
    resolve_skill_preset_requirements,
)
from stackos.operations.skill_presets import (
    setup_guidance as skill_preset_setup_guidance,
)
from stackos.operations.spec import (
    OperationExample,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import NotFoundError
from stackos.workflows.template_loader import WorkflowTemplateLoader, WorkflowTemplateSummaryOut
from stackos.workflows.template_schema import (
    WorkflowAgentRequirementSpec,
    WorkflowSkillPresetRequirementSpec,
    WorkflowSkillRequirementSpec,
)

AgentRequirementKind = Literal["required", "recommended", "optional"]


class AgentPresetListInput(MCPInput):
    """List reusable generic agent presets."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"workflow_key": "core.project-memory-review"}},
    )

    project_id: int | None = None
    repo_root: str | None = None
    plugin_slug: str | None = None
    workflow_key: str | None = None
    include_shadowed: bool = False


class AgentPresetDescribeInput(MCPInput):
    """Describe one reusable generic agent preset."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"key": "stackos.sdlc.planning"}},
    )

    project_id: int | None = None
    key: str
    repo_root: str | None = None
    plugin_slug: str | None = None
    source: str | None = None


class AgentPresetResolveForWorkflowInput(MCPInput):
    """Resolve agent presets and setup skills for one workflow template."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"workflow_key": "core.project-memory-review"}},
    )

    project_id: int | None = None
    workflow_key: str
    repo_root: str | None = None
    plugin_slug: str | None = None
    source: str | None = None
    include_optional: bool = False


class AgentProjectAdaptationPacket(BaseModel):
    generic_preset: bool = True
    adaptation_required: bool = True
    do_not_use_verbatim: bool = True
    adaptation_status: str = "required"
    instruction: str
    required_agent_action: str
    prompt_assembly_order: list[str]
    required_context_refs: list[dict[str, object]]
    conditional_context_refs: list[dict[str, object]]


class AgentPresetDescribeOut(BaseModel):
    preset: LoadedAgentPreset
    project_adaptation: AgentProjectAdaptationPacket
    setup_guidance: list[str] = Field(default_factory=list)


class ResolvedWorkflowAgentOut(BaseModel):
    role: str
    requirement: AgentRequirementKind
    purpose: str = ""
    applies_to_steps: list[str] = Field(default_factory=list)
    handoff_notes: list[str] = Field(default_factory=list)
    preset: LoadedAgentPreset
    project_adaptation: AgentProjectAdaptationPacket


class UnresolvedWorkflowAgentOut(BaseModel):
    role: str
    requirement: AgentRequirementKind
    agent_preset_ref: str
    purpose: str = ""
    reason: str


class AgentPresetWorkflowResolutionOut(BaseModel):
    workflow: WorkflowTemplateSummaryOut
    agent_requirements: list[WorkflowAgentRequirementSpec]
    skill_requirements: list[WorkflowSkillRequirementSpec]
    skill_preset_requirements: list[WorkflowSkillPresetRequirementSpec]
    required_agents: list[ResolvedWorkflowAgentOut]
    recommended_agents: list[ResolvedWorkflowAgentOut]
    optional_agents: list[ResolvedWorkflowAgentOut]
    unresolved_requirements: list[UnresolvedWorkflowAgentOut]
    required_skill_presets: list[ResolvedWorkflowSkillPresetOut] = Field(default_factory=list)
    recommended_skill_presets: list[ResolvedWorkflowSkillPresetOut] = Field(default_factory=list)
    optional_skill_presets: list[ResolvedWorkflowSkillPresetOut] = Field(default_factory=list)
    unresolved_skill_preset_requirements: list[UnresolvedWorkflowSkillPresetOut] = Field(
        default_factory=list
    )
    setup_guidance: list[str]


def _adaptation_packet(preset: AgentPresetSpec) -> AgentProjectAdaptationPacket:
    adaptation: AgentProjectAdaptationSpec = preset.project_adaptation
    return AgentProjectAdaptationPacket(
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


def _setup_guidance(*, workflow_keys: list[str] | None = None) -> list[str]:
    guidance = [
        "Agent presets are generic MCP/tool-consumer contracts, not daemon-run agents.",
        (
            "Do not use a generic preset verbatim; adapt it to project rules, "
            "tech stack, references, tracker workflow, and signoff expectations first."
        ),
        (
            "When the operator explicitly asks to use a workflow, engineering "
            'workflow, StackOS workflow, or "the workflow", create or resolve '
            "the workflow-backed run plan before tracker.createTask or "
            "tracker.createTicket. Put discovery, design, delivery, verification, "
            "and closeout tickets under the workflow task/run plan from the "
            "start. Direct tracker tasks only apply when the operator asks for "
            "task/dependency tracking without invoking a workflow."
        ),
        (
            "Local agent files are host/project-specific. StackOS does not detect, "
            "write, or register .codex, markdown-frontmatter, or other host agent "
            "formats. The main agent should inspect host-local files such as "
            ".codex/config.toml and .codex/agents/*.toml, then adapt presets into "
            "local files only when the host/project guidance calls for it."
        ),
        (
            "Incomplete workspace profile fields such as framework or content_model_json "
            "are adaptation hints, not blockers. After inspecting repository guidance, "
            "record durable hints with workspace.updateProfile when useful."
        ),
        (
            "recommended_tools are StackOS operation refs. Call them directly only "
            "when the host exposes them; otherwise inspect with toolbox.describe and "
            "invoke with toolbox.call."
        ),
        (
            "Use the existing StackOS tracker for planning and delivery: create/update "
            "tasks and tickets, encode dependencies, claim ready work, and record "
            "completion evidence."
        ),
        (
            "Workflow templates are inert reusable contracts. Create a concrete run "
            "plan before executing multi-step workflow work."
        ),
        (
            "For engineering decision or evidence records, read existing records with "
            "resource.query. Write reusable workflow evidence only through run-plan "
            "grants such as resource.upsert or decision.record."
        ),
        (
            "Use the StackOS skill when the host supports skills; it teaches MCP, "
            "tools, workflows, run plans, tracker tasks/tickets, and evidence conventions."
        ),
        (
            "Workflow skill_preset_requirements are reusable main-agent operating "
            "contracts. Resolve them with skillPreset.* operations or this workflow "
            "resolution response, then adapt them to the project before use."
        ),
    ]
    support_workflows = {
        "communications.customer-feedback-intake",
        "support.issue-investigation",
        "support.delivery-task-handoff",
    }
    if support_workflows.intersection(workflow_keys or []):
        guidance.append(
            "This selected support path is feedback intake to canonical Slack thread, "
            "then issue investigation, then same-thread-authorized delivery handoff, "
            "then engineering.tracked-delivery. Preserve route, media, thread, task, "
            "and approval refs; do not collapse investigation and task creation."
        )
    return guidance


def _resolved_agent(
    requirement: WorkflowAgentRequirementSpec,
    preset: LoadedAgentPreset,
) -> ResolvedWorkflowAgentOut:
    return ResolvedWorkflowAgentOut(
        role=requirement.role,
        requirement=requirement.requirement,
        purpose=requirement.purpose,
        applies_to_steps=list(requirement.applies_to_steps),
        handoff_notes=list(requirement.handoff_notes),
        preset=preset,
        project_adaptation=_adaptation_packet(preset.preset),
    )


async def agent_preset_list(
    inp: AgentPresetListInput,
    _ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> AgentPresetListOut:
    return AgentPresetLoader().list_presets(
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        workflow_key=inp.workflow_key,
        include_shadowed=inp.include_shadowed,
    )


async def agent_preset_describe(
    inp: AgentPresetDescribeInput,
    _ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> AgentPresetDescribeOut:
    preset = AgentPresetLoader().describe_preset(
        key=inp.key,
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        source=inp.source,
    )
    return AgentPresetDescribeOut(
        preset=preset,
        project_adaptation=_adaptation_packet(preset.preset),
        setup_guidance=_setup_guidance(workflow_keys=list(preset.preset.applies_to_workflows)),
    )


async def agent_preset_resolve_for_workflow(
    inp: AgentPresetResolveForWorkflowInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> AgentPresetWorkflowResolutionOut:
    workflow = WorkflowTemplateLoader(ctx.session).describe_template(
        key=inp.workflow_key,
        project_id=inp.project_id,
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        source=inp.source,
    )
    loader = AgentPresetLoader()
    required: list[ResolvedWorkflowAgentOut] = []
    recommended: list[ResolvedWorkflowAgentOut] = []
    optional: list[ResolvedWorkflowAgentOut] = []
    unresolved: list[UnresolvedWorkflowAgentOut] = []
    for requirement in workflow.spec.agent_requirements:
        if requirement.requirement == "optional" and not inp.include_optional:
            continue
        try:
            preset = loader.describe_preset(
                key=requirement.agent_preset_ref,
                repo_root=inp.repo_root,
            )
        except NotFoundError as exc:
            unresolved.append(
                UnresolvedWorkflowAgentOut(
                    role=requirement.role,
                    requirement=requirement.requirement,
                    agent_preset_ref=requirement.agent_preset_ref,
                    purpose=requirement.purpose,
                    reason=str(exc),
                )
            )
            continue
        resolved = _resolved_agent(requirement, preset)
        if requirement.requirement == "required":
            required.append(resolved)
        elif requirement.requirement == "recommended":
            recommended.append(resolved)
        else:
            optional.append(resolved)
    (
        required_skill_presets,
        recommended_skill_presets,
        optional_skill_presets,
        unresolved_skill_preset_requirements,
    ) = resolve_skill_preset_requirements(
        list(workflow.spec.skill_preset_requirements),
        repo_root=inp.repo_root,
        include_optional=inp.include_optional,
    )
    return AgentPresetWorkflowResolutionOut(
        workflow=workflow.summary,
        agent_requirements=list(workflow.spec.agent_requirements),
        skill_requirements=list(workflow.spec.skill_requirements),
        skill_preset_requirements=list(workflow.spec.skill_preset_requirements),
        required_agents=required,
        recommended_agents=recommended,
        optional_agents=optional,
        unresolved_requirements=unresolved,
        required_skill_presets=required_skill_presets,
        recommended_skill_presets=recommended_skill_presets,
        optional_skill_presets=optional_skill_presets,
        unresolved_skill_preset_requirements=unresolved_skill_preset_requirements,
        setup_guidance=[
            *_setup_guidance(workflow_keys=[workflow.spec.key]),
            *skill_preset_setup_guidance(),
        ],
    )


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="agentPreset.list",
            summary="List reusable generic agent presets available to the current StackOS project.",
            input_model=AgentPresetListInput,
            output_model=AgentPresetListOut,
            handler=agent_preset_list,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/agentPreset.list/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call agentPreset.list"),
            ),
            purpose=(
                "Use this when an agent needs to discover available generic role presets before "
                "choosing or adapting a role for project work."
            ),
            when_to_use=(
                "A caller is setting up local agents or workflow-specific operating roles.",
                "A caller needs presets filtered to one workflow template.",
            ),
            prerequisites=(
                (
                    "Treat returned presets as generic. The caller must adapt them to "
                    "the project before use."
                ),
            ),
            returns=(
                (
                    "Preset summaries with role, workflow applicability, source, and "
                    "adaptation-required status."
                ),
            ),
            examples=(
                OperationExample(
                    title="List presets for a workflow",
                    arguments={"workflow_key": "core.project-memory-review"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="agentPreset.describe",
            summary="Describe one generic agent preset with its project-adaptation contract.",
            input_model=AgentPresetDescribeInput,
            output_model=AgentPresetDescribeOut,
            handler=agent_preset_describe,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/agentPreset.describe/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call agentPreset.describe"),
            ),
            purpose=(
                "Use this before installing or prompting an agent role. The response includes "
                "the generic contract plus explicit project-adaptation instructions."
            ),
            when_to_use=(
                "A caller needs the full prompt contract for one role.",
                "A caller needs required project references before adapting the role.",
            ),
            prerequisites=(
                "Read the adaptation packet and project refs before using the prompt contract.",
                "Use the existing tracker for planning, delivery, dependencies, and evidence.",
            ),
            returns=(
                (
                    "The loaded preset, adaptation-required packet, prompt assembly "
                    "order, and setup guidance."
                ),
            ),
            examples=(
                OperationExample(
                    title="Describe planning agent",
                    arguments={"key": "stackos.sdlc.planning"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="agentPreset.resolveForWorkflow",
            summary=(
                "Resolve required/recommended agent presets and StackOS skill preset setup "
                "for one workflow."
            ),
            input_model=AgentPresetResolveForWorkflowInput,
            output_model=AgentPresetWorkflowResolutionOut,
            handler=agent_preset_resolve_for_workflow,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/agentPreset.resolveForWorkflow/call",
                ),
                cli=OperationSurface(
                    enabled=True,
                    command="ops call agentPreset.resolveForWorkflow",
                ),
            ),
            purpose=(
                "Use this when setting up agents for a workflow template. It returns the inert "
                "workflow's agent requirements, installed skill requirements, resolved agent "
                "presets, resolved skill presets, and mandatory project-adaptation guidance."
            ),
            when_to_use=(
                "A main agent is deciding which local agents or roles to configure for a workflow.",
                (
                    "A caller wants to turn a reusable workflow template into a "
                    "concrete run-plan setup."
                ),
                "A caller needs to know which skills should be loaded before workflow execution.",
                "A caller needs main-agent operating skill presets for a workflow.",
            ),
            prerequisites=(
                "A workflow template key is required.",
                (
                    "Workflow templates are presets/contracts; call runPlan.create to "
                    "create executable workflow state."
                ),
                (
                    "Use the StackOS skill when available so the main agent understands "
                    "MCP/tools/tasks/tickets."
                ),
            ),
            returns=(
                (
                    "Workflow summary, agent requirements, installed skill requirements, "
                    "skill preset requirements, resolved full presets, unresolved preset refs, "
                    "and setup guidance."
                ),
            ),
            examples=(
                OperationExample(
                    title="Resolve roles for project memory review",
                    arguments={"workflow_key": "core.project-memory-review"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
            secret_policy="no-secret-output",
        ),
    ]


__all__ = [
    "AgentPresetDescribeInput",
    "AgentPresetDescribeOut",
    "AgentPresetListInput",
    "AgentPresetResolveForWorkflowInput",
    "AgentPresetWorkflowResolutionOut",
    "agent_preset_describe",
    "agent_preset_list",
    "agent_preset_resolve_for_workflow",
    "operation_specs",
]
