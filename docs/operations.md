# StackOS Operations

StackOS operations are the protocol-neutral callable layer. An operation is
registered once, then adapters expose it through MCP, REST, and CLI when its
surface policy allows that.

```text
OperationSpec
  -> MCP tool
  -> REST operation call
  -> CLI command
  -> UI operation catalog
```

The service, repository, connector, auth, and audit code remains the source of
behavior. Operations define the callable contract around that behavior:

- name and summary
- input and output models
- handler
- mutating/read-only classification
- grant and secret policy
- enabled surfaces
- agent guidance, prerequisites, return notes, and examples

## Agent Documentation

Agents are the primary callers for workflow execution operations. Here, agent
means the MCP/tool consumer calling StackOS, not necessarily a process with
repository filesystem access. Scripts can use the same catalog for automation
and setup, but run-plan mechanics are optimized for agents that need precise
schemas, grants, examples, and safe return notes. Callers should not guess
operation payloads. The operation registry returns an agent-readable
description for every registered operation:

```bash
stackos ops list
stackos ops describe action.execute --json
stackos ops describe action.run --json
stackos ops describe runPlan.claimStep --json
```

The same metadata is available through REST:

```http
GET /api/v1/operations
GET /api/v1/operations/action.execute
GET /api/v1/operations/action.run
```

Native MCP clients can read the same operation catalog without leaving the MCP
session. In the agent bridge, these calls are hidden setup tools: call them
through `toolbox.call` rather than expecting them in the first-level MCP tool
list.

```text
toolbox.call({
  "tool_name": "operation.list",
  "arguments": { "surface": "mcp", "mode": "grouped", "response_mode": "compact" }
})
toolbox.call({
  "tool_name": "operation.describe",
  "arguments": { "name": "communication.send", "surface": "mcp", "response_mode": "compact" }
})
toolbox.call({
  "tool_name": "agentPreset.resolveForWorkflow",
  "arguments": { "workflow_key": "core.project-memory-review" }
})
toolbox.call({
  "tool_name": "integration.list",
  "arguments": {}
})
```

The discovery operations are OperationSpecs too. `operation.list` includes
`operation.list` and `operation.describe`, and `operation.describe` can describe
both discovery tools with the same schemas, examples, and guidance it returns
for domain operations. Use `surface: "mcp"`, `mode: "grouped"`, and
`response_mode: "compact"` when an agent only needs operation names by category
instead of the full inventory. After that, call `toolbox.describe` with exact
`tool_names` before invoking the selected operation. In workspace-bound bridge
sessions, omit injected fields such as `project_id`; the bridge supplies the
current workspace scope and refuses cross-project calls.

For action discovery, `action.list` answers "what can I use now?" and hides
disconnected, deferred, project-local, missing-connector, and otherwise
non-executable external-provider actions by default. Pass
`include_unavailable_integrations=true` only for setup or deliberate catalog
inspection. Use `integration.list` for compact provider readiness and hidden
action counts, and `readiness.check` for one selected workflow/action.
Generated provider actions use stable public refs in `action.list`; internal
inventory storage keys and retired generated rows are audit history, not
callable options. Search indexes action metadata, input/output schema fields,
category/tags, paths, and operation ids so agents can discover actions from
business intent before fetching one detailed schema.

Each description includes:

- enabled surfaces: `mcp`, `rest`, `cli`
- grant policy, for example `run-plan-step-action-ref`
- secret policy
- response policy: default mode, allowed modes, ack safety, and raw-only reason
- JSON input schema
- JSON output schema
- prerequisites
- return notes
- examples

## Response Modes

Operation responses are shaped for agent decision-making. The canonical
operation result is always the raw redacted payload. When an agent requests a
different shape, StackOS stores raw first for idempotency, then shapes the
returned payload.

- `compact`: next-call-sufficient ids, refs, counts, warnings, schema summaries,
  bounded lists/text, and action hints. Compact mode omits file/content bodies.
