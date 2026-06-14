# Workflow Templates

Workflow templates are reusable setup for agent work. They are not hidden
automation. A template gives the agent a strong starting structure, then the
agent creates a concrete run plan for the current project and goal.

## Authoring Source

The canonical workflow authoring guide is the StackOS operation
`workflowTemplate.authoringGuide`. Agents should call it through
`toolbox.call`, REST, or CLI from any repository, then validate drafts with
`workflowTemplate.validate`. Keep this page as repo-local reference material,
not a second copy of the authoring path.

## Complete Workflow Package

A complete workflow package is more than workflow YAML. Build it as one
contract:

- plugin manifest capability/provider/resource/action/workflow/preset entries
- resource schemas with `ui_schema`, `record_kind`, and `agent_guidance`
- action contracts plus executable connectors, or explicit `execution_mode`
  deferral
- workflow templates with inputs, context, policies, approvals, grants, ordered
  steps, outputs, and failure handling
- agent presets for specialist roles and skill presets for the main-agent
  orchestration loop
- provider setup context for registration, connection, API-key, billing, docs,
  fallback URLs, and readiness diagnostics when external services are involved
- tests and docs proving load, resolution, readiness, run-plan grants, and the
  business invariants the package must preserve

Use this reasoning path before authoring files:

1. Define the reusable boundary, generic/plugin facts, project overlay facts,
   and operator adaptation points.
2. Name the operator-facing job and closeout first. If one user request would
   need several new workflow templates to complete, collapse those stages into
   one workflow with ordered steps.
3. Inventory existing plugins, resources, actions, workflows, agent presets,
   skill presets, and provider setup so names and connectors are reused.
4. Model durable state and invariants first; then write workflow steps around
   those records and guarantees. Resources are future memory; artifacts hold
   bulky content and must be indexed by resources when the output should be
   discoverable later.
5. Separate decision, review, and execution roles. The orchestrator coordinates
   the package, but specialist presets own bounded reasoning or mechanical
   duties.
6. Reuse or adapt existing generic presets before inventing new agents. Add a
   specialist role only when it owns a materially distinct boundary.
7. Wire runtime behavior explicitly with action refs, resource refs,
   approval gates, grants, readiness checks, tracker evidence, and run-plan
   outputs.
8. Verify against the actual domain source, operator brief, or official
   provider documentation. Do not sign off only against the code just written.

Workflow identity should follow the engineering pattern: one template represents
one complete piece of work, not a private chain of reusable stages. A domain may
ship multiple workflows, but each one should be independently invokable and
able to reach its own closeout. Optional branches such as generated images,
provider execution, extra review depth, or channel variants belong inside the
same workflow when they are part of the same operator request. It is fine for
different workflows to duplicate internal step patterns; avoid composable
workflow fragments that make one deliverable require several workflow runs.

The closeout should prove future accessibility. If a workflow creates something
the project will need later, it must write a queryable resource record that
stores the summary, state, decision refs, artifact refs, approvals, and follow-up
hooks. Do not leave durable truth only in chat, local files, or artifacts.

Use this mechanical closeout before claiming done:

- validate manifests and every workflow template
- resolve all workflow agent and skill preset requirements
- run `readiness.check` for affected workflows/actions
- prove representative `runPlan.create` / `runPlan.validate` paths and
  step-grant shape
- run focused tests for manifests, actions/connectors, MCP grants, readiness,
  and plugin inventory
- update only the docs that are authoritative for the package, then remove
  temporary planning files
- get independent signoff for flow correctness, domain accuracy, provider/action
  contracts, readiness, and self-service documentation

## Template Schema

A template should be generic across domains and include:

- `schema_version`
- `key`, `name`, `version`, `description`
- `domain` and optional plugin slug
- `owner`
- `when_to_use` and `when_not_to_use`
- `inputs`
- `context_requirements`
- `agent_requirements`
- `skill_requirements`
- `skill_preset_requirements`
- `capability_requirements`
- `auth_requirements`
- `action_contracts`
- `resource_contracts`
- `policies`
- `approval_gates`
- `steps`
- `outputs`
- `learning_hooks`
- `failure_handling`

