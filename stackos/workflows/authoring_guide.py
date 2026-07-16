"""Canonical workflow authoring guidance for StackOS agents."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

WORKFLOW_AUTHORING_GUIDE_VERSION = "stackos.workflow-authoring-guide.v2"
WORKFLOW_AUTHORING_GUIDE_OPERATION = "workflowTemplate.authoringGuide"

MINIMAL_WORKFLOW_TEMPLATE_YAML = """schema_version: stackos.workflow-template.v1
key: custom.example-review
name: Example Review
version: 0.1.0
description: Review bounded project context and produce a next-step plan.
domain: custom
owner:
  team: Project
experience:
  problem: Project history is easy to lose or reconstruct incorrectly.
  outcome: A source-linked memory review and explicit next-step recommendation.
  operator_path:
    - State the question and review the recommendation.
  agent_path:
    - Read bounded context, expose gaps, and stop before implicit follow-up work.
  safe_stopping_points:
    - Stop after the recommendation unless follow-up is explicitly authorized.
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


class WorkflowAuthoringMode(BaseModel):
    """One explicit agent intent mode with bounded mutations and completion."""

    model_config = ConfigDict(extra="forbid")

    key: str
    use_when: str
    mutation_boundary: str
    completion_condition: str
    next_mode: str | None = None


class WorkflowSetupPhase(BaseModel):
    """One deterministic workflow setup or operation phase."""

    model_config = ConfigDict(extra="forbid")

    key: str
    purpose: str
    ordered_actions: list[str]
    allowed_writes: list[str]
    completion_conditions: list[str]
    prohibited_actions: list[str]


