# StackOS Operations

StackOS operations are the protocol-neutral callable layer. An operation is
registered once, then adapters expose it through MCP, REST, and CLI when its
surface policy allows that.

Operations implement the explicit-execution boundary in the canonical
[`Product Contract`](./product-direction.md#product-contract). They expose
reusable behavior; they do not own agent strategy or workflow-specific
business decisions.

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

## Project workflow schedules

`schedule.list`, `schedule.set`, `schedule.toggle`, `schedule.remove` and
`schedule.runs` share the project schedule repository through MCP, generic
REST operation calls and `stackos ops call`. The existing project schedule
REST URLs delegate to that same owner, including the URL project ID on
PATCH/DELETE. ID-based mutations require `project_id`; another project's
job is reported missing before any effect.

`schedule.set` replaces configuration under a stable project `kind` key.
It accepts a display `name`, five-field `cron_expr`, IANA `timezone`, existing
`template_key`, concrete `inputs_json`, and `execution_json`. Execution settings
reuse `tracker.startDelivery`: required `project_dir`, optional `host_key`,
`delivery_agent`/`review_agent` overrides and `mcp_config_ref`. Task keys,
ticket assignments, run-plan IDs and step IDs belong to occurrences and are
rejected here. Roles must carry explicitly prepared workflow instructions in
project defaults or these overrides; validation does not judge their semantic
suitability. Credentials remain daemon-held; raw secret fields are rejected,
including in disabled drafts. Opaque secret markers keep their existing rules.
Cron parsing uses the existing APScheduler `CronTrigger.from_crontab` contract:
numeric weekdays run from Monday (`0`) to Sunday (`6`); named weekdays such as
`mon` avoid ambiguity. Persisted next-fire timestamps use UTC.

Schedules default to disabled. Incomplete disabled drafts are permitted;
supplied cron, timezone and execution shape must still be valid. Enabling
strictly validates effective workflow inputs/defaults with the existing
read-only run-plan validator and resolves current host/role settings through
the handoff preflight. This creates no request, run plan, run or tracker task,
and grants no external action approval. Legacy metadata rows migrate to
disabled, retain their IDs/key/cron/history and show a needs-setup issue.

`schedule.remove` actually deletes configuration and reconciles its timer;
it returns `{project_id, job_id, removed}` in the write envelope. It never
cancels existing work or deletes its request, plan, run or task.
Schedule IDs are never reassigned after removal. Migration `0037_schedule_id_nonreuse`
preserves existing IDs and advances allocation beyond retained schedule history,
so a new automation cannot inherit a removed automation's runs or stale references.
The daemon installs `operation_services.schedule_reconcile(project_id, job_id)`
for affected timer reconciliation; every mutation reaches that seam after
commit, and queued admission must re-read current configuration.

Native availability uses the registered REST-only `desktop.availability`
transport: Electron main POSTs `/api/v1/desktop/availability` with the locally
read daemon token and holds the response stream open. The response begins with
an SSE `ready` event and has no replay or heartbeat. Connection lifetime is the
availability fact; it is not a JSON presence setter. Generic JSON operation
calls cannot open it, and browser console tokens, CLI and MCP cannot enable it.
Quit, suspend or disconnect removes workflow timers while maintenance and
admitted tasks remain active. Reconnection arms future times only. Workflow
jobs override the scheduler's permissive defaults with one-second jitter
tolerance and generation/configuration checks before admission.

`schedule.runs` pages existing scheduled requests linked to project-scoped
plans (`limit` 1–200, descending request ID cursor `after_id`, optional
`schedule_id`). Scheduled request metadata carries `schedule_id` and
`schedule_name` snapshots; `source_kind` is `schedule`. Rows retain these
snapshots after configuration rename/removal. The projection returns actual
request/plan/run IDs, workflow title/key, nullable canonical task key, task
status, timestamps/derived duration, `agent_stats` and `active_execution` from
the tracker. A draft plan has no run ID until started; an unmaterialized or
skipped tick has no row. There is no schedule occurrence/status/usage ledger.
Run and claim tokens and arbitrary request metadata are never projected.

For example, save an inert draft with
`stackos ops call schedule.set --input automation.json`, using:

```json
{
  "project_id": 1,
  "kind": "weekly-review",
  "name": "Weekly review",
  "cron_expr": "0 9 * * 1",
  "timezone": "America/Los_Angeles",
  "template_key": "core.project-memory-review",
  "inputs_json": {"goal": "Review project memory"},
  "execution_json": {"project_dir": "/work/project"},
  "enabled": false
}
```

Finish suitable role setup before enabling. The desktop admission and timer
runtime own whether a future firing is currently available; saved enabled
configuration alone is not evidence of a running desktop app.

## Local backup administration

`backup.status`, `backup.configure` and `backup.create` share the generic
encrypted local backup service. They are local-admin REST/CLI operations,
with explicitly browser-safe controls on Backups (`/setup`). They
are excluded from MCP, project/workflow grants and derived agent-token
authority. Their exact registered REST paths enforce the local-admin boundary.

Configuration stores a public recovery key, explicit external folder targets,
destination and opt-in schedule. Partial configuration preserves omitted
fields. Status and creation return safe coverage, time, path, size, checksum
and failure metadata; no archive bytes, private recovery key, credential or
financial contents are returned. Status includes covered managed project IDs,
or null when coverage could not be verified. Creation includes all managed
project files and enrolled targets and
rejects unsafe destinations. Recovery private key generation and verified
restore remain host-local CLI operations. See [backup and recovery](./backups.md)
for setup, automatic project coverage and key custody.

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
stackos ops describe actionCall.get --json
stackos ops describe runPlan.claimStep --json
```

The same metadata is available through REST:

```http
GET /api/v1/operations
GET /api/v1/operations/action.execute
GET /api/v1/operations/action.run
GET /api/v1/operations/actionCall.get
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

In a global session, first call
`workspace.startSession({"global_session":true,"project_id":42})` for the
chosen existing project, or omit the ID to discover projects first. Startup
creates no project/directory binding and never sets a default. Pass an explicit
positive existing `project_id` on every scoped call, inside `arguments` for
`toolbox.call`. Direct browser calls use their top-level `project_id`.
Run-aware discovery also needs the project before the bridge refreshes grants:

```javascript
toolbox.describe({"project_id": 42, "run_id": 7, "tool_names": ["resource.upsert"]})
toolbox.call({
  "tool_name": "runPlan.get",
  "arguments": {"project_id": 42, "run_plan_id": 3}
})
browser.profile.list({"project_id": 42})
```

Use IDs from the selected project/run, not these example numbers. A run or plan
from another project, missing project scope, or failed authority refresh cannot
reuse cached permission. Direct-read and direct-run-audit-write inner IDs remain
operation targets, so historical `run.get` and ordinary run maintenance do not
require an active workflow. An outer `toolbox.call.run_id` denotes execution
context and is validated separately. Provider `*_json` payload IDs are opaque.
For pending discovery, normal folder startup, named setup, and saved-ID adoption,
see [the setup path](agent-operating-model.md#setup-path).

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

`secret.set` is the narrow exception for write-only action payload ingress. It
is available only through MCP/toolbox, is scoped to the selected project, and
returns one opaque `secret_ref`. It is intentionally absent from REST and CLI
and has no plaintext read/list operation. Put the ref into an action string
field only as the exact marker `{"$secret_ref":"secret_..."}`. This is
separate from `credential_ref`, which remains provider authentication.

## Public Getting-Started Guide

`guide.gettingStarted` is the agent-readable path to the public first-use
guide. It is a global, read-only operation, so an agent can call it before a
workspace is bound:

```text
toolbox.call({
  "tool_name": "guide.gettingStarted",
  "arguments": { "response_mode": "raw" }
})
```

The raw response returns the current Markdown plus two stable references:
`guide_url` is the designed page for people, and `markdown_url` is the
plain-text source for tools. Agents should answer from the fetched content and
include `guide_url` for the person. They must not turn the skill prompt or MCP
response into a second maintained guide. If the website fetch is temporarily
unavailable, the operation still returns both references and a warning;
share `guide_url` and avoid inventing replacement instructions. Compact mode
keeps the references and omits the document body.

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

When a background action is accepted, its response has status `running` and
includes `action_call_id`, `poll_operation=actionCall.get`, exact
`poll_arguments`, and `next_poll_after_ms`. Call the project-scoped,
read-only `actionCall.get` operation until the persisted status is terminal.
Its live progress counters exist only in the daemon process and are never
terminal evidence; stored status, output/error, and completion time are the
authority. After a daemon restart, an orphaned running call is stored as
`failed` with `outcome_unknown=true` and `retry_safe=false`.

`runPlan.recordStep` rejects `success` and `skipped` while the same project's
plan step has running action calls. Its validation error returns pending action
IDs and polling repair context; no step lifecycle state changes on rejection.
Failure and blocked recovery retain their normal semantics.

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

## Agent activity reads

`tracker.agentStats` (`project_id`) and `tracker.agentStatsAll` (`is_active`:
true/false/null for Active/Archived/All, default true) expose one tracker-owned
projection of recorded invocation receipts. The project operation uses
`direct-read` with project scope; the global operation uses `local-admin-read`,
so project-scoped MCP callers cannot read it. Registry-generated MCP, REST and
CLI surfaces share `TrackerAgentStatsOut` and support compact/raw, without ack.

Both return scope, `as_of`, `totals` and grouped `rows`, preserving all numeric
usage and missing/classification coverage in compact responses. No private
receipt payloads are returned, and reads change no state. See
[metric semantics](./task-tracker.md#recorded-agent-statistics).

```json
{"arguments": {"project_id": 1, "response_mode": "compact"}}
```

Use that payload at `/api/v1/operations/tracker.agentStats/call`. Local admin
may call `/api/v1/operations/tracker.agentStatsAll/call` with
`{"arguments":{"is_active":null}}` for all projects.

## Browser Operations

The browser operation surface owns runtime status, profile create/list, and
session start/list/status/stop. Session discovery reconciles owned gstack state
and OS process identity after daemon restart. Every session row includes a ready
`cli_argv` prefix: `["stackos.browser","--session",session_ref]`.

Agents append upstream gstack arguments and run that command in their terminal.
The standalone launcher resolves the exact session once through the existing
authenticated status operation, then replaces itself with the native process.
Native commands, output, terminal streams and exit status do not pass through
StackOS operation dispatch. There is no browser command relay or browser-method
allowlist. Upstream owns parsing and browser behavior.

Selected start/status returns `native_cli={executable,cwd,env}` independently of
observed health when its runtime and profile binding are valid. Bulk lists omit
that context. A live sibling profile owner prevents a competing handoff.

The same OperationSpec supplies lifecycle MCP, generic REST, generic CLI, and
Operations UI discovery. Selected paths are local capabilities; state contents
and tokens are not exposed. Native output may contain requested page data.
Agents preserve useful captures explicitly through generic artifacts. See
[browser sessions](browser-automation.md) for usage and profile continuity.

## Canonical ownership operations

The `run.owner*` contracts use the existing tracker identity and run/event owner.
Resolve external aliases first; mutations accept only one canonical `task:<id>`
or `ticket:<id>`. Ambiguous aliases reject. These operations store integrity and
audit; they launch no process, signal no cancellation and grant no publication.

| Operation | Contract |
| --- | --- |
| `run.ownerResolve` | Read one native identity or existing opaque external alias |
| `run.ownerGet` | Read stored fence/continuity plus current-source consistency and capture time |
| `run.ownerAcquire` | Reserve once; duplicate assignment returns `acquired=false` or conflict |
| `run.ownerCheckpoint` | CAS a reported checkpoint reference, preserving scope and counters |
| `run.ownerStop` | Add a stop without clearing ownership, counts or original scope |
| `run.ownerReassign` | Explicit replacement, or explicit `new_owner_ref=null` revocation, after backend-certified quiescence/effect disposition |
| `run.ownerConsume` | Increase counts or reconcile authoritative floors; reject negative/reset/limit/ref changes |
| `run.ownerRecordAttempt` | Annotate an existing bound child run's reported observation or terminal attempt reason |

Writes require the current controller run, exactly one running step, an explicit
tool grant with exact canonical `targets`, and the item's persisted plan/step
binding. They recheck the shared grant owner and current canonical continuity
inside the SQLite write transaction before audit/commit. System/local-admin
traffic, arbitrary owner strings and evidence references cannot bypass this
boundary. Controller tokens stay controller-held; `owner_ref` names an assignment.
See [ownership grants](./run-plans.md#canonical-ownership-grants).

Before first admission the canonical item's existing `metadata_json` holds
`ownership_continuity`: scope digest, original budget/stop/history refs, known
stopped/denied flags, cumulative attempts/failures and positive attempt/failure
limits. Existing effect/deadline refs are retained. These are corroborated inputs
from the work-item owner, not a caller's replacement policy. Unknown or changed
authority blocks the affected write. Consumption preserves immutable refs and
limits, takes the maximum stored/current counts and adds only nonnegative deltas.
Late failures remain recorded after a stop; they cannot admit another attempt.
Exhausted failures persist a stop. No operation clears stops or extends a budget.
`authority_valid` in inspection describes continuity consistency, not permission.

Observation annotations and terminal reasons stay on existing child `Run`
metadata/status. The child must match controller parent, project, canonical item,
owner, epoch and scope. Reported states are running, waiting, suspected stall,
blocked, scope drift and review ready. Resuming blocked/drift observations needs
recorded clearance, current unstopped authority, reconciled canonical counters,
and remaining failure budget. Zero-delta `ownerConsume` reconciles externally
advanced counters; exhausted failures persist a stop and cannot be cleared by an
observation. Resuming an already charged attempt does not admit another attempt.
Verified hang, host suspension and accepted completion are backend-only; their
trusted producers remain separate.
Terminal reasons map to existing run status: succeeded→success, failed→failed,
cancelled/abandoned/superseded→aborted. Supersession needs current backend evidence.
Terminal attempts cannot reactivate; succeeded never completes a tracker ticket.

Compact/raw ownership receipts retain the complete canonical fence, checkpoint,
continuity refs, request digest, actual controller `run_id` and replay status;
ack is not supported. Idempotency matches exact operation/input/run/step before
replay and revalidates current authority/fence. Acquisition replay always returns
`acquired=false`. Attempt receipts also revalidate the child's identity, ownership
binding/annotations and terminal lifecycle; heartbeat or unrelated metadata updates
alone do not invalidate replay. A changed request or stale result conflicts. If
commit precedes response/cache interruption, inspect `run.ownerGet`; an in-flight
key is not permission to retry or dispatch. No quiescence certificate setter
exists on MCP, REST or CLI. MYB-95 supplies genuine host/effect evidence; storage fixtures are
not that proof.

The generic operation catalog/REST/CLI and UI operation inspector consume these
same schemas. This source contract does not upgrade a loaded daemon, alter an
existing grant snapshot or activate the stopped evaluation.

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

## Finance local console operations

Finance registers these exact REST/CLI operations over one project-owned
`local-json-v1` master. They are disabled on MCP; agents perform finance method
through existing host/workflow contracts, not domain MCP primitives.

| Operation | Behavior | Authority/response |
| --- | --- | --- |
| `finance.status` | Prepare the enabled project's default Finance store and inspect readiness/snapshot. Never replace missing or corrupt existing records. No caller-selected root. | `local-admin-console-write`; handler-owned retry validation; compact/raw safe receipt. |
| `finance.query` / `finance.record.get` | Bounded filters, source coverage, current facts/history and undated record refs. | Current project/root/read mode; compact/raw. |
| `finance.record.preview` | Validate a sourced draft against its source snapshot without writing. | Current correction mode; compact/raw. |
| `finance.record.commit` | Guarded additive correction/create/proposal, exact preview and revision/hash, actual readback. | `local-admin-console-write`; handler-owned retry and under-lock authority recheck. |
| `finance.evidence.read` / `finance.report.render` | Registered private original or derived period/independently reviewed analysis result. | Raw-only bounded content with exact hash/state; no ack/compact. |

The generic endpoint is `/api/v1/operations/{name}/call`; CLI `ops call {name}`
uses the same authenticated REST owner. Trusted local context requires the exact
registered path, browser-safe surface, MCP-disabled spec and matching read/write
policy. Headers, caller actor/admin fields and run tokens cannot widen it.
`OperationSpec.idempotency_policy` defaults to `dispatcher`; finance writes opt
into `handler` so every retry revalidates current root authority and original
external intent. Descriptions expose that metadata. Existing dispatcher raw/ack
replay remains unchanged for its default consumers.

Private evidence/report responses are bare `FinanceContentOut` in raw mode;
other default compact console calls return the normal `data` envelope.
Exact minor-unit strings and source revision/hash survive shaping. Registry models
generate UI contracts through `scripts/write-openapi.py`, without a second schema.
Browser `localAgentChat.createMessage` is a separate bounded inbound human intent:
safe refs/query/snapshot only, no actor/outbound spoofing, provider work or model
execution. Clear private search text and apply filters before analysis; read
[the actual host and result protocol](../plugins/finance/references/workflow-handoffs.md#management-review-request-and-result).

## Automated delivery results and manual instruction adoption

The generic `stackos.task_delivery` module owns task execution.
Its host calls register actual agent/model/start lifetime at
`stackos.host_mcp.service.run_agent`. Tracker task-index, ticket and detail reads
expose the same nullable `active_execution` projection; persisted status never
substitutes for an active call. Registration/cleanup publish
`tracker.execution.changed` through the existing tracker SSE feed without changing
ticket status. The same host-service registry owns live usage observation for
active Codex calls: one five-second collector reads appended native counters
from the exact rollout source, with a fixed prior-call baseline on resume.
No active calls means no collector timer or native reads. Other hosts retain
their existing final collection; unproven live counters remain unavailable.

The existing context timeline SSE transport also sends replaceable
`tracker-usage` frames, without durable event IDs or per-sample database writes.
Project streams retain project scope; `/api/v1/context/timeline/stream` supplies
the same local-admin transport for Overview's global scope. Reconnect sends the
current transient state; durable tracker events retain their existing cursor.
UI subscribers share the transport and refresh owner; usage-only Work updates
patch metrics and execution in place without rebuilding the page or graph.
Native exit immediately closes readers and removes live identity. Bounded measured
samples and counters remain readable through the synchronous usage-to-receipt
handoff, which persists them with the actual execution identity in the existing
invocation receipt. Subsequent registry cleanup releases that in-memory snapshot,
including when notification fails; durable receipt history remains available for
task/ticket projections. Native result and candidate acceptance are still validated
afterwards. Receipt-statistics changes publish `tracker.usage.changed` atomically
with that write, including evidence-only
updates. The stream includes affected task/ticket projections after persistence;
the UI does not treat that usage event as another page-reload request. There is
no per-sample event write, separate history collector or new retry policy.
Statistics readers copy provisional counters before loading fresh receipt
evidence, so a request overlapping the handoff cannot lose both contributions.
Recorded usage wins over an older request copy; the existing SSE refresh shares
that copy across its task/ticket metrics. No database read holds the registry
lock or extends an invocation's reader/sample lifetime.
`tracker.configureDelivery` is the narrow local-admin REST-only setup write for
`TaskTracker.config_json.delivery`; it replaces that object while preserving
unrelated tracker configuration. Send `arguments` with `project_id` and
`delivery` to `POST /api/v1/operations/tracker.configureDelivery/call`. The raw
response includes saved configuration and tracker revision; MCP, CLI, workflow
grants, compact and ack are unsupported for this setup write. Read current
defaults through `tracker.get` with `response_mode=raw`. Changes affect explicit future handoffs. Queued/running executions keep their
snapshot; an explicit rehandoff after complete/stopped execution resolves current
defaults and overrides. Historical task/run snapshots and ticket evidence retain
their settings. Migration `0035_generic_task_delivery` transfers the retired
engineering plugin defaults once and preserves existing generic values.

`tracker.startDelivery` freezes project defaults, task overrides and optional
`agent_assignments` into one execution snapshot. The map identifies canonical
ticket keys and existing `delivery_agent`/`review_agent` roles; frozen step
extension `agent_assignment` supplies mirror defaults and explicit entries win.
For workflows the running `step_id` is entry context. The existing job traverses
all dependency-ready work, activates nested groups without launching them,
claims mirrors through the run-plan owner, runs accepted descendants and then
launches each remaining frozen mirror contract once. Zero-child mirrors launch
directly. Only `RunPlanRepository` records terminal mirror/plan/run state.
Bound controller context stays daemon-held; worker packets carry no raw token.

Every native result has `status` (success/repair/blocked/failed), `summary`,
`outputs` (a JSON-encoded object string), and `followups` (text objects).
This closed envelope transports arbitrary output contracts through native
harness restrictions. Malformed JSON/nonobjects reject; mirror outputs pass to
the canonical frozen-schema validation. Agents explicitly map domain conclusions
to status. Runtime has no domain verdict table. Only success accepts a gate;
unfinished non-ready work stops. After native validation, a valid success or
repair result following supported persisted correction/reopen returns to
dependency scanning without accepting the superseded occurrence. Mirror repair
without persisted progress stops; mirrors do not enter a duplicate ordinary-ticket
review loop. See the
[agent-owned correction method](task-tracker.md#agent-owned-automatic-corrections).

Ordinary tickets retain independent review and up to three scoped repairs in the same
delivery/review sessions, with persisted rejection, attempt receipts and consumed
repair allowance surviving requeue. Reviewers execute planned specs and relevant
integration checks; failures report repair, unavailable required checks blocked.
Unrelated notes stay in nonactionable followups and never enter repair prompts,
dependencies or automatic work.

Git is optional. Supplied `outputs.commit_sha` evidence retains clean-current-HEAD,
ancestry and exact review identity checks. Observation may cite unchanged HEAD;
project guidance requires separate commits for source changes/repairs.
Non-Git tasks invoke no Git commands; Codex uses `--skip-git-repo-check`.
Native/access/model/resume/schema failure, candidate mismatch or rejection after the third repair
stops the same task, with no fresh-session, host or model fallback.
See [task delivery](task-tracker.md#automated-task-delivery) for session/followup
storage and native execution modes.

Source/local guidance changes require manual adoption per project through the
local-admin `tracker.configureDelivery` configuration owner. Ordinary
MCP grants do not authorize that setup. Read the current configuration, copy its
complete `delivery` object, replace only selected role `instructions`, and save
that object; a supplied `delivery` replaces the key. Preserve names, task/role
hosts, models, provider/profile and unrelated fields, then read back exact equality
outside those instruction strings and exact content equality inside them. Keep
the approved source refs/revisions and candidate with the receipt, and verify a
subsequent new handoff prompt. No source edit refreshes saved prompts or active/
historical snapshots, and no synchronizer, watcher or cross-project rollout exists.

For this repository the manual mapping after independent candidate approval is:

| Existing saved field | Accepted local source |
| --- | --- |
| `delivery.delivery_agent.instructions` | `.codex/agents/sdlc-delivery.toml` parsed `developer_instructions` |
| `delivery.review_agent.instructions` | `.codex/agents/sdlc-delivery-reviewer.toml` parsed `developer_instructions` |

Current-project adoption is a separately authorized setup action after independent
source/local review and old-job completion. A reviewed one-time operational
helper may perform it when explicitly assigned, outside the daemon lifetime;
this is not a shipped synchronizer. Compare instruction strings with canonical
whitespace normalization and every other setting exactly. Preserve completed
tasks and frozen run/templates; an explicit rehandoff after completed/stopped
execution may refresh only current task execution settings while retaining
ticket receipts, sessions and consumed repair allowance. Never refresh a
queued/running job silently.
Other projects select and adapt their own available sources individually; host
files are optional and no other project is changed by this handoff.

## Human task content

`tracker.updateTaskContent` is an exact-path browser-safe REST/CLI operation
with MCP disabled and `local-admin-console-write` authority. Its strict input
accepts an existing project/task, `expected_updated_at`, a stable
`idempotency_key`, and `description` and/or `additional_input`. It cannot write
task title, goal, lifecycle, ownership, dependencies, run bindings or arbitrary
JSON. The handler derives the operator actor and atomically persists the
content, tracker revision and intent-bound retry receipt. Stale snapshots and
reused keys with changed intent return structured conflicts without mutation.
The raw-only response keeps the saved task, revision, input receipt and replay
status; ACK and compact modes are not available. See [task content](task-tracker.md#ui)
for storage and UI behavior. Broader tracker operations remain agent/controller
surfaces and do not become browser-safe through this exception.

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
stackos actions run communications.telegram.message.send \
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

Use `tracker.startDelivery` for a task-level background handoff of an independent
task whose tickets and dependencies are prepared. The `handoff.json` input file
contains the task handoff (task key, project directory, and optional delivery/review
role overrides). Project defaults use generic `TaskTracker.config_json.delivery`
through local-admin `tracker.configureDelivery`; omitted role fields inherit those
defaults and resolved roles must be complete. Task overrides merge by role once,
preserving unrelated metadata. No ticket settings or permission expansion is needed.
See [Automated Task Delivery](task-tracker.md#automated-task-delivery)
for its structure. The call returns immediately while the daemon runs ready tickets
sequentially through delivery and review. Inspect progress with `tracker.get`
using `response_mode="raw"`.

```bash
stackos ops call tracker.startDelivery --project 1 --input handoff.json --response-mode raw
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
- `actionCall.get`
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
- `browser.profile.create`
- `browser.profile.list`
- `browser.session.start`
- `browser.session.stop`
- `browser.session.list`
- `browser.session.status`
- `communication.reply`
- `communication.send`
- `communicationProfile.accountUsage`
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
- `ingressEndpoint.confirmManualUpdate`
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
- `tracker.startDelivery`
- `tracker.verify`
- `tracker.history`
- `tracker.changed`
- `tracker.search`
- `tracker.createTask`
- `tracker.createTicket`
- `tracker.updateTask`
- `tracker.updateTaskContent`
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
setup items are missing? Read `structurally_ready`, `context_status`,
`required_providers_ready`, and `execution_ready` separately; structural validity
does not mean execution is ready. Unresolved required action contracts and
missing required provider setup block execution readiness and include repair
context. Optional
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
   raw inline by default for CLI calls. Use `output_policy_json.mode` for a
   deliberate override: `inline`, `file_if_large`, `always_file`, or
   `transient`. File-backed output can take a `semantic_name` and absolute
   directory `path`; StackOS generates the filename there. For a foreground
   provider read that should not retain its result, use `transient` with
   `response_mode=raw` and no replay key. The immediate response contains a
   bounded result; the audit retains a content-free receipt and `actionCall.get`
   cannot replay the result. For file-backed responses, inspect the returned
   file path before rerunning paid/provider calls. If the response envelope
   contract is needed, call `schema.get` with the returned `schema_ref`. Use
   `artifact.read` only for intentional StackOS artifacts.
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

1. A workspace bridge supplies its bound project. Global bridge sessions and
   scripts pass an explicit `project_id`.
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
   dependencies, and frozen required output keys and JSON schemas before
   changing state. Validation failures keep the step running and return repair
   paths. Tracker graph warnings stay visible through
   `tracker.get(include_graph=true)` and `runPlan.get.consistency_issues`, but
   do not by themselves reject a valid step result.
7. `runPlan.recordStep(success|failed|skipped)` persists a terminal step result
   and closes the plan/run when the last step finishes or a step fails.
8. `runPlan.update` records safe metadata or an explicit operator approval
   decision through the project-scoped MCP, REST, or CLI operation. The operator
   decides; an agent can record a decision already given verbally or in
   authoritative evidence using `decided_by` and `decision_json`. It does not
   need another approval ceremony. Pending/rejected gates still block their
   steps and actions, and project scope is checked before mutation.
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
Template-backed providers use the same operation rather than a second messaging
API. Pass `content.template_ref` plus optional scalar `content.template_data`.
HubSpot accepts only that template form: the target resolves one opaque contact
ref, its policy must record transactional-purpose, recipient-eligibility,
legal-basis, and marketing-contact-state evidence, and the connection must have
the Transactional Email add-on explicitly confirmed. StackOS resolves the
contact's primary email inside the daemon, sends `contactProperties={}` so the
send cannot mutate the CRM contact, derives a fixed safe HubSpot `sendId` from
the same StackOS idempotency key, and returns provider status/event identifiers
only as safe refs. Raw text/HTML, attachments, controls, and bulk-recipient
approximation reject instead of changing semantics.
For media-bearing sends, text/caption and files stay in the provider's native
media message path: Slack resolves to file upload with `initial_comment`, and
Telegram resolves to file/photo upload or media group rather than sending an
extra text message first.
Simple one-off sends can run directly. Workflow sends can also run with a
`run_token`; in that case the active step must grant `communication.send` with
explicit `targets` such as `communication-target:ops-alerts`. Workflow replies
must grant `communication.reply` with explicit origin `sources` such as
`telegram`, `slack-bot`, or a source surface ref.

`communication.sendBatch` accepts an explicit list of up to 1,000
`telegram-user:` or `telegram-chat:` refs plus the same actor, target and content
intent as a normal send. Its target must declare
`send_policy.destination_mode=recipient-list`, explicitly allow the sending
profile, and authorize the broadcast action. Fixed channel targets cannot
authorize arbitrary DMs. A bare numeric ID is not a recipient ref. StackOS
validates ref syntax and target policy, deduplicates the list, and seals it with
the content into a durable ActionCall. It does not make per-recipient Telegram
reachability calls before accepting the job. Each leased item resolves its peer
and records Telegram's send result, including an inaccessible-peer failure.
Poll `actionCall.get`, inspect `actionCall.items`, and use
`actionCall.pause/resume/cancel/retry` for lifecycle controls. Workflow calls require
a `communication.sendBatch` grant naming the exact target.

For durable sends, replies, and batches, `delivery.due_at` sets the earliest UTC
dispatch time and `delivery.expires_at` sets a deadline for new attempts.
`delivery.account_interval_seconds` and `delivery.destination_interval_seconds`
may slow the provider's minimum pacing. These values are sealed at acceptance;
reusing an `intent_id` with changed content, recipients, or timing rejects
instead of creating another delivery. Providers without durable support reject
these options before sending.

Resume and selected-item retry require explicit direct confirmation and an
intent summary, or the original active workflow step with its matching grant.
Retry accepts only items with a proven safe, no-effect outcome. Completed,
in-flight, partial, and unknown effects cannot be replayed. Cancellation and
expiry stop remaining pending or safely deferred work and preserve existing
receipts and in-flight effects.

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
If the resolved action runs in the background, the response reports
`status=running` with `poll_operation=actionCall.get`, exact `poll_arguments`,
and `next_poll_after_ms`. Acceptance is not a delivery receipt. Workflow sends
and replies link their action call to the active plan and step; that step cannot
complete while its delivery is still running. A replay reports `replayed=true`
and returns the existing action result without starting another send.
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
provider ingress. Configure stores the generic endpoint, refresh updates it
from explicit input or driver discovery, routes derives provider webhook URLs,
sync writes safe route metadata into communication profiles where applicable,
and status reports readiness. Slack and HubSpot app-level URLs remain explicit
manual provider updates. Telegram receives native TDLib updates and has no webhook route.
HubSpot routes are derived only for credentials with signed ingress explicitly
enabled. `ngrok` is only a local tunnel provider configured under
`driver_config`; production uses `driver=public-url` with a deployed HTTPS base
URL.

## Provider authentication operations

The operation registry separates global Account lifecycle from project
Connection authorization:

- `account.list` inventories reusable Accounts globally; `connection.list`
  returns only Accounts attached to one project.
- `account.test` is the normal daemon-side Account health probe. For a Telegram
  Account with saved sign-in, it reopens the encrypted TDLib database, checks
  authorization with `getMe`, and closes the temporary session; an already
  active session is reused. Agent calls require the Account to be attached to
  the current project; the global Accounts Test button is local-admin setup.
  It does not set durable desired-connected state.
  These operations return opaque refs and sanitized results only.
- `account.session.status` is an agent-readable Telegram TDLib session
  projection for one explicitly attached Account. `account.session.connect` and
  `account.session.disconnect` are agent-controlled writes with the same
  project attachment check. They set or clear one durable desired-connected
  state; they never attach an Account, start a user authorization challenge, or
  expose native secrets. Their safe response includes connection state, attached
  project ids, whether another project shares the impact, and the next action.
  Connect is the only path that records restart restoration; disconnect is the
  only path that clears it. Local user authorization may open TDLib to complete
  a challenge, but it saves the sign-in and leaves the session disconnected; it
  does not choose the durable connection state. A later attached-project
  connect normally restores that saved user authorization without a phone,
  code, or password unless Telegram invalidated it. Disconnect preserves saved
  authorization. While desired-connected is set, `account.update` permits a
  display-name change only; Telegram, API, token, and proxy changes require
  explicit disconnect, save, then connect, with no automatic reconnect.
  An application identity or bot-token edit invalidates the saved authorization
  and requires local setup again. A proxy-only edit preserves authorization;
  `account.test` checks the new proxy while leaving the session disconnected.
  An unauthorized user connect returns `authorization_required` while retaining
  the explicit connection request; local authorization status exposes the
  current phone, code, password, or QR challenge so an operator continues it
  directly without another `account.start`.
- `connection.attach` and `connection.detach` change only project
  authorization. They are local-admin setup mutations, never copy, rotate, or
  delete Account secrets, and are not callable by normal agent sessions.
- `account.start` is a local-admin setup operation. For an interactive method
  it accepts the provider, auth method, existing opaque `credential_ref`,
  optional project to attach after success, and trusted return surface. It
  returns the provider authorization URL created from the provider contract and
  never accepts authorization codes or caller-selected redirect URIs.
- For Telegram, `account.start` is the local-admin native authorization action
  after an operator begins or retries sign-in. User Accounts continue through
  Telegram's phone, code, and optional password challenge. Bot Account creation
  checks the saved token once and closes TDLib; a failed first check leaves the
  Account pending so local setup can retry. A normal agent that connects an
  unauthorized user Account receives repair guidance instead of challenge
  material. An explicit bot session connect can use its daemon-held token
  without returning it.
- `account.create`, `account.get`, and `account.update` are registered
  REST-only local-admin contracts because their write-only credential fields
  and edit state must not cross MCP or CLI. The exact REST routes and the other
  Account/Connection adapters dispatch through these same operation handlers;
  there is no parallel repository route lifecycle.
- `account.revoke` remains a local-admin setup mutation. Revocation is blocked
  while project Connections still use the Account.
- `auth.callback` is REST-only at exact GET
  `/api/v1/auth/oauth/callback`. It is absent from MCP and CLI so provider
  callback values cannot be routed through an agent or command invocation.

The callback consumes the bound transaction, attaches the Account when a
project origin was requested, and returns a sanitized 303 to Accounts or that
project's Connections page. Action execution later resolves a fresh token and
enforces both the project attachment and action scopes inside the daemon. No
operation adapter or connector may implement a parallel OAuth callback,
refresh, or token-acquisition path.

`toolProfile.resolve` is the agent-friendly target resolver. Use it before
`action.run` or workflow setup when the agent needs one safe execution tuple for
a provider: optional project tool profile, daemon-held `credential_ref`,
provider auth status, and a concise setup `next_action` when the tuple is not
ready. For Telegram it resolves the `communication-profile` plus its
attached Account. For SMTP, IMAP, and other providers it resolves the requested
Account directly. It never returns secret payloads and never
chooses the workflow or provider action for the agent.

No operation adapter should bypass repository/connector auth, grant, idempotency,
or audit code.