- `raw`/`standard`/`verbose`: full redacted daemon payload.
- `ack`: minimal success envelope for safe internal writes only.

Errors are never compacted. Validation failures, grant denials, auth/setup
diagnostics, and provider partial failures keep structured repair context so an
agent can decide whether to retry, claim a step, reconnect a provider, or stop.

External provider actions through `action.run` and `action.execute` are
compact file-backed by default for MCP/REST and raw-by-default for CLI, with
explicit `response_mode` still allowed by policy. MCP/REST compact responses
must keep the action-call id, provider/action refs, cost, status, file path,
checksum, `schema_ref`, `schema_operation`, and receipt metadata; raw provider
response data lives in the file-backed action-output envelope for MCP/REST
defaults. CLI calls default to raw inline output unless an explicit output
policy says otherwise. Agents call `schema.get` with `schema_ref` only when they
need the envelope schema.
Communication delivery operations
such as `communication.send` and `communication.reply` remain raw-only until
their own provider-safe compact contract exists.

MCP tools are generated from the same operation specs, so the daemon has one
callable contract per StackOS primitive. The agent bridge advertises
`workspace.startSession`, `workspace.resolve`, `toolbox.describe`,
`toolbox.call`, and local full-control `browser.*` tools. Use
`operation.list` and `operation.describe` through the toolbox for purpose,
prerequisites, return notes, examples, and schemas. Use `toolbox.describe` for
exact hidden setup and step-granted tools before calling them.

The UI reads the same docs at `/projects/{project_id}/operations`. That page is
not a second registry; it renders `GET /api/v1/operations` and
`GET /api/v1/operations/{operation_name}`.

## Browser Operations

Browser automation is exposed as generic StackOS operations instead of
provider-specific workflow glue. `browser.page.call` and
`browser.context.call` mirror public Playwright methods through raw
method names plus `args`, `kwargs`, or named `arguments`. Prefer named
`arguments` for manifest-documented convenience methods, such as
`arguments: {"url": "https://example.com"}` for `goto`, while raw `args` and
`kwargs` remain available for direct public Playwright parity. Calls that return
non-page browser objects return transient `handle_ref` values; use
`browser.handle.call` to call public methods or properties on those returned
objects. Convenience operations such as `browser.script.run`,
`browser.script.inject`, and `browser.page.screenshot` sit on top of that
parity model.

Do not turn the browser method manifest into an allowlist. It exists for
discovery, guidance, and drift tests while preserving full browser control for
agents. Browser side-effect operations are raw-only; persisted receipts store
redacted summaries and artifact refs rather than raw scripts, returned values,
or daemon-local paths.

Browser side-effect operations advertise `secret_policy=raw-browser-output`.
That means the immediate operation payload can contain page, cookie, storage,
or JavaScript-returned data. Browser session state and generated artifacts are
local operational state and must be treated as sensitive; receipts are redacted
for audit, not as a browser capability restriction.

## Generic REST Calls

The generic REST adapter is command-shaped and meant for scripts, CLI, CI, and
external automation:

```http
POST /api/v1/operations/{operation_name}/call
```

Payload:

```json
{
  "arguments": {
    "project_id": 1,
    "action_ref": "utils.sitemap.fetch",
    "input_json": {
      "urls": ["https://example.com/sitemap.xml"]
    },
    "run_token": "..."
  }
}
```

UI-friendly resource routes still exist separately, for example
`GET /api/v1/projects/{project_id}/action-calls`. The operation endpoint is the
generic callable surface; resource routes remain the ergonomic query/read model
for the UI.

## CLI Calls

The CLI uses the same REST operation adapter:

```bash
stackos ops call action.describe \
  --input describe-action.json
```

In a source checkout, the CLI entrypoint is available at `.venv/bin/stackos`
after dependency sync. If the shell has not activated the repo venv, use
`.venv/bin/stackos ...`, `uv run stackos ...`, or export
`PATH="$PWD/.venv/bin:$PATH"` before using the shorter `stackos ...` form.