class WorkflowAuthoringGuideOut(BaseModel):
    """Agent-facing workflow authoring contract."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = WORKFLOW_AUTHORING_GUIDE_VERSION
    source_of_truth_operation: str = WORKFLOW_AUTHORING_GUIDE_OPERATION
    title: str
    summary: str
    audience: list[str]
    response_guidance: list[str]
    intent_modes: list[WorkflowAuthoringMode]
    principles: list[str]
    project_workflow_authoring_path: list[str]
    complete_package_scope: list[str]
    package_authoring_path: list[str]
    workflow_setup_protocol: list[WorkflowSetupPhase]
    agent_materialization_policy: list[str]
    prerequisite_persistence_policy: list[str]
    setup_completion_contract: list[str]
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
            "Use this operation as the canonical agent contract for selecting one workflow "
            "intent mode, authoring or customizing the reusable method when needed, setting up "
            "project-adapted agents deterministically, and executing only after strict readiness. "
            "Repository docs and installed skills should point here and keep only repo-local "
            "summaries that are tested against this contract."
        ),
        audience=[
            "Agents working outside the StackOS source checkout",
            "Operators reviewing reusable workflow templates",
            "Plugin authors shipping built-in workflow templates",
        ],
        response_guidance=[
            "Use the default compact response to classify intent and perform existing-workflow "
            "setup, prerequisite preparation, or operation without loading the full authoring "
            "manual.",
            "After selecting author_project or publish_plugin, request response_mode=raw once to "
            "load the complete project/package authoring, schema, review, and example contract.",
            "Do not request both agentPreset.resolveForWorkflow and "
            "skillPreset.resolveForWorkflow for the same setup unless a standalone skill packet is "
            "needed; the agent resolution response already includes skill presets.",
        ],
        intent_modes=[
            WorkflowAuthoringMode(
                key="setup_existing",
                use_when=(
                    "The operator selected an existing workflow and wants recurring, "
                    "project-local agent capability prepared without running the workflow."
                ),
                mutation_boundary=(
                    "May create or confirm the workspace binding, materialize reviewed "
                    "host-local execution contracts when the host supports them, update "
                    "useful workspace profile hints, and save a non-empty validated workflow "
                    "extension only when durable project setup is actually needed."
                ),
                completion_condition=(
                    "The effective workflow and preset topology are resolved, required roles "
                    "are materialized or have an explicit session-only fallback, structural "
                    "validation passes, strict validation reports either success or exact "
                    "deferred prerequisite keys, and no run plan, tracker work, or workflow "
                    "output was created."
                ),
                next_mode="prepare_prerequisites or execute",
            ),
            WorkflowAuthoringMode(
                key="customize_existing",
                use_when=(
                    "An existing workflow still represents the same operator-facing job but "
                    "needs project defaults, selected context, guardrails, roles, approvals, "
                    "or step guidance."
                ),
                mutation_boundary=(
                    "Use workflowExtension.validate/upsert against the existing workflow key. "
                    "Do not save or fork a template unless the reusable workflow identity "
                    "actually changes."
                ),
                completion_condition=(
                    "The reviewed non-empty extension is saved, the effective workflow is "
                    "re-described and re-resolved, and setup_existing verification passes."
                ),
                next_mode="setup_existing",
            ),
            WorkflowAuthoringMode(
                key="author_project",
                use_when=(
                    "The project needs a genuinely new reusable method that is local to one "
                    "project or user and does not require a distributed plugin package."
                ),
                mutation_boundary=(
                    "Draft and validate without writes; call workflowTemplate.save only after "
                    "explicit operator/local-admin approval of the complete template. Reuse "
                    "existing resources, actions, agent presets, and skill presets whenever "
                    "possible."
                ),
                completion_condition=(
                    "The complete project/user template validates, is explicitly approved and "
                    "saved, then passes setup_existing verification."
                ),
                next_mode="setup_existing",
            ),
            WorkflowAuthoringMode(
                key="publish_plugin",
                use_when=(
                    "The reusable workflow is intended for distribution or requires new plugin "
                    "capabilities, providers, resources, actions, presets, or navigation."
                ),
                mutation_boundary=(
                    "Repository/package changes require explicit implementation authority and "
                    "must evolve manifests, schemas, connectors, templates, presets, tests, "
                    "docs, grants, and readiness together."
                ),
                completion_condition=(
                    "The complete package passes mechanical gates, domain/provider review, "
                    "black-box agent review, and representative run-plan verification."
                ),
                next_mode="setup_existing",
            ),
            WorkflowAuthoringMode(
                key="one_off_run",
                use_when=("The work is multi-step or audited but not a reusable operating method."),
                mutation_boundary=(
                    "Create only one concrete run plan after its bounded inputs, grants, and "
                    "approval requirements validate; do not create a reusable template, "
                    "extension, or host-local agent installation."
                ),
                completion_condition=(
                    "The one-off plan reaches its explicit closeout with evidence and no "
                    "unintended reusable setup state."
                ),
            ),
            WorkflowAuthoringMode(
                key="execute",
                use_when=(
                    "The operator wants one occurrence of an already selected and prepared "
                    "workflow to run."
                ),
                mutation_boundary=(
                    "Strictly validate concrete inputs and execution readiness before "
                    "runPlan.create; after start, mutate only through active-step grants."
                ),
                completion_condition=(
                    "Run-plan, tracker, approval, output, and evidence state agree at the "
                    "workflow's declared terminal condition."
                ),
            ),
        ],
        principles=[
            "Projects store durable state, workflow templates define reusable methods, "
            "and run plans are concrete execution instances.",
            "Templates are inert contracts. Agents decide strategy; StackOS validates "
            "state, resolves safe refs, executes explicit calls, and records audit.",
            "Start with the problem AI should help solve, the useful outcome, and the "
            "operator and agent experience before choosing steps, roles, or tools.",
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
            "Agents decide business strategy and project adaptation. Workflow setup mechanics, "
            "allowed writes, readiness gates, and completion evidence must be deterministic.",
        ],
        project_workflow_authoring_path=[
            "Select author_project only when no existing workflow plus project extension "
            "represents the same operator-facing job. Use customize_existing otherwise.",
            "Bind the intended project, then write the compact experience contract first: "
            "problem, outcome, operator_path, agent_path, recovery, and safe stopping points. "
            "Public prerequisites and proof belong under public only for a reviewed public "
            "catalog workflow.",
            "Draft one complete reusable job with stable identity, bounded inputs/context, "
            "existing agent and skill preset refs, policies, approvals, ordered steps, outputs, "
            "and failure handling. Do not add plugin package files for a project/user template.",
            "Call workflowTemplate.validate on the complete draft and repair every error. "
            "Validation is read-only and must precede save.",
            "Obtain explicit operator/local-admin approval before workflowTemplate.save. The "
            "saved template must use source=project or source=user and must not claim "
            "metadata.builtin=true.",
            "After save, enter setup_existing: describe the saved template, resolve its roles, "
            "check readiness, materialize host-local execution contracts, and run structural "
            "plus strict read-only plan validation. Do not create a run during authoring/setup.",
        ],
        complete_package_scope=[
            "Plugin manifest entries for capabilities, providers, resources, actions, "
            "and navigation when the workflow belongs to a domain. Workflow templates, "
            "agent presets, and skill presets ship from the standard plugin directories.",
            "Resource schemas with ui_schema, record_kind, and agent_guidance for durable "
            "state the workflow reads or writes.",
            "Action contracts and connector registrations for executable provider or "
            "internal operations, or explicit deferred execution_mode/deferred_reason "
            "when a connector is intentionally not executable.",
            "Workflow templates that encode reusable inputs, context, contracts, "
            "approval gates, grants, ordered steps, outputs, policies, failure handling, "
            "and a compact human-and-agent experience contract.",
            "Agent presets for specialist roles and skill presets for the main-agent "
            "orchestrator loop, all marked for project adaptation when generic.",
            "Provider setup context in manifest provider config when external actions "
            "need registration, connection, API-key, billing, or docs URLs.",
            "Tests and docs proving the package loads, resolves, validates, reports "
            "readiness, creates run plans, grants tools, and keeps business invariants visible.",
        ],
        package_authoring_path=[
            "Use this package path only for publish_plugin. Project/user custom workflows "
            "follow project_workflow_authoring_path and do not require plugin source changes.",
            "Define the package boundary first: domain, reusable method, generic vs "
            "project-specific facts, and the level of operator adaptation expected.",
            "Write experience.problem, experience.outcome, operator_path, agent_path, "
            "safe stopping, and recovery before designing the run. For reviewed built-in "
            "public workflows, write public.prerequisites and public.proof separately.",
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
            "Do not use artifact.create as a default scratchpad. Intermediate drafts, "
            "research notes, angle exploration, and review scratch belong to the local "
            "project's agent conventions until the workflow intentionally preserves an "
            "approved output, final packet, durable evidence, operator-approved draft, "
            "or other retained blob. For iterative workflows, set "
            "metadata.artifact_grant_policy=explicit and grant artifact.create, "
            "artifact.update, artifact.archive, and artifact.supersede only on durable "
            "artifact steps.",
            "Separate reasoning roles from mechanical execution roles: decision makers, "
            "adversarial reviewers, operators, and the main orchestrator must have "
            "clear non-overlapping authority.",
            "Reuse or adapt existing generic presets before inventing new agents. Add "
            "a specialist preset only when it owns a materially distinct boundary.",
            "Define the terminal condition explicitly. A cross-workflow handoff returns "
            "the next safe path; it does not authorize starting or mutating that workflow.",
            "Draft the package together: manifest resources/actions, workflow templates, "
            "agent presets, skill presets, docs, and tests should evolve as one contract.",
            "Wire runtime execution explicitly: action refs, resource refs, auth/provider "
            "requirements, approval gates, run-plan grants, tracker evidence, and "
            "readiness diagnostics.",
            "For workflow-backed tracker work, keep the run-plan step mirror tickets "
            "as the one execution spine. Create child tickets with run_plan_id and "
            "step_id at creation time, attach them to the relevant step ticket, and "
            "add dependency edges so the workflow graph has exactly one root: the "
            "first workflow step mirror.",
            "Validate mechanically with manifest/template loaders, workflowTemplate.validate, "
            "agentPreset.resolveForWorkflow, readiness.check, and read-only runPlan.validate. "
            "Use runPlan.create only in a deliberate execution smoke with complete inputs and "
            "cleanup/audit expectations, never as workflow infrastructure setup proof.",
            "Verify behavior against the real source of truth for the domain, not only "
            "against local docs or the implementation that was just written.",
            "Run a black-box agent audit with a vague realistic request and no expected "
            "workflow key. Record discovery burden, guessing, missing context, tool and "
            "output clarity, approval and recovery understanding, and selected-path confidence.",
            "For engineering delivery, include an explicit test-design gate before "
            "implementation. That gate should own TDD/red-first proof, automated "
            "checks, risk-appropriate manual proof depth, expected outcomes, and "
            "evidence artifacts. The orchestrator chooses that depth from quality, "
            "production risk, and user/business impact, not speed; full manual "
            "signoff or production-like rehearsal is required when the risk calls "
            "for it.",
            "Close with independent signoff, documentation updates, changelog/release "
            "notes when user-facing, and a clean diff that excludes temporary files.",
        ],
        workflow_setup_protocol=[
            WorkflowSetupPhase(
                key="infrastructure",
                purpose=(
                    "Prepare the selected workflow and its project-adapted agent execution "
                    "layer without collecting domain prerequisites or creating workflow work."
                ),
                ordered_actions=[
                    "Select intent mode setup_existing or customize_existing before any write. "
                    "If the request is ambiguous, ask one bounded question that distinguishes "
                    "existing setup, project customization, new project workflow, and execution.",
                    "When project ownership is uncertain and no setup write is yet authorized, "
                    "use workspace.resolve for read-only diagnosis. Once setup is authorized, "
                    "call workspace.startSession; use workspace.connect/bootstrap only when "
                    "StackOS requires explicit project identity.",
                    "Require an explicit workflow key or deterministically select one by comparing "
                    "workflowTemplate.list results against when_to_use and when_not_to_use. Report "
                    "the selected key and reason before project-specific writes.",
                    "Call workflowTemplate.describe for the effective workflow and inspect any "
                    "existing workflow extension before deciding that an extension write is "
                    "needed.",
                    "If a non-empty durable project overlay is required, call "
                    "workflowExtension.validate and then workflowExtension.upsert. Do not create "
                    "an "
                    "empty extension. Re-describe the effective workflow after the write.",
                    "Call agentPreset.resolveForWorkflow once for the final effective workflow. It "
                    "already returns agent requirements, installed skill requirements, and "
                    "resolved "
                    "skill presets; call skillPreset.resolveForWorkflow separately only when a "
                    "standalone skill-preset packet is specifically needed.",
                    "Inspect project rules and existing host-local agent, skill, command, and "
                    "orchestrator files. Produce an exact required/recommended/optional role "
                    "materialization plan before editing them.",
                    "Materialize or update host-local execution contracts according to "
                    "agent_materialization_policy, preserving unrelated operator-authored "
                    "content and recording applicable workflow keys, source preset versions, "
                    "and file mappings.",
                    "Call readiness.check for the final effective workflow. Treat "
                    "structurally_ready as the infrastructure gate; never interpret "
                    "missing_count=0 "
                    "as execution readiness when context_status=not_evaluated.",
                    "Call runPlan.validate with enforce_required_inputs=false for structural "
                    "proof, "
                    "then with enforce_required_inputs=true and only known defaults/inputs to "
                    "expose "
                    "the exact deferred prerequisite gate. Both calls are read-only.",
                    "Return the complete setup_completion_contract and stop before runPlan.create.",
                ],
                allowed_writes=[
                    "The workspace binding created or reused by workspace.startSession after setup "
                    "authority and project identity are established.",
                    "Reviewed host-local execution-contract files when the operator requested "
                    "workflow/local-agent setup and project guidance permits those files.",
                    "A validated, non-empty workflow extension needed for durable project "
                    "defaults, "
                    "selected context, guardrails, or workflow-field overrides.",
                    "Useful non-secret workspace profile hints after inspecting the project.",
                ],
                completion_conditions=[
                    "The project binding and deterministic future binding path are explicit.",
                    "The exact workflow key, source, version, and effective extension state are "
                    "known.",
                    "Every required agent and skill preset resolves; required roles are "
                    "materialized "
                    "or have an explicit session-only fallback, and recommended omissions have "
                    "reasons.",
                    "structurally_ready is true and structural run-plan validation passes.",
                    "Strict validation either passes or reports only explicitly deferred "
                    "prerequisite "
                    "keys; context/provider readiness limitations remain visible.",
                    "No run plan, workflow task/ticket, business output, artifact, resource, "
                    "decision, "
                    "learning, experiment, communication, or provider side effect was created.",
                ],
                prohibited_actions=[
                    "Do not call runPlan.create, runPlan.start, tracker.createTask, or "
                    "tracker.createTicket as infrastructure proof.",
                    "Do not collect durable domain prerequisites or produce recurring/business "
                    "output.",
                    "Do not create an empty workflow extension or store a literal placeholder such "
                    "as "
                    "deferred, TBD, or unknown as if it were a real input.",
                    "Do not overwrite unrelated host-local files, create global domain agents, or "
                    "put "
                    "secrets, project ids, run state, or prerequisite values in agent files.",
                ],
            ),
            WorkflowSetupPhase(
                key="prerequisites",
                purpose=(
                    "Collect only durable workflow preparation data and save each value to its "
                    "declared owner without producing the workflow's recurring output."
                ),
                ordered_actions=[
                    "Bind the existing project and re-describe the effective workflow, extension, "
                    "and resolved preset topology.",
                    "Classify every missing value as infrastructure, durable_prerequisite, or "
                    "run_input "
                    "before asking the operator for it. If ownership is ambiguous, ask rather than "
                    "persisting a guess.",
                    "When the effective workflow or resolved orchestrator explicitly assigns a "
                    "durable "
                    "prerequisite to another workflow, return that workflow key as a safe "
                    "setup/operation "
                    "handoff instead of silently executing it. If the operator authorizes setup of "
                    "the "
                    "workflow family, resolve every selected workflow and materialize the "
                    "deduplicated "
                    "union of their required and recommended roles.",
                    "Ask only for missing durable_prerequisite values. Never ask for raw secrets "
                    "in "
                    "chat; use provider connection/setup flows and persist only safe refs.",
                    "Save safe stable defaults or refs in the validated workflow extension, "
                    "project "
                    "context in selected_context_json, and run-gated resources/decisions only "
                    "through "
                    "a dedicated onboarding run whose active step grants those writes.",
                    "Re-describe and re-check readiness. Preserve per-run inputs as unresolved "
                    "until a "
                    "concrete execution request supplies them.",
                    "Report durable values stored, remaining run inputs, provider/operator "
                    "blockers, "
                    "context evaluation status, and the next safe action; stop before business "
                    "output.",
                ],
                allowed_writes=[
                    "Validated safe workflow-extension defaults, selected context, guardrails, and "
                    "refs.",
                    "Approved project setup state through its owning operation.",
                    "Run-gated resources, artifacts, decisions, or learnings only inside an "
                    "explicit "
                    "onboarding run with active-step grants.",
                ],
                completion_conditions=[
                    "Every durable prerequisite is stored in one explicit source of truth or "
                    "reported "
                    "as a named blocker owned by the operator/provider.",
                    "Every remaining required value is classified as a concrete per-run input.",
                    "No recurring/business workflow output was produced.",
                ],
                prohibited_actions=[
                    "Do not persist per-run goals, final variants, provider object ids, or one-off "
                    "business decisions as project defaults.",
                    "Do not treat context_status=not_evaluated as ready and do not hide provider "
                    "blockers.",
                    "Do not start the recurring workflow merely to prove prerequisite setup.",
                ],
            ),
            WorkflowSetupPhase(
                key="operation",
                purpose="Execute one concrete occurrence of a prepared workflow.",
                ordered_actions=[
                    "Bind the existing project, describe the effective workflow, and resolve "
                    "current "
                    "agent/main-agent preset requirements without rewriting setup unless drift "
                    "exists.",
                    "Collect the concrete per-run inputs and selected route/variant decisions for "
                    "this "
                    "occurrence.",
                    "Call readiness.check and require execution_ready=true for the selected route, "
                    "or "
                    "stop with an explicit operator/provider blocker.",
                    "Call runPlan.validate with enforce_required_inputs=true and the concrete "
                    "inputs. "
                    "Repair every validation error before creating state.",
                    "Call runPlan.create only after strict validation succeeds, then start, claim "
                    "and "
                    "record steps, use only granted operations/actions, and preserve approvals.",
                    "Close only when run-plan, tracker, evidence, output, and approval state "
                    "agree.",
                ],
                allowed_writes=[
                    "The concrete run plan and its automatically mirrored workflow tracker state.",
                    "Only the resources, artifacts, actions, decisions, learnings, experiments, "
                    "and "
                    "communications granted to the active run-plan step.",
                ],
                completion_conditions=[
                    "Strict validation passed before run creation.",
                    "The selected execution route was ready or every remaining blocker is "
                    "explicit.",
                    "The workflow reached its declared terminal condition with truthful evidence "
                    "and "
                    "residual risk.",
                ],
                prohibited_actions=[
                    "Do not create a replacement plan to escape a recoverable blocked/stale run; "
                    "use "
                    "consistency and recovery operations.",
                    "Do not use direct setup writes or tracker lifecycle patches to bypass "
                    "run-plan "
                    "step grants and mirrored lifecycle ownership.",
                ],
            ),
        ],
        agent_materialization_policy=[
            "Required agent and skill presets must resolve. Materialize every required specialist "
            "role when the host supports project-local agents; otherwise adapt it into an explicit "
            "session-only role packet and report that recurring cross-session setup is limited.",
            "Materialize recommended specialist roles by default when the host supports them. An "
            "omission is valid only with a recorded project/host/risk reason.",
            "Materialize optional roles only when selected by the operator, project extension, or "
            "current workflow branch.",
            "For an explicitly requested workflow family or an operator-approved prerequisite "
            "workflow, "
            "resolve each selected workflow and materialize the deduplicated union by preset key. "
            "Do not "
            "infer or install unrelated workflow roles from domain proximity alone.",
            "Agent presets become specialist role contracts. Skill presets become main-agent "
            "orchestrator guidance and must never be registered as subagents. Installed host "
            "skills "
            "in skill_requirements remain host skills and are not copied into either role type.",
            "Inspect the host convention and existing files before writing. Preserve unrelated "
            "operator-authored content, adapt or replace overlapping roles deliberately, and never "
            "create a second competing agent registry.",
            "Every materialized file or session packet must record the applicable workflow "
            "key(s), preset key/version, target role, project references used for adaptation, "
            "and whether it is required, recommended, or optional. Report the exact "
            "preset-to-target mapping.",
            "Adaptation must incorporate project rules, stack, docs, tools, tracker/run-plan "
            "model, "
            "verification, release/signoff expectations, and current extension context. Do not use "
            "the generic preset verbatim.",
            "Repository-local .stackos/agent-presets or .stackos/skill-presets are StackOS preset "
            "overrides, not normal host-agent materialization. Create them only when the operator "
            "explicitly requests a checked-in project override; a workflow setup request alone "
            "does "
            "not authorize them.",
            "Validate host syntax/registration after writing and report any host restart/reload "
            "needed. "
            "Keep secrets, credentials, project ids, run tokens, run state, and domain "
            "prerequisites out "
            "of host-local execution contracts.",
        ],
        prerequisite_persistence_policy=[
            "Classify each setup value as infrastructure, durable_prerequisite, or run_input "
            "before "
            "choosing a storage operation.",
            "Use extension input defaults only for stable safe project values that should merge "
            "into "
            "future runs; use selected context for project guidance and safe refs.",
            "Use approved project resources or a dedicated onboarding run when durable state "
            "belongs "
            "to a resource, artifact, decision, or learning and therefore requires step grants.",
            "Keep one-off goals, chosen variants, final business decisions, and provider object "
            "ids "
            "in "
            "the concrete run. Do not promote them to project defaults.",
            "Omit unresolved values and report them as deferred keys. Never persist literal "
            "placeholder "
            "strings or fabricate defaults to make validation pass.",
            "Never store raw secrets in workflow templates, extensions, host-local agent files, or "
            "chat.",
        ],
        setup_completion_contract=[
            "Selected intent mode and why it applies.",
            "Workspace/project binding and deterministic future binding path.",
            "Workflow key, source, version, and effective extension state; say absent/not_required "
            "instead of creating an empty extension.",
            "Required, recommended, and optional agent/skill preset decisions with exact versions "
            "and "
            "preset-to-file/session mapping.",
            "Host-local files changed with absolute paths, validation status, and any "
            "restart/reload need.",
            "Readiness tuple: structurally_ready, context_status, required_providers_ready, and "
            "execution_ready without collapsing them into one ready flag.",
            "Structural runPlan.validate outcome and strict validation outcome with exact deferred "
            "keys.",
            "Allowed setup writes actually performed and any partial-setup/recovery requirement.",
            "Next safe action: prerequisite setup, operator/provider repair, or a future concrete "
            "run.",
            "Explicit confirmation that infrastructure setup created no run plan, workflow tracker "
            "task/ticket, business output, or external side effect.",
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
            "Does test design cover the accepted requirements with automated proof "
            "or risk-appropriate manual proof, and has the orchestrator verified "
            "that proof depth before implementation?",
            "Are reviewer findings treated as claims for orchestrator adjudication "
            "instead of automatically becoming tickets or blockers?",
            "Can an agent execute each claimed step from its bounded packet without "
            "loading the full plan, broadening context fields, or guessing outputs?",
            "Can a future agent answer how to register, connect, prepare, run, and recover "
            "the package without reading private implementation notes?",
            "Can a new agent map a vague operator request to this workflow, distinguish "
            "structural, context, provider-route, and execution readiness, and find a "
            "safe stopping point without hidden repository knowledge?",
        ],
        mechanical_gates=[
            "Plugin manifest validates and indexes every declared resource, provider, action, "
            "workflow template, agent preset, and skill preset.",
            "All workflow templates pass workflowTemplate.validate and their referenced "
            "agent/skill presets resolve.",
            "Every built-in public workflow has reviewed experience and public contracts; "
            "every bundled agent explicitly declares reasoning, mechanical, or review role_class.",
            "Every executable action has a connector, registry entry, grant path, "
            "auth/no-secret boundary, input/output schemas, docs, and tests; every "
            "non-executable action is explicitly deferred.",
            "readiness.check reports precise missing credentials, connectors, budgets, "
            "provider routes, context evaluation state, and provider setup URLs for "
            "affected workflows or actions.",
            "Structural and strict runPlan.validate cover setup without writes; representative "
            "runPlan.create execution tests use complete inputs and prove grants match the "
            "workflow's "
            "action, resource, artifact, decision, context, and communication needs.",
            "Workflow-backed tracker graphs have exactly one root workflow step ticket; attached "
            "child tickets are dependency-bridged from their step and terminal child "
            "tickets block the next step until resolved.",
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
            "Black-box agent reviewer: use fresh context and a vague request, then compare "
            "the selected path, prerequisites, approvals, stopping, and recovery with the "
            "expected workflow contract.",
        ],
        decision_path=[
            "Select exactly one intent_modes key before mutation. A request to set up an "
            "existing workflow maps to setup_existing; it does not imply author_project, "
            "publish_plugin, one_off_run, or execute.",
            "Use customize_existing when the operator-facing job and workflow key remain "
            "the same. Use author_project only for a genuinely new project-local reusable "
            "method, and publish_plugin only for distributed package behavior.",
            "If the user described one non-reusable occurrence, choose one_off_run. If the "
            "user wants an occurrence of an existing prepared workflow, choose execute.",
            "When intent or project ownership is ambiguous, perform read-only discovery and "
            "ask one bounded question before any project/template/extension/run write.",
            "Follow only the selected mode's mutation boundary and completion condition. "
            "Changing modes requires an explicit new operator intent or a reported safe handoff.",
            "For setup_existing/customize_existing, follow workflow_setup_protocol in order. "
            "For author_project, follow project_workflow_authoring_path, obtain explicit save "
            "authority, then enter setup_existing. For publish_plugin, follow "
            "package_authoring_path.",
        ],
        template_contract_fields=[
            "schema_version",
            "key",
            "name",
            "version",
            "description",
            "domain",
            "owner",
            "experience",
            "public for built-in public catalog workflows",
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
            "Setup-time context that should be merged into future run plans. Run-plan resources, "
            "artifacts, decisions, learnings, and actions are written later only inside "
            "a started run with explicit step grants.",
        ],
        execution_path=[
            "Phase 1 infrastructure: inspect/bind, select and describe the effective workflow, "
            "conditionally validate/upsert a non-empty project extension, resolve the final agent "
            "and main-agent preset topology, materialize host execution contracts, check "
            "structural "
            "readiness, run structural and strict read-only validation, report proof, and stop.",
            "Phase 1 must not call runPlan.create or create tracker work. Strict validation may "
            "intentionally report deferred prerequisite keys; that is a setup gate, not permission "
            "to fabricate inputs or create a draft run.",
            "Phase 2 prerequisites: classify missing values, collect only durable prerequisites, "
            "persist each through its declared owner, re-check context/provider readiness, report "
            "remaining per-run inputs, and stop before recurring/business output.",
            "Phase 3 operation: obtain concrete per-run inputs, require selected-route execution "
            "readiness, call strict runPlan.validate, then runPlan.create/start/claimStep, use "
            "only "
            "step-granted operations/actions, and close through runPlan.recordStep plus evidence.",
            "A cross-workflow handoff reports the next safe mode and refs; it never silently "
            "changes "
            "mode or authorizes setup, extension mutation, or execution of the next workflow.",
        ],
        canonical_operations=[
            WorkflowAuthoringOperationRef(
                name="workflowTemplate.authoringGuide",
                purpose="Return this canonical workflow authoring contract.",
            ),
            WorkflowAuthoringOperationRef(
                name="workspace.resolve",
                purpose=(
                    "Inspect workspace ownership without creating setup state when binding or "
                    "operator authority is uncertain."
                ),
            ),
            WorkflowAuthoringOperationRef(
                name="workspace.startSession",
                purpose=(
                    "Create or reuse the authorized workspace binding before project-scoped setup."
                ),
            ),
            WorkflowAuthoringOperationRef(
                name="workflowTemplate.list",
                purpose=(
                    "Discover workflow candidates when the operator supplied an outcome instead of "
                    "an exact key."
                ),
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
                name="workflowExtension.get",
                purpose=(
                    "Distinguish an absent/not-required project overlay from an existing extension."
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
                name="agentPreset.resolveForWorkflow",
                purpose=(
                    "Resolve required/recommended/optional specialist agents, installed host "
                    "skills, "
                    "and main-agent skill presets for one effective workflow."
                ),
            ),
            WorkflowAuthoringOperationRef(
                name="skillPreset.resolveForWorkflow",
                purpose=(
                    "Return a standalone main-agent skill-preset packet only when the combined "
                    "agent "
                    "resolution response is not the desired setup packet."
                ),
            ),
            WorkflowAuthoringOperationRef(
                name="readiness.check",
                purpose=(
                    "Report structural, context, required-provider, route, and execution readiness "
                    "without collapsing those dimensions."
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
                name="runPlan.validate",
                purpose=(
                    "Provide read-only structural validation and strict required-input validation. "
                    "Use this, never run creation, as infrastructure setup proof."
                ),
            ),
            WorkflowAuthoringOperationRef(
                name="runPlan.create",
                purpose=(
                    "Materialize a strictly validated workflow into concrete execution state only "
                    "in operation or an explicit execution smoke, never in infrastructure setup."
                ),
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
            WorkflowAuthoringExample(
                title="Resolve the complete agent and main-agent preset topology once",
                operation="agentPreset.resolveForWorkflow",
                arguments={"workflow_key": "engineering.tracked-delivery"},
            ),
            WorkflowAuthoringExample(
                title="Check workflow readiness dimensions",
                operation="readiness.check",
                arguments={"workflow_key": "engineering.tracked-delivery"},
            ),
            WorkflowAuthoringExample(
                title="Validate infrastructure structure without requiring run inputs",
                operation="runPlan.validate",
                arguments={
                    "workflow_key": "engineering.tracked-delivery",
                    "enforce_required_inputs": False,
                },
            ),
            WorkflowAuthoringExample(
                title="Strictly validate concrete inputs before operation",
                operation="runPlan.validate",
                arguments={
                    "workflow_key": "engineering.tracked-delivery",
                    "inputs_json": {"goal": "Deliver the approved bounded change."},
                    "enforce_required_inputs": True,
                },
            ),
        ],
    )


__all__ = [
    "WORKFLOW_AUTHORING_GUIDE_OPERATION",
    "WORKFLOW_AUTHORING_GUIDE_VERSION",
    "WorkflowAuthoringExample",
    "WorkflowAuthoringGuideOut",
    "WorkflowAuthoringMode",
    "WorkflowAuthoringOperationRef",
    "WorkflowSetupPhase",
    "workflow_authoring_guide",
]