The template should not contain project secrets or one-off task state.

## Agent And Skill Requirements

`agent_requirements` names the generic roles a workflow expects. Each item has
`role`, `requirement`, `agent_preset_ref`, `purpose`, optional
`applies_to_steps`, and optional `handoff_notes`. The preset ref resolves
through `agentPreset.resolveForWorkflow`.

Returned presets are generic and must be adapted to project rules, tracker
workflow, tech stack, documentation references, and signoff expectations before
use.

`skill_requirements` names host-side skills that can help the main agent
operate the workflow. Built-in workflows recommend `stackos:stackos`, which
teaches StackOS MCP, operations, workflow templates, run plans, tracker
tasks/tickets, dependencies, and evidence.

`skill_preset_requirements` names reusable main-agent operating contracts that
should be resolved and adapted like agent presets. They are not installed host
skills and do not create subagent roles. Use one generic skill preset when
multiple workflows share the same orchestration loop; add a workflow-specific
skill preset only when that workflow has distinct sequencing, evidence, safety,
or closeout mechanics that cannot be expressed by a shared preset plus the
workflow's agent requirements.

The main agent decides whether it can load installed skills. If not, it should
read the referenced docs and still follow the same tracker/run-plan model.
Skill presets are resolved through StackOS operations and adapted before use.

All agents should work through the existing tracker. Planning agents should
break work into deliverable tickets, encode logical dependencies and sequencing,
avoid loose ends, and make blockers and definition of done visible.

Workflow selection takes precedence over tracker ticket creation. When an
operator explicitly asks to use a workflow, engineering workflow, StackOS
workflow, or "the workflow", agents must create or resolve the workflow-backed
run plan before creating tracker tickets. All discovery, design, delivery,
verification, and closeout tickets for that work belong under the workflow
task/run plan from the start. Direct tracker tasks are valid only when the
operator asks for task/dependency tracking without invoking a workflow.

For engineering workflows, the reusable SDLC baseline should keep the method
explicit even when a project adapts the role names or host-agent format:

```text
requirements -> discovery -> planning -> design -> design review -> test design
-> delivery -> verification -> delivery review -> tracker audit -> release
```

Reported customer issues use a separated feedback/support chain before
delivery:

```text
feedback -> route and media preflight -> canonical Slack thread -> intake reaction
-> support.issue-investigation -> full-thread read
-> same-thread clarification when needed -> support conclusion
-> support.delivery-task-handoff after same-thread instruction
-> delivery task creation -> same-thread task handoff
-> task-created reaction -> engineering.tracked-delivery
```

The baseline separates method from specialization. Requirements define what
must be true, discovery and architecture map how the current project actually
works, and test design owns the full proof plan before delivery begins. That
test design includes TDD/red-first proof when needed, targeted automated checks,
risk-appropriate manual proof, expected outcomes, and the evidence artifacts
needed for signoff. The orchestrator chooses manual proof depth by reasoning
from quality, production risk, and user/business impact, not by optimizing for
speed. That depth may be no manual step, a narrow smoke, full manual signoff, a
browser/user walkthrough, a provider live check, a migration rehearsal, or an
operator-owned release gate. Delivery changes the repo, verification executes
or truthfully marks every relevant proof item, and reviewers challenge
behavior, test evidence, and durable tracker truth. A template may compact or
skip optional specialist roles for small work, but it should not erase
acceptance criteria, dependencies, evidence, or closeout truth from the run
plan.

The test design itself must be verified before implementation starts, either by
the main orchestrator or a designated reviewer. A mechanical "manual smoke" is
not a test design when the risk calls for full signoff or a production-like
rehearsal. Manual proof scenarios should name preconditions, exact user or
agent steps, expected outcomes, evidence such as screenshots/logs/artifacts,
and whether each scenario is closeout-blocking, residual, or operator-owned.

