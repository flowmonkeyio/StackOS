# Agent Experience Operational Audit

Date: 2026-05-26

Status: historical baseline. This document records the live surface observed on
the date above; it is not the current operating contract. Use
[`agent-operating-model.md`](./agent-operating-model.md),
[`workflow-templates.md`](./workflow-templates.md), and
[`operations.md`](./operations.md) for current behavior. In particular, current
StackOS exposes scoped operation guidance through the toolbox, accepts workflow
inputs during run-plan validation, distinguishes structural/context/provider/
execution readiness, and returns bounded active-step expectations and dependency
handoffs. Retain the findings below as provenance for regression comparison, not
as present-tense product guidance.

This audit reviews StackOS as an agent-facing operating surface. The goal is
not only whether APIs exist, but whether an agent can understand what StackOS
does, how to choose the right path, which schemas apply, what outputs mean, how
to recover from errors, and where state is written.

For this audit, agent means the MCP/tool consumer of StackOS operations and
plugin actions. A host may also provide that agent with repository filesystem
tools, but that capability is outside StackOS. StackOS workspace binding only
resolves and enforces the project scope for StackOS calls.

## Verdict

StackOS has strong foundations for an agent-first runtime:

- Project-scoped MCP binding works and injects the current project safely.
- `toolbox.describe` gives a compact way to discover direct, setup, and
  step-granted tools.
- `toolProfile.resolve` gives excellent missing-credential repair context.
- `communication.send` and `communication.reply` are the right provider-neutral
  abstraction for normal agent messaging.
- The tracker is the most agent-friendly area: bulk ticket creation, dry-run
  review, dependency previews, compact reads, and atomic updates are practical.
- Operation specs are rich in CLI/REST and include purpose, prerequisites, and
  examples.

The product is not yet green for a release claim that "agents are the primary
users and can infer the full operating model from the exposed interfaces." The
blocking gaps are clarity and consistency, not a missing core idea:

- The MCP path does not expose enough of the rich operation guidance that exists
  in the operation registry.
- Workflow/run-plan execution still has too much hidden knowledge around
  `toolbox.call`, run tokens, active steps, and executable grant snapshots.
- Template-derived plans validate structurally even when an agent still cannot
  execute provider actions or writes without additional grant authoring.
- Communications are powerful, but target resolution, dry-run semantics, local
  chat reply behavior, and surface safety metadata need tightening.

## External Criteria

The audit criteria came from current public guidance:

