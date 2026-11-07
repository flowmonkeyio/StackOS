# Task Tracker

The StackOS task tracker is project-scoped work state for agents and human
navigation. It is inspired by `llm-tracker`, but it lives inside StackOS and
uses the same operation registry, project isolation, run-plan audit, and
no-secret boundary as the rest of the platform.

The tracker does not decide what work should happen. Agents decide strategy and
use tracker operations to persist tasks, tickets, dependencies, ownership,
status, provenance, and verification state.

## Model

```text
project
-> task tracker
-> task
-> ticket
-> dependency/reference/link/revision
```

- **Task**: durable work objective. A task can come from a workflow run,
  manual agent setup, an agent request, or an external issue.
- **Ticket**: executable work unit under a task. This is the agent-facing unit
  for `next`, `pick`, `brief`, `execute`, and `verify`.
- **Dependency**: graph edge from the prerequisite ticket to the dependent
  ticket. Dependencies are first-class state so agents can ask for ready work
  without guessing.
- **Link**: typed provenance to StackOS or external objects, such as run plans,
  run-plan steps, runs, agent requests, resources, artifacts, and action calls.
- **Revision**: append-only tracker history. Agents can use `tracker.changed`
  to refresh context from a known revision.

Statuses are intentionally small and generic:

- `not-started`
- `in-progress`
- `complete`
- `deferred`
- `aborted`
- `failed`
- `skipped`

The same status vocabulary applies to tasks and tickets. `deferred` means the
work was not executed because of an explicit instruction or external
prerequisite and requires a named resume condition. `aborted` means
intentionally stopped, cancelled, or rejected. `failed` means attempted and
unsuccessful; a failed attempt cannot be relabeled deferred. `skipped` means
intentionally not executed. Supersession is a relationship or disposition in
metadata/links, not a lifecycle status; do not use `deferred` as a generic
replacement for "moved elsewhere."

## Automated Task Delivery

The planner chooses this optional path by calling `tracker.startDelivery` after
preparing a task's self-contained tickets and dependencies. The existing
terminal-managed path continues to use the normal tracker operations.

Project agent defaults live in `TaskTracker.config_json.delivery`. An operator
saves them through local-admin `tracker.configureDelivery` setup at
`POST /api/v1/operations/tracker.configureDelivery/call`. This narrow REST-only
operation accepts `arguments` containing `project_id` and `delivery`:

```json
{
  "arguments": {
    "project_id": 1,
    "delivery": {
      "host_key": "codex",
      "delivery_agent": {"name": "builder", "instructions": "Implement, check and commit the assigned ticket."},
      "review_agent": {"name": "reviewer", "instructions": "Independently review the exact commit; do not change files or commits."}
    }
  }
}
```

`tracker.configureDelivery` replaces only `config_json.delivery`, preserving
unrelated tracker keys and recording a tracker revision. Include all intended
defaults when updating it; an empty object clears the defaults. Instructions
must be adapted to the project. The operation returns the saved tracker and
revision in raw mode; compact and ack are unsupported. Existing
`tracker.get` with `response_mode=raw` exposes `tracker.config_json` for readback.
MCP/system and workflow grants cannot authorize this local-admin write.

The local console exposes this setup at **Configure → Agent defaults**
(`/projects/:id/agent-defaults`). It reads `tracker.status` in raw mode and edits
a copy of the complete saved delivery object, changing only selected fields.
Untouched optional fields and other role settings are retained; the operation
preserves unrelated tracker configuration. Blank optional selectors save as
null; untouched absent fields remain absent. Harness/model choices and supported
provider/profile controls use the shared host capability catalog, with no model
access claim. After a harness change, incompatible saved provider/profile values
remain visible until explicitly cleared in the same input. Clear them before
using that harness; saving defaults does not establish execution readiness.
A successful save reloads and checks the stored delivery object
against the operation result and displays its revision. Failures remain visible
and retain the draft. Existing task snapshots and ticket receipts stay unchanged;
saving does not call `tracker.startDelivery` or refresh instructions from files.

Migration `0035_generic_task_delivery` moves existing engineering plugin
delivery defaults once, preserving role instructions, names, host, model,
provider, profile, unrelated plugin/tracker keys and existing generic values
(including explicit nulls). Existing generic fields take precedence; missing
fields are filled from the legacy defaults. The retired plugin `delivery` key
is removed. Task snapshots, run snapshots and ticket evidence remain unchanged.
Runtime reads only tracker defaults; enabling the engineering plugin is no
longer a prerequisite for task automation.

Source/local instruction adoption is a reviewed, explicit current-project setup
action. Copy the full saved delivery object and replace only selected role
instructions through `tracker.configureDelivery`; preserve names, hosts, models,
provider/profile and all other fields. Read back exact preserved settings and
instructions with canonical whitespace normalization. An explicitly authorized
one-time operational helper may perform this after independent source/local
approval and old-job completion; source edits alone never update saved settings,
and no synchronizer or cross-project rollout is implied.

The handoff is task-level; role objects and their fields can be omitted when
project defaults supply them:

```json
{
  "task_key": "prepared-task",
  "project_dir": "/path/to/working-project",
  "host_key": "codex",
  "delivery_agent": {
    "name": "project-delivery",
    "instructions": "The project's adapted delivery instructions.",
    "host_key": "claude-code",
    "model": "claude-sonnet-5-5"
  },
  "review_agent": {
    "name": "project-review",
    "instructions": "The project's adapted independent review instructions.",
    "model": "gpt-6.1-sol"
  }
}
```

The workspace bridge supplies `project_id`; REST and CLI callers supply it
explicitly. Agent identity and instructions come from project defaults or the
planner's task adaptation. Task overrides merge field by field within each role;
an explicit null clears an optional role selection (model/provider/profile/host),
while a resolved name or instructions cannot be missing, null or empty.
Projects need no `.codex` folder, and StackOS does not substitute generic presets
for those instructions. Harness and role choices are frozen under the task's `metadata_json.delivery`,
with other task metadata preserved. Queued/running launches keep that snapshot.
An explicit handoff after an execution completes or stops resolves a new current
snapshot from current defaults and overrides. Prior ticket receipts, native
sessions and consumed repair allowance survive. Completed tasks and frozen
run/template snapshots are never refreshed. Tickets carry no
agent settings. Invalid resolved roles or model selections reject before scheduling.
Each role may override `host_key`, `model`, `provider`, and (for Hermes)
`profile`. The merged role host overrides the task host, then the project host,
then `codex`. Clearing a role host uses that task/project host chain.
Omitting the model preserves native host settings. Explicit selections reach
native CLI arguments and launch evidence; StackOS never substitutes another
host or model after a failure. Hermes requires an explicit provider/model pair,
for example `{"host_key":"hermes","provider":"openrouter","model":"<provider model ID>","profile":"work"}`.
Codex also supports its configured `model_provider` selector. Other hosts reject
`provider`; their existing native authentication/configuration remains in control.

For a workflow task, pass `run_plan_id` and `step_id` together with the
matching `task_key` (`workflow-<run_plan_id>`). The plan must be started and
the entry step running. `step_id` is entry context; automation traverses the
whole task by canonical dependencies, including nested groups and fork/join.
Groups activate and complete structurally and never launch agents. A dependency
on a containing group activates its children while the group is running, using
the same `activates` semantics as workflow parents.