For common cross-cutting fields, the CLI can merge flags into the input JSON:

```bash
stackos ops call action.execute \
  --project 1 \
  --run-token "$RUN_TOKEN" \
  --idempotency-key sitemap-1 \
  --response-mode raw \
  --input action-input.json
```

`--input -` reads the operation arguments from stdin.

Common operations also have aliases that still call the generic operation
endpoint:

```bash
stackos actions list --project 1 --plugin trackbooth --query "top offers" --executable
stackos actions describe utils.sitemap.fetch --project 1
stackos actions validate utils.sitemap.fetch \
  --project 1 \
  --context-ref ctx_reporting \
  --input action-input.json
stackos actions execute utils.sitemap.fetch \
  --project 1 \
  --run-token "$RUN_TOKEN" \
  --context-ref ctx_reporting \
  --input action-input.json
stackos actions run communications.telegram-bot.message.send \
  --project 1 \
  --credential-ref cred_123 \
  --confirm-direct \
  --intent-summary "User asked to send one status message." \
  --intent-id telegram-send-status-1 \
  --input telegram-message.json

stackos run-plans validate --project 1 --template-key core.project-memory-review
stackos run-plans create --project 1 --input run-plan.json
stackos run-plans start 42 --project 1
stackos run-plans claim-step 42 --step-id fetch-sitemap --run-token "$RUN_TOKEN"
stackos run-plans record-step 42 \
  --step-id fetch-sitemap \
  --status success \
  --result step-result.json \
  --run-token "$RUN_TOKEN"

stackos agent-requests list --project 1 --claimable
stackos agent-requests prepare-run-plan 42 \
  --project 1 \
  --claimed-by codex \
  --input run-plan.json \
  --idempotency-key prepare-agent-request-42
stackos agent-requests complete 42 \
  --project 1 \
  --claim-token "$CLAIM_TOKEN" \
  --status resolved

stackos tracker next --project 1
stackos tracker brief workflow-42-review --project 1
stackos tracker pick --project 1 --assignee codex
stackos tracker patch --project 1 --input tracker-patch.json
```

Provider execution context stays outside endpoint payloads. Pass it directly
with `--provider-context provider-context.json`, or store it once on an
execution context and pass `--context-ref` on subsequent calls.

For Trackbooth on-behalf execution, `provider-context.json` can contain:

```json
{
  "acting_as_account": "acct_123"
}
```

StackOS validates that provider context separately from `--input`; the
Trackbooth connector maps `acting_as_account` to the `X-Acting-As-Account`
header. It is not sent as REST body/query/path data.

Examples:

```bash
stackos actions run trackbooth.reporting.aggregate \
  --project 1 \
  --credential-ref cred_trackbooth \
  --provider-context provider-context.json \
  --input reporting-aggregate.json \
  --dry-run

stackos actions run trackbooth.offers.update \
  --project 1 \
  --context-ref ctx_revtrix_reporting \
  --input offer-update.json \
  --confirm-direct \
  --intent-summary "Operator approved updating one offer." \
  --idempotency-key trackbooth-offer-update-123
```

Agent tracker reads such as `tracker.status`, `tracker.next`,
`tracker.blockers`, `tracker.brief`, `tracker.why`, `tracker.execute`,
`tracker.verify`, `tracker.history`, `tracker.changed`, and `tracker.search`
are compact by default on the agent-facing bridge and on tracker-specific
commands. For direct REST, direct daemon MCP, or generic `ops call`, pass
`response_mode: "compact"` when the caller wants the compact shape and
`response_mode: "raw"` or `"standard"` when the full diagnostic row payload is
needed.

## Registered Core Operations

The live operation registry is the source of truth. Use `operation.list` and
`operation.describe` for the current inventory, schemas, response policy, grant
policy, examples, and enabled surfaces. The static list below is a convenience
index for common core operations, not a replacement for registry discovery.