Reviewer output is evidence to adjudicate, not truth to copy into tickets. The
orchestrator should classify each reviewer claim before action: valid
unresolved issue, fixed issue, unsupported/false-positive claim, residual risk,
or informational note. Status markers should be contextual: ✅ means verified
good, fixed, or passing; ❌ means an unresolved blocker or failed required
check; ⚠️ means residual risk, coverage gap, or operator-owned gate; ℹ️ means
context or a rejected/unsupported claim.

For workflow-backed tracker work, attachment is not readiness. A ticket created
with `run_plan_id` and `step_id` is contained under the mirrored workflow step,
but no execution dependency exists until the agent adds dependency edges.
Tracked-delivery planning must bridge child tickets into the workflow spine:
first executable child depends on its step ticket, the next step depends on the
prior step's terminal child tickets, and verification/docs/signoff work cannot
float as a ready branch beside delivery.

The customer feedback baseline is deliberately split. Slack is the canonical
thread for support work, even when feedback originates in Telegram or another
surface, but a configured Slack target is not itself route approval. Non-Slack
feedback needs a matching route or current operator instruction naming the
Slack target before content is copied. Intake agents must inventory every
source media item and forward all route-approved media in the same canonical
Slack handoff message when supported; if the provider path cannot carry every
item, intake stops before partial handoff or asks for explicit text-only
approval. Investigation agents read the full thread before analysis and before
posting the support conclusion. If evidence is missing, they ask clarification
questions in the same thread and reread before deciding. Handoff agents create
tracker tasks only after explicit human instruction in that same thread.
Created tasks and tickets must preserve source, canonical Slack
thread/message, clarification, support conclusion, instruction, task handoff,
source media, forwarded media, and tracked-delivery refs in tracker context or
`references_json`. Once tasks exist, implementation proceeds through
`engineering.tracked-delivery`.

## Context Requirements

Context requirements define how the agent can retrieve history without loading
everything:

```yaml
context_requirements:
  - id: recent_related_runs
    source: runs
    filters:
      domain: media-buying
      statuses: [success, failed]
    fields: [kind, status, summary, output_json, ended_at]
    max_items: 10
    return_mode: compact
```

Supported sources should include runs, resources, artifacts, learnings,
experiments, decisions, action calls, and provider status.

## Steps

Steps are defaults, not a prison. A good step defines:

- `id`
- `title`
- `purpose`
- `instructions`
- `allowed_actions`
- `inputs`
- `expected_outputs`
- `approval_gate` if needed
- `completion_criteria`

Agents can adapt a run plan when the project requires it. If a project repeats
the same setup defaults or extra guidance, the agent should save a workflow
extension. Save a project-scoped template version only when the reusable
workflow method itself has changed.

Template step refs are planning contracts, not executable grants. For example,
`action_refs: [send_email]` points at an `action_contracts` entry. When an
agent derives a run plan, it must resolve that contract to concrete action refs
and MCP grants such as `action.execute` with `action_refs:
[communications.smtp.email.send]`. `runPlan.validate` returns warnings when a
template-derived plan is structurally valid but lacks the grants needed to run.
Set `optional: true` on an action contract only for a branch the workflow can
legitimately skip, such as one of several provider-specific video generators.
Readiness still reports missing setup for optional contracts, but those gaps use
`required_for: optional_action_execution` so the base workflow can remain
execution-ready when the optional branch is not selected.

Workflow templates are inert reusable contracts. They do not act by themselves.
An agent turns a template into concrete workflow state with `runPlan.create`,
then uses `runPlan.start`, `runPlan.claimStep`, granted tools, and tracker
tickets to execute and record work.

## Built-In And Project Templates

StackOS can ship built-in templates through plugins. A project can also save
its own templates. Project templates should record their source and version so
agents can understand whether they are using a built-in pattern or a local
operating method.