Mirrors claim their bound step through `RunPlanRepository`, wait for accepted
descendant/bound-child work, then launch their remaining frozen contract once.
Zero-child mirrors launch directly. The packet contains the full frozen
`RunPlanStepOut`, inputs, context, dependency results, tracker brief, accepted
receipts and current operator inputs. It is not reconstructed from latest YAML.
Only the run-plan owner validates frozen output schemas and writes terminal
mirror/plan/run lifecycle, preserving approval, dependency and pending-action
checks. Controller binding stays in the daemon; raw controller tokens never
enter prompts or task metadata. Worker agents return only their own evidence.

Optional task-level `agent_assignments` maps canonical ticket keys to the existing
`delivery_agent` or `review_agent`. Frozen step extensions
`{"agent_assignment":"review_agent"}` provide mirror defaults; explicit map
entries win. The resolved map is frozen with each execution snapshot.
An unassigned mirror uses `delivery_agent`. Models and instructions remain
project/task settings. No workflow or step name selects behavior.

The call returns task/job/status refs immediately. The existing memory scheduler
executes serially until all task work completes or execution stops:

```text
ordinary: ready -> delivery -> independent review -> success -> complete
                         -> scoped repair -> same delivery/review sessions (once)
mirror: claim step -> accepted descendants -> frozen contract -> record outputs
```

All launches use `RESULT_SCHEMA` in
[`task_delivery.py`](../stackos/task_delivery.py): `status`
(`success`, `repair`, `blocked`, `failed`), `summary`, `outputs`, and
`followups`. All four fields are required; extra fields are forbidden.
`outputs` is a JSON-encoded object string so native harnesses can transport
arbitrary frozen output schemas using a closed envelope. The daemon decodes it,
rejects nonobjects/malformed JSON, and passes mirror outputs unchanged to the
run-plan owner's validation. Work may include optional `commit_sha` candidate
evidence, including mirrors assigned either existing role. The envelope's
`followups` is an array of `{"text":"..."}` objects, empty when none.

Agents explicitly map their domain conclusions to a generic disposition.
The runtime has no domain verdict table. Only `success` advances.
An ordinary review `repair` invokes the existing one-pass loop. A supported
persisted correction/reopen causes a dependency rescan after a valid native
return; it never accepts the superseded mirror occurrence. Mirror `repair`
without that persisted progress, blocked/failed, malformed output and native
failure stop the same task.
No ready work with unfinished tickets is stopped, never reported complete.

```json
{"status":"success","summary":"Executed checks.","outputs":"{\"report\":{\"count\":2}}","followups":[]}
```

Reviewers execute relevant planned specs and integration checks themselves and
record commands/results/current SHA. Failed required specs reject; genuinely
unavailable required verification blocks. Review covers business regression,
architecture gaps, inaccurate wiring and canonical ownership where relevant to
the ticket plus its integration. Required repairs cite scoped criteria/evidence.
Unrelated findings go only in `followups`, never required-repair summary.

Engineering guidance treats plans, listed paths, design and proof as fallible
evidence of the original operator outcome. Delivery and independent review trace
actual owners and affected consumers, resolving necessary omitted files, wiring,
specs and docs without routine main-agent relay. Receipts explain the discovered
gap, necessity, proportionality, consumers and executed proof; review checks
completeness and runs relevant specs including necessary omitted checks. Unrelated
naming/style/debt remains nonblocking followups. Explicit exclusions, unavailable
authority, actual ownership conflicts and materially different outcomes stop
affected work for main reconciliation. These are agent judgments, not new tracker
settings or filesystem grants; existing lifecycle and repair limits still apply.

The runner appends notes to `task.context_json.followups` as
`{text, source_ticket_key, source_attempt, created_at}`, preserving other context
including `operator_inputs`. The existing task detail displays them read-only
with source navigation. They create no tasks/dependencies, launch no work,
never enter actionable delivery/repair prompts and do not block completion.
Filtering applies to these task/receipt notes. Frozen contracts, domain inputs
and declared outputs retain fields named `followups`; scoped review summaries
remain intact.

Ticket `completion_evidence_json.automatic_delivery` stores native `sessions`
by delivery/review role (thread ID and harness identity), prior `attempts`, latest
receipts and `repair_pass_used`. First launch creates a session; later launches
resume that exact role session with task-frozen settings. Roles/tickets stay
distinct. Persist the scoped rejection before repair, supplying occurrence
phase/attempt/candidate and persisted `review_result` status/summary/outputs.
One automatic repair pass requires a separate new descendant commit, then the
same reviewer rechecks that exact SHA. The consumed pass survives explicit
requeue. Blocked required verification, native/access/model/resume/schema failure,
dirty/mismatched candidate or a second rejection stops the same task with history;
there is no fresh-session, host or model fallback. Current independent approval
completes the ordinary ticket; historical approval cannot satisfy a correction.

Each new invocation receipt also records `selected_role` (`delivery_agent` or
`review_agent`) at the actual call site, including one-phase workflow mirrors.
`validated_outcome` is the returned generic disposition only after native,
schema, session and candidate integrity checks; otherwise it is null. Mirror
success additionally requires the run-plan owner's output validation. A recorded
reviewer reject requires `selected_role=review_agent` and
`validated_outcome=repair`. Phase, agent name, current defaults and raw returned
status are not substitutes. Older receipts can lack these facts.

Optional native usage travels separately from agent-authored outputs through
`AgentRunResult.usage`. The shared owner in `host_mcp/result.py` validates
`usage_snapshot(scope, basis, input_count, output_count, total)` and measures
`normalize_usage(usage, session_id=..., resumed=..., previous=...)` for the
receipt. Numeric fields use safe names because credential redaction masks keys
containing `token`, including numeric values; credential redaction is unchanged.
The receipt's `usage` value has this shape:

```json
{
  "unit": "tokens",
  "scope": "gemini.models",
  "basis": "session",
  "session_id": "native-session-id",
  "snapshot": {"input": 160, "output": 70, "total": 230},
  "status": "measured",
  "reason": null,
  "measured": {"input": 60, "output": 20, "total": 80}
}
```

Only `measured` is additive. `basis=invocation` contributes its counters once.
For `basis=session`, a fresh session contributes its snapshot; a resumed call
contributes the difference from the immediately preceding invocation of that
same role/session/unit/scope/basis. For example, snapshots 150 then 230 contribute
150 then 80. A legacy resumed session without a compatible baseline retains its
snapshot but contributes no measured value; its next compatible snapshot can
produce a delta. Missing intervening telemetry is not bridged. Negative or
inconsistent deltas are unavailable, never clamped to zero. Unavailable usage
has `status=unavailable`, `measured=null` and a reason (`missing_or_unsupported`,
`malformed`, `missing_session`, `missing_baseline`, or `invalid_delta`). Valid
snapshots retain unit/scope/basis/session metadata even when the delta is
unavailable. Missing telemetry is unknown, including on historical receipts.
Current receipts and archived `attempts` preserve these same facts and sessions;
no separate ledger or historical rewrite is involved. A receipt occurrence is
identified by ticket, attempt number and phase; tracker-history copies are not
additional invocations.