- `action.describe`
- `action.list`
- `action.validate`
- `action.run`
- `action.execute`
- `artifact.create`
- `artifact.read`
- `artifact.update`
- `artifact.archive`
- `artifact.supersede`
- `agentPreset.list`
- `agentPreset.describe`
- `agentPreset.resolveForWorkflow`
- `skillPreset.list`
- `skillPreset.describe`
- `skillPreset.resolveForWorkflow`
- `agentRequest.list`
- `agentRequest.get`
- `agentRequest.create`
- `agentRequest.claim`
- `agentRequest.prepareRunPlan`
- `agentRequest.release`
- `agentRequest.linkRunPlan`
- `agentRequest.complete`
- `agentRequest.ignore`
- `browser.runtime.status`
- `browser.method.manifest`
- `browser.profile.create`
- `browser.profile.list`
- `browser.session.start`
- `browser.session.stop`
- `browser.session.list`
- `browser.session.status`
- `browser.page.call`
- `browser.context.call`
- `browser.handle.call`
- `browser.script.run`
- `browser.script.inject`
- `browser.page.snapshot`
- `browser.page.screenshot`
- `communication.reply`
- `communication.send`
- `communicationProfile.list`
- `communicationProfile.get`
- `communicationProfile.upsert`
- `communicationSurface.list`
- `communicationSurface.upsert`
- `communicationContact.list`
- `communicationContact.upsert`
- `communicationMembership.list`
- `communicationMembership.upsert`
- `communicationTarget.list`
- `communicationTarget.resolve`
- `communicationTarget.upsert`
- `communicationRoute.list`
- `communicationRoute.upsert`
- `communicationContext.query`
- `context.query`
- `context.timeline`
- `decision.query`
- `decision.record`
- `executionContext.discover`
- `executionContext.resolve`
- `executionContext.create`
- `executionContext.artifact.list`
- `executionContext.artifact.read`
- `experiment.query`
- `learning.create`
- `learning.query`
- `ingressEndpoint.configure`
- `ingressEndpoint.refresh`
- `ingressEndpoint.routes`
- `ingressEndpoint.sync`
- `ingressEndpoint.status`
- `localAgentChat.createMessage`
- `toolProfile.resolve`
- `readiness.check`
- `tracker.status`
- `tracker.get`
- `tracker.next`
- `tracker.blockers`
- `tracker.brief`
- `tracker.why`
- `tracker.execute`
- `tracker.verify`
- `tracker.history`
- `tracker.changed`
- `tracker.search`
- `tracker.createTask`
- `tracker.createTicket`
- `tracker.updateTask`
- `tracker.updateTicket`
- `tracker.patch`
- `tracker.rejectTask`
- `tracker.reopen`
- `tracker.pick`
- `tracker.release`
- `tracker.linkRunPlan`
- `runPlan.validate`
- `runPlan.create`
- `runPlan.start`
- `runPlan.get`
- `runPlan.getStep`
- `runPlan.checkConsistency`
- `runPlan.list`
- `runPlan.update`
- `runPlan.recover`
- `runPlan.reopen`
- `runPlan.abort`
- `runPlan.claimStep`
- `runPlan.recordStep`
- `resource.get`
- `resource.query`
- `resource.upsert`
- `schema.get`
- `workflowExtension.list`
- `workflowExtension.get`
- `workflowExtension.delete`
- `workflowExtension.validate`
- `workflowExtension.upsert`
- `workflowTemplate.authoringGuide`
- `workflowTemplate.list`
- `workflowTemplate.describe`
- `workflowTemplate.validate`
- `workflowTemplate.save`
- `workflowTemplate.fork`

