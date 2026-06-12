"""Canonical workflow authoring guidance for StackOS agents."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

WORKFLOW_AUTHORING_GUIDE_VERSION = "stackos.workflow-authoring-guide.v1"
WORKFLOW_AUTHORING_GUIDE_OPERATION = "workflowTemplate.authoringGuide"

MINIMAL_WORKFLOW_TEMPLATE_YAML = """schema_version: stackos.workflow-template.v1
key: core.example-review
name: Example Review
version: 0.1.0
description: Review bounded project context and produce a next-step plan.
domain: core
owner:
  team: StackOS
when_to_use:
  - A reusable agent workflow should review context before planning.
when_not_to_use:
  - The user asked for one direct provider action with no durable workflow state.
skill_requirements:
  - skill_ref: stackos:stackos
    requirement: recommended
    purpose: Teach the main agent StackOS workflow, run-plan, tracker, and evidence mechanics.
inputs:
  - key: goal
    name: Goal
    type: string
    required: true
context_requirements:
  - id: recent_runs
    source: runs
    fields: [kind, status, last_step, metadata_json]
    max_items: 10
    return_mode: compact
policies:
  - key: agent-decides
    kind: boundary
    description: StackOS stores state and executes explicit calls; the agent decides strategy.
steps:
  - id: clarify-goal
    title: Clarify Goal
    purpose: Restate the goal, assumptions, constraints, and missing inputs.
    input_refs: [goal]
    output_refs: [context_summary]
  - id: review-context
    title: Review Context
    purpose: Read bounded prior evidence before recommending next work.
    context_refs: [recent_runs]
    depends_on: [clarify-goal]
    output_refs: [recommended_plan]
outputs:
  - key: context_summary
    name: Context Summary
    type: object
    required: true
  - key: recommended_plan
    name: Recommended Plan
    type: object
    required: true
failure_handling:
  - If context is insufficient, return missing context requirements instead of inventing facts.
metadata:
  builtin: true
"""


class WorkflowAuthoringOperationRef(BaseModel):
    """One operation in the canonical workflow authoring path."""

    model_config = ConfigDict(extra="forbid")

    name: str
    purpose: str


class WorkflowAuthoringExample(BaseModel):
    """Minimal operation example for agents building workflows."""

    model_config = ConfigDict(extra="forbid")

    title: str
    operation: str
    arguments: dict[str, object] = Field(default_factory=dict)


class WorkflowAuthoringGuideOut(BaseModel):
    """Agent-facing workflow authoring contract."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = WORKFLOW_AUTHORING_GUIDE_VERSION
    source_of_truth_operation: str = WORKFLOW_AUTHORING_GUIDE_OPERATION
    title: str
    summary: str
    audience: list[str]
    principles: list[str]
    complete_package_scope: list[str]
    package_authoring_path: list[str]
    reasoning_gates: list[str]
    mechanical_gates: list[str]
    independent_signoff: list[str]
    decision_path: list[str]
    template_contract_fields: list[str]
    template_must_not_include: list[str]
    extension_uses: list[str]
    execution_path: list[str]
    canonical_operations: list[WorkflowAuthoringOperationRef]
    minimal_template_yaml: str
    examples: list[WorkflowAuthoringExample]