Supported native contracts, checked 2026-11-06:

| Host / scope | Native basis and arithmetic |
| --- | --- |
| Codex / `codex.turn` | Native `exec --json` emits cumulative thread usage on `turn.completed`, verified with CLI 0.167.0 fresh/resume captures and the [native producer](https://github.com/openai/codex/blob/rust-v0.167.0/codex-rs/exec/src/event_processor_with_jsonl_output.rs) (`usage_from_last_total`). Normalize as session snapshots: 18442 then 36925 contribute 18442 then 18483, totaling 36925. Input plus output gives total; cached input/cache writes and reasoning output are already included subsets. `last_token_usage` covers only the latest model request and is not a substitute for a multi-request invocation. No historical version boundary is inferred from the captures. |
| Claude Code / `claude.model-usage.v2.1` | The [Agent SDK cost-tracking contract](https://code.claude.com/docs/en/agent-sdk/cost-tracking#accumulate-costs-across-multiple-calls) defines a version boundary for `claude -p`: stable 2.1 releases before 2.1.277 report per-invocation `modelUsage`; 2.1.277+ restore session totals on resume. A bounded `--version` probe of the same selected executable establishes the basis. Unknown, prerelease, unsupported-series or failed version probes leave usage unavailable and preserve execution. Sum models' input, cache-read and cache-creation counts for input, plus output for total. The narrower main-loop `usage` is not a fallback. Missing counters and potentially zeroed crash results are unavailable. |
| Gemini CLI / `gemini.models` | [Headless JSON](https://google-gemini.github.io/gemini-cli/docs/cli/headless.html) supplies `stats.models`; [telemetry hydration](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/telemetry/uiTelemetry.ts) restores these counters on resume. Sum each model once: input is prompt (already includes cached), output is candidates plus thoughts plus tool, total must match their sum. Nested role counters and the uncached input subtotal are not added. |
| Hermes / unavailable | The [CLI reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands) does not prove a usage report compatible with the integrated `chat --oneshot` command. No telemetry flag is appended. |
| Grok / `grok.headless` | Native `--output-format json` reports invocation usage, verified with CLI 1.0.77 fresh/resume captures and the [headless contract](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/14-headless-mode.md#json). Full input is `input_tokens` + `cache_read_input_tokens` + `cache_creation_input_tokens`; output already includes `reasoning_tokens`. Require all counters and a matching `total_tokens`. The captured calls contribute 15709 and 15796 (31505 total). Do not add `modelUsage` again or apply this arithmetic to persisted session `inputTokens`, which already includes cache. Missing, malformed or explicitly incomplete native usage remains unavailable; agent-authored usage is ignored. |

Usage is optional evidence, including when native execution, result/schema
validation or session checks fail. Malformed or unsupported telemetry never
changes host/model/default selection, resume matching, candidate validation,
review/repair allowance or stop behavior. Source capture tests do not establish
installed-daemon activation or real native/API/UI reconciliation.

Claude's version probe runs after the agent process, without changing its
arguments or retrying it. It times out after three seconds and never becomes an
auth, model, session or execution gate. The `v2.1` scope identifies the supported
stable release series and excludes snapshots recorded under the earlier
unproven `claude.model-usage` scope from cumulative baselines. A missing compatible
baseline stays unavailable even when the current version is known.

Codex receipts recorded with the former `basis=invocation` mapping cannot serve
as cumulative baselines. On resume, retain the new session snapshot with
`reason=missing_baseline`; the next compatible receipt can produce a delta.
Correcting collection does not rewrite historical receipts or activate an
already installed daemon. Historical repair and installed activation require
their separately reviewed evidence.

### Recorded agent statistics

`tracker.agentStats` reads one project's default tracker with enforced project
scope. `tracker.agentStatsAll` is a local-admin read over the same query:
`is_active=true` (default) selects active projects, `false` archived projects,
and `null` all projects. A project read includes its receipts even when archived.
Neither operation creates a tracker, launches work, or changes configuration.

Both return `TrackerAgentStatsOut`: scope (`project_id`, `is_active`), `as_of`,
`totals`, and `rows`. Each row groups project ID/name/slug, recorded `agent_name`,
`selected_role`, `host_key`, and `model`. Agent/role/host/model come from receipts,
never current defaults; missing historical identities remain null.

- **Launch attempts** (`launch_attempts`) count recorded invocation receipts once
  per ticket/attempt/phase across current and archived attempts. Resumes are new
  occurrences. Tracker history, configured unused roles and group tickets without
  invocation receipts contribute nothing. An attempt need not have reached a model.
- **Reported tokens** (`reported_usage`, unit `tokens`, with numeric `input`,
  `output`, `total`) sum only normalized `usage.measured` contributions. Snapshots,
  cache/reasoning subsets and provider subtotals are never added again. No usable
  contribution means null; measured zero is zero; an empty scope has zero totals.
- **Recorded reviewer rejects** (`recorded_reviewer_rejects`) count explicit
  `selected_role=review_agent` plus `validated_outcome=repair`, including reviewer
  mirrors stored in the delivery phase. Failed, blocked, native/schema/candidate
  validation errors do not count. Phase, title and agent name never classify a reject.

Totals and rows include `coverage`: `usage_measured_attempts`,
`usage_unavailable_attempts`, `classification_known_attempts` and
`classification_unknown_attempts`. `usage_status` and `classification_status` are
`empty`, `complete`, `partial` or `unavailable`. Explicit null validated outcomes
record known validation failure; absent outcomes or roles remain unclassified.
Display coverage with the values: historical unknown does not establish zero use
or zero rejects. Compact and raw responses retain the same complete facts and
rows, excluding prompts, instructions, raw execution output and credentials.

For example, delivery 10 → reviewer repair 20 → resumed delivery 30 → reviewer
success 40 produces four attempts, 100 reported tokens and one reject, even when
the current attempt also appears in the archive. A one-phase reviewer mirror
with validated repair adds one attempt and one reject.

Project Home and the main Overview share an **Agent activity** section backed
by these operations. It shows launch attempts, reported tokens and recorded
reviewer rejects, a compact attempt-count chart and the complete grouped table.
The chart shows up to eight groups in table order; the table retains every group.
Overview uses its existing Active/Archived/All project visibility for these reads
and keeps project IDs/names/slugs in the breakdown. Model and role labels are
recorded selections, independent of current Agent defaults.

Coverage appears beside both totals and row values: a measured zero stays `0`,
unavailable measurements/classification say `Unavailable`, and partial sums or
reject counts retain their measured/classified attempt coverage. A failed
refresh retains same-scope values with a warning and their original update time;
scope changes clear those values. Both pages reuse their existing refresh owner,
with no statistics writes, receipt aggregation in Vue, or extra timer.

Agent/process failure also stops the task's execution. Ticket evidence records
both agent results, launch details, thread references, prior HEAD and verified
commit SHA before dependencies are released. Git is optional. Non-Git tasks invoke no Git commands; Codex uses its native
`--skip-git-repo-check` launch option. Supplied candidate evidence must resolve
to clean current HEAD and descend from the observed base. Observation work may
cite unchanged HEAD. Engineering guidance still requires separate source-change
and repair commits; a supplied repair candidate must be a new descendant.
Invalid/mismatched candidates, dirty Git state and review of a different SHA stop
the task. Preserve unrelated dirty work and reconcile it before handoff.
Independent tasks retain the existing parent-task rollup. An active duplicate
handoff for the same scope returns the current execution instead
of scheduling another job. Inspect the full task metadata and ticket evidence
with `tracker.get` using `response_mode="raw"`.

Codex delivery and review use `--approve-for-me` for workspace-write execution with
automatic approval review; authorized `.git` and `.codex` writes go through
normal escalation and a denial stops the task. Review instructions prohibit
production-file, acceptance-test and commit changes while permitting ordinary
test outputs. Reasoning settings remain inherited; native mode comes from the
shared `agent_review_sandbox` capability owner, not guidance or a permission bypass.

The existing `stackos.host_mcp` registry owns execution capabilities and model
IDs, reused by host discovery and launch validation. Its dated official sources
and capability notes appear in host status `agent_execution`. Current selections:

| Host | Native model IDs | Delivery / review |
| --- | --- | --- |
| Codex | `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-luna`; configured provider models with `provider` | Automatic approval review for delivery and test-capable review |
| Claude Code | `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-fable-5-1` | Native `auto` permissions for delivery and test-capable review |
| Gemini CLI | `gemini-3.1-pro-preview`, `gemini-3-pro-preview`, `gemini-3-flash-preview`, `gemini-2.5-pro`, `gemini-2.5-flash` | `auto_edit`, unapproved shell denied / normal native permissions, review-only instructions |
| Hermes | Explicit provider's model ID; no Hermes model family | Native one-shot approvals / normal native permissions, review-only instructions |
| Grok | CLI selections from `grok models`, including configured aliases such as `grok-build`; distinct from API ID `grok-4.7` | `workspace` sandbox, `auto` for delivery and test-capable review |
| Claude Desktop | None for headless execution | Unsupported |

Review modes permit focused shell-based tests under native permissions; they
do not enforce reviewer source immutability. Gemini plan mode can leave plan mode in headless execution,
and Hermes has no established strict read-only switch. Both support independent
review through normal native sessions with instructions to avoid changing files
or commits; these instructions provide no enforced read-only isolation. The shared
host capability owner selects the mode (`review_sandbox`); launch receipts record
`sandbox: "normal"` for these reviews. Gemini review adds no approval-mode override;
Hermes retains native one-shot permissions. Unsupported hosts still reject before scheduling.
Native permissions may deny required work, including committing; a structured
blocked result stops the task even if the process exit code is zero.

Source contracts checked 2026-11-05:
[Codex models](https://learn.chatgpt.com/docs/models),
[Claude CLI](https://code.claude.com/docs/en/cli-reference) and
[models](https://platform.claude.com/docs/en/models/overview),
[Gemini headless](https://geminicli.com/docs/cli/headless/) and
[models](https://geminicli.com/docs/cli/model/),
[Hermes CLI](https://hermes-agent.nousresearch.com/docs/reference/cli-commands),
[Grok CLI](https://docs.x.ai/build/cli/reference) and
[headless](https://docs.x.ai/build/cli/headless-scripting),
[Claude Desktop](https://code.claude.com/docs/en/desktop).
Grok's installed help and bundled headless documentation confirm its native
JSON envelope (`text`, `sessionId`, `stopReason`), distinct from its Messages
stream format. All adapters enforce the same caller-supplied result schema.

Installed/MCP-connected status establishes neither credential presence nor
model access. Discovery reports these as `not_checked` and provides documented
native auth-status commands where available. `codex login status`,
`claude auth status`, and `hermes auth status` (for the selected provider/profile)
only report native authentication state, not entitlement to every model.
Gemini/Grok auth-status support is unverified. Actual launch failures, including
errors on stdout, missing credentials, inaccessible models, and broken CLI shims,
stop the current task with their reason. No credential setup is performed.

Ordinary automatic review allows only the one durable scoped repair pass above;
dependency-driven corrections preserve that consumed allowance. Delivery does
not resume an interrupted job after daemon restart. `tracker.execute` remains a read-only
brief operation. Explicit taskwide handoff delegates mechanical mirror progression; agents retain
domain judgment and final integrated proof obligations.

## Workflow Mirroring

`runPlan.create` automatically creates a tracker task and one ticket per
run-plan step. Step dependencies become ticket dependencies.

Workflow selection happens before ticket creation. When an operator explicitly
asks to use a workflow, engineering workflow, StackOS workflow, "the
workflow", to follow the SDLC, for release-grade delivery, or equivalent
lifecycle enforcement, agents must create or resolve the workflow-backed run plan before
creating tracker tickets. All discovery, design, delivery, verification, and
closeout tickets for that work should be created under the workflow task/run
plan from the start. Direct tracker tasks are valid only when the operator asks
for task/dependency tracking without invoking a workflow.

Lifecycle mirroring is mechanical:

| Run-plan event | Tracker effect |
| --- | --- |
| `runPlan.create` | creates `workflow-{run_plan_id}` task and `workflow-{run_plan_id}-{step_id}` tickets |
| `runPlan.start` | marks workflow task `in-progress` and links the run id |
| `runPlan.claimStep` | marks the matching ticket `in-progress`, assigns the claimer, and stores run id |
| `runPlan.recordStep(success)` | marks the step mirror ticket `complete` and stores the step outcome |
| `runPlan.recordStep(skipped)` | marks the ticket `skipped` |
| `runPlan.recordStep(failed)` | marks the ticket `failed` with `blocker_reason` |
| `runPlan.recordStep(blocked)` | keeps the ticket active with `blocker_reason`; the step can be reclaimed after repair |
| `runPlan.recover` | reopens a system-recoverable failed/aborted workflow or safely recoverable live step into the same live run-plan task |
| `tracker.reopen` | normal agent/operator follow-up after closeout; reopens the task or the linked run-plan/run/task mirror as one lifecycle |
| `runPlan.reopen` | lower-level lifecycle primitive used by controller/admin paths after the canonical closed run plan has already been selected |
| `runPlan.abort` / stale abort reconciliation | marks the workflow task and unfinished linked tickets `aborted` |

Only generated workflow step mirror tickets are lifecycle-owned by run-plan
operations. These tickets use the key shape `workflow-{run_plan_id}-{step_id}`.
The `{step_id}` segment preserves the exact already-validated run-plan step ID;
it is not normalized.
Child tickets attached to a step with `run_plan_id` and `step_id` remain
tracker-owned work items. Agents should update their status, evidence, outcome,
and blockers with `tracker.updateTicket` when the work changes; reserve
`runPlan.claimStep` and `runPlan.recordStep` for the mirrored step ticket and
run-plan lifecycle.

StackOS only mirrors state. It does not invent tickets beyond the concrete
run-plan steps and it does not decide how to repair failures. Use `blocked`
for real execution blockers; graph warnings should usually be reviewed,
triaged, and repaired as tracker hygiene without freezing unrelated progress.
Use `failed` only for attempted unsuccessful work that should terminally fail
the run plan.
Completed or failed workflow run plans cannot be rejected through tracker
state; call `tracker.reopen` when more work should continue in the same
canonical workflow, or inspect `runPlan.get`/`runPlan.checkConsistency` when the
state looks inconsistent. If diagnostics show a system-recoverable lifecycle failure, use
`runPlan.recover` to restore the same canonical workflow before creating any
replacement plan. `tracker.rejectTask` is a rejection override for independent
tasks or draft/started workflow plans that should first abort through
`runPlan.abort`.
`tracker.linkRunPlan` adds provenance only. It does not make a manual task's
lifecycle follow the linked run plan.

When an agent needs to create delivery tickets under a workflow step, it should
call `tracker.createTicket` with `run_plan_id` and `step_id`. StackOS resolves
the mirrored `workflow-{run_plan_id}` task and step ticket, records the
workflow refs on the new ticket, and avoids asking the agent to infer generated
tracker keys. Pass both `run_plan_id` and `step_id` together or omit both. If
only one value is known, fetch `tracker.brief`, `tracker.get`, or `runPlan.get`
first to recover the workflow handoff instead of retrying a partial
`tracker.createTicket` call. Passing `run_plan_id` and `step_id` attaches the
ticket under the mirrored workflow step but does not add dependency edges.
Agents must still add `dependency_keys` or `dependencies_json` so
workflow-backed tickets are dependency-bridged into the workflow spine.

Dependency rows use two readiness meanings:

- `activates`: the child is ready while its attached parent workflow step is
  `in-progress` or `complete`;
- `blocks`: the dependent remains blocked until the prerequisite is
  `complete`.

Containment remains parentage, not a readiness edge. Evidence invalidation and
supersession are recorded in workflow/evidence contracts rather than overloaded
onto blocking readiness.

For workflow-backed delivery, graph readiness should be visible:

- every independent first child ticket for a workflow step has an `activates`
  edge from the mirrored step ticket
- the next mirrored workflow step has `blocks` edges from the terminal child ticket or
  tickets from the prior step
- delivery verification, docs, signoff, and release tickets sit inside the
  dependency graph, not beside it
- no detached delivery, test, docs, or signoff branch can become ready
  independently of the workflow order
- completing a mirrored workflow step while required attached child work remains
  unfinished is a blocking tracker-truth finding; optional, skipped, waived, or
  not-applicable child work must be explained rather than silently hidden

`runPlan.claimStep` and `runPlan.recordStep(success)` enforce run-plan
lifecycle, approvals, transitive step dependencies, and frozen output schemas.
They do not hard-block
on tracker graph warnings. Agents should use graph warnings to improve
sequencing, record follow-up cleanup, or block intentionally when the warning
is material to the current step's definition of done.

For customer feedback, tracker-backed delivery starts after support
investigation, not during intake. `communications.customer-feedback-intake`
creates a canonical thread and refs only. `support.issue-investigation` mirrors
its own investigation run-plan steps and posts the support conclusion, but does
not create delivery tasks. `support.delivery-task-handoff` creates delivery
tasks/tickets only after the operator replies with task-creation instructions
in the canonical Slack thread. Those delivery tasks must link back to the Slack
thread and include the investigation summary, validated root cause or bounded
uncertainty, fix direction, affected surfaces, acceptance criteria, and
verification expectations. They must also preserve source refs, canonical
Slack thread/message refs, clarification refs, support conclusion refs,
instruction message refs, task handoff refs, source media refs, forwarded
media message refs or an explicit media waiver, and tracked-delivery refs in
task context/metadata or ticket `references_json` so `tracker.brief` gives the
delivery agent enough chat context to continue without re-investigating.
Implementation then proceeds through `engineering.tracked-delivery`.

`tracker.brief` and `tracker.execute` include `workflow_handoff` for mirrored
workflow tickets. That packet carries `run_plan_id`, `run_plan_step_id`,
`run_id` when available, the template/step refs, and the intended operation
sequence:

```text
runPlan.get
-> runPlan.start when no run exists yet
-> runPlan.claimStep
-> toolbox.describe for granted step tools
-> runPlan.recordStep
```

The handoff is navigation context only. The run-plan token, active step, and
grant snapshot remain the execution boundary.

For workflow-backed tickets, tracker status is not an alternate run-plan
lifecycle. The mirrored step ticket is owned by `runPlan.claimStep` and
`runPlan.recordStep`. Child tickets attached to a workflow step are
tracker-owned implementation, verification, docs, or audit work; agents may move
those child tickets through normal tracker statuses with evidence while keeping
their dependency bridge to the workflow spine accurate.
For mirrored step tickets, run-plan-owned fields such as assignment, lane,
title, source, parentage, kind, and blocker state are also controlled by
`runPlan.*`; use child tickets for agent-owned delivery details.

If the daemon reaps a stale workflow audit run, the reaper reconciles the linked
run plan, unfinished steps, approvals, and tracker mirror together. If a caller
finds split state from old data or manual repair, use `runPlan.checkConsistency`
or inspect `runPlan.get.consistency_issues` before continuing.

Lifecycle timestamps follow the visible state. Moving a task or ticket into a
terminal status sets `completed_at`; reopening it to `in-progress` or
`not-started` clears `completed_at` so audits do not treat active work as
historically complete.

## Delegated Work And Recovery

Workflow guidance decides who may accept a ticket. For terminal-managed
engineering tracked delivery, the main agent assigns explicit keys, serializes tracker writes and
marks a child complete only after independent review and acceptance of its
integrated result. Implementation, review and repair use the same
`in-progress` ticket with phase/assignment context in existing metadata and
receipts in evidence. The tracker does not enforce this semantic acceptance:
`tracker.verify` checks structural state, and `tracker.pick` is an assignment
update rather than an exclusive lease. `tracker.next` may include in-progress
work. Workers must not independently pull the same queue.

Compact briefs are navigation packets. Request targeted
`tracker.brief(ticket_key=..., response_mode="raw")` when assignment or review
needs full goals, definition of done, ownership, context and evidence. Preserve
existing JSON contents when patching evidence or metadata: supplied JSON
fields replace the prior field rather than deep-merging it.

Store assignment attempt, contract revision and candidate references with
receipts so delayed results cannot satisfy a newer assignment. Before parking
or reassigning work, confirm the worker and its processes are quiescent. Keep
the blocker, unfinished obligations and resume condition visible after compute
capacity is released; a failed attempt remains failed evidence.

`tracker.reopen` with an explicit `step_id` can also return a started workflow
to an earlier gate. It resets that step and all later positions and clears their
current step results; it does not stop agents or actions, reset attached child
statuses, or refresh approval decisions. For a terminal-managed rewind, preserve retained
receipts in ticket evidence or other authorized durable records, quiesce all
agents/processes and in-flight actions, and identify affected evidence and
accepted children. Reopen through the run-plan lifecycle, explicitly reconcile
affected child statuses and dependents, re-evaluate approvals for current scope,
and reclaim the step before resuming. Unaffected valid evidence can be reused.
Ordinary ticket repair within an active delivery step needs no workflow rewind.
Existing plans retain their frozen order, grants and output contracts after a
source-template update.

### Agent-Owned Automatic Corrections

With explicit automatic handoff and a supporting runtime/assignment, an assigned
verifier or reviewer may route a scoped required correction to existing owning
tickets without main-agent relay. This is a bounded exception to specialist
write restrictions; terminal-managed routing remains with main. It grants no
self-acceptance, step claim/record, agent dispatch or replacement workflow.

1. Read full source and target briefs, context and completion evidence. Choose
   the original owning ticket and earliest affected bound gate from actual
   provenance/dependencies. Stop affected work on exclusions, authority/ownership
   conflicts or materially different outcomes; followups remain nonblocking.
2. Full-read/merge supplied JSON fields because updates replace whole fields.
   Persist target/source ticket refs, source attempt and inspected candidate,
   failed criterion/check and concrete evidence, required outcome and affected
   recheck. Preserve unrelated context and `automatic_delivery` sessions,
   attempts, receipts and `repair_pass_used`. Save current failed findings and
   outputs outside step `result_json` before rewind clears it.
3. Requeue affected ordinary children with `tracker.updateTicket`, using
   `complete -> in-progress -> not-started` for completed children, and reconcile
   affected dependents with their history intact. Reopening a gate alone does
   not invalidate child acceptance. Finish own checks/other actions and quiesce
   other affected processes and in-flight actions. The caller remains active to
   make the last call; it must not wait for itself to terminate.
4. Call `tracker.reopen` with explicit `step_id` and reason as the final mutation,
   then immediately return `repair` with truthful findings and declared frozen
   outputs. The facade returns safe run/plan/reset refs without a controller
   token value. Do not claim/record the reset gate or dispatch another agent.

After native/session/schema/candidate validation, the runner reloads persisted
state, retains the superseded observation as history and returns to its existing
dependency scan. It alone reclaims the pending bound gate and resumes original
delivery/review/verifier sessions. No phase-name selector or correction service
is involved. Fresh persisted correction is actionable before an earlier approved
review, which remains historical evidence. Corrected source needs a separate
descendant commit and independent exact-SHA approval; consumed ordinary repair
allowance is preserved, never reset to evade a second rejection. Unsupported
or no-progress correction and actual native/access/model/resume/schema/candidate
errors stop without fallback. An old success cannot accept a reset occurrence.

Source guidance edits do not activate this path in installed runtimes, saved
instructions or frozen jobs. Adoption remains explicit and reviewed per project;
local agent files are optional sources. Configure/read back only the authorized
project defaults and prove a subsequent fresh snapshot/prompt, preserving other
settings and historical receipts. Isolated source rehearsal proves source
behavior; it does not establish live instruction adoption or installed activation.

## Status Events

Tracker task and ticket status changes are also written to the project timeline
as append-only `project_events` rows:

- `tracker.task.status_changed`
- `tracker.ticket.status_changed`

These events are emitted through the shared StackOS event emitter only after a
real persisted status transition. Dry-run updates, same-status updates, and
non-status edits do not emit status events. Parent task status changes caused
by ticket aggregation and workflow run-plan mirroring emit the same task event
as direct task status updates.

Timeline metadata includes the entity key, old/new status, and tracker URL
path, plus event-envelope metadata such as `schema_version` and actor when
supplied by the producer. Event rows preserve source provenance with `source_type` set to
`tracker_task` or `tracker_ticket` and `source_id` set to the persisted tracker
row id. The macOS desktop app consumes this timeline for native notifications,
but the MVP only displays native notifications for completed tasks. Ticket
status events remain available for audit, UI timelines, and future notification
policy without creating native ticket notifications today.

The browser UI consumes selected-task status changes through the project
timeline stream:

```text
GET /api/v1/projects/{project_id}/context/timeline/stream?task_key=...
```

The stream emits sanitized `ProjectEventOut` payloads as
`event: tracker-status` frames and optional `event: heartbeat` frames. Normal
browser streams start from the latest matching tracker status event so opening a
task does not replay historical status changes into the chart. Debug and tests
may pass `replay=true` with `max_events` to read a bounded historical batch.
Stream filtering is task-scoped so two tabs on different active tasks can
subscribe independently.

## Operations

Tracker behavior is registered once as StackOS operations. MCP, REST, CLI, and
UI consume the same contracts.

Agent read operations:

- `tracker.status`
- `tracker.ticketCounts` (current seven-status ticket counts without loading the
  full work graph)
- `tracker.get` (compact snapshot by default; request `response_mode: raw` only
  when UI/debug/list review audits need the full tracker snapshot)
- `tracker.next`
- `tracker.blockers`
- `tracker.brief`
- `tracker.why`
- `tracker.execute`
- `tracker.verify`
- `tracker.history`
- `tracker.changed`
- `tracker.search`

The local-admin `tracker.ticketCountsAll` read provides the same counts across
active, archived, or all projects. Both count default-tracker ticket rows,
including manual, group, and workflow-mirrored tickets, without adding task or
run totals. `project.portfolio` supplies per-project counts and can select
projects with tickets in one status before pagination. These are current
snapshots, not historical trends; blocked remains a separate condition rather
than a lifecycle status. Overview and project Home use these reads to open the
corresponding Work status filter.

Agent write operations:

- `tracker.createTask`
- `tracker.createTicket`
- `tracker.updateTask`
- `tracker.updateTaskContent` (human console REST/CLI only)
- `tracker.updateTicket`
- `tracker.patch`
- `tracker.rejectTask`
- `tracker.reopen`
- `tracker.pick`
- `tracker.release`
- `tracker.linkRunPlan`

REST callers use the generic operation adapter:

```http
POST /api/v1/operations/tracker.get/call
POST /api/v1/operations/tracker.patch/call
POST /api/v1/operations/tracker.rejectTask/call
POST /api/v1/operations/tracker.reopen/call
```

Ticket list creation and review reuse the same operation names. Do not add
parallel tracker endpoints for list behavior.

1. Draft and validate a list without writing by calling `tracker.createTicket`
   with `tickets_json` and `dry_run=true`.
2. Review the returned `results`, `errors`, and `warnings`. Use `tracker.get`
   with `task_key`, `ticket_keys`, `ticket_ids`, `block_state`, or
   `dependency_ticket_key` when the review needs existing tracker state.
3. Create the same list by calling `tracker.createTicket` again with
   `dry_run=false` or without `dry_run`.
4. Patch many tickets by calling `tracker.updateTicket` with `updates_json`.
   Each entry identifies one ticket by `ticket_key` or `ticket_id` and supplies
   a `patch_json`. Updates are patch-only: omitted fields are preserved.
   Dependency edits can use `add_dependency_keys` and `remove_dependency_keys`
   for small atomic edge changes. Use `dependency_keys` only when intentionally
   replacing the full dependency list; it cannot be combined with add/remove
   dependency fields in the same patch.
   Terminal child-ticket updates can roll up the parent task status when the
   task's remaining tickets are terminal. Ticket evidence is not copied to the
   task: compact mutation responses expose `task_rollup` and
   `completion_evidence_present` so agents can see when task-level closeout
   evidence still needs an explicit task patch.
5. Before a large dependency cleanup, call `tracker.updateTicket` with
   `dry_run=true` on the same `patch_json` or `updates_json`. The tracker
   returns `dependency_preview` entries with current, final, added, removed, and
   kept dependency keys, plus advisory warnings for suspicious bulk removals.
   Dry-run preview does not write tickets, dependencies, history, timestamps, or
   tracker revisions.
6. When an operator rejects or aborts a task or workflow run, call
   `tracker.rejectTask` with either `task_key` or `run_plan_id` plus a reason.
   It is a task-level operation: the task is marked aborted/rejected and every
   child ticket is closed aborted with rejection outcome and metadata.
7. When an operator wants closed work reopened, call `tracker.reopen` with
   `task_key`, `run_plan_id`, or linked `run_id` plus a reason. StackOS decides
   whether to reopen only the task or the mirrored run-plan/run/task lifecycle
   together.

```json
{
  "project_id": 1,
  "task_key": "manual-comms",
  "tickets_json": [
    {
      "key": "manual-comms-schema",
      "title": "Add tracker schema",
      "completion_evidence_json": {
        "changed_files": ["stackos/repositories/tracker.py"]
      }
    },
    {
      "key": "manual-comms-ui",
      "title": "Expose tracker UI",
      "dependency_keys": ["manual-comms-schema"]
    }
  ],
  "dry_run": true
}
```

```json
{
  "project_id": 1,
  "dry_run": true,
  "updates_json": [
    {
      "ticket_key": "manual-comms-schema",
      "patch_json": {
        "status": "complete",
        "completion_evidence_json": {
          "changed_files": ["stackos/repositories/tracker.py"],
          "summary": "Schema persisted and tested."
        }
      }
    },
    {
      "ticket_key": "manual-comms-ui",
      "patch_json": {
        "assignee": "codex",
        "add_dependency_keys": ["manual-comms-docs"],
        "remove_dependency_keys": ["manual-comms-schema"]
      }
    }
  ]
}
```

After reviewing the preview, send the same payload without `dry_run` to apply
the patch.

CLI aliases exist for common calls but still use the same operation adapter:

```bash
stackos tracker status --project 1
stackos tracker next --project 1
stackos tracker brief workflow-7-review --project 1
stackos tracker pick --project 1 --assignee codex
stackos tracker create-task manual-comms "Manual communications" --project 1
stackos tracker create-ticket manual-comms send-ack "Send ack" --project 1
stackos tracker patch --project 1 --input tracker-patch.json
```

## Agent Flow

For workflow work:

```text
workflowTemplate.describe
-> runPlan.create(workflow_key=...)
-> tracker.status or tracker.brief
-> tracker.createTicket(run_plan_id=..., step_id=...) when extra delivery tickets are needed
-> bridge workflow-backed tickets with explicit dependency_keys or dependencies_json
-> tracker.get(run_plan_id=..., include_graph=true) and review workflow-spine warnings
-> runPlan.start
-> runPlan.claimStep
-> do the step
-> runPlan.recordStep
-> tracker.verify
```

Workflow-backed task graphs should have exactly one root: the first workflow
step mirror ticket. Create child tickets with `run_plan_id` and `step_id` from
the start, then dependency-bridge them into the step chain. Ordinary non-workflow
tasks may still have several independent roots when the work is genuinely
parallel.

For engineering SDLC workflow work, the mirrored tickets should preserve the
requirements-to-release order instead of collapsing everything into one
implementation ticket:

```text
scope-work
-> define-requirements
-> discover-impact
-> design-approach
-> design-tests
-> plan-tickets
-> review-design
-> verify-delivery
-> review-delivery
-> audit-tracker
-> release-closeout
```

New engineering plans attach implementation tickets to `verify-delivery`.
After readiness, claim that step as the automatic handoff entry. Its first
children activate from the mirror; terminal children block `review-delivery`.
The runner accepts all attached children before dispatching the verification
agent, so children must not block their own mirror. Terminal-managed delivery
accepts the same children before running integrated verification. Delivery
commits and review evidence remain on each ticket without a summary-only step.

The tracker is the durable execution map. It is not the proof that work is
correct. Completion evidence should point to the code, commands, manual checks,
docs, run-plan steps, artifacts, or reviewed decisions that make a ticket true.
If the evidence is missing, the ticket should remain open or move to the
specific terminal status that matches reality.

For workflow-backed work, graph warnings are planning and audit signals. After
creating or updating child tickets under a workflow step, run `tracker.get` with
`include_graph=true` for the run plan and triage workflow-spine warnings.
Repair material warnings when they hide required work or make ready-state
misleading; otherwise record the risk or cleanup item and keep moving.

For direct/manual work:

```text
confirm the operator asked for task/dependency tracking without invoking a workflow
-> tracker.status
-> tracker.createTask
-> tracker.createTicket
-> tracker.next
-> tracker.pick
-> tracker.brief
-> do the work
-> tracker.updateTicket or tracker.patch
-> tracker.updateTask/updateTicket(status=deferred) when the operator parks resumable work
-> tracker.rejectTask when the operator rejects or aborts the task
-> tracker.verify
```

Use `tracker.brief` before working a ticket. Agent tracker reads such as
`tracker.status`, `tracker.next`, `tracker.blockers`, `tracker.brief`,
`tracker.why`, `tracker.execute`, `tracker.verify`, `tracker.history`,
`tracker.changed`, and `tracker.search` default to compact response mode: keys,
titles, statuses, dependency keys, run-plan refs, non-empty counts, links,
checks, compact history summaries, and suggested next actions. Use
`response_mode="standard"` or `"verbose"` only when diagnostics need the full
tracker rows with timestamps, metadata, definitions, and source payloads. Use
`tracker.changed` when you know a previous revision and only need new context.

`tracker.status` and `tracker.blockers` report active blockers for executable
work: non-terminal tickets with an explicit blocker reason or incomplete
dependencies. Historical blocker notes on terminal tickets are kept for context,
but they do not make the project look actively blocked.

Use `tracker.get` only when the caller needs a project tracker snapshot, a
filtered task/ticket set, or the UI graph projection. It returns a compact
snapshot by default; request `response_mode: raw` only when the full dependency,
link, and graph payload is needed for UI/debug/list review audits. Normal agents
should prefer `tracker.status`, `tracker.next`, `tracker.brief`,
`tracker.verify`, `tracker.search`, and `tracker.changed` to keep context small.
Operator task selectors may call `tracker.get` with `task_index_only=true`,
`include_graph=false`, and `response_mode=raw`. That projection returns all
matching tasks with `ticket_summary` rollups (status counts, terminal/in-progress/
blocked totals, assignees, and searchable ticket terms) while omitting ticket rows,
dependencies, links, and graph data. Ticket-scoped filters are intentionally
rejected in this mode; load one focused task or a normal filtered snapshot when
ticket detail is required.
Bounded overview surfaces that do not need a complete selector may also pass
`task_statuses` (for example, `not-started` and `in-progress`). This filters the
top-level task rows while retaining aggregate summaries across each returned
task's tickets; it is distinct from `statuses`, which filters ticket rows.
When `include_graph=true`, the graph projection may include advisory warnings.
These warnings are produced by core tracker analysis. Generic warnings call out
review issues such as sparse dependency plans, many isolated active tickets,
likely pre-implementation gate direction mistakes, or large dependency removals
surfaced by dry-run preview. Workflow-spine warnings are stronger tracker-truth
signals: missing child bridges, detached child branches, next-step handoff gaps,
and open children under a workflow step should be investigated and may be
blocking for tracker audit or release closeout when they hide required work.

## UI

The Work UI provides two views, Board and Graph. It renders:

- Board by default, with seven fixed columns for the current task statuses:
  `not-started`, `in-progress`, `complete`, `deferred`, `aborted`, `failed`,
  and `skipped`. Cards cannot be dragged between columns or change status.
- an inline Board / Graph switch in the same desktop toolbar as the
  current view's controls. There are no separate Stories or Tickets views.
- project-wide task cards with a leading title and a compact footer for task
  ID, creation date, completed ticket count, and owner. Recognized agent owners
  use the same logos as Overview; other owners use neutral initials. Workflow
  source and ticket dependency/blocker details remain in inspection rather than
  appearing as task labels. An unmet child dependency does not label the whole
  task blocked. Only `complete` tickets count as complete; other terminal
  statuses do not. Search, workflow and assignee filters narrow the Board.
  Task-status filters apply to tasks; `ticket_status` links explicitly filter
  tasks containing tickets with that status.
- a task selector only in Graph, independent of Board filters and
  including terminal tasks. After the project index loads, an explicit URL
  selection takes precedence over the last valid selection remembered for
  that project, then the first task in the unfiltered picker (newest creation
  first, descending task ID for ties). The automatic selection replaces the
  current URL entry. Invalid explicit selections remain unavailable; loading,
  failed and empty indexes do not invent a selection.
- one focused Vue Flow dependency graph loaded on demand, with ticket nodes
  laid out left-to-right by prerequisite/dependent relationships and
  dependency edges kept visually quiet at large scale.
- graph-local status and block filters. Status filters select tickets by
  lifecycle status. Block filters use unresolved dependency state, with open
  `blocker_reason` treated as blocked; terminal notes are not counted as active
  blockers. The blocked view keeps the immediate blocking
  ticket visible so the dependency edge still explains why work is blocked. The
  map toolbar reports the visible ticket and dependency counts when filters are
  active, not the full task totals.
- edge click highlighting for the selected dependency chain. Unrelated nodes
  and edges dim while upstream and downstream dependencies remain readable.
- node click or keyboard activation selects and highlights work within the map
  and shows its selection card. Only the card's Details action opens the
  existing ticket side inspector. Selecting a node or edge, clearing selection,
  or clicking the canvas closes that inspector. These interactions do not
  change the Board popup URL or open a centered popup.
- a minimap and controls for navigating large task graphs
- small graphs initially fit all nodes. Large graphs focus on the selected or
  in-progress ticket and its dependency context. Refresh preserves the user's
  viewport, and switching back to Board preserves its filters and scroll.
- ticket nodes show lifecycle status as text plus color, and active or recently
  updated nodes may animate. Motion must respect reduced-motion preferences.
- project-wide status events refresh the Board without requiring a task
  selection. Graph uses the selected task's timeline stream to
  refresh its graph and mark touched nodes as recently updated.
- one centered, persistent task/ticket detail popup using the shared
  `UiDialog layout="detail"` preset. Its viewport-bounded frame stays the same
  through loading, errors, editing and disclosure changes; the body scrolls
  while header and footer remain visible. The ticket list shows at most six
  40px rows in a 240px scrolling region, with every record reachable.
  Board task cards and the map's explicit Task details action open a task
  popup independently of the map selection. Ticket details inside it offer
  Back to task; View graph opens the map for that exact task. URL
  history preserves these identities, and unavailable tasks are explicit.
  Task-to-ticket inspection starts at the ticket heading; Back restores the
  task's body/list scroll, expanded sections, editor draft and originating row
  focus. Choosing another task resets that reading state. Final close restores
  the popup opener.

The UI is for humans to navigate and inspect. Agents use tracker operations
directly for lifecycle writes. The Work detail popup also permits a bounded
human content correction through `tracker.updateTaskContent`: edit the task's
description or append additional input. Title, goal, status, ownership,
dependencies, run bindings, constraints and evidence remain read-only there.
The popup shows input history, preserves drafts on errors or conflicts, and
protects unsaved text when closing, changing task or navigating away. Those
actions are blocked while a save is in flight. Inspecting a ticket and returning
within the same task retains the draft without discarding it. After saving, it fetches current details and Board
summaries rather than treating a mutation receipt as current display state.
Save progress, success, errors and conflict-repair actions appear in the pinned
footer beside the content actions, so long input histories cannot hide them.
After a save or latest-content refresh, focus stays on that feedback when the
same task pane is active; finishing a save does not move focus out of ticket
inspection. Labels distinguish editing a description, adding input and saving
both together without splitting the atomic submission.

The content operation takes `project_id`, `task_key`, the task's current
`expected_updated_at`, a stable `idempotency_key`, and `description` and/or
`additional_input`. An empty description clears it; additional input must be
nonblank. Both strings are limited to 20,000 characters and use the existing
secret-text redaction. Inputs are appended under `context_json.operator_inputs`
with server-owned `id`, `text`, `created_at` and `actor` fields while preserving
the task's other context. The existing tracker revision history records the
before/after state. No new task store or schema migration is required.

This is an exact-path browser-safe, MCP-disabled local console operation, not
permission to expose the broader `tracker.updateTask` patch surface. It accepts
no actor, run token, arbitrary JSON or execution fields. A serialized transaction
checks the task timestamp and saves content, revision and the canonical raw
retry receipt together. A stale timestamp returns a conflict with the current
timestamp; the UI keeps the draft for review after refresh. Identical retries
return the original receipt with `replayed=true`; a reused key with different
intent fails. Content editing never resumes work or changes lifecycle, and
workflow mirroring preserves these description/input additions.

## Graph Projection

Storage is normalized and not Vue Flow-specific. `tracker.get` returns an
optional graph projection so the UI can compose Vue Flow without rediscovering
relationships:

```json
{
  "graph": {
    "nodes": [],
    "edges": [],
    "warnings": [],
    "layout_hints": {
      "direction": "LR",
      "group_by": "task"
    }
  }
}
```

The mechanical mapping is:

- task -> task/group node
- ticket -> ticket node
- task/ticket relationship -> containment
- ticket dependency -> dependency edge
- StackOS/external links -> provenance metadata or link edges

## Boundaries

Canonical execution ownership is integrity state keyed by the existing native
task/ticket, not another tracker lifecycle. `run.ownerResolve` reads existing
external links; `run.ownerGet` exposes its stored epoch/revision and current
continuity consistency. Ownership mutations require the item's persisted current
plan/step binding plus explicit step grants, and emit existing project events
with the controller run and tracker source. A workflow parent task alone cannot
authorize a child or substitute for a missing step binding. Attempt observations
and terminal reasons remain on existing runs; they never close a tracker item.
See [operation contracts](./operations.md#canonical-ownership-operations).

- Do not store secrets in tracker fields. Metadata, context, references, and
  patches are redacted.
- Do not add provider or workflow-specific tracker logic. Domain behavior
  belongs in plugins, templates, run plans, and agents.
- Do not add bespoke REST routes for tracker behavior. Register operations once
  and use the generated MCP/REST/CLI surfaces.
- Do not use the tracker as a replacement for run-plan grants. Workflow writes
  still require active run-plan step grants where applicable.