Project-specific setup should normally use a workflow extension instead of a
fork. A workflow extension is keyed by `project_id` and `workflow_key`, then
layered over the selected base template when `runPlan.create` materializes a
run. It can provide:

- `input_defaults_json` for stable project refs such as communication routes,
  named targets, default handoff workflow keys, or local signoff conventions
- `selected_context_json` for project guidance, channel purpose, audience,
  data-scope/share boundaries, and safe external refs
- `required_input_keys_json` for inputs that must be present after defaults and
  per-run inputs are merged
- `guardrails_json` for project policy hints the agent must preserve in the
  run plan metadata
- `step_overrides_json` for additive step guidance such as
  `extra_instructions`, `instructions_prepend`, `success_criteria`, or
  metadata
- `template_overrides_json` for an atomic top-level workflow patch. Each key in
  this object replaces the matching key on the base workflow, then StackOS
  validates the resulting effective `WorkflowTemplateSpec` before saving or
  creating a run. Use the same workflow keys agents already see in templates,
  such as `agent_requirements`, `skill_requirements`,
  `skill_preset_requirements`, `steps`, `policies`, or YAML aliases like
  `metadata`, `extensions`, and `ui`.

Extensions do not duplicate or shadow the base workflow identity. They can
bind run setup and can override any workflow template field atomically for the
project. Use `workflowExtension.validate` before saving, then
`workflowExtension.upsert` to persist reviewed project setup. Use
`workflowExtension.get` or the Workflow Templates UI to inspect the extension;
use `workflowExtension.delete` to remove stale or test setup entirely. Use
`workflowTemplate.describe` to inspect the effective workflow after enabled
project overrides are applied. Template detail responses include
`project_extension`, and template summaries include `project_extension_id` /
`project_extension_enabled` so agents can see when a project extension exists.

Use a project template or `workflowTemplate.fork` only when the workflow
identity changes or a genuinely new reusable method should be published. Use an
extension when the same workflow key needs project-specific route refs, channel
context, default inputs, guardrails, agent/skill guidance, contracts, approval
gates, or steps.

When an agent needs to adjust agents or skills for one project, it should
override the workflow's existing `agent_requirements` or `skill_requirements`
inside `template_overrides_json`. These are atomic top-level replacements, so
provide the full desired list rather than a partial fragment. Do not invent a
new context-sharing field for agent guidance; use `selected_context_json` for
project context and the existing workflow requirement fields for agent/skill
contracts.

New workflow authoring is available through the contract interface:
`workflowTemplate.validate`, `workflowTemplate.save`, `workflowTemplate.fork`,
and `runPlan.create`. Use `workflowTemplate.validate({ "key":
"core.project-memory-review" })` to validate an installed/catalog template by
key. Use `template_json` or `template_yaml` when validating a draft before
saving it. The UI can inspect and use templates, but it is not yet a full
visual workflow-builder.

For customer feedback workflows, configure the canonical Slack route and target
as project extension defaults on `communications.customer-feedback-intake`.
The base support workflows remain generic; the project extension supplies refs
such as `communication_route_ref`, `canonical_slack_target_ref`, and
`project_workflow_context` so the agent can create the correct intake run plan
without rediscovering channel setup every time. If the project needs different
agents, step ordering, or support instructions for any workflow in the chain,
put those changes in that workflow's `template_overrides_json` and let
`workflowExtension.validate` prove the effective workflow is still well-formed.

## Examples

SEO templates can describe keyword discovery, page refresh, or search
opportunity analysis. Media-buying templates can describe campaign launch, creative
testing, budget pacing, or account QA. GTM templates can describe list building,
sequence setup, pipeline hygiene, or launch retrospectives.

All of them should use the same StackOS primitives: context, resources,
artifacts, actions, approvals, learnings, experiments, and run plans.

## Validation

Template validation should check:

- stable keys and versions
- valid input schema
- known plugin/capability/provider/action references
- bounded context filters
- approval gates referenced by steps
- no embedded secrets
- no domain-only assumptions in core fields
