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
session:

```text
operation.list({ "surface": "mcp" })
operation.list({ "category": "actions", "query": "sitemap" })
operation.list({ "mode": "grouped" })
operation.describe({ "name": "communication.send", "surface": "mcp" })
operation.describe({ "name": "operation.describe", "surface": "mcp" })
agentPreset.resolveForWorkflow({ "workflow_key": "core.project-memory-review" })
```

The discovery operations are OperationSpecs too. `operation.list` includes
`operation.list` and `operation.describe`, and `operation.describe` can describe
both discovery tools with the same schemas, examples, and guidance it returns
for domain operations. Use `category`, `query`, and `mode: "grouped"` when an
agent only needs a compact operation route instead of the full inventory.

Each description includes:

- enabled surfaces: `mcp`, `rest`, `cli`
- grant policy, for example `run-plan-step-action-ref`
- secret policy
- JSON input schema
- JSON output schema
- prerequisites
- return notes
- examples

MCP tools are generated from the same operation specs, so MCP clients still get
tool schemas through `tools/list`. Native MCP clients should use
`operation.list` and `operation.describe` for OperationSpec purpose,
prerequisites, return notes, examples, and schemas. Use `toolbox.describe` for
hidden setup and step-granted tools that are not advertised directly.

The UI reads the same docs at `/projects/{project_id}/operations`. That page is
not a second registry; it renders `GET /api/v1/operations` and
`GET /api/v1/operations/{operation_name}`.

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

For common cross-cutting fields, the CLI can merge flags into the input JSON:

```bash
stackos ops call action.execute \
  --project 1 \
  --run-token "$RUN_TOKEN" \
  --idempotency-key sitemap-1 \
  --input action-input.json
```

`--input -` reads the operation arguments from stdin.

Common operations also have aliases that still call the generic operation
endpoint:

```bash
stackos actions describe utils.sitemap.fetch --project 1
stackos actions validate utils.sitemap.fetch --project 1 --input action-input.json
stackos actions execute utils.sitemap.fetch \
  --project 1 \
  --run-token "$RUN_TOKEN" \
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

Agent tracker reads such as `tracker.status`, `tracker.next`,
`tracker.blockers`, `tracker.brief`, `tracker.why`, `tracker.execute`,
`tracker.verify`, `tracker.history`, `tracker.changed`, and `tracker.search`
are compact by default. Pass `response_mode: "standard"` through `ops call`,
MCP, or REST only when the full diagnostic row payload is needed.

## Registered Core Operations

The current core operation registry includes:

- `action.describe`
- `action.list`
- `action.validate`
- `action.run`
- `action.execute`
- `agentPreset.list`
- `agentPreset.describe`
- `agentPreset.resolveForWorkflow`
- `agentRequest.list`
- `agentRequest.get`
- `agentRequest.create`
- `agentRequest.claim`
- `agentRequest.prepareRunPlan`
- `agentRequest.release`
- `agentRequest.linkRunPlan`
- `agentRequest.complete`
- `agentRequest.ignore`
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
- `ingressEndpoint.configure`
- `ingressEndpoint.refresh`
- `ingressEndpoint.routes`
- `ingressEndpoint.sync`
- `ingressEndpoint.status`
- `localAgentChat.createMessage`
- `toolProfile.resolve`
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
- `tracker.pick`
- `tracker.release`
- `tracker.linkRunPlan`
- `runPlan.validate`
- `runPlan.create`
- `runPlan.start`
- `runPlan.get`
- `runPlan.list`
- `runPlan.update`
- `runPlan.claimStep`
- `runPlan.recordStep`

Tracker list workflows reuse existing tracker operations. Use
`tracker.createTicket` with `tickets_json` and `dry_run=true` for draft review,
call the same operation without dry-run to create the list, use `tracker.get`
filters for review, and use `tracker.updateTicket` with `updates_json` for
atomic per-ticket patches. Do not add separate list-specific tracker endpoints.

Agent preset setup reuses the same operation infrastructure. Use
`agentPreset.list` to discover generic role presets, `agentPreset.describe` to
read one role and its project-adaptation contract, and
`agentPreset.resolveForWorkflow` to resolve a workflow template into
required/recommended agents plus `skill_requirements`.

Workflow templates are inert presets/contracts. They do not act by themselves.
The main agent should resolve preset and skill requirements, adapt generic roles
to the project, create a concrete run plan with `runPlan.create`, and work
through tracker tasks/tickets with explicit dependencies and evidence.

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
5. Direct action responses are compact by default. Use `verbose=true` only when
   the full redacted action call and output payload are needed.
6. The execution writes the same `action_calls` audit row as workflow
   execution, but without run-plan linkage.

Agent-facing MCP setup/discovery tools also default to compact bridge-shaped
responses. Use `response_mode=standard` for the normal daemon payload and
`response_mode=verbose` for tools that support expanded diagnostics. REST and UI
surfaces keep their full contracts.

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
8. The execution writes an `action_calls` audit row with redacted request,
   response, metadata, and run-plan linkage.

`runPlan.*` keeps the same boundary everywhere:

1. `runPlan.validate`, `runPlan.create`, `runPlan.start`, `runPlan.get`, and
   `runPlan.list` are bootstrap/setup operations.
2. `runPlan.claimStep` and `runPlan.recordStep` require the `run_token` returned
   by `runPlan.start`.
3. `runPlan.claimStep` activates only the frozen grants for the claimed step.
4. A run plan may have only one running step. Record the running step before
   claiming another.
5. `runPlan.recordStep` persists the terminal result and closes the plan/run
   when the last step finishes.
6. `runPlan.update` records safe metadata or approval-gate decisions through
   the local REST/CLI admin surface. Direct MCP agents are not base-granted to
   approve their own gates.

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
