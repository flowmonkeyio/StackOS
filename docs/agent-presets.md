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

The adapting agent must rewrite the generic role for the current project:
technology stack, rules, documentation references, available MCP tools,
workflow/run-plan model, tracker task/ticket conventions, verification
commands, and release expectations.

`recommended_tools` are StackOS operation references. Some hosts mount those
operations as direct MCP tools, while the StackOS bridge may expose only
`workspace.startSession`, `workspace.resolve`, `toolbox.describe`, and
`toolbox.call`. Treat the operation refs as the intent-level tool list: call
them directly only when the host exposes them, otherwise inspect the exact names
with `toolbox.describe` and invoke them through `toolbox.call`.

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
voice note, screenshot, URL, artifact, or provider file ref must either be
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
| `stackos.sdlc.test-designer` | Maps acceptance criteria to proof surfaces and red-first gates when needed. |
| `stackos.sdlc.delivery` | Implements scoped tickets, debugs root causes, verifies the diff, and records tracker/evidence updates truthfully. |
| `stackos.sdlc.delivery-reviewer` | Reviews design and delivery across behavior, contracts, tests, tracker truth, docs, security, and release risk. |
| `stackos.sdlc.release-ops` | Closes release readiness, limitations, rollback/watch notes, and communications. |

Use this subset as the only support/engineering agent set for a project.
Workflows pick the roles they need. For example, customer feedback intake uses
the communications intake role; support investigation uses the support
investigator and may use codebase exploration; delivery task handoff uses the
support handoff role and may use planning; tracked delivery uses requirements,
discovery, planning, architecture, test design, delivery, delivery review, and
release. If a project already has local agents, adapt or replace them so each
role maps cleanly to this subset without overlapping responsibilities.

The main agent should detect or read the host convention before writing local
agents. For Codex-style projects, inspect `.codex/config.toml` and existing
`.codex/agents/*.toml` before proposing new files or updates. For other hosts,
look for that host's agent convention first. StackOS does not scan, write, or
register those host-local files; it only provides the generic preset contracts
and workflow role requirements. If no convention is available, use the resolved
workflow agents as operating guidance in the current session or ask the
operator which host format to use.

Missing workspace profile fields such as `framework` or `content_model_json`
mean the project is under-described for future adaptation. They do not block
project-scoped tools. After reading repo guidance and stack details, record
durable hints with `workspace.updateProfile` when they will help later agents.

## StackOS Skill

Workflow templates declare `skill_requirements`. The built-in workflows
recommend `stackos:stackos`, the host-side skill that teaches an agent how to
use StackOS MCP, operations, workflows, run plans, tracker tasks/tickets,
dependencies, and evidence.

This is explicit setup guidance. The main agent decides whether its host can
load the skill. If the host cannot, the agent should read the same project docs
and still follow the tracker/run-plan model.

## Tracker Use

All presets are expected to work through the existing StackOS tracker:

- planning agents create scoped tasks/tickets
- support investigation agents create tasks only after explicit same-thread human
  instruction in the canonical Slack thread
- support investigation tasks/tickets preserve source, thread, conclusion,
  instruction, handoff, and delivery refs in tracker context or references
- dependencies are encoded so ready work and blockers are visible
- delivery agents claim/update tickets as work starts and completes
- verifier and reviewer agents compare completion claims with actual evidence
- reviewers verify evidence before closeout
- tracker truth reviewers check that durable state matches code, docs, tests,
  run-plan steps, and verification outcomes
- release agents compare signoff claims with tracker state

Planning agents should produce deliverable tickets with logical sequencing,
clear dependencies, no dangling loose ends, and concrete definition of done.

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
- `resource.upsert`, `artifact.create`, `decision.record`: write durable
  workflow evidence only from a started run-plan step with an explicit grant

Example:

```text
agentPreset.resolveForWorkflow({ "workflow_key": "core.project-memory-review" })
```

The response includes:

- workflow summary
- `agent_requirements`
- `skill_requirements`
- resolved required/recommended/optional presets
- unresolved preset refs, if any
- setup guidance that reminds the caller to adapt presets and use tracker state

## Workflow Relationship

Workflow templates are inert reusable contracts. They do not act by themselves.
An agent selects a workflow template, resolves its preset/skill guidance, then
creates a concrete run plan with `runPlan.create`. Work execution then happens
through the run plan, granted tools, provider actions, and tracker tickets.

New workflow authoring is contract-driven today:

- validate with `workflowTemplate.validate`
- save project/user templates with `workflowTemplate.save`
- fork built-ins with `workflowTemplate.fork`
- create executable workflow state with `runPlan.create`

The UI can inspect and use templates, but it is not yet a full visual workflow
builder.
