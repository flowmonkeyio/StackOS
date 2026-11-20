# Workflow Templates

Workflow templates are reusable setup for agent work. They are not hidden
automation. A template gives the agent a strong starting structure, then the
agent creates a concrete run plan for the current project and goal.

Workflow authors must first read the canonical
[`Product Contract`](./product-direction.md#product-contract). Templates
organize a reusable method inside that product model; they do not justify
workflow-specific core logic, bespoke UI, or code enforcement of agent
judgment.

## Start With The Experience

Workflow authoring starts with the problem AI should help solve, not a list of
agents or tools. Write the small `experience` contract before designing steps:

- `problem`: the failure, friction, or decision the workflow addresses
- `outcome`: the useful result and its side-effect boundary
- `operator_path`: what the person provides, sees, decides, and approves
- `agent_path`: what the main agent must discover, coordinate, hand off, and
  verify
- optional `why_ai`, `progress_signals`, `recovery`,
  `safe_stopping_points`, and cross-workflow `handoffs`

Built-in public workflows also own a reviewed `public` contract with
`audience`, `setup`, `prerequisites`, `proof`, and `featured`. The website
catalog is generated from these fields. Do not maintain separate marketing-copy
maps or global word-replacement rules; they hide weak source contracts and can
corrupt legitimate names.

Keep the experience compact. It should remove guessing, not prescribe every
conversation. The main agent may ask bounded questions and adapt the plan when
the request or project calls for it.

## Authoring Source

The canonical workflow authoring guide is the StackOS operation
`workflowTemplate.authoringGuide`. Agents should call it through
`toolbox.call`, REST, or CLI from any repository, then validate drafts with
`workflowTemplate.validate`. The guide first requires one explicit intent mode
and returns the deterministic setup phases, agent materialization policy,
prerequisite persistence policy, and completion evidence. Use its default
compact response for mode selection and setup; request `response_mode=raw` once
after selecting `author_project` or `publish_plugin`. Keep this page as a tested
repo-local summary, not a competing authoring path.

## Complete Workflow Package

A complete workflow package is more than workflow YAML. Build it as one
contract:

- plugin manifest capability/provider/resource/action/navigation entries
- workflow templates, agent presets, and skill presets in the standard plugin
  directories
- resource schemas with `ui_schema`, `record_kind`, and `agent_guidance` when
  StackOS is the declared durable owner; an external-system-of-record package
  may intentionally declare none
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

1. Reconfirm the product contract and inspect the existing canonical owner,
   active consumers, related workflows, presets, resources, and operations.
   Record the demonstrated gap before introducing a new primitive or parallel
   workflow.
2. Define the reusable boundary, generic/plugin facts, project overlay facts,
   and operator adaptation points.
3. Name the operator-facing job and closeout first. If one user request would
   need several new workflow templates to complete, collapse those stages into
   one workflow with ordered steps.
4. Inventory existing plugins, resources, actions, workflows, agent presets,
   skill presets, and provider setup so names and connectors are reused.
5. Model durable state and invariants first; then write workflow steps around
   those records and guarantees. Name the correct durable owner before choosing
   a StackOS resource: resources are future memory only when StackOS owns the
   record. Artifacts hold bulky content and must be indexed by resources when
   the output should be discoverable later in StackOS.
   Artifact creation is not the workflow scratchpad. Step `output_refs` may
   describe logical outputs that stay in chat, local project working files,
   tracker evidence, or resources until the workflow deliberately preserves
   them. Use StackOS artifacts for approved outputs, final packets, durable
   evidence, operator-approved drafts, or intentionally retained blobs. When a
   template has many iterative outputs, set
   `metadata.artifact_grant_policy: explicit` and add
   `metadata.mcp_tool_grants` for `artifact.create`, `artifact.update`,
   `artifact.archive`, and `artifact.supersede` only on steps that truly need
   durable artifacts. Existing templates that omit this policy keep the
   backward-compatible behavior where steps with `output_refs` receive artifact
   lifecycle grants automatically. Do not prescribe local scratch paths in a
   generic StackOS workflow; local projects own those conventions through their
   agent instructions.
6. Separate decision, review, and execution roles. The orchestrator coordinates
   the package, but specialist presets own bounded reasoning or mechanical
   duties.
7. Reuse or adapt existing generic presets before inventing new agents. Add a
   specialist role only when it owns a materially distinct boundary.
8. Wire runtime behavior explicitly with action refs, resource refs,
   approval gates, grants, readiness checks, tracker evidence, and run-plan
   outputs.
9. Verify against the actual domain source, operator brief, or official
   provider documentation. Do not sign off only against the code just written.
10. Run a black-box agent audit. Give a fresh agent a realistic vague request,
   no workflow key, and no design rationale. It should find the intended path,
   prerequisites, context, tools, outputs, approvals, safe stopping point, and
   recovery without repository-only hidden knowledge. Record where it searched,
   guessed, or mistook structural validity for execution readiness.

Workflow identity should follow the engineering pattern: one template represents
one complete piece of work, not a private chain of reusable stages. A domain may
ship multiple workflows, but each one should be independently invokable and
able to reach its own closeout. Optional branches such as generated images,
provider execution, extra review depth, or channel variants belong inside the
same workflow when they are part of the same operator request. It is fine for
different workflows to duplicate internal step patterns; avoid composable
workflow fragments that make one deliverable require several workflow runs.

The closeout should prove future accessibility. If StackOS owns something a
project will need later, it must write a queryable resource record that stores
the summary, state, decision refs, artifact refs, approvals, and follow-up
hooks. Do not leave StackOS-owned durable truth only in chat, local files, or
artifacts. If an external backend is explicitly the system of record, that
backend owns the durable packet and contents; StackOS closeout returns only the
safe external refs, state, action/approval proof, and recovery/handoff refs.

## External System-Of-Record Workflow Packages

The [agency package](../plugins/agency/README.md) adds minimal nonfinancial agency
and client-project setup using the same resource/persist/retrieve pattern as
other setup workflows. Those descriptive associations do not create nested
StackOS projects or inherited access. Finance may be set up independently or
from an agency root; its external financial authority is unchanged. Optional
project attribution is a financial relationship in that external record, not
financial data in agency resources.

Some workflows coordinate a domain whose records must live outside StackOS. A
package may intentionally have no resource contracts and no resource/artifact
write grant when it declares one selected external backend. The package must:

- identify the selected backend and a safe external workspace or record ref as
  run inputs; do not turn a host path into a StackOS path or filesystem action;
- keep `metadata.artifact_grant_policy: explicit`, omit finance/domain content
  from workflow, tracker, resource, and artifact output, and return only safe
  external/provider/action/approval/proof refs and bounded lifecycle state;
- make any temporary provider-file staging a bounded connector transport with
  URI containment, host-owned file authority, explicit cleanup, and recovery—
  never a hidden artifact or new storage layer;
- distinguish a provider action's generic sanitized action audit from the
  external record of authority. The audit may retain bounded transport fields
  under the executor contract, but cannot be described as the domain ledger or
  approval record; and
- name technical action/step gates separately from the external business-scoped
  approval evidence. A gate does not bind an unverified payload or allow an
  agent to self-approve.

The finance workflows are the current example: `local-json` is host-managed
external storage with `finance.json` as the sole authoritative local financial
record, including mutable setup. `FINANCE.md` is guidance/navigation; reports
are derived, CSV is import/export, and original attachments remain evidence.
Packet refs resolve to stable JSON record IDs and scoped versions, never separate
editable masters. Providers own their actual state; JSON keeps dated observations.
First-time setup uses a new workspace and never overwrites existing files.
Local bookkeeping remains
`prepared/unposted`; Stripe and IMAP are explicit connector routes; and no
finance resource, artifact, ledger, filesystem connector, scheduler, or custom
UI is introduced.

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
- `experience`
- `public` for built-in public catalog workflows
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

Use `stackos.workflow-orchestrator` when a workflow needs the normal selection,
readiness, handoff, approval, stopping, verification, and recovery loop. An
empty `applies_to_steps` means the main-agent contract spans the whole workflow.
Keep a specialized orchestrator only when the domain has materially different
sequencing, evidence, safety, or closeout mechanics, as branding, campaign
production, and tracked engineering delivery do.

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

Tracked delivery keeps planning and delivery roles required while architecture,
test-design, and delivery-review specialists are recommended depth tools. The
main agent selects them from architectural and production risk, records a
reason when omitting them, and still produces each workflow disposition. This
keeps the SDLC complete without forcing a subagent or ceremony for every small
change.

`engineering.tracked-delivery` compiles material operator input into one
effective, precedence-aware contract. Current invariants, product decisions,
implementation constraints, prohibitions, authority, deferrals, supersession,
and acceptance cases retain the traceability needed for the delivery. Retired
entries remain auditable but are not supplied beside current requirements as if
both still applied. Material unresolved contradictions block implementation.

For repository-bound delivery, the agent identifies the integrated candidate
covered by verification. Executed evidence records its HEAD commit, staged and
unstaged diff checksums, untracked-file manifest checksum, covered paths,
migration head, generated-contract revision, and capture time. A material
covered change makes affected proof stale and returns the workflow to the
earliest affected gate. StackOS validates the declared output and evidence
contracts; the agent decides which evidence was affected from project context.

The test design itself must be verified before implementation starts, either by
the main orchestrator or a designated reviewer. A mechanical "manual smoke" is
not a test design when the risk calls for full signoff or a production-like
rehearsal. Manual proof scenarios should name preconditions, exact user or
agent steps, expected outcomes, evidence such as screenshots/logs/artifacts,
and whether each scenario is closeout-blocking, residual, or operator-owned.
For browser-assisted platform flows that require login, those preconditions
must also name the stable StackOS browser `profile_key`, state whether the
operator must log in once, and say whether reusing the resulting cookies/session
is required for closeout or remains an operator-owned prerequisite.

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
an `activates` edge makes the first executable child ready while its parent
step is active, `blocks` edges hold work until real prerequisites complete, the
next step blocks on the prior step's terminal child tickets, and
verification/docs/signoff work cannot float as a ready branch beside delivery.

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

Template-derived run plans keep context grants source-specific: each context
requirement receives its own `context.query` grant and field allowlist. Do not
merge resource fields such as `data_json` into a learning grant. Resource-backed
context supports `plugin_slug` and `resource_keys` filters so agents can reuse a
bounded prior opportunity set without loading unrelated project records.

## Steps

For optional automatic task execution, step `extensions.agent_assignment` selects
one existing `delivery_agent` or `review_agent`. A task-level `agent_assignments`
map may explicitly override it at handoff; unassigned mirrors use delivery_agent.
This is frozen role selection, with models/instructions in project/task settings.
Native source settings take precedence over task and project fallbacks; omission
retains inheritance and explicit null clears only the fallback. Setup/Update and
scheduled main-agent launches use the separate saved `delivery.main_agent`
preferences and shared host capabilities. Neither template updates nor picker
catalog refreshes rewrite saved selections or admitted native snapshots.
It does not introduce phase roles or runtime branches on workflow/step names.
The generic runner traverses dependencies, then asks the selected agent to execute
the remaining frozen mirror contract once after accepted descendants. Only the
run-plan owner records lifecycle and validates declared outputs. Source template
changes apply to future plans; use an explicit task map for an older frozen plan
without rewriting its snapshot. See [task automation](task-tracker.md#automated-task-delivery).

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

For execution, the claimed step packet is the brief. Use its resolved inputs,
bounded context, outputs, criteria, grants, and dependency handoffs; call
`runPlan.getStep` only when that packet reports truncation.

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
Readiness keeps missing setup for optional contracts on the detailed action,
using `required_for: optional_action_execution`; it does not place those items
in the top-level blocker list.

When several providers are alternatives for the same job, set `route_group`
and `route_key` on each action contract. Contracts with the same group and key
form one concrete route. Readiness requires all non-optional actions in one
route when the group is required. If every action in the group is optional, the
group describes optional execution choices and does not block preparation.
Missing alternative routes stay visible on their detailed route/action records
and do not enter the top-level blocker list; the agent must still validate the
selected concrete route before a side effect.

Optional interviews use the same principle. `interview_mode: auto` lets the
agent interview when missing experience, judgment, surprise, stakes, or
tradeoffs would materially weaken the result. `required` always interviews;
`skip` records why existing approved evidence is sufficient. Do not make every
workflow conversational by default.

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
`workflowExtension.upsert` to persist reviewed project setup. Upserts are safe
partial updates by default: omitted fields are preserved. Use
`clear_fields_json` to intentionally clear a field, or `update_mode="replace"`
only for a reviewed full rewrite because omitted fields reset to defaults. Use
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

Follow [canonical project guidance consumption](./agent-operating-model.md#canonical-project-guidance-consumption)
when selecting or consuming those references. Keep authoritative content at its
existing owner; carry safe refs, scope, current source revision/digest and
approval evidence through selected context and bounded handoffs. An effective
template digest or a frozen run snapshot does not prove that a referenced
resource or artifact is still current. Re-read affected source owners at
dispatch, resume and before application. Missing optional brand guidance does
not block unrelated work; do not turn reference sharing into new access or
execution authority. Infrastructure setup only validates/materializes guidance;
authorized prerequisite setup persists applicable refs through existing owners.

The optional `source` argument on template and extension operations filters the
template origin (`plugin`, `project`, `user`, or `repo`). It is not provenance;
use `created_by` for the extension write actor.

## Agent Workflow Setup Lifecycle

`workflowTemplate.authoringGuide` is the canonical operating contract. Call it
before setup or authoring; it supplies `workflow_setup_protocol`,
`agent_materialization_policy`, `prerequisite_persistence_policy` and
`setup_completion_contract`. For repository work without a running daemon, the
same definitions live in
[`stackos/workflows/authoring_guide.py`](../stackos/workflows/authoring_guide.py).
The phase summaries below are navigation, not another copy of that procedure.

The guide's `project_setup_method` authors the shared setup/update agent prompt,
bounded input contract, mode instructions, session notices and concrete question/
result wording for both existing-session and managed entrypoints. Consumers load
it with `response_mode=raw`; they do not keep separate prompt text. Inspect repository
intent and current setup before inferring workflows from ordinary goals and the
catalog's outcome/use boundaries, rather than requiring an exact operation-name
match. A local development goal can select applicable engineering setup without
executing a delivery or build; a one-off command does not need a new workflow just
because the catalog lacks it. Setup readiness and execution authority stay separate.
Resolve apparent instruction conflicts using precedence, scope and current project
configuration/scripts. Follow clear authority and flag stale docs; a material
unresolved choice needs a bounded `needs_input` question while independent setup
continues. An unattempted build is not a build failure.
Harmless questions need no workflow run; a newly relevant goal can set up the
appropriate method within current authority. The public getting-started guide
remains the human walkthrough.

The shared method also applies the
[internal handoff rules](agent-operating-model.md#internal-handoffs-and-complete-deliverables):
keep scope/status, identities, decisive outcomes, missing obligations and next
action inline; retain detailed source-to-target mappings and checks once in the
existing setup receipt. Both entrypoints preserve complete adapted guidance,
required reports, partial/reused evidence and activation obligations. Continuation
uses current amendments and remaining work with retained identities, not a replay
of unchanged history. A concise report does not prove native consumption or
authorize adoption; missing/stale detail remains an explicit gap.

### 1. Workflow Infrastructure Setup

Bind the intended workspace, describe the effective workflow, inspect its
extension and resolve its agents/main-agent guidance. Adapt required, recommended
and selected optional roles under the guide's materialization policy, preserving existing
host content. Host files are execution contracts, not prerequisite/state stores.
Discover project instruction entrypoints and routed scoped guidance rather than
requiring a particular filename or directory tree. Resolve package references from
the effective workflow or preset response origin, not an assumed source checkout.
Historical setup records and host or browser references are navigation aids, not
current binding, readiness, or authority proof.
Prove readiness with structural and strict read-only `runPlan.validate` calls;
return the guide's complete setup proof, including deferred inputs and exact
preset mappings. Do not create a run plan, workflow tracker work or business
output as setup proof.

Ordinary Setup and Update request `ready_to_use`, including adaptation, canonical
configuration and verified activation. Only explicit `infrastructure_only` stops
at preparation; that invocation creates no execution state and gains no execution
authority. Missing local-admin capability remains an actionable gap for the
authorized owner, never a silent scope downgrade. Resolve the safe activation
method from the effective workflow/presets. Engineering's method lives in the
`setup_activation_method` policy of `engineering.tracked-delivery` and its presets.
Inspect saved defaults, exact role bindings and local lifecycle rules at their
existing owners; use canonical reviewed adoption/readback. Task overrides cannot
prove missing project defaults. Preserve compatible customization, supported host
preferences and frozen queued/running sessions.

First reuse actual completed delivery, independent review and closeout evidence
only for claims covered at the current project/source/default/native/environment
basis. Record covered and missing claims, then execute only missing or invalidated
small harmless native/configuration/behavior checks within the existing owning
setup/task. Use existing setup/native continuation when fresh loading is needed.
Do not automatically create a separate full verification task/workflow, tracker
parent or review workflow merely to establish readiness, or nested queue/wait/drain
machinery solely for proof. Existing product/adaptation tasks retain independent
review; standalone setup follows its effective method and genuine current evidence.
Aborted, incomplete, simulated or another project's evidence cannot become completed
activation. Distinguish prepared files, saved readback, actual native consumption,
reused evidence and newly executed checks in the existing receipt. Missing required
proof leaves `ready_to_use` unfinished; `activation_pending` is not success.

Engineering's effective `setup_activation_method.config_json.required_role_claims`
declares `reviewer_verification` with `role: review_agent` and
`when_execution_requirements: {filesystem_write: true}`. Applicability comes from
canonical saved role requirements, never a task override or command parsing.
Setup declares `execution_requirements.filesystem_write=true` for test/scratch-running
reviewers through the canonical defaults owner. Managed role proposals carry this
same typed requirement; adoption preserves omission and saves explicit values
atomically with source adoption, rejecting incompatible execution before commit.
Explicit false retains inspection-only behavior.
When applicable, its reviewer executes a bounded,
project-selected representative tool/scratch/isolated-fixture check using the
selected native settings and normal approvals. A successful generic conversation
or a setup coordinator's `verification_status` cannot establish that capability.
Keep the observation in the existing setup receipt and native result; do not add a
separate proof task, receipt store, full workflow canary or all-host launch matrix.
An inspection-only project adaptation must explicitly select its applicable method
and claim coverage; core never infers capability requirements from shell commands.

`SetupActivationCheck.execution` binds role, agent, host, workspace binding, source
ref/digest when source-backed, and the digest of resolved execution settings.
The execution binding names the actual approved source/task tree, which may be a
secondary same-project worktree; the setup basis retains the primary folder and
canonical saved-default selection. A different or foreign binding cannot relabel
the tree actually launched.
Inline hosts use their actual frozen snapshot without invented source refs. A
`task_phase` reference identifies `task_id`, `ticket_id`, `attempt`, `phase`,
`invocation_id`, and the canonical native result's `outcome_digest`. It resolves
current and retained historical `automatic_delivery` receipts at their existing
ticket owner. Completion, actual native session/snapshot, role/settings, current
saved selection and project/environment must match. Existing `native_request` and
`workflow_step` references still cover their original claims; a generic coordinator
or workflow result does not acquire reviewer identity by assertion. A role-bearing
`native_request` is accepted only with the actual specialist's frozen source,
settings, instructions, native session, invocation and retired logical outcome.

Fresh managed setup needs no prior reviewer task. Its verification coordinator
returns one `role_verification_request` containing `role`, exact missing `claims`,
project-selected bounded `instructions`, `environment_ref` and `evidence_ref`.
The existing setup owner resolves and freezes that selected specialist, then uses
its finite request continuation for a `role_verification` turn in a distinct
native session. The native request records the real role snapshot and observations.
After retirement the original coordinator session resumes with
`completed_role_verifications`, including the canonical reference and execution
binding. Completion combines this evidence with covered generic verification in
the existing workflow receipt. Matching completed evidence is reused instead.
Failed/cancelled/stale/wrong-role checks cannot complete setup; explicit recovery
resumes the same frozen specialist and returns to the retained coordinator.
This adds no proof task, workflow, evidence store or replacement runner.

The reviewer records an observation in its JSON-encoded `outputs`:

```json
{"setup_activation_checks":[{"claims":["reviewer_verification"],"status":"passed","environment_ref":"<actual environment identity>","evidence_ref":"<command, exit status and observed results>"}]}
```

The setup check names the same environment and evidence ref. A generic success,
missing observation, foreign project, failed/cancelled/simulated execution or
mismatched snapshot cannot cover the role claim. Reuse completed matching evidence;
when relevant source, settings, required capability or environment changes, retain
the prior observation and execute only the affected missing checks. Refreshing
saved readback cannot relabel an old native source as newly loaded. A denial stops
that write and dependent proof; continue authorized alternatives and unaffected
work while leaving unavailable required proof unfinished. Explicit review-only
replacement (`replace_review`) belongs to the authorized recovery owner for a
quiescent stopped review, preserving candidate, completed delivery, failed reviewer
history and consumed repair allowance. Source preparation, saved defaults, native
consumption and installed activation remain distinct.

For other domains use their own safe method conditionally. Technical setup never
authorizes unrelated finance transactions, publication, communications or trading.
An unavailable safe method/capability remains explicit; genuine per-run business
inputs may remain deferred separately from operational setup. No all-role launch
matrix or replacement runner is needed.

### Applied setup evidence

`ProjectSetupRepository` owns one current receipt per project/workflow key.
`projectSetup.get` is the shared Home/agent read. Template selection, an active
version pointer, a source hash, or a reminder dismissal never establishes applied
setup. Legacy setup without verified provenance remains unknown and can be
inspected/adopted by the setup agent.

A successful repository result records the effective template origin/version,
main-agent and specialist sources, meaningful extension provenance, bounded source
snapshots, local adaptation/file provenance, role decisions, source-obligation
coverage, host syntax/registration/reference and behavior checks, activation
evidence, the separate readiness tuple, deferred input keys, time and attempt/result
refs. Snapshot digests cover canonical JSON. Snapshots are receipt-owned comparison
evidence, so the prior applied base remains available after a source disappears;
live overrides remain under their existing owners. There is no second source
version registry or empty extension for bookkeeping.

Report each source's exact inspected `kind`, `key`, `origin`, `ref`, `version`
and `digest`; omit its `content` body to avoid echoing large workflow and role
documents through the agent tool call. The receipt owner compares the complete
source set against current authoritative sources, restores omitted bodies and
revalidates the full result before storing it. Supplied bodies still require a
matching digest. Unknown, duplicate or changed sources reject without advancing
the receipt. Stored snapshots stay complete; this does not waive source reading,
obligation coverage, host proof or ownership and revision checks.

An update compares previous applied snapshots, current local choices and new
effective template/main-agent/specialist sources. Verified legacy setup can be
adopted without forced rewrites; unknown historical versions remain unknown.
Map the full applicable source obligations to exact adapted targets and proof,
preserving compatible overrides and declared resilience rules with their original
conditions and owners. Adaptations and source-permitted omissions need reasons;
material unresolved loss cannot complete the affected workflow. Finish independent
workflows and ask a concrete question for the incompatible choice. This is agent
judgment with receipt evidence, not a runtime semantic policy engine.

Agents judge compatible adaptations and source-policy exceptions. Explicit
unresolved obligations, missing required role mappings, failed checks and
unjustified required omissions prevent success. This structural validation does
not judge semantic completeness or prove actual host loading; the executing agent
and independent review must check the underlying evidence. A known reload need
or unknown activation stays visible as `activation_pending`.

Results advance each workflow independently using its expected receipt revision.
An already-running user agent admits one project-scoped attempt through
`projectSetup.start`, then reports checked results through `projectSetup.report`.
The request-owned, versioned setup namespace retains session/request lineage,
receipt revisions, progress, concrete questions and result refs. Concurrent entries
attach to the same owner without receiving writer authority; stale/foreign reports
cannot change receipts. Attempt success rechecks source and receipt freshness.
Existing-session questions and answers stay in that session. The shared
`projectSetup.get` state is also consumed by Home; no external conversation navigation,
business run or tracker delivery task is invented for infrastructure setup. See the
[operation contract](operations.md#project-setup-receipts-and-reminders) for admission,
ownership and response details.

If that bridge disappears, the operator first stops its setup work in the agent
app. Console cancellation then requires explicit stopped-work acknowledgement and
the revision captured when confirmation opened. It revokes only that request's
claim, retains successful receipts and history, and records possible file changes
without claiming rollback or observed process exit. A conflict requires a fresh
confirmation. The cancelled record remains visible; **Set up again** is a separate
deliberate launch, with normal source and receipt validation. No session takeover,
rebinding or automatic replacement occurs.

Home's managed entry invokes one request-native coordinator with the same
`workflowTemplate.authoringGuide` setup-method reference. It freezes the supported
project agent selection and folder association, queues through shared task capacity,
and records native session/usage alongside setup reports. Closing Home does not
cancel work. Cancellation fences the owning invocation and preserves successful
workflow receipts and partial reports; unknown native retirement stays explicit.
Managed launch does not create a business workflow run or delivery task.

Managed questions are answered through revision-checked `projectSetup.answer` in
the console. The answer links a successor request under the same attempt and
resumes the exact native session only after the previous invocation retires.
`projectSetup.continue` recovers remaining partial, failed, interrupted or cancelled
work with the same frozen selection. Confirmed never-started cancelled/missed
launches use an explicit Start again/Retry setup action: the same owner links one
first-launch successor only when no callback, native evidence, resume identity or
possible edits exist. Unknown or possibly started work still requires confirmed
retirement and the exact native session; missing identity never permits fallback.
Prior execution/usage and successful workflow
receipts remain intact; changed source/receipt evidence requires revalidation.
Recovery reports possible partial edits and never invents rollback or launches a
competing session. Existing-session questions continue in their original agent.

Failed/unfinished results retain the previous successful sources and separately
record possible partial edits. They can still be recorded when the selected folder
becomes unavailable. Available updates compare the receipt with current effective
template/preset/extension sources, including same-version content and origin
changes. Missing sources remain diagnostic uncertainty rather than fabricated
success. Writes reserve project availability against canonical cleanup.

Malformed or unreadable sources return bounded repair diagnostics while retaining
applied evidence and partial-result/reminder write responses. Comparison follows
current declarations when previously selected optional presets become required or
are removed; new success reports still require valid current optional selections.

The two permanent project reminder timestamps control unsolicited presentation
only. Directoryless projects are ineligible; an unavailable selected folder keeps
receipts and preferences and exposes folder repair. Receipt rows cascade through
the existing project cleanup, while encrypted whole-database backup/restore includes
the rows and preferences. Repository folders and existing backups are preserved.

### 2. Workflow Prerequisite Setup

Collect only missing durable inputs for the selected workflow and persist them
through their declared owner. Keep per-run choices out of defaults, omit unknown
values and follow explicit prerequisite handoffs. Setup of one workflow does
not authorize another workflow's execution or recurring business output.

### 3. Workflow Operation

Use concrete occurrence inputs, selected-route execution readiness and strict
validation before creating a run, or resume the intended existing run through
its consistency/recovery path. Execute with the resolved roles, active step
grants and approvals; close with truthful outputs and evidence.

Use workflow extensions for setup-time context that should be applied to future
run plans. Use run-plan-gated resources, artifacts, decisions, learnings, and
actions only after a run exists and the active step grants those writes.

Before authoring or mutating workflow state, select one explicit mode from the
canonical guide: `setup_existing`, `customize_existing`, `author_project`,
`publish_plugin`, `one_off_run`, or `execute`. A custom project workflow does
not imply plugin source changes. Use `customize_existing` when an extension can
preserve the workflow identity; use `author_project` only for a genuinely new
project/user reusable method; use `publish_plugin` only for distributed package
behavior. Changing modes requires explicit operator intent or a reported safe
handoff.

New project/user workflow authoring is available through the contract
interface: `workflowTemplate.validate`, `workflowTemplate.save`, and
`workflowTemplate.fork`. Use `workflowTemplate.validate({ "key":
"core.project-memory-review" })` to validate an installed/catalog template by
key. Use `template_json` or `template_yaml` when validating a draft before
saving it. An explicit operator request or approval authorizes saving the complete
validated draft through the normal bound-project toolbox; do not ask again when
that authorization is already clear. `workflowTemplate.save` and
`workflowTemplate.fork` are project-scoped setup writes, not local-admin-only
tools or run-step grants. Both `source="project"` and `source="user"` records
belong to the selected project. Saving a template creates no run, ticket, or
provider action and grants no execution permission. After save, follow infrastructure setup
and use read-only `runPlan.validate`; `runPlan.create` belongs only to operation
or an explicitly authorized execution smoke. The UI can inspect and use
templates, but it is not yet a full visual workflow-builder.

For an existing JSON draft, pass `{"template_json": <complete draft>, "source":
"project"}` to `workflowTemplate.save`. A local CLI fallback is
`stackos ops call workflowTemplate.save --project <id> --input <arguments.json>`;
the file contains that operation-argument wrapper, not the bare workflow. The
CLI handles local authentication; never ask for the daemon token. Older builds
that report `local-admin-workflow-template-write` for this setup operation need
an app/agent-session update, not another consent prompt or a fabricated grant.

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