Tracker list workflows reuse existing tracker operations. Use
`tracker.createTicket` with `tickets_json` and `dry_run=true` for draft review,
call the same operation without dry-run to create the list, use `tracker.get`
filters for review, and use `tracker.updateTicket` with `updates_json` for
atomic per-ticket patches. Do not add separate list-specific tracker endpoints.
Terminal child-ticket updates can roll up a parent task's terminal status, but
ticket evidence is not copied into the task; compact tracker mutation responses
include `task_rollup` and evidence-presence flags when this matters.
Use `tracker.rejectTask` for operator-level rejection or abort: it accepts a
task key or run-plan id, marks the parent task aborted/rejected, and cascades all
child tickets to aborted with rejection evidence. For resumable parked work, use
normal tracker status updates with `deferred` instead of rejection. For
workflow-backed tasks, rejection only applies to draft/started run plans and
routes through `runPlan.abort` first. Completed or failed workflow run plans
remain canonical; create follow-up tracker work instead of overriding their
terminal lifecycle.
`tracker.linkRunPlan` is a provenance link only and does not transfer lifecycle
ownership from tracker to the run plan.

Agent preset setup reuses the same operation infrastructure. Use
`agentPreset.list` to discover generic role presets, `agentPreset.describe` to
read one role and its project-adaptation contract, and
`agentPreset.resolveForWorkflow` to resolve a workflow template into
required/recommended agents, installed `skill_requirements`, and resolved
main-agent `skill_preset_requirements`. Use `skillPreset.*` when the agent
needs skill-preset-only discovery or diagnostics.

Workflow templates are inert presets/contracts. They do not act by themselves.
The main agent should resolve preset and skill requirements, adapt generic roles
to the project, create a concrete run plan with `runPlan.create`, and work
through tracker tasks/tickets with explicit dependencies and evidence.

Workflow extensions are the project-configuration layer for templates. Use
`workflowExtension.validate` and `workflowExtension.upsert` when a base workflow
should stay reusable but a project needs stable input defaults, communication
route refs, channel/target context, guardrails, extra step guidance, or atomic
workflow-field overrides. `template_overrides_json` replaces the provided
top-level workflow keys, then StackOS validates the effective template before
saving or creating a run. Agents should put project-specific agent, installed
skill, or skill preset changes in the existing `agent_requirements` /
`skill_requirements` / `skill_preset_requirements` workflow
fields, not in a new context mechanism. `workflowTemplate.describe` returns the
attached `project_extension`, and `runPlan.create` applies enabled extension
defaults before per-run inputs. Use `workflowTemplate.fork` only when a new
workflow identity or separately reusable method is needed.
When supplied, `source` is a template-origin filter (`plugin`, `project`,
`user`, or `repo`), not audit or workflow-run provenance.

Use `readiness.check` before broad setup scans when the agent already knows a
workflow key or action ref. It answers the scoped question: is this workflow or
action executable now, and which exact credentials, budgets, connectors, or
setup items are missing? For workflow templates, `ready=true` means the template
is usable for planning/run-plan creation; `execution_ready=false` means only the
listed action dependencies need setup before affected steps execute. Optional
action-contract gaps are reported as `required_for:
optional_action_execution`; resolve them only when the operator selected that
branch. Missing credential rows include provider setup guidance from
`config.setup`, including the local connection URL and safe vendor registration,
API-key, billing, docs, confidence, and fallback fields when known.

## Execution Context Refs

Use execution contexts when several provider actions share the same project,
credential, provider scope, output policy, request budget, or artifact
namespace. The context is a StackOS object, not plugin business logic.

Agent flow:

1. Create or discover a context with `executionContext.create`,
   `executionContext.list`, or `executionContext.discover`. Link it to the
   relevant task, ticket, run plan, or run so resumed agents can find it.
2. Inspect compatibility with `executionContext.resolve` before a new action
   family or provider scope is used.
3. Pass `context_ref` to `action.validate`, `action.run`, or
   `action.execute`. StackOS applies the daemon-held `credential_ref`, validates
   and merges typed `provider_context_json`, records output/request-budget
   metadata, and keeps endpoint `input_json` separate.