- MCP client guidance recommends progressive discovery: search/catalog first,
  inspect full schemas only for selected tools, then execute. It also recommends
  multiple detail levels, grouping tools by server/source, caching definitions,
  and avoiding large intermediate result payloads in the model context.
  Source: [MCP Client Best Practices](https://modelcontextprotocol.io/docs/develop/clients/client-best-practices).
- MCP tool definitions should provide a unique name, human-readable
  description, JSON input schema, optional output schema, and annotations.
  Output schemas matter because they let hosts generate precise return types
  instead of treating results as unstructured values.
  Source: [MCP Tools specification](https://modelcontextprotocol.io/specification/draft/server/tools).
- MCP security guidance emphasizes per-client consent, no token passthrough,
  SSRF protection, secure sessions, sandboxing local servers, and least
  privilege.
  Source: [MCP Security Best Practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices).
- Anthropic's tool-design guidance recommends clear and distinct tool purposes,
  namespacing, high-signal responses, semantic identifiers, verbosity controls,
  pagination/filtering/truncation, actionable errors, and tool descriptions
  written like onboarding material for a new teammate.
  Source: [Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents).
- Anthropic's agent architecture guidance recommends the simplest sufficient
  pattern, composable workflows for predictable paths, agents for dynamic tool
  use, and well-documented tool interfaces.
  Source: [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents).
- OpenAI function-calling guidance frames tool use as an explicit multi-step
  loop: make tools available, receive calls, execute application-side code,
  return tool outputs, and continue. It recommends strict schema adherence for
  reliable tool arguments.
  Source: [OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling).
- OpenAI evaluation guidance recommends task-specific evals, early and
  continuous evaluation, logging, tool-selection and argument-precision checks,
  and trace grading for end-to-end agent decisions and tool calls.
  Sources: [Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices),
  [Trace grading](https://developers.openai.com/api/docs/guides/trace-grading).

## Agent-Facing Model

The intended StackOS model is coherent:

1. A repo-bound agent resolves the current project.
2. The agent uses StackOS state for project context, auth status, plugins,
   templates, run plans, resources, artifacts, communication state, and tracker
   work.
3. The agent decides strategy. StackOS validates explicit inputs, resolves
   daemon-held credentials, executes configured calls, and records audit.
4. Direct actions are for one explicit provider/tool call.
5. Workflow run plans are for multi-step, auditable work with grants, active
   steps, and run tokens.
6. The tracker is project work state and navigation; it does not replace
   run-plan grants.
7. Communications are provider-neutral state and delivery operations; provider
   actions are lower-level escape hatches.

The main agent-experience issue is that this model is clearer in docs and code
than it is in the live MCP surface.

## Surface Map

| Job | Primary agent surface | Expected inputs | Expected outputs | Current clarity |
| --- | --- | --- | --- | --- |
| Start in a repo | `workspace.startSession`, `workspace.resolve` | cwd/repo hints injected by bridge | `project_id`, binding, connect status | Good |
| Connect repo | `toolbox.call(workspace.connect)` | repo name/root/framework metadata | daemon-owned binding | Good, admin authority could be clearer |
| Discover capabilities | `toolbox.call(plugin.list/catalog.list/provider.list/capability.list)`, `toolbox.describe(tool_names=[...])` | optional plugin/provider filters or exact tool names | plugins, providers, requested hidden/setup tools | Good inventory, but broad catalog calls should be skill/workflow-directed |
| Resolve auth target | `toolbox.call(toolProfile.resolve/auth.status/auth.test)` | provider key, optional profile/ref | safe credential ref, status, missing items, next action | Strong |
| Inspect action | `toolbox.call(action.describe)` | action ref or plugin/action key | manifest, availability, schema | Standard good, compact loses important schema shape |
| Validate action | `toolbox.call(action.validate)` | action input, credential ref when needed | validation issues and estimated cost | Good, but some duplicate issues |
| Run direct action | `toolbox.call(action.run)` | explicit input, confirmation for non-read writes | action call audit and redacted result | Good guardrail, repair could be more self-contained |
| Template discovery | `toolbox.call(workflowTemplate.list/describe/validate)` | fully qualified template key | template spec and contracts | Good after key pattern is known |
| Run-plan authoring | `toolbox.call(runPlan.validate/create/start)` | template key or explicit plan JSON | plan, run token, tracker mirror | Structural validation is not enough execution readiness |
| Step execution | `toolbox.describe(run_id)`, `toolbox.call`, `action.execute` | run id, active step, grant-qualified arguments | step/action result and audit | Most confusing path today |
| Durable data | `toolbox.call(resource.* / artifact.* / context.* / learning.* / experiment.* / decision.*)` | filters or granted writes | bounded sanitized data | Reads clear, write examples sparse |
| Tracker work | `tracker.*` | task/ticket keys, patches, dry-run lists | compact work state and revisions | Strong |
| Agent requests | `agentRequest.*` | request id, claim token, idempotency key | claim, linked run plan, terminal status | Good lifecycle, schemas over-broad in spots |
| Local chat | `localAgentChat.createMessage` | thread/message keys, direction, request flag | stored message, optional request | Inbound clear, outbound reply path unclear |
| Communications | `communication.send`, `communication.reply`, `communicationTarget.*`, `communicationContext.query` | named target/origin, content, context | resolved send/reply, stored history | Strong concept, some mismatched repair semantics |
| Ingress | `ingressEndpoint.*` | endpoint config/sync options | routes and provider setup status | Useful, dry-run wording is misleading |
| CLI/REST | `stackos ops *`, `/api/v1/operations/*` | operation key and JSON payload | rich OperationSpec docs and call results | Good, richer than MCP |
| UI | `/projects/:id/tasks`, resources, runs, connections | browser navigation | human inspection | Useful for human signoff, not primary agent path |

## Simulated Flows

The table below combines live MCP/CLI/browser probes, code and documentation
inspection, and focused subagent audits. Live probes were kept read-only or
dry-run unless the operation itself is designed to update safe project metadata.

| # | Flow | Simulated path | Result | Agent feedback |
| --- | --- | --- | --- | --- |
| 1 | Repo workspace resolution | `workspace.resolve(response_mode=standard)` | Bound to `project_id=1`; no connect needed | Good first call. |
| 2 | Session/tool discovery | `toolbox.describe(tool_names=[...])` | Direct tools, requested setup tools, and active-step grants returned without dumping the whole catalog | Useful when exact tools are requested; skills/workflows should point agents to the right tool names. |
| 3 | Legal states | `meta.enums` | State transitions returned compactly | Good for lifecycle-aware agents. |
| 4 | Plugin/provider inventory | `plugin.list`, `provider.list`, `capability.list` | Broad catalog visible | Useful but high volume. Prefer job-based catalog search. |
| 5 | Auth status | `auth.status` | Slack and Telegram connected; most others missing | Good overview. |
| 6 | Ready profile resolution | `toolProfile.resolve(slack-bot)` | Safe credential ref and account metadata returned | Strong pattern, no secrets leaked. |
| 7 | Missing provider resolution | `toolProfile.resolve(openai-images)` | `ready=false`, `missing=["credential"]`, next action points to connections page | Excellent repair UX. |
| 8 | Compact action describe | `action.describe(communications.slack-bot.message.send, compact)` | Availability ready, but `required` empty and nested `anyOf`/`oneOf` dropped | Compact mode hides required alternatives. |
| 9 | Standard action describe | same with `response_mode=standard` | Full schema includes `anyOf`, `oneOf`, nested limits | Good, but the compact result should tell agents to inspect standard mode. |
| 10 | Action validation success | `action.validate` with Slack credential, surface, text | Valid | Good. |
| 11 | Action validation errors | Slack validate without credential, target, or content | Clear credential, destination, and content errors | Good repair text, but agents may prefer `toolProfile.resolve` first. |
| 12 | Cost action validation | `utils.image.generate` with missing OpenAI credential | Invalid with credential and budget issues | Good availability, but validation issues duplicate required prompt in one case. |
| 13 | Optional credential action | `utils.web.read` describe/validate | Ready with optional missing credential and valid input | Good model for unauthenticated reads. |
| 14 | Template discovery | `workflowTemplate.list/describe` for communications | Full spec returned only with fully qualified key | Accept short key when `plugin_slug` is supplied or return a suggestion. |
| 15 | Template plan validation | `runPlan.validate(template_key=communications.outbound-notification)` | Valid plan returned | Structural validation is good but does not prove executable grants exist. |
| 16 | Template validation inputs | `runPlan.validate(..., inputs_json=...)` | Validation rejected extra `inputs_json` | `runPlan.create` accepts inputs but validate does not. That is confusing. |
| 17 | Workflow execution path | Subagent/code audit of `runPlan.start -> toolbox.call claim -> action.execute -> record` | Path exists | Agent docs should center the bridge path, not raw direct calls. |
| 18 | Resource and artifact state | `resource.query`, `artifact.query`, docs/tests for writes | Bounded reads available; writes are grant-gated | Good. Need more write examples through granted steps. |
| 19 | Tracker status and graph | `tracker.status`, `tracker.get(include_graph=true)` | Compact counts and graph projection returned | Strong agent UX. |
| 20 | Tracker bulk ticket draft | `tracker.createTicket(tickets_json, dry_run=true)` | Validated two draft tickets without writing | Good. Dependency preview could be richer for dry-run-only keys. |
| 21 | Tracker atomic bulk patch | `tracker.updateTicket(updates_json, dry_run=true)` | Validated per-ticket changed fields | Strong and aligned with the user's requested flow. |
| 22 | Tracker patch schema discovery | attempted `completion_evidence`, then corrected to `completion_evidence_json` | First patch rejected unsupported field | Error is useful, but allowed patch fields should be discoverable. |
| 23 | Tracker UI | Browser opened `/projects/1/tasks?task=agent-experience-operational-audit` | Task dropdown, filters, graph, details modal visible | Good human signoff surface. Agents should still use MCP. |
| 24 | Communication target planning | `communicationTarget.list/resolve`, then `communication.send` dry-run | Resolver denied `slack-roadmap`; send dry-run succeeded | Align actor resolution or explain the difference. |
| 25 | Communication dry-run latency | `communication.send(to=slack-roadmap, dry_run=true)` | Validated and created dry-run audit row, but took about 35 seconds | Too slow for local agent loops. Avoid unnecessary provider calls. |
| 26 | Stored communication history | `communicationContext.query(provider_key=slack-bot)` and field-error probe | Stored messages returned; live provider history boundary is explicit | Good boundary. Unsupported fields error should list allowed fields. |
| 27 | Surface safety metadata | `communicationSurface.list(provider_key=slack-bot)` | Many surfaces have `audience=unknown`, empty intent/guidance/data scope | Risky for agent decisions in mixed/customer spaces. |
| 28 | Ingress setup and sync | `ingressEndpoint.status/routes/sync(dry_run_provider_webhooks=true)` | Slack manual update required; Telegram profile sync updated | Useful, but dry-run wording should state metadata writes. |
| 29 | CLI/REST operation docs | `stackos ops describe communication.send`, sandbox probe | Rich purpose/prerequisites/examples; sandbox needed escalation | CLI/REST docs are stronger than MCP; MCP should mirror this guidance. |
| 30 | Queue, chat, and signoff flows | Subagent audit of `agentRequest.*`, local chat, and release docs | Claim-token lifecycle and signoff exist; local chat reply path unclear | Require idempotency in schema, document outbound local-chat reply, add flow-to-test matrix. |

## Findings

### P0. Publish One Native Agent Happy Path

Agents currently have to assemble the operating model from `AGENTS.md`, the
StackOS plugin skill, operation docs, workflow docs, and live MCP responses.
That is too much for a primary-user interface.

Create one canonical agent runbook that starts from the native MCP surface:

```text
workspace.resolve
-> toolbox.describe
-> choose direct action, tracker work, or workflow run plan
-> for direct action: toolProfile.resolve -> action.describe -> action.validate -> action.run
-> for workflow: workflowTemplate.describe -> runPlan.validate/create/start
-> toolbox.describe(run_id)
-> toolbox.call(runPlan.claimStep)
-> toolbox.call(action.execute/resource.upsert/artifact.create as granted)
-> toolbox.call(runPlan.recordStep)
-> tracker.verify / runPlan status / action audit
```

This should be a docs release blocker because it changes how agents understand
every other interface.

### P0. Expose Operation Guidance Through MCP

The CLI/REST operation specs contain rich purpose, prerequisites, examples,
grant policy, and agent guidance. The live MCP surface mostly exposes schemas
and short tool descriptions. This violates the progressive-discovery ideal:
agents can see many tools, but they do not get enough "when to use this and what
to do next" context at the point of choice.

Recommendations:

- Add MCP `operation.list` and `operation.describe`, or enrich
  `toolbox.describe` with OperationSpec purpose, prerequisites, examples,
  return notes, and grant notes.
- Label tools as `operation-backed`, `direct-mcp`, `setup-toolbox`, or
  `step-granted`.
- Add `next_call` hints to high-value operations and actions.
- Make `response_mode` behavior discoverable in every compact response that
  drops schema or diagnostic detail.

### P0. Make Workflow Validation Reflect Executability

`runPlan.validate(template_key=...)` can return `valid=true` for a template
derived plan that still lacks executable `mcp_tool_grants`. The output proves
that the plan shape is valid, not that an agent can run the provider actions or
write resources/artifacts.

Recommendations:

- Add warnings when a plan has `action_refs`, resource outputs, context writes,
  or artifact writes but no executable grants.
- Return suggested grant skeletons from template validation.
- Distinguish template action contract refs such as `send_telegram_message`
  from executable action refs such as `communications.telegram-bot.message.send`.
- Align `runPlan.validate` and `runPlan.create` input handling. If `create`
  accepts `inputs_json`, validate should accept it too or explain why not.

### P0. Center The Bridge Step Path

Docs and examples still make it easy to think `runPlan.claimStep` and
`action.execute` are always directly callable. The bridge reality is that agents
often need `toolbox.describe(run_id)` and `toolbox.call` so grants and run
tokens are refreshed/injected safely.

Recommendations:

- Rewrite run-plan docs around the bridge path first.
- Keep raw run-token calls as REST/CLI/test detail.
- In `toolbox.describe(run_id)`, show active step, allowed tools, grant
  qualifiers, and whether the bridge will inject the run token.
- Normalize grant-denied repair payloads with requested tool/action, active
  step, allowed grants, and the exact missing grant shape.

### P1. Tighten Communication Agent Semantics

Communications are conceptually strong, but several live paths are confusing:

- `communicationTarget.resolve(slack-roadmap)` denied `profile_not_allowed`,
  while `communication.send(to=slack-roadmap)` succeeded by resolving the
  default actor profile.
- `communication.send(dry_run=true)` created an action-call audit row and took
  about 35 seconds locally.
- `communicationContext.query(fields=[...])` rejected unsupported fields but did
  not list allowed fields.
- Many Slack surfaces have unknown audience and empty intent/guidance/data
  scope, which leaves agents without enough safety context.
- Local chat inbound is clear, but the official outbound reply path is not.

Recommendations:

- Align `communicationTarget.resolve` and `communication.send` actor resolution,
  or make the difference explicit in return notes.
- Make dry-run side effects explicit in docs and result notes.
- Ensure dry-run validation does not make unnecessary provider calls.
- Return allowed field names for `communicationContext.query` field errors.
- Require or strongly prompt audience, intent, agent guidance, and data-scope
  metadata for real communication surfaces.
- Either route local chat through `communication.reply` or document and test
  `localAgentChat.createMessage(direction="outbound", create_request=false)` as
  the official reply path.

### P1. Improve Action Schemas And Output Contracts

Many actions have strict enough input schemas, but output schemas are broad
`additionalProperties: true`. Compact action descriptions also drop nested
schema constraints that matter for agent use.

Recommendations:

- Preserve full nested schema in compact mode for one selected action, or mark
  `schema_truncated=true` with a `response_mode=standard` hint.
- Add minimal output contracts for first-party actions, especially message send,
  read, delete, reaction, fetch, image, and mock actions.
- Add per-action examples to plugin manifests.
- Make generated `action_ref` visible beside manifest action keys, so agents do
  not infer `plugin_slug.key` composition.

### P1. Normalize Error Repair Context

Good repair context exists in some places, especially `toolProfile.resolve` and
communication rich-feature rejection. Other places are more uneven.

Examples:

- Tracker unsupported patch field errors are clear, but the allowed patch field
  set is not returned.
- `communicationContext.query` lists invalid fields but not allowed fields.
- Direct action confirmation errors should include a retry shape.
- CLI daemon errors collapse structured data unless using MCP/REST directly.
- Some validation paths produce duplicate issue rows.

Recommendation: standardize operation error payloads:

```json
{
  "effect": "none",
  "retryable": false,
  "failed_fields": ["..."],
  "allowed_fields": ["..."],
  "required_next_call": "toolProfile.resolve",
  "safe_retry_shape": {}
}
```

### P1. Clarify Setup Authority

Agents can resolve workspace bindings and inspect project state, but plugin
enablement, provider credentials, local daemon setup, public ingress, and
connection repair often require human/admin UI or CLI steps. That is fine, but
the boundary should be explicit.

Recommendations:

- In setup docs, mark each setup task as `agent`, `operator`, or `admin`.
- For missing credentials, always include the project connections URL and the
  provider/profile name.
- For ingress, distinguish "sync local metadata" from "apply remote provider
  webhook".

### P2. Add A Flow-To-Test Matrix

`make signoff` is good but heavy. Agents need a release matrix that maps changed
surfaces to targeted checks:

- MCP bridge and toolbox
- Operation registry and CLI docs
- Auth and tool profile resolution
- Direct action validation/run
- Run-plan grants and active-step execution
- Resource/artifact granted writes
- Tracker bulk create/update/dependency graph
- Communication send/reply/context/ingress
- UI task tracker smoke

Delivered in [`release-signoff.md`](./release-signoff.md) as the agent flow
matrix. Keep it current whenever an agent-facing flow, grant, schema, or
release command changes.

## What Is Already Agent-Friendly

- `toolProfile.resolve` is the best current repair surface. It says whether the
  provider is ready, what is missing, and the next operator action.
- Tracker bulk ticket creation and atomic update are the right design. Reusing
  `tracker.createTicket` and `tracker.updateTicket` with list payloads is better
  than endpoint multiplication.
- Tracker compact reads are effective. `tracker.status`, `tracker.next`,
  `tracker.brief`, `tracker.verify`, `tracker.changed`, and `tracker.search`
  keep context small.
- `communication.send` and `communication.reply` are the right normal path for
  agents. Provider actions should remain escape hatches.
- Stored communication history is correctly bounded and explicit: it reads
  StackOS-stored history only, not arbitrary Slack/Telegram provider history.
- CLI operation descriptions are high quality and should be mirrored closer to
  MCP.

## Self-Test Notes From Agent Use

- The tracker made this audit easier once tickets existed. Bulk creation and
  dry-run review are useful agent primitives.
- The tracker also revealed that field naming needs better patch schema
  discoverability. I first tried `completion_evidence`, got a useful rejection,
  and then corrected to `completion_evidence_json`.
- The most confusing area as an agent was workflow execution, not tracker. The
  mental load is: template refs, run plan refs, active step refs, grant snapshot
  refs, executable action refs, run ids, and run tokens.
- The second most confusing area was communications target planning. The normal
  send path worked, but the read-only resolver disagreed about permission.
- Compact responses are helpful until they omit the exact schema feature that
  explains how to call the tool.
- Dry-run operations need stronger semantics. An agent will assume "dry-run"
  means no external effect and ideally no durable write, unless the response
  states otherwise.

## Recommended Release Gate

Before calling the agent experience release green, complete these:

1. Add the native MCP agent happy-path runbook.
2. Expose OperationSpec guidance through MCP or `toolbox.describe`.
3. Add run-plan validation warnings/suggested grants for non-executable
   template-derived plans.
4. Rewrite run-plan examples around `toolbox.describe(run_id)` and
   `toolbox.call`.
5. Fix or document the `communicationTarget.resolve` versus
   `communication.send` actor-resolution mismatch.
6. Make communication dry-run side effects and latency acceptable for local
   agent loops.
7. Document/test the local-chat outbound reply path.
8. Add a flow-to-test matrix to release signoff.

These are agent-experience release blockers. The rest can follow as P1/P2
hardening.

## Suggested Follow-Up Work

Create a follow-up task named `agent-experience-release-hardening` with tickets:

1. `agent-happy-path-doc`
2. `mcp-operation-describe`
3. `run-plan-executability-validation`
4. `bridge-step-runbook`
5. `communication-resolution-semantics`
6. `local-chat-reply-path`
7. `action-output-contracts`
8. `flow-to-test-matrix`

Do not add a dependency-template feature for this. Workflow templates are the
right place for standardized dependency structure later.
