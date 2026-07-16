# Agent Presets

Agent presets are reusable operating contracts for agents that call StackOS
tools. They are not daemon-run agents, not hidden orchestration, and not final
project prompts.

Every bundled preset is generic and must be adapted before use:

- `generic_preset: true`
- `project_adaptation.required: true`
- `project_adaptation.do_not_use_verbatim: true`
- required adaptation references such as `AGENTS.md`, `stackos:stackos`, and
  project-local docs or skills when present
- a prompt assembly order:
  `generic_agent_preset -> project_adaptation_overlay -> workflow_agent_requirements -> current_tracker_or_run_plan_context -> user_request`

Every bundled preset also declares `role_class`:

- `reasoning`: makes bounded judgments and returns the rationale
- `mechanical`: performs a defined transformation or handoff without taking
  over strategy
- `review`: challenges another role's result independently and returns claims
  for the orchestrator to adjudicate

`role_class` is an expectation signal, not a model selector. The actual
`prompt_contract` still owns mission, boundaries, handoff inputs, handoff
outputs, success criteria, and self-checks.

The adapting agent must rewrite the generic role for the current project:
technology stack, rules, documentation references, available MCP tools,
workflow/run-plan model, tracker task/ticket conventions, verification
commands, and release expectations.

Workflow infrastructure setup uses one deterministic materialization policy:

- every required role must be materialized when the host supports local agents,
  or represented by an explicit session-only fallback with the cross-session
  limitation reported;
- recommended roles are materialized by default unless the adapting agent
  records a project, host, or risk reason for omission;
- optional roles are materialized only when selected by the operator, project
  extension, or current workflow branch;
- agent presets become specialist roles, skill presets become main-agent
  orchestrator guidance, and installed host skills remain skills;
- every materialized target records the applicable workflow key(s), preset
  key/version, requirement level, project references used for adaptation, and
  exact host target; syntax/registration and any reload requirement are verified
  before setup is reported complete.

A request to set up workflow infrastructure or local agents authorizes reviewed
host-local execution contracts when project guidance permits them. It does not
authorize `.stackos/agent-presets` or `.stackos/skill-presets` overrides, global
domain agents, a new workflow template, or workflow execution. Repository-local
StackOS preset overrides require an explicit checked-in override request.

`recommended_tools` are StackOS operation references. Some hosts mount those
operations as direct MCP tools, while the StackOS bridge may expose only
`workspace.startSession`, `workspace.resolve`, `toolbox.describe`, and
`toolbox.call`. Treat the operation refs as the intent-level tool list: call
them directly only when the host exposes them, otherwise inspect the exact names
with `toolbox.describe` and invoke them through `toolbox.call`.

Setup guidance must be scoped to the selected preset or workflow. Generic
agent responses explain host adaptation, the toolbox boundary, tracker/run-plan
state, and skill-preset resolution. Domain chains such as feedback intake to
support investigation appear only for presets or workflows in that chain.
Agents should not have to filter unrelated support or engineering setup from a
branding, SEO, media, or GTM handoff.

Provider-action presets should prefer stable `action.list` refs plus
`action.describe`/`action.validate` before execution. External provider action
outputs are file-backed by default for MCP and REST calls, so presets must tell
agents to inspect the returned file paths before rerunning provider calls and
to call `schema.get` with `schema_ref` only when they need the response-file
envelope schema. CLI calls default to raw inline output unless an explicit
output policy says otherwise.
When repeated calls share credential, provider scope, output policy, request
budget, or reusable execution context, use
`executionContext.discover`/`executionContext.resolve`
or create a context and pass `context_ref` to `action.run` or
`action.execute`. Keep endpoint payload in `input_json`; keep provider scope in
provider context or the execution context.

Bundled presets must not assume customer repositories contain StackOS' own
documentation files. StackOS operating guidance comes from the installed
`stackos:stackos` skill and MCP tool descriptions. Repo-local docs such as
`docs/README.md`, `docs/task-tracker.md`, or local skills should be used when
they exist, but absence of those files is a project-context gap, not a broken
StackOS install.

## Engineering Setup

StackOS presets are not local agent files and they are not registered back into
StackOS as daemon-managed agents. They are generic contracts that a main agent
may adapt into whatever the host/project supports: `.codex` agents, markdown
frontmatter files, plugin-specific agent files, or no local files at all.

Engineering setup uses the same workflow path as other domains. Start with the
workflow that matches the work, resolve its agents with
`agentPreset.resolveForWorkflow`, then create/start a run plan when executing
the work. Use `communications.customer-feedback-intake` when customer feedback
needs a canonical Slack thread, `support.issue-investigation` when the thread
needs an evidence-backed conclusion, `support.delivery-task-handoff` when a
same-thread operator instruction asks for task creation, and
`engineering.tracked-delivery` once work is scoped for implementation,
verification, review, and release. The workflows reference generic presets as
required and recommended roles, but the workflow owns the flow.