4. External provider actions are file-backed by default for MCP/REST calls and
   raw inline by default for CLI calls. Use `output_policy_json.mode` only for
   deliberate overrides such as `inline`, `file_if_large`, a custom
   `semantic_name`, or an absolute directory `path`. StackOS generates the
   response filename inside that directory. For file-backed responses,
   inspect the returned file path before rerunning paid/provider calls. If the
   response envelope contract is needed, call `schema.get` with the returned
   `schema_ref`. Use `artifact.read` only for intentional StackOS artifacts.
5. Treat `request_budget_json` as the coordination contract for agents using
   the context. Supported fields are `max_parallel`, `max_calls`,
   `max_calls_per_run`, `window_seconds`, and `notes`. Current action-call rows
   are terminal audit records, so StackOS records these limits and exposes them
   in action metadata/UI; agents should obey them until a running-call lifecycle
   exists for hard concurrency enforcement.

Agent-facing discovery surfaces reinforce this path. `action.describe` returns
`execution_context` guidance for the selected action, and
`executionContext.discover` returns a scoped `executionContext.create` next call
when no reusable context exists yet.

Risk labels come from the action or generated catalog contract, not HTTP method
alone. Catalog-marked or semantically read-only reporting/list/export POST
actions can be read actions, while idempotent mutations remain write actions
and still require the normal confirmation/idempotency protections.

## Direct Actions Vs Workflows

`action.run` is for one explicit action when no workflow state is needed:

1. The current bridge workspace must resolve to a project, or scripts must pass
   `project_id`.
2. The caller must pass an explicit action ref or plugin/action pair.
3. Non-read actions require `confirm_direct=true` and `intent_summary`.
   Callers may pass `intent_id` or `idempotency_key` for stable retries; when
   absent, StackOS derives a safe key before connector execution.
4. Credentials are resolved inside the daemon; callers pass only
   `credential_ref`.
5. Provider execution context, when supported by the selected action, is passed
   through `provider_context_json` and validated against the action's
   `provider_context_schema_json`. This context is connector-owned and is not
   endpoint body/query/path payload.
6. When a reusable execution context exists, callers should pass `context_ref`
   instead of repeating credential and provider scope. Explicit
   `credential_ref` or `provider_context_json` still works as a deliberate
   override when the context allows it.
7. Direct external-provider action responses are compact file-backed by default
   for MCP/REST and raw inline by default for CLI. File-backed responses point
   to a sanitized action-output file with schema version, checksum, byte size,
   and semantic name. Request raw only when the public audit shape itself is
   required; read the returned file path only when the request/response envelope
   is needed.
8. The execution writes the same `action_calls` audit row as workflow
   execution, but without run-plan linkage.

Agent-facing MCP setup/discovery tools default to compact operation-shaped
responses when the operation policy allows it. The operation response shaper is
the single source of truth for compact/raw/ack behavior; the bridge forwards the
requested mode and protects legacy non-operation replies only. Use
`response_mode=raw` or `response_mode=standard` for the normal daemon payload,
and `response_mode=ack` only for safe internal writes. REST and UI operation
calls use each operation's response policy default, which is usually compact for
agent-facing reads and writes; callers that need the full contract must request
`response_mode=raw` explicitly.

Compact `resource.get` and `resource.query` responses retain bounded resource
and record summaries, record ids, `data_json`, timestamps, counts, and cursors.

Use `action.execute` when the action belongs to a workflow:

1. A valid `run_token` is required.
2. The run token must belong to a started run plan.
3. Exactly one run-plan step must be running.
4. The requested `project_id` must match the plan project.
5. The active step must declare the requested `action_ref`.
6. The frozen `mcp_tool_grants` snapshot must grant `action.execute` for that
   exact `action_ref`.
7. Credentials are resolved inside the daemon; callers pass only
   `credential_ref`.
