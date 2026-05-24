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

Agents are the primary callers for workflow execution operations. Scripts can
use the same catalog for automation and setup, but run-plan mechanics are
optimized for agents that need precise schemas, grants, examples, and safe
return notes. Callers should not guess operation payloads. The operation
registry returns an agent-readable description for every registered operation:

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
tool schemas through `tools/list`.

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
```

## Registered Core Operations

The current core operation registry includes:

- `action.describe`
- `action.validate`
- `action.run`
- `action.execute`
- `agentRequest.list`
- `agentRequest.get`
- `agentRequest.create`
- `agentRequest.claim`
- `agentRequest.prepareRunPlan`
- `agentRequest.release`
- `agentRequest.linkRunPlan`
- `agentRequest.complete`
- `agentRequest.ignore`
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
- `communicationProfile.list`
- `communicationProfile.get`
- `communicationProfile.upsert`
- `ingressEndpoint.configure`
- `ingressEndpoint.refresh`
- `ingressEndpoint.routes`
- `ingressEndpoint.sync`
- `ingressEndpoint.status`
- `localAgentChat.createMessage`
- `toolProfile.resolve`
- `runPlan.validate`
- `runPlan.create`
- `runPlan.start`
- `runPlan.get`
- `runPlan.list`
- `runPlan.update`
- `runPlan.claimStep`
- `runPlan.recordStep`

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
4. `runPlan.recordStep` persists the terminal result and closes the plan/run
   when the last step finishes.
5. `runPlan.update` remains an admin-only MCP operation and is intentionally not
   exposed through REST or CLI.

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
send an external reply.

`communicationProfile.*`, `communicationSurface.*`,
`communicationContact.*`, `communicationMembership.*`, `communicationTarget.*`,
and `communicationRoute.*` are setup/read
operations for provider-neutral communication state. They store identities,
surfaces, contacts, memberships, named destinations, and handoff routes.
Surfaces should carry `audience`, `intent`, `agent_guidance`, `data_scope`, and
`external_context` when a channel/DM/mailbox can contain customer or internal
operational context. These fields are static guidance for the operating agent:
they do not authorize a send, choose a workflow, or fetch live provider history.
`communicationTarget.resolve` returns an explicit provider action ref and safe
defaults; it does not send. Pass `profile_ref`, `source_surface_ref`, and
`invoker_ref` when the source request has them so target policy can evaluate
the communication profile, source surface, and approved human/bot actor. Target
`send_policy` supports `allowed_profile_refs`, `allowed_invoker_refs`,
`allowed_source_surface_refs`, and `allowed_target_refs`; all configured
allowlists must match. The resolver returns provider-ready defaults when it can
derive them safely, such as Slack `surface_ref` or Telegram `chat_ref` plus
`profile_key` from a communication profile Telegram facet. The caller still
adds the message-specific fields and validates the final explicit action.
`communicationContext.query` returns bounded stored communication-message
history only. Live provider history fetches must be explicit provider actions.

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