When an operator explicitly asks to use a workflow, engineering workflow,
StackOS workflow, or "the workflow", agents must create or resolve the
workflow-backed run plan before creating tracker tickets. All discovery, design,
delivery, verification, and closeout tickets for that work belong under the
workflow task/run plan from the start. Direct tracker tasks are valid only when
the operator asks for task/dependency tracking without invoking a workflow.

The tracked-delivery workflow uses this baseline:

```text
scope
-> requirements and flow definition
-> codebase impact discovery
-> tracker ticket planning
-> architecture/design
-> design review
-> test and verification design
-> delivery
-> verification
-> delivery review
-> tracker truth audit
-> release closeout
```

The customer feedback/support workflow chain uses this baseline:

```text
incoming feedback
-> route and media preflight
-> canonical Slack thread
-> intake reaction
-> full-thread read
-> same-thread clarification when needed
-> support investigation conclusion in the same thread
-> separate same-thread instruction for task creation
-> delivery task creation
-> task handoff message in the same thread
-> task-created reaction
-> engineering.tracked-delivery handoff
```

This baseline is intentionally project-neutral. When adapted into another
repository, keep the engineering mechanics and replace the stack, docs, test
commands, host-agent format, and release/signoff rules with that project's own
sources. Do not carry product or domain facts from the project where the
baseline was derived.

For non-Slack feedback, communications agents must not infer route approval
from a resolvable Slack target. `communicationTarget.resolve` answers where a
named target would send; `communicationRoute.*` or a current operator
instruction answers whether this source is allowed to go there. The same
preflight owns media fidelity: every inbound image, document, video, audio,
voice note, screenshot, URL, or provider file ref must either be
forwarded in the same canonical Slack handoff message when supported, or become
an explicit blocker/waiver before the workflow continues.

The core support and engineering preset subset is deliberately small:

| Preset | Role |
| --- | --- |
| `communications.workflow.customer-feedback-intake` | Normalizes customer feedback into one route-approved Slack support thread, preserves source media, and returns support investigation refs. |
| `support.workflow.issue-investigator` | Investigates the full canonical thread and project evidence, asks same-thread clarifications, and posts a support conclusion with root-cause evidence or bounded uncertainty. |
| `support.workflow.delivery-handoff` | Converts a same-thread operator instruction and support conclusion into delivery-ready tracker work with durable chat refs. |
| `stackos.sdlc.requirements-flow-definer` | Defines actors, flows, acceptance criteria, non-goals, and evidence expectations. |
| `stackos.sdlc.codebase-explorer` | Maps real execution paths, ownership, downstream fallout, tests, and docs before changes. |
| `stackos.sdlc.planning` | Converts requirements and impact evidence into tracker tickets with dependencies and definition of done. |
| `stackos.sdlc.architecture` | Chooses and challenges the project-native design, canonical owner, contracts, rollout, and validation plan. |
| `stackos.sdlc.test-designer` | Owns the full proof plan: TDD/red-first gates, automated checks, risk-appropriate manual proof, expected outcomes, and signoff evidence. |
| `stackos.sdlc.delivery` | Implements scoped tickets, debugs root causes, verifies the diff, and records tracker/evidence updates truthfully. |
| `stackos.sdlc.delivery-reviewer` | Reviews design and delivery across behavior, contracts, tests, tracker truth, docs, security, and release risk. |

Use this subset as the only support/engineering agent set for a project.
Workflows pick the roles they need. For example, customer feedback intake uses
the communications intake role; support investigation uses the support
investigator and may use codebase exploration; delivery task handoff uses the
support handoff role and may use planning; tracked delivery uses requirements,
discovery, planning, architecture, test design, delivery, delivery review, and
release closeout coordinated by the main agent. Test design is the single owner
for test planning: it maps accepted requirements to automated proof, red-first
TDD slices when needed, manual proof depth, expected outcomes, and evidence
links, screenshots, or logs. The main orchestrator chooses that proof depth by reasoning from
quality, production risk, and user/business impact; a quick smoke is not a
substitute for full manual signoff or rehearsal when the risk calls for it.
When browser-assisted platform work depends on login state, the test plan must
name the stable StackOS browser `profile_key`, whether the operator must log in
once, whether cookies/session reuse is expected, and whether that precondition
is closeout-blocking or operator-owned. The main orchestrator or a designated
reviewer verifies that plan before delivery starts. If a project already has
local agents, adapt or replace them so each role maps cleanly to this subset
without overlapping responsibilities.

The main agent should detect or read the host convention before writing local
agents. For Codex-style projects, inspect `.codex/config.toml` and existing
`.codex/agents/*.toml` before proposing new files or updates. For other hosts,
look for that host's agent convention first. StackOS does not scan, write, or
register those host-local files; it only provides the generic preset contracts
and workflow role requirements. If no convention is available, use the resolved
workflow agents as operating guidance in the current session or ask the
operator which host format to use. Preserve unrelated operator-authored content
and do not create a second competing agent registry.

Missing workspace profile fields such as `framework` or `content_model_json`
mean the project is under-described for future adaptation. They do not block
project-scoped tools. After reading repo guidance and stack details, record
durable hints with `workspace.updateProfile` when they will help later agents.