8. Pass `context_ref` when a workflow/task-linked execution context supplies
   credential, provider scope, output policy, request budget, or artifact
   namespace defaults for the step.
9. The execution writes an `action_calls` audit row with redacted request,
   response, metadata, and run-plan linkage.

`runPlan.*` keeps the same boundary everywhere:

1. `runPlan.validate`, `runPlan.create`, `runPlan.start`, `runPlan.get`,
   `runPlan.getStep`, `runPlan.checkConsistency`, and `runPlan.list` are bootstrap/setup
   operations.
2. `runPlan.claimStep` and `runPlan.recordStep` require the `run_token` returned
   by `runPlan.start`.
3. `runPlan.claimStep` activates only the frozen grants for the claimed step.
4. A run plan may have only one running step. Record the running step before
   claiming another.
5. `runPlan.recordStep(blocked)` records a recoverable blocker and keeps the
   plan started so the same step can be reclaimed after repair.
6. `runPlan.recordStep(success)` enforces run-plan lifecycle, transitive step
   dependencies, and the frozen required output keys and JSON schemas before
   changing state. Validation failures keep the step running and return output
   repair paths. Tracker graph warnings stay visible through
   `tracker.get(include_graph=true)` and `runPlan.get.consistency_issues`, but
   do not by themselves reject a valid step result.
7. `runPlan.recordStep(success|failed|skipped)` persists a terminal step result
   and closes the plan/run when the last step finishes or a step fails.
8. `runPlan.update` records safe metadata or approval-gate decisions through
   the local REST/CLI admin surface. Direct MCP agents are not base-granted to
   approve their own gates.
9. `runPlan.recover` is a narrow lifecycle repair for system-recoverable
   terminal states, such as an old daemon rejecting a recoverable blocked step
   or a daemon-restart orphan aborting the canonical workflow. It restores the
   same plan/run into a live blocked or pending step instead of creating a
   duplicate replacement workflow.
10. `tracker.reopen` is the normal operator follow-up path after closeout. It
   accepts a task key, run-plan id, or linked run id and reopens the task, or
   the linked plan/run/task mirror when the target is workflow-backed. Agents
   should use it instead of patching workflow task status directly.
11. Stale-run reaping, explicit run aborts, and run-plan aborts reconcile the
   linked run, plan, unfinished steps, pending approvals, and tracker mirror
   through one lifecycle path. If old data or manual edits leave a mismatch,
   `runPlan.get.consistency_issues` and `runPlan.checkConsistency` expose the
   read-side diagnostics.

`agentRequest.*` keeps the same boundary everywhere:

1. `agentRequest.list` and `agentRequest.get` read sanitized queue state.
2. `agentRequest.claim` is a bootstrap work-queue mutation and requires
   `claimed_by` plus `idempotency_key`; the raw `claim_token` is returned only
   in the claim response or its idempotency replay.
3. `agentRequest.prepareRunPlan` is a bootstrap work-queue mutation that
   atomically claims a request, creates the caller-supplied run plan or
   template-backed run plan, links both records, and returns the claim token.
   It does not choose the plan, start execution, call a model, call providers,
   or send replies.
4. `agentRequest.release`, `agentRequest.linkRunPlan`, `agentRequest.complete`,
   and `agentRequest.ignore` require the active `claim_token`.
5. `agentRequest.create` is registered for REST/CLI/MCP parity, but it is not a
   bootstrap/system write. Normal callers need a `run_token` whose active
   run-plan step explicitly grants `agentRequest.create`.
6. Agent request operations never call Telegram, SMTP, IMAP, or any provider API.

`localAgentChat.createMessage` stores local human/agent chat messages as
communications resources and may create a generic `agent_request` for inbound
human messages. It does not run a model, choose a workflow, call a provider, or
send an external reply. Agent responses in the same local chat thread use the
same operation with `direction=outbound`, the same `thread_key`, a new
`message_key`, and `create_request=false`; the result is a stored
`communication-message`, not a new request.