def workflow_authoring_guide() -> WorkflowAuthoringGuideOut:
    """Return the single StackOS workflow authoring contract."""

    return WorkflowAuthoringGuideOut(
        title="StackOS Workflow Authoring Guide",
        summary=(
            "Use this operation as the canonical guide for building, extending, "
            "validating, saving, forking, and executing StackOS workflows from any "
            "repository. Repository docs and installed skills should point here instead "
            "of duplicating the authoring path."
        ),
        audience=[
            "Agents working outside the StackOS source checkout",
            "Operators reviewing reusable workflow templates",
            "Plugin authors shipping built-in workflow templates",
        ],
        principles=[
            "Projects store durable state, workflow templates define reusable methods, "
            "and run plans are concrete execution instances.",
            "Templates are inert contracts. Agents decide strategy; StackOS validates "
            "state, resolves safe refs, executes explicit calls, and records audit.",
            "A workflow identity should represent one end-to-end job the operator can "
            "ask for, not a composable stage that must be chained with other workflows "
            "to finish one deliverable.",
            "Create a new template only when the reusable method is genuinely new.",
            "Use a workflow extension when an existing workflow only needs project "
            "defaults, selected context, guardrails, agent/skill requirements, or step guidance.",
            "Use a one-off run plan when the work is not reusable.",
            "Optional branches such as media generation, provider execution, or extra "
            "review depth belong inside the workflow with explicit approvals and "
            "optional action contracts when they are part of the same job.",
            "Keep domain behavior in plugins through manifests, resources, actions, "
            "templates, and extensions.",
        ],
        complete_package_scope=[
            "Plugin manifest entries for capabilities, providers, resources, actions, "
            "workflow templates, presets, and navigation when the workflow belongs to a domain.",
            "Resource schemas with ui_schema, record_kind, and agent_guidance for durable "
            "state the workflow reads or writes.",
            "Action contracts and connector registrations for executable provider or "
            "internal operations, or explicit deferred execution_mode/deferred_reason "
            "when a connector is intentionally not executable.",
            "Workflow templates that encode reusable inputs, context, contracts, "
            "approval gates, grants, ordered steps, outputs, policies, and failure handling.",
            "Agent presets for specialist roles and skill presets for the main-agent "
            "orchestrator loop, all marked for project adaptation when generic.",
            "Provider setup context in manifest provider config when external actions "
            "need registration, connection, API-key, billing, or docs URLs.",
            "Tests and docs proving the package loads, resolves, validates, reports "
            "readiness, creates run plans, grants tools, and keeps business invariants visible.",
        ],
        package_authoring_path=[
            "Define the package boundary first: domain, reusable method, generic vs "
            "project-specific facts, and the level of operator adaptation expected.",
            "Name the operator-facing job and expected closeout first. If one user "
            "request would need several new workflow templates to complete, collapse "
            "those stages into one workflow with ordered steps.",
            "Inventory existing plugins, resources, actions, workflows, agent presets, "
            "skill presets, and provider setup before creating new names.",
            "Model durable state and invariants before steps: decide which records are "
            "the source of truth, which fields drive readiness, and which claims must "
            "be auditable later.",
            "Treat resources as future memory and artifacts as bulky evidence or output "
            "blobs. The workflow closeout should write a queryable resource index that "
            "points at artifacts, decisions, approvals, and follow-up hooks.",
            "Separate reasoning roles from mechanical execution roles: decision makers, "
            "adversarial reviewers, operators, and the main orchestrator must have "
            "clear non-overlapping authority.",
            "Reuse or adapt existing generic presets before inventing new agents. Add "
            "a specialist preset only when it owns a materially distinct boundary.",
            "Draft the package together: manifest resources/actions, workflow templates, "
            "agent presets, skill presets, docs, and tests should evolve as one contract.",
            "Wire runtime execution explicitly: action refs, resource refs, auth/provider "
            "requirements, approval gates, run-plan grants, tracker evidence, and "
            "readiness diagnostics.",
            "Validate mechanically with manifest/template loaders, workflowTemplate.validate, "
            "agentPreset.resolveForWorkflow, skillPreset.resolveForWorkflow, readiness.check, "
            "and runPlan.validate/create where applicable.",
            "Verify behavior against the real source of truth for the domain, not only "
            "against local docs or the implementation that was just written.",
            "Close with independent signoff, documentation updates, changelog/release "
            "notes when user-facing, and a clean diff that excludes temporary files.",
        ],
        reasoning_gates=[
            "Is the workflow one complete operator-facing job with a clear closeout, "
            "rather than one stage in a private workflow chain?",
            "Does this package preserve one source of truth for durable state, setup "
            "guidance, and workflow method?",
            "Are generic/plugin-level facts cleanly separated from project/operator overlays?",
            "Will future agents find created work through resources and artifact refs "
            "without reading chat history, temporary files, or private notes?",
            "Are business invariants encoded in schemas, workflow policies, review steps, "
            "approval gates, and tests instead of living only in prose?",
            "Are decisions made by agents/humans and execution performed by tools/connectors, "
            "with no hidden strategy inside connectors?",
            "Are adversarial review roles separated when the workflow depends on independent "
            "judgment, such as claim, safety, voice, quality, or release review?",
            "Can a future agent answer how to register, connect, prepare, run, and recover "
            "the package without reading private implementation notes?",
        ],
        mechanical_gates=[
            "Plugin manifest validates and indexes every declared resource, provider, action, "
            "workflow template, agent preset, and skill preset.",
            "All workflow templates pass workflowTemplate.validate and their referenced "
            "agent/skill presets resolve.",
            "Every executable action has a connector, registry entry, grant path, "
            "auth/no-secret boundary, input/output schemas, docs, and tests; every "
            "non-executable action is explicitly deferred.",
            "readiness.check reports precise missing credentials, connectors, budgets, "
            "and provider setup URLs for affected workflows or actions.",
            "runPlan.create/runPlan.validate produce grants that match the workflow's "
            "action, resource, artifact, decision, context, and communication needs.",
            "Tracker/run-plan evidence, approval gates, and audit outputs are part of "
            "the definition of done, not after-the-fact notes.",
            "Docs route users to the canonical StackOS operation and only summarize "
            "repo-local details that cannot live in the operation response.",
        ],
        independent_signoff=[
            "Workflow/package reviewer: validate flow, roles, approval gates, failure "
            "handling, tracker dependencies, and run-plan grant shape.",
            "Business/domain reviewer: compare schemas, policies, and workflow outputs "
            "against the actual domain source or operator brief, not the implementation.",
            "Action/provider reviewer: compare executable actions against official "
            "provider docs or connector contracts, including setup URLs and no-secret auth.",
            "Test/readiness reviewer: prove validation, readiness, and representative "
            "run-plan execution paths with focused automated tests or recorded manual evidence.",
            "Documentation/self-service reviewer: confirm future agents can discover "
            "what exists, when to use it, where to connect/register, and how to execute it.",
        ],
        decision_path=[
            "Choose the workflow identity: new template, project extension, or one-off run plan.",
            "Draft the reusable contract with stable key, inputs, bounded context, "
            "agent/skill requirements, action/resource contracts, policies, approval "
            "gates, ordered steps, outputs, learning hooks, and failure handling.",
            "Validate drafts with workflowTemplate.validate before saving, forking, or "
            "creating a run.",
            "For project-specific setup on an existing workflow, validate and save a "
            "workflow extension instead of copying the template.",
            "Before execution, describe the effective workflow, resolve agent and skill "
            "presets, check readiness, create and validate a run plan, then execute "
            "through step-scoped grants.",
        ],
        template_contract_fields=[
            "schema_version",
            "key",
            "name",
            "version",
            "description",
            "domain",
            "owner",
            "when_to_use",
            "when_not_to_use",
            "inputs",
            "context_requirements",
            "agent_requirements",
            "skill_requirements",
            "skill_preset_requirements",
            "capability_requirements",
            "auth_requirements",
            "action_contracts",
            "resource_contracts",
            "policies",
            "approval_gates",
            "steps",
            "outputs",
            "learning_hooks",
            "failure_handling",
            "metadata",
        ],
        template_must_not_include=[
            "Raw secrets or credentials",
            "Run tokens",
            "Concrete credential refs",
            "Exact provider payloads for one execution",
            "One-off task state",
            "Selected variants or final business decisions",
            "Provider object ids that belong to a concrete run",
            "Repository-local documentation copies of this authoring guide",
        ],
        extension_uses=[
            "Stable project refs such as communication routes or named targets",
            "Project guidance, channel purpose, audience, data-scope boundaries, and "
            "safe external refs",
            "Required input keys that must be present after defaults and run inputs merge",
            "Project guardrails the agent must preserve in run-plan metadata",
            "Additive step guidance such as instructions_prepend, extra_instructions, "
            "success_criteria, or metadata",
            "Atomic top-level workflow field overrides such as agent_requirements, "
            "skill_requirements, skill_preset_requirements, policies, approval_gates, or steps",
        ],
        execution_path=[
            "workflowTemplate.authoringGuide",
            "workflowTemplate.validate",
            "workflowTemplate.save or workflowTemplate.fork only with explicit "
            "local-admin authority",
            "workflowExtension.validate and workflowExtension.upsert for project-specific overlays",
            "workflowTemplate.describe",
            "agentPreset.resolveForWorkflow",
            "skillPreset.resolveForWorkflow",
            "readiness.check",
            "runPlan.create",
            "runPlan.validate",
            "runPlan.start",
            "runPlan.claimStep",
            "step-granted toolbox.call or action.execute",
            "tracker evidence and runPlan.recordStep",
        ],
        canonical_operations=[
            WorkflowAuthoringOperationRef(
                name="workflowTemplate.authoringGuide",
                purpose="Return this canonical workflow authoring contract.",
            ),
            WorkflowAuthoringOperationRef(
                name="workflowTemplate.validate",
                purpose="Validate a draft template or installed template key without saving state.",
            ),
            WorkflowAuthoringOperationRef(
                name="workflowTemplate.describe",
                purpose=(
                    "Inspect the effective workflow, including enabled project extension layering."
                ),
            ),
            WorkflowAuthoringOperationRef(
                name="workflowExtension.validate",
                purpose=(
                    "Validate project defaults, selected context, guardrails, step "
                    "overrides, and atomic workflow-field overrides."
                ),
            ),
            WorkflowAuthoringOperationRef(
                name="workflowExtension.upsert",
                purpose=(
                    "Persist reviewed project-specific workflow setup without "
                    "duplicating the base template."
                ),
            ),
            WorkflowAuthoringOperationRef(
                name="workflowTemplate.save",
                purpose=(
                    "Persist a reviewed project/user template with explicit local-admin authority."
                ),
            ),
            WorkflowAuthoringOperationRef(
                name="workflowTemplate.fork",
                purpose=(
                    "Create a separately named reusable workflow identity with "
                    "explicit local-admin authority."
                ),
            ),
            WorkflowAuthoringOperationRef(
                name="runPlan.create",
                purpose="Materialize a reusable workflow into concrete project execution state.",
            ),
        ],
        minimal_template_yaml=MINIMAL_WORKFLOW_TEMPLATE_YAML,
        examples=[
            WorkflowAuthoringExample(
                title="Read the canonical authoring guide",
                operation="workflowTemplate.authoringGuide",
                arguments={},
            ),
            WorkflowAuthoringExample(
                title="Validate an existing workflow by key",
                operation="workflowTemplate.validate",
                arguments={"key": "engineering.tracked-delivery"},
            ),
            WorkflowAuthoringExample(
                title="Validate a draft template",
                operation="workflowTemplate.validate",
                arguments={"template_yaml": MINIMAL_WORKFLOW_TEMPLATE_YAML},
            ),
            WorkflowAuthoringExample(
                title="Inspect effective workflow setup before execution",
                operation="workflowTemplate.describe",
                arguments={"key": "engineering.tracked-delivery", "project_id": 1},
            ),
        ],
    )


__all__ = [
    "WORKFLOW_AUTHORING_GUIDE_OPERATION",
    "WORKFLOW_AUTHORING_GUIDE_VERSION",
    "WorkflowAuthoringExample",
    "WorkflowAuthoringGuideOut",
    "WorkflowAuthoringOperationRef",
    "workflow_authoring_guide",
]