## StackOS Skill And Skill Presets

Workflow templates declare `skill_requirements`. The built-in workflows
recommend `stackos:stackos`, the host-side skill that teaches an agent how to
use StackOS MCP, operations, workflows, run plans, tracker tasks/tickets,
dependencies, and evidence.

Workflow templates declare `skill_preset_requirements` for reusable main-agent
operating guidance that should be resolved and adapted like agent presets. A
skill preset is not an installed host skill, not an agent preset, and not a
subagent role. For example, `stackos.sdlc.delivery-orchestrator` teaches the
main agent how to sequence SDLC delivery, while the active project still
provides the concrete rules, docs, tests, reviewers, and release expectations.

This is explicit setup guidance. The main agent decides whether its host can
load installed skills such as `stackos:stackos`. Skill presets are StackOS
contracts returned by `skillPreset.*` and workflow resolution operations; the
agent adapts them before use.

## Tracker Use

All presets are expected to work through the existing StackOS tracker:

- planning agents create scoped tasks/tickets
- explicit workflow intent takes precedence over direct tracker tasks; create or
  resolve the workflow-backed run plan before tracker ticket creation
- all discovery, design, delivery, verification, and closeout tickets for an
  explicit workflow request belong under the workflow task/run plan from the
  start
- support investigation agents create tasks only after explicit same-thread human
  instruction in the canonical Slack thread
- support investigation tasks/tickets preserve source, thread, conclusion,
  instruction, handoff, and delivery refs in tracker context or references
- dependencies are encoded so ready work and blockers are visible
- for workflow-backed child tickets, `run_plan_id` and `step_id` are
  attachment/provenance only; agents update child ticket status and evidence
  with `tracker.updateTicket` while reserving `runPlan.claimStep` and
  `runPlan.recordStep` for generated workflow step mirror tickets
- workflow-backed ticket creation must pass `run_plan_id` and `step_id`
  together or omit both; if an agent only has one, it should fetch
  `tracker.brief`, `tracker.get`, or `runPlan.get` to recover the workflow
  handoff before calling `tracker.createTicket`
- when an operator wants closed work continued or extended, agents call
  `tracker.reopen` with the task, run-plan, or linked run id plus a reason
- planning agents must dependency-bridge child tickets into the mirrored
  workflow step chain
- delivery agents claim/update tickets as work starts and completes
- verifier and reviewer agents compare completion claims with actual evidence
- reviewers verify evidence before closeout
- reviewer findings are claims until the orchestrator adjudicates them against
  evidence, root cause, and user/business impact
- tracker truth reviewers check that durable state matches code, docs, tests,
  run-plan steps, and verification outcomes
- detached workflow step tickets versus child-ticket chains are blocking
  findings, not generic missing-dependency notes
- release agents compare signoff claims with tracker state

Planning agents should produce deliverable tickets with logical sequencing,
clear dependencies, no dangling loose ends, and concrete definition of done.
For workflow-backed work, their plan must include a graph check covering the
parent step ticket, first executable child, terminal children, next-step
handoff, and detached branches. After creating or changing workflow-backed
tickets, call `tracker.get` with `run_plan_id` and `include_graph=true`;
repair warnings that hide required work or invalidate readiness claims, and
record non-blocking cleanup explicitly instead of freezing progress.

## Operations

Use these operations through MCP, REST, or CLI:

- `operation.list`: discover available StackOS operations before asking for
  exact schemas or invoking hidden toolbox tools
- `agentPreset.list`: discover available generic presets
- `agentPreset.describe`: read one preset and its adaptation contract
- `agentPreset.resolveForWorkflow`: resolve a workflow template into required
  and recommended agents plus skill requirements
- `resource.query`: read existing workflow resources such as
  `engineering-decision` and `engineering-evidence`
- `resource.upsert`, `decision.record`: write durable workflow evidence only
  from a started run-plan step with an explicit grant

Example:

```text
agentPreset.resolveForWorkflow({ "workflow_key": "core.project-memory-review" })
```

The response includes:

- workflow summary
- `agent_requirements`
- `skill_requirements`
- `skill_preset_requirements`
- resolved required/recommended/optional presets
- resolved required/recommended/optional skill presets
- unresolved preset refs, if any
- unresolved skill preset refs, if any
- setup guidance that reminds the caller to adapt presets and use tracker state

## Workflow Relationship

Workflow templates are inert reusable contracts. They do not act by themselves.
An agent selects a workflow template, resolves its preset/skill guidance, then
creates a concrete run plan with `runPlan.create`. Work execution then happens
through the run plan, granted tools, provider actions, and tracker tickets.
If the operator explicitly invoked a workflow, this run-plan selection must
happen before tracker task/ticket creation so delivery truth is not split across
a direct task and a later workflow task.

New workflow authoring is contract-driven today:

- validate with `workflowTemplate.validate`
- save project/user templates with `workflowTemplate.save`
- fork built-ins with `workflowTemplate.fork`
- create executable workflow state with `runPlan.create`

The UI can inspect and use templates, but it is not yet a full visual workflow
builder.