`communicationProfile.*`, `communicationSurface.*`,
`communicationContact.*`, `communicationMembership.*`, `communicationTarget.*`,
and `communicationRoute.*` are setup/read
operations for provider-neutral communication state. They store identities,
surfaces, contacts, memberships, named destinations, and handoff routes.
Surfaces should carry `audience`, `intent`, `agent_guidance`, `data_scope`, and
`external_context` when a channel/DM/mailbox can contain customer or internal
operational context. These fields are static guidance for the operating agent:
they do not authorize a send, choose a workflow, or fetch live provider history.

`communication.send` and `communication.reply` are the normal delivery
operations for agents. The agent passes a named target or request id plus
message content; StackOS resolves actor/profile, target, provider action,
daemon-held credential, policy, capabilities, idempotency, and action audit.
If a requested rich feature cannot be delivered exactly, the operation rejects
with `effect: none`, failed JSON paths, supported capabilities, and repair
options. It does not silently convert buttons, attachments, private delivery,
threads, or notification behavior.
For media-bearing sends, text/caption and files stay in the provider's native
media message path: Slack resolves to file upload with `initial_comment`, and
Telegram resolves to file/photo upload or media group rather than sending an
extra text message first.
Simple one-off sends can run directly. Workflow sends can also run with a
`run_token`; in that case the active step must grant `communication.send` with
explicit `targets` such as `communication-target:ops-alerts`. Workflow replies
must grant `communication.reply` with explicit origin `sources` such as
`telegram-bot`, `slack-bot`, or a source surface ref.

`communicationTarget.resolve` remains a read-only planning/debug helper. It
returns an explicit provider action ref and safe defaults; it does not send.
It evaluates send policy with the same target/default profile selection that
`communication.send` uses when `from` is omitted, and returns
`policy_profile_ref` so agents can see which actor profile was checked. Pass
`profile_ref`, `source_surface_ref`, and `invoker_ref` when the source request
has them so target policy can evaluate a specific communication profile, source
surface, and approved human/bot actor. Provider actions through
`action.run`/`action.execute` are lower-level escape hatches for provider-specific
work, not the default agent path.

`communication.send` and `communication.reply` with `dry_run=true` validate the
provider payload and write a dry-run `action_calls` audit row, but do not call
the provider connector. The response `effects` field states whether the provider
connector was called or only validation/audit happened.
`communicationContext.query` returns bounded stored communication-message
history only. It can return outbound messages StackOS sent, inbound messages or
interactions delivered through ingress, and state changes StackOS recorded.
It does not call Slack, Telegram, email, or future providers to recover history
that never reached StackOS. Live provider history fetches and backfills must be
explicit provider actions with provider scopes, pagination, rate-limit handling,
visibility checks, and audit.
Invalid `fields` requests return both the rejected `fields` and the
`allowed_fields` set so agents can repair the query without guessing.

`ingressEndpoint.*` stores the project-level public webhook endpoint for
communications. Configure stores the generic endpoint, refresh updates it from
explicit input or driver discovery, routes derives provider webhook URLs, sync
writes safe route metadata into profiles, and status reports readiness. `ngrok`
is only a local tunnel provider configured under `driver_config`; production
uses `driver=public-url` with a deployed HTTPS base URL.

`toolProfile.resolve` is the agent-friendly target resolver. Use it before
`action.run` or workflow setup when the agent needs one safe execution tuple for
a provider: optional project tool profile, daemon-held `credential_ref`,
provider auth status, and a concise setup `next_action` when the tuple is not
ready. For Telegram it resolves the `communication-profile` plus its
credential profile. For SMTP, IMAP, and other provider credentials it resolves
the requested auth profile directly. It never returns secret payloads and never
chooses the workflow or provider action for the agent.

No operation adapter should bypass repository/connector auth, grant, idempotency,
or audit code.
