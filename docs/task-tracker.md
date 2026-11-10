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

Explicit workflow abort uses `runPlan.abort` and the shared delivery/native launch
boundary described in [run plans](./run-plans.md#explicit-run-abort). Canonical
aborted task state and the stopped delivery handoff fence queued callbacks, spawn
registration and late success/error/exception acceptance. Each accepted
`tracker.startDelivery` handoff has its own identity in the existing delivery
snapshot and scheduled callback. Reopening a run does not revive its stopped
handoff, even after the native invocation retires. A fresh explicit handoff gets a
new identity; old callbacks and buffered returns cannot claim its steps, launch
agents or overwrite its evidence. Scheduler job IDs also include that handoff
identity. Delayed registration removes only its own stale job, and registration
failure can stop only its current handoff. Only registry-owned invocations can be
stopped; external sessions receive state changes only. The existing execution/usage
handoff finishes readers and retains measured counters without inventing final-only usage.
Runs, Board and Graph share the same confirmation and refresh their current
transport-backed projections. A plain task has no inferred run-plan abort action.

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
      "agents": {
        "delivery_agent": {"source_ref": {"host_key": "codex", "native_name": "builder", "source_path": ".codex/agents/builder.toml"}},
        "review_agent": {"source_ref": {"host_key": "codex", "native_name": "reviewer", "source_path": ".codex/agents/reviewer.toml"}}
      }
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

The local console exposes native project definitions at **Setup → Agents**
(`/projects/:id/agents`). It lists, adds and edits actual native files through
`projectAgent.*`; generic presets are not the live project list. Its saved-default
adoption uses the preview/write/readback contract below. The old fixed
delivery/review form is removed. Editing a source or adopting defaults never
calls `tracker.startDelivery` and does not establish execution readiness.

Migration `0035_generic_task_delivery` moves existing engineering plugin
delivery defaults once, preserving role instructions, names, host, model,
provider, profile, unrelated plugin/tracker keys and existing generic values
(including explicit nulls). Existing generic fields take precedence; missing
fields are filled from the legacy defaults. The retired plugin `delivery` key
is removed. Task snapshots, run snapshots and ticket evidence remain unchanged.
Runtime reads only tracker defaults; enabling the engineering plugin is no
longer a prerequisite for task automation.

Codex adoption is explicit current-project setup through the same REST operation.
Send `project_dir`, `adopt_sources: {role: {source_ref, expected_digest}}` and
`preview: true`, omitting `delivery`. The read-only preview returns instruction,
identity and native-setting differences plus the proposed defaults and effective
agents. Review those differences, then repeat with `preview: false`. Stale source
digests reject before saving. Adoption preserves unrelated roles, all execution
fallback choices (including absent/null fields) and other tracker settings; it
replaces only selected editable instruction owners with `agents` references and
checks the saved readback. No source file is rewritten. Queued/running task state,
run/template snapshots, receipts and history remain unchanged. No database upgrade,
watcher, preset lookup or same-name file inference performs adoption.

For other-host inline defaults, after independent source/local approval,
explicitly authorized instruction-only saved-default adoption may occur during a running task.
Queued/running snapshots stay frozen. Explicit rehandoff after complete/stopped
execution resolves current defaults while preserving prior ticket receipts,
native sessions and consumed repair allowance. Completed tasks and frozen
run/template snapshots stay unchanged. Installed activation is separately
authorized and main-owned after task closure. Adoption does not authorize
restarting the daemon orchestrating the active task; source checks do not prove
saved adoption, native consumption or installed activation.

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
    "source_ref": {"host_key": "codex", "native_name": "project-review", "source_path": ".codex/agents/project-review.toml"},
    "model": "gpt-6.1-sol"
  }
}
```

The workspace bridge supplies `project_id`; REST and CLI callers supply it
explicitly. Fresh Codex admission requires an exact project `source_ref`; legacy
inline Codex choices return `adoption_required`. Other hosts retain intentional
inline contracts. `agents` maps arbitrary safe role keys to selected definitions
and execution fallback choices. Task overrides merge field by field within each role;
an explicit null clears an optional role selection (model/provider/profile/host),
while a resolved name or instructions cannot be missing, null or empty.
Source-native model/effort and supported reduction-only feature/skill settings win.
Omitted values inherit task choices, project choices, then the native host. Source
fields cannot replace provider/profile, permissions, sandbox or MCP routes;
unsupported settings reject with repair context. The existing native launcher uses
supported `--model`/`-c` options, never an invented `--agent` option. Harness and role
choices, source ref/digest, instructions and native settings are frozen under the
task's `metadata_json.delivery.agents`,
with other task metadata preserved. Queued/running launches keep that snapshot.
An explicit handoff after an execution completes or stops resolves a new current
snapshot from current defaults and overrides. Prior ticket receipts, native
sessions and consumed repair allowance survive. Completed tasks and frozen
run/template snapshots are never refreshed. Tickets carry no
agent settings. Invalid resolved roles or model selections reject before scheduling.
An already admitted inline Codex task stopped across the source-adoption upgrade
retains its saved agents and assignments on unchanged explicit rehandoff, including
work that has not launched yet. Current defaults cannot replace that selection;
changed host, agent settings or assignments reject. Existing workspace, MCP-route,
host-preflight and native-session identity guards still apply. This exception does
not admit a new inline Codex task or refresh its prior receipts or repair count.
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
`RunPlanStepOut`, inputs, context, dependency results, projected tracker brief,
relevant accepted outcomes and current operator inputs. It is not reconstructed
from latest YAML.
Only the run-plan owner validates frozen output schemas and writes terminal
mirror/plan/run lifecycle, preserving approval, dependency and pending-action
checks. Controller binding stays in the daemon; raw controller tokens never
enter prompts or task metadata. Worker agents return only their own evidence.

Fresh and resumed launches project only the context added by StackOS; native
session history stays with the host. The current ticket retains its requirements,
constraints, context, contract revision, operator amendments and authority, plus
the occurrence's attempt, candidate and latest scoped feedback. Earlier approvals
remain historical when current context requests a correction. Statistics, live
usage (including retained samples and last execution), saved execution settings
and nested old attempt receipts are omitted.
Ordinary assignments receive compact outcomes for direct completed prerequisites.
Mirrors receive completed outcomes within their dependency scope, including
descendant/bound work and prior dependency containers, rather than every completed
ticket in the task. Each outcome carries its candidate, available acceptance
status and a `tracker.brief` call with `response_mode=raw` to retrieve full evidence.
Related tickets in the brief retain navigation refs instead of duplicate receipts.

This projection neither truncates arbitrary strings nor rewrites stored evidence.
Frozen step contracts, domain inputs/outputs and their fields named `followups`
retain their values; compact accepted outcomes refer to their full stored outputs.
The runner separately keeps every completed ticket key for correction progress
detection and dependency rescans, including tickets omitted from the prompt.
Packet-size comparisons measure serialized characters and UTF-8 bytes; they do
not measure model tokens or establish answer-quality improvements.

Optional task-level `agent_assignments` maps canonical ticket keys to selected
role keys. Frozen step extensions
`{"agent_assignment":"review_agent"}` provide mirror defaults; explicit map
entries win. The resolved map is frozen with each execution snapshot.
Otherwise a mirror selects the unique applicable selected declared role from its
frozen template `agent_requirements/applies_to_steps`; ambiguity requires an explicit
assignment. Legacy plans without declared roles retain their explicit execution
bindings. One-role workflows need no engineering pair. Ordinary tickets require a
separate `review_agent` binding and distinct native identities/sessions. Resumed
source sessions retain their frozen agent snapshot; new untouched work uses the
new admission snapshot. External skill bytes and the host installation are not
frozen. No workflow or step name selects behavior.

The Codex mapping follows the supported definition fields in
[OpenAI's custom-agent contract](https://learn.chatgpt.com/docs/agent-configuration/subagents)
(checked 2026-11-09). Tests exercise native argument/config mapping, rejected
authority increases, source precedence and unrelated execution settings. Actual
installed-host consumption is a separate integration proof.

An already-automated frozen planning contract may explicitly authorize
`tracker.createTicket` and dependency updates for contract-declared children
in that same task/run and named attachment steps. Include briefs, ownership,
checks and dependencies, inspect `tracker.get(include_graph=true)`, and return
the declared outputs. Attach work to the declared downstream step so the same
runner can activate and accept its children before executing that step's
remaining contract. This permission grants no claim/record/accept/dispatch
authority and does not start another runner or wait for main advancement.
Tracker tools retain their existing direct-write project/task checks; actual
resources, artifacts and actions still require frozen step grants. Terminal
planning returns packets for main to materialize. Saved instruction adoption
remains an explicit reviewed current-project action; active snapshots stay frozen.

The call returns task/job/status refs immediately. Each task has its own memory
scheduler job (`task-delivery:<project_id>:<task_id>`). Distinct canonical working
directories can execute concurrently, within the existing long executor's three
workers shared with other long jobs. Tickets and delivery/review calls within
one task remain serial until all task work completes or execution stops:

```text
ordinary: ready -> delivery -> independent review -> success -> complete
                         -> scoped repair -> same delivery/review sessions (once)
mirror: claim step -> accepted descendants -> frozen contract -> record outputs
```

The accepted `project_dir` stays with the task through delivery, review, repair
and explicit rehandoff after a stop. A rehandoff that selects another directory
rejects before queueing; directory aliases still refer to the accepted directory.
Queued and running task snapshots reserve their writable directories. Admission
checks and saves that reservation atomically, preventing two tasks (even in
different projects on this daemon) from acquiring the same directory. Symlink
and filesystem aliases conflict. Separate Git worktrees sharing repository
metadata remain distinct; Git is optional. No worktree creation or branch merge
is performed by the runner.

### Task-local native tool routing

For a Codex task in a separate working tree, pass optional task-only
`mcp_config_ref: {"path": "/absolute/outside-git/routes.toml", "sha256": "<64 lowercase hex>"}`
to `tracker.startDelivery`. This is separate from project delivery defaults.
The fragment contains only `[mcp_servers.<existing-name>]` tables with
`args` (strings), `cwd` (existing absolute directory), and `env` (non-secret
strings). Commands, new servers, HTTP/auth fields, `env_vars`, model, approval,
sandbox and instruction settings reject. Runtime/search-path environment
overrides also reject. Keep credentials and auth references in their existing
native/daemon owners; never copy them into the fragment.

The Codex adapter reads effective servers through native `mcp list --json`
with the assigned directory as the subprocess cwd. Native 0.167.0 discovery
uses process cwd even when `--cd` is supplied; preflight and fresh/resumed
execution must use the same directory. It
preserves their executables, and serializes allowlisted route leaves as native
`-c` overrides for both fresh and exact-session resume. Individual env leaves
preserve unrelated existing environment/auth settings. Validation hashes and
parses one in-memory byte buffer. Missing, malformed or changed fragments stop
before native execution. Other selected hosts reject this reference before
queueing. Only canonical path/digest identity enters snapshots, session
receipts and prompts; fragment contents do not. Omission on explicit rehandoff
retains the reference; a different path/digest rejects, including adding a
route to a task that already started without one. Queued/running snapshots stay
frozen. Prepare a new task when a different native routing identity is needed.

Engineering preparation owns clean trees, disjoint changes, accepted bases,
tool-target checks and explicit integration. Use the project's canonical
Serena wrapper with unique per-tree project/name/port/state; do not fork a
deprecated shim or edit global configuration. Supply each StackOS bridge's
actual tree through `--workspace-root` and the intended daemon context.
`--cd` only routes the harness and cannot prove MCP targeting.

Project-local MCP definitions also require native project trust. Confirm the
original servers are visible from each actual tree before applying route
overrides. A clone with a different repository root does not inherit the source
repository's trust. For an explicitly approved fixture, Git worktrees attached
to the already trusted repository can preserve that existing trust while keeping
distinct working directories and fixture-only branches. Record the actual Git
common directory and verify native visibility; never infer trust from a path or
silently trust new roots. Keep any worktree registration/integration/cleanup
within its authorized ownership. The route fragment cannot grant trust.

Before handoff, connect each actual tree through its own native bridge:
`workspace.startSession(auto_bootstrap=false)`, then
`workspace.connect(project_id=P, rebind_existing=false)`, then
`workspace.resolve` and repeat `workspace.startSession(auto_bootstrap=false)`.
P must already be explicitly approved. Let the bridge inject the observed
root/fingerprint; omit invented cwd/fingerprint, aliases, bootstrap and global
mode. Stop on any existing mismatched binding. Require distinct binding IDs
and path fingerprints, the same P, and each exact tree root on readback.
Never use the already-bound main bridge for another tree's setup.

For parallel proof, the assigned verifier prepares only its authorized
disposable daemon/project and two clean trees, with different sentinel files.
Record exact source SHA, branches, reference hashes, bindings, native executable
identity, ports and ownership in the existing evidence receipt. Read sentinels
through native Serena and check native StackOS scope in fresh delivery and
independent review sessions and after controlled stop/resume. Observe overlap
and independent receipts/commits before explicitly cherry-picking the reviewed
branch commits into the fixture integration branch and running combined specs.
Resolve conflicts through the named integration owner; the runner never merges.
Tear down only owned fixture jobs, daemon, wrapper processes and trees.

Configuration sources checked 2026-11-09:
[official precedence](https://learn.chatgpt.com/docs/config-file/config-basic#configuration-precedence),
[native TOML overrides](https://learn.chatgpt.com/docs/config-file/config-advanced#one-off-overrides-from-the-cli),
and [stdio fields](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
CLI overrides take priority over project/profile/user defaults, subject to
enforced requirements. The selected native executable must still pass
configuration-read probes; source parsing alone does not prove live targeting.

Stopping or completing a task releases only its own directory reservation;
scheduling failure also releases the failed task's reservation. Other task jobs,
sessions and receipts continue independently. Reservation state is the existing
task delivery snapshot, not a second lease service or an execution-wide lock.
The shared tracker mutation owner allocates each revision atomically from the
stored counter in the entity change's transaction, so concurrent task jobs keep
distinct audit revisions without serializing their host calls.

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
An ordinary review `repair` invokes the bounded three-repair loop. A supported
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
receipts and `repair_count`. First launch creates a session; later launches
resume that exact role session with task-frozen settings. Roles/tickets stay
distinct. Persist the scoped rejection before repair, supplying occurrence
phase/attempt/candidate and persisted `review_result` status/summary/outputs.
Up to three automatic repair passes are allowed per ticket after the initial
delivery. Each pass requires a separate new descendant commit, then the same
reviewer rechecks that exact SHA. The runner increments the durable count before
launching a repair; explicit requeue preserves it and cannot bypass the limit.
Legacy evidence with `repair_pass_used=true` and no count means one consumed
repair, not a fresh allowance. Rejection after the third repair stops the task.
This counts ordinary review rejections. An integration-origin correction after
child approval preserves that count and may requeue at `repair_count=3`.
Its corrected descendant still needs independent review; a subsequent ordinary
rejection at three permits no fourth ordinary repair. This is not a total cap
on corrections from all origins and does not reset sessions or history.
Blocked required verification, native/access/model/resume/schema failure or a
dirty/mismatched candidate also stops the same task with history;
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
`usage_snapshot(scope, basis, input_count, output_count, total, *, cache_read=None,
cache_write=None, reasoning=None)` and measures
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
  "snapshot": {"input": 160, "output": 70, "total": 230, "cache_read": 100, "cache_write": null, "reasoning": 10},
  "status": "measured",
  "reason": null,
  "measured": {"input": 60, "output": 20, "total": 80, "cache_read": 20, "cache_write": null, "reasoning": 5}
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

The optional detail fields are `cache_read`, `cache_write`, and `reasoning` in
both `snapshot` and `measured`. New snapshots always include these keys with
nonnegative integers or `null`; older three-counter maps remain valid. Raw input
already includes cache reads and writes; output already includes reasoning.
Total remains raw input + output. Displayed Cache means cache read; displayed
Input excludes it through the paired `remaining_input` projection. Cache write
is retained detail, not another headline or an extra total.
Known details satisfy cache_read + cache_write <= input and reasoning <= output.
A malformed, negative, or out-of-bounds detail becomes null without discarding
valid parent counters. If both cache values individually fit but their sum does
not, both are unknown. Each detail needs its own compatible baseline on resume;
decreasing or out-of-bounds detail deltas become null independently of base usage.

For example, fresh Codex counters contribute their full snapshot:

```json
{"input":100,"output":50,"total":150,"cache_read":20,"cache_write":10,"reasoning":5}
```

A same-session snapshot of input 160, output 70, cache read 40, cache write 10
and reasoning 5 contributes this `measured` map, including measured zeros:

```json
{"input":60,"output":20,"total":80,"cache_read":20,"cache_write":0,"reasoning":0}
```

If the preceding snapshot contains only input 100, output 50 and total 150,
the same current snapshot still contributes valid base usage:

```json
{"input":60,"output":20,"total":80,"cache_read":null,"cache_write":null,"reasoning":null}
```

The current detailed snapshot then provides the next compatible detail baseline.
This also holds when the preceding receipt has `status=unavailable`,
`reason=operator_reset`, `measured=null` but retains its native snapshot. The
marker changes analytics eligibility, not the snapshot's resume identity. An
intervening missing snapshot still prevents bridging. No historical backfill is
performed by collection.

Supported native contracts, checked 2026-11-06:

| Host / scope | Native basis and arithmetic |
| --- | --- |
| Codex / `codex.turn` | Native `exec --json` emits cumulative thread usage on `turn.completed`, verified with CLI 0.167.0 fresh/resume captures and the [native producer](https://github.com/openai/codex/blob/rust-v0.167.0/codex-rs/exec/src/event_processor_with_jsonl_output.rs) (`usage_from_last_total`). Normalize as session snapshots: 18442 then 36925 contribute 18442 then 18483, totaling 36925. Input plus output gives total; cached input/cache writes and reasoning output are already included subsets. `last_token_usage` covers only the latest model request and is not a substitute for a multi-request invocation. No historical version boundary is inferred from the captures. |
| Claude Code / `claude.model-usage.v2.1` | The [Agent SDK cost-tracking contract](https://code.claude.com/docs/en/agent-sdk/cost-tracking#accumulate-costs-across-multiple-calls) defines a version boundary for `claude -p`: stable 2.1 releases before 2.1.277 report per-invocation `modelUsage`; 2.1.277+ restore session totals on resume. A bounded `--version` probe of the same selected executable establishes the basis. Unknown, prerelease, unsupported-series or failed version probes leave usage unavailable and preserve execution. Sum models' input, cache-read and cache-creation counts for input, plus output for total. The narrower main-loop `usage` is not a fallback. Missing counters and potentially zeroed crash results are unavailable. |
| Gemini CLI / `gemini.models` | [Headless JSON](https://google-gemini.github.io/gemini-cli/docs/cli/headless.html) supplies `stats.models`; [telemetry hydration](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/telemetry/uiTelemetry.ts) restores these counters on resume. Sum each model once: input is prompt (already includes cached), output is candidates plus thoughts plus tool, total must match their sum. Nested role counters and the uncached input subtotal are not added. |
| Hermes / unavailable | The [CLI reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands) does not prove a usage report compatible with the integrated `chat --oneshot` command. No telemetry flag is appended. |
| Grok / `grok.headless` | Native `--output-format json` reports invocation usage, verified with CLI 1.0.77 fresh/resume captures and the [headless contract](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/14-headless-mode.md#json). Full input is `input_tokens` + `cache_read_input_tokens` + `cache_creation_input_tokens`; output already includes `reasoning_tokens`. Require all counters and a matching `total_tokens`. The captured calls contribute 15709 and 15796 (31505 total). Do not add `modelUsage` again or apply this arithmetic to persisted session `inputTokens`, which already includes cache. Missing, malformed or explicitly incomplete native usage remains unavailable; agent-authored usage is ignored. |

Native detail mappings (same scope and basis as their parent counters):

| Host | cache_read | cache_write | reasoning |
| --- | --- | --- | --- |
| Codex | `cached_input_tokens` | `cache_write_input_tokens` | `reasoning_output_tokens` |
| Claude Code | Sum `modelUsage.*.cacheReadInputTokens` | Sum `modelUsage.*.cacheCreationInputTokens`; TTL partitions are not added again | Sum `modelUsage.*.thinkingTokens`, only when every model reports a valid subset of its output |
| Grok | `usage.cache_read_input_tokens` | `usage.cache_creation_input_tokens` | `usage.reasoning_tokens` |
| Gemini CLI | Sum `stats.models.*.tokens.cached`, only when every model reports a valid subset of its prompt | Unavailable | Sum `stats.models.*.tokens.thoughts`, already included in output; nested roles are not added |
| Hermes | Unavailable | Unavailable | Unavailable |

Claude and Grok still require their native cache buckets to compute valid full
input; an absent bucket cannot be assumed zero. Missing optional thinking or
reasoning never erases valid base usage. Codex/Gemini missing optional cache
detail likewise preserves their already-inclusive input. Native arguments,
version checks, session identity, scopes and bases are unchanged. Grok fresh and
resumed invocations each contribute their entire map once, even with a previous
receipt; they are never differenced. Gemini mappings are covered by structured
counter fixtures/source contracts, not new authenticated Gemini calls. The
[native usage specs](../tests/unit/test_host_agent_usage.py) preserve minimal
native-shaped Codex, Claude and Grok captures; positive Codex cache-write
behavior was not observed in those native samples.

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

Workflow 779's one-off `scripts/repair-agent-usage.py` uses a frozen, hashed
allowlist of 112 pre-779 project-1 occurrences on 59 completed tickets. It has
no inventory/discovery mode. The separate allowlist and dry-run manifest are
review evidence, not a new statistics store. Run it from this source checkout
with the repository Python environment:

```bash
TPF_LLM_TOOL=codex tpf env -u PYTHONHOME -u PYTHONPATH .venv/bin/python scripts/repair-agent-usage.py --database /path/to/stackos.db --allowlist /path/to/reviewed-allowlist.json --manifest /path/to/new-dry-run.json --source-commit <full-reviewed-source-sha>
```

Dry-run opens SQLite with `mode=ro` and `query_only=ON`. Native session metadata,
project directory, turn boundaries, launch interval and exact terminal result
hash must agree. Persisted Codex `total_token_usage` is cumulative in the
[0.166.0 native protocol](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/protocol/src/protocol.rs#L2339-L2372),
also observed in the 0.167.0 captures. The tool checks that native shape and
uses the shared normalizer with the immediately preceding completed native
turn's compatible snapshot. Creation version is provenance, not proof of a
later resume executable. Missing/intervening usage, ambiguous results,
decreasing counters and unfinished native sessions remain unsupported; an
unsupported occurrence is left unchanged. No transcript or result text enters
the manifest. Historical selected role/outcome remain unknown without the
original frozen assignment, schema and candidate/output validation chain;
phase or a raw approve/reject is insufficient.

Only the separately authorized historical-apply ticket may add `--apply`,
`--reviewed-manifest-sha256 <file-sha256>` and `--backup <new-private-file>` to
that command. Independent exact-commit review must precede apply. The tool
checks the allowlist, manifest/source/database identity, native file hashes and
every expected full-row hash, then backs up the exact affected rows in a private
file before one narrow transaction. Only receipt `usage`, `selected_role` and
`validated_outcome` may change; current/archive copies are synchronized. The
full-merge tracker mutation API would change history/lifecycle metadata, so
this explicitly authorized repair writes only existing evidence JSON directly.
Results, launches, sessions, commits, attempts, repair allowance, ticket/task
lifecycle, existing audit and other projects remain intact. Reapplying the
same reviewed manifest is a no-op; concurrent row or native-file changes refuse
apply and require a fresh reviewed dry-run. Keep the private backup and manifest
with the apply receipt and reconcile the shared statistics projection afterward.

### Recorded agent statistics

`tracker.agentStats` reads one project's default tracker with enforced project
scope. `tracker.agentStatsAll` is a local-admin read over the same query:
`is_active=true` (default) selects active projects, `false` archived projects,
and `null` all projects. A project read includes its receipts even when archived.
Neither operation creates a tracker, launches work, or changes configuration.

Both return `TrackerAgentStatsOut`: scope (`project_id`, `is_active`), `as_of`,
`group_by`, `start_date`, `end_date`, `timezone`, `totals`, `rows`, `daily`, and
`undated`. `group_by` accepts `agent`, `model`, or `agent_model` (default).
Agent and Agent+model preserve project identity globally. Explicit Model groups
combine the visible projects and expose `project_ids`; singular project fields
are null for a multi-project group. `group_key` is an opaque stable series key.
Role and host do not split groups. They remain metadata when uniform, otherwise
null. Agent/model labels come from recorded launch selections, never current
defaults or name-inferred roles. `model` is the **selected model**, not a claim
about a measured actual model; missing identity stays null/unrecorded.

Statistics exclude exactly `usage.status="unavailable"`,
`usage.reason="operator_reset"`, and an explicitly present `usage.measured=null`
before counting, classification, grouping, coverage and daily projection. Near
misses, including an absent `measured` key, remain eligible. The canonical
`invocation_receipts` operational inventory, archived/current deduplication,
snapshots, sessions, outcomes and recovery state are unchanged. Reset-only
scopes have empty metrics and no rows.

- **Launch attempts** (`launch_attempts`) count recorded invocation receipts once
  per ticket/attempt/phase across current and archived attempts. Resumes are new
  occurrences. Tracker history, configured unused roles and group tickets without
  invocation receipts contribute nothing. An attempt need not have reached a model.
- **Reported tokens** (`reported_usage`, unit `tokens`) sum only normalized
  `usage.measured` contributions: `input`, `output`, `total`, nullable
  `cache_read`, `cache_write`, and `reasoning`. Cache read/write are subsets of
  Input; reasoning is a subset of Output; total remains Input+Output. Snapshots
  and native/provider fields are never guessed or added again. Optional detail
  validation reuses the native result owner's subset validator and cannot erase
  valid parent totals. No usable contribution means null; measured zero is zero;
  an empty scope has zero totals.
- **Recorded reviewer rejects** (`recorded_reviewer_rejects`) count explicit
  `selected_role=review_agent` plus `validated_outcome=repair`, including reviewer
  mirrors stored in the delivery phase. Failed, blocked, native/schema/candidate
  validation errors do not count. Phase, title and agent name never classify a reject.

Totals and rows include `coverage`: `usage_measured_attempts`,
`usage_unavailable_attempts`, `classification_known_attempts` and
`classification_unknown_attempts`. `usage_status` and `classification_status` are
`empty`, `complete`, `partial` or `unavailable`. Explicit null validated outcomes
record known validation failure; absent outcomes or roles remain unclassified.
`coverage.fields` maps each of `input`, `output`, `total`, `cache_read`,
`cache_write`, `reasoning` to `measured_attempts`, `unavailable_attempts`, and
`status` with the same coverage enum. Each denominator is all eligible attempts
in that scope. A partial cache sum describes only known measurements; it does
not imply zero cache for the others. `review_outcomes` counts explicit
review-role validated `success`, `repair`, `blocked`, `failed`, or
`validation_failed` (explicit null); absent/unknown role or outcome remains
unclassified. This is not an approval rate. Keep missing-data accounting in the
API and accessible exact-value coverage. Fixed Input / Cache / Output summaries
show compact muted N/A when measurement is absent; measured zero remains 0.
The shared display projection checks per-field measured attempts (paired cache
coverage for Input), never numeric zero alone. Retained group/daily columns use
N/A for missing cells; wholly unsupported columns and optional panels stay omitted.
Empty/unavailable chart samples remain null, including absent agent series;
they do not become zero-height measured bars or synthetic totals.
Compact and raw responses retain
identical complete facts, including daily series and missing-date disclosure, excluding prompts,
instructions, raw output and credentials. Neither operation accepts `ack`.

For Tokens chart composition, `remaining_input` sums Input minus cache read
**only on receipts with known cache read**, including cache write in that
remainder. `input_with_unknown_cache` is measured Input on receipts whose
cache-read split is unknown. These three internal accounting portions (known
cache read, known remainder, Input with unreported split) reconcile to measured
Input. The unknown-cache portion is not a displayed chart series. Tokens shows
only available Input, Cache and Output, in that order; Input uses `remaining_input`.
When the plotted stack differs from
the reported Input + Output, concise available-measurement context distinguishes
it from a complete total. Exact accessible values use **Available tokens**.
When all cache reads are unknown, cache and remainder stay null; an all-empty
API scope returns additive zeros with empty coverage, displayed as N/A in fixed
summaries. Do not subtract a partial cache sum from all Input.

Supply `start_date` and `end_date` together as inclusive local calendar dates,
ordered and at most 366 days, with an IANA `timezone` (default `UTC`). Invalid
filters reject before the query. UI owns the last-seven-calendar-days default
and browser timezone selection. Daily attribution uses only offset-aware
`launch.started_at`, converted to the requested timezone; completion time and
receipt order are not substitutes. Each date appears once, including empty
days. Totals, table rows and daily totals use exactly the same selected
occurrences. Every day also carries `by_agent`, `by_model` and `by_agent_model`
summaries with stable keys, produced by the same `_group_rows` owner as the
table. The existing Agent / Model / Agent + model selection controls both table
and identity chart. Model labels preserve the exact recorded version; combined
labels include agent and version. Both use the loaded response's `group_by`
while a regrouping request is pending. Shared group-key colors match the daily
bars, below-plot legend, tooltip and table markers without client receipt
aggregation. These series and each day's totals reconcile, including
per-field coverage and review facts.

`undated` summarizes eligible receipts with missing, invalid or offset-free
launch dates in the selected project visibility. With a date range they are
excluded from totals/rows/days and disclosed separately. Without dates,
totals/rows cover retained lifetime usage including `undated`, and `daily` is
empty (no unbounded all-history time series). Work projections always use
retained lifetime usage, independently of these analytics filters.

Request example for `tracker.agentStats` (`tracker.agentStatsAll` accepts the
same filters and `is_active` instead of `project_id`):

```json
{"project_id":1,"group_by":"agent_model","start_date":"2026-11-01","end_date":"2026-11-03","timezone":"America/Los_Angeles","response_mode":"compact"}
```

The reusable deterministic receipt fixture is
[`tests/fixtures/agent_statistics_dated.json`](../tests/fixtures/agent_statistics_dated.json).
`seed_dated_statistics` in `test_agent_statistics.py` loads it into an isolated
test database, including a duplicate current/archive occurrence, a fall-back
hour repeated twice, measured zero with unrecorded identity, missing cache/date,
a reset-only agent and an archived project. Primary-project response excerpts:

```json
{
  "totals": {
    "launch_attempts": 4,
    "recorded_reviewer_rejects": 1,
    "review_outcomes": {"repair":1},
    "reported_usage": {"unit":"tokens","input":350,"output":60,"total":410,"cache_read":160,"cache_write":30,"reasoning":9,"remaining_input":140,"input_with_unknown_cache":50},
    "coverage": {"fields":{"cache_read":{"measured_attempts":3,"unavailable_attempts":1,"status":"partial"}}}
  },
  "undated": {"launch_attempts":1,"reported_usage":{"input":7,"output":1,"total":8}},
  "daily": [
    {"date":"2026-11-01","reported_usage":{"input":300,"output":50,"total":350,"cache_read":160}},
    {"date":"2026-11-02","reported_usage":{"input":50,"output":10,"total":60,"cache_read":null,"remaining_input":null,"input_with_unknown_cache":50}},
    {"date":"2026-11-03","reported_usage":{"input":0,"output":0,"total":0,"cache_read":0}}
  ]
}
```

This is an excerpt, not a replacement schema; generated `ui/src/api.ts` exposes
the complete typed rows, daily color series and coverage. Primary lifetime
total is 428; All-project dated total is 454; the combined `selected-sol` Model
group is 394 across the primary and archived projects. Consumers should render
these returned metrics directly and retain explicit missing-data coverage.

For example, delivery 10 → reviewer repair 20 → resumed delivery 30 → reviewer
success 40 produces four attempts, 100 reported tokens and one reject, even when
the current attempt also appears in the archive. A one-phase reviewer mirror
with validated repair adds one attempt and one reject.

`tracker.get` also returns `agent_stats` on each task and ticket, using the same
`AgentStatsMetricsOut` summarizer and coverage semantics as project/global totals.
A ticket counts only its own deduplicated invocation receipts. A task counts all
its tickets' own receipts, including group and workflow-mirror invocations, once;
it never adds recursive parent subtotals. Ticket/graph visibility filters do not
change the task total, so a focused graph and its board card share one scope.
An empty task has zero totals with `empty` coverage; missing usage on recorded
attempts remains null with `unavailable` coverage, and measured zero stays zero.

The task index batches whole-task metrics alongside its ticket summaries and
keeps tickets, dependencies and links empty. Consumers read the returned metrics
without per-card statistics requests or receipt arithmetic. Compact task/ticket
projections retain the complete safe metrics, including null usage and coverage;
they omit private receipt content. Generated API and maintained tracker types
expose the same `agent_stats` field. The optional/null schema default supports
objects without an attached projection; it does not establish measured zero.

Project Home and the main Overview share an **Agent usage** section backed by
these operations. Available Input / Cache / Output appear alongside launch
attempts, daily stacked bars and a complete Agent / Model / Agent+model table.
Displayed Input is the existing paired `remaining_input`, Cache is `cache_read`,
and Output is `output`. Raw API `input` remains inclusive. Summary, table and chart use the same
selected date range, defaulting to today plus the previous six local calendar
days in the visibly identified browser IANA timezone. Exactly two chart views,
**Tokens** and **Agents**, share this scope. Tokens stacks Input, Cache and
Output; Input uses the repository's paired Input-minus-Cache projection,
never an aggregate subtraction that invents a remainder for missing cache.
The shared UI mapping also owns summary values, numeric table data, exact hover
and accessible values, and daily rows. Input availability and coverage follow
paired cache measurements. For receipts 100/80/5 and 50/unknown/3, the known
display is Input 20, Cache 80, Output 8; those 108 tokens are not the full raw
total of 158. Complete 11,545,485/11,232,768/46,397 counters display Input 312,717,
Cache 11,232,768, Output 46,397, preserving total 11,591,882. A measured zero
remains zero; no second subtraction or historical counter mutation occurs.
Agents splits the same reported Input + Output by agent. Incomplete composition
uses available-measurement context rather than claiming a complete total. Both
views retain the same plot, axis gutter, bounded legend and selection-caption
dimensions. Long legend names are elided with their unchanged identity available
on focus/hover; exact per-series text stays accessible without a visible raw-value
transcript. Exact hover/keyboard values follow the
[shared Home component contract](ui-component-inventory.md#view-decomposition-pattern).
Overview retains its existing Active/Archived/All project visibility. Agent
groups retain project identity; Model grouping combines the selected projects.
Model labels reflect recorded selections, independent of current Agent defaults.

A measured zero stays `0`; fixed Input / Cache / Output summaries and missing
cells in retained columns show `N/A` without measurement. Unsupported optional
metrics, columns, legends, sidebars and details remain omitted;
known Input/Cache/Output remain visible and null remains null in the API. There
is no synthetic unknown-cache series. A fixed-column, vertically bounded daily
table shows Date and available Input, Cache, Output and Total, independent
of agent count. Compact values retain exact focus/hover labels. Agent identities
are preserved unchanged, as are exact recorded model versions. Breakdown rows
have plain labels with compact review facts; project IDs/lists, host names,
repeated identity and isolated counts do not create row disclosures. Token
details retain supported cache-write/reasoning values, while compact summary/row
facts show recorded review counts (passed, changes requested, blocked, failed or invalid result), with no
empty review sentences, raw role keys, classification prose or inferred approval
rate. Exact-value accessible labels retain field coverage for partial measurements.
A failed refresh retains same-scope values with a warning and
their original update time. Regrouping retains the dated chart/totals and the
returned grouping label on existing rows until the next response; date, project
and visibility changes clear those values. Both pages reuse
their existing refresh owner, with no statistics writes, receipt aggregation in
Vue, or extra timer.

Agent/process failure also stops the task's execution. Ticket evidence records
both agent results, launch details, thread references, prior HEAD and verified
commit SHA before dependencies are released. Git is optional. Non-Git tasks invoke no Git commands; Codex uses its native
`--skip-git-repo-check` launch option. Supplied candidate evidence must resolve
to clean current HEAD and descend from the observed base. Observation work may
cite unchanged HEAD. Engineering guidance still requires separate source-change
and repair commits; a supplied repair candidate must be a new descendant.
Git presence alone does not require a commit or clean worktree: noncoding work
may execute in a dirty Git directory and return non-Git outputs. Baseline HEAD
is an observation, not a source-candidate requirement. A valid repository with no
initial commit supplies no baseline SHA; noncoding work needs no initial commit.
Other Git errors still stop execution. Invalid/mismatched supplied
candidates, dirty source candidates and review of a different SHA stop the task.
An ordinary ticket that supplied a candidate must retain that contract on repair
or explicit requeue; omitting its SHA cannot bypass validation. Applicable project,
role and workflow guidance owns source-edit and commit requirements, including
for workflow mirrors; the generic launch packet supplies no universal source-edit
ban. Preserve unrelated dirty work. A failed returned delivery that started clean
and leaves unfinished Git changes records an `owned_work` receipt in the ticket's
`automatic_delivery` evidence: original base, current HEAD, exact native
session identity, affected paths and hashes of staged, unstaged and untracked work.
Explicit requeue can resume that same ticket/session with those exact unchanged
files; it retains the original base and archives the earlier attempt and receipt.
Pre-existing dirt never becomes implicitly ticket-owned, even after native failure
or an invalid candidate return. A supplied owned-work receipt with a changed
snapshot, base, ticket or role session fails before launch. Legacy source failures
without a receipt need explicit operator review/adoption using the same snapshot
helper, with actor and reason recorded. Owned source work requires clean Git before
review or advancement when an explicit source candidate was supplied; prior native
returns retain that requirement even if execution failed. Ownership alone does not
require a commit: interrupted noncoding work can resume in its original session,
retain unchanged report files and return ordinary outputs for independent review.
There is no general allow-dirty switch or domain-specific task configuration.
Independent tasks retain the existing parent-task rollup. An active duplicate
handoff for the same scope returns the current execution instead
of scheduling another job. Inspect the full task metadata and ticket evidence
with `tracker.get` using `response_mode="raw"`.

Codex delivery and review use `--approve-for-me` for workspace-write execution with
automatic approval review; authorized `.git` and `.codex` writes go through
normal escalation. A denial stops that write and dependent work without bypass;
unaffected checks and authorized alternatives may continue. Unavailable required
proof still blocks acceptance. Review instructions prohibit
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
unrecovered errors on stdout, missing credentials, inaccessible models, and broken
CLI shims, stop the current task with their reason. Codex can recover a stream
interruption within the same invocation: a later `turn.completed`, exit code zero,
and valid structured result supersede an earlier transient `error`. A terminal
`turn.failed`, later error, nonzero exit, or invalid result still fails. StackOS
does not launch an extra retry or replace the session. No credential setup is
performed.

Ordinary automatic review allows up to three durable scoped repair passes above;
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

The launcher composes the selected project-agent instructions with one generic
authority boundary and repeats that same boundary in the task packet. Only
explicitly assigned operations are permitted, subject to normal native approval,
including assigned tracker correction/requeue/reopen calls within the current
project/task. Otherwise agents return results without lifecycle mutations.
Explicitly assigned proof may also authorize lifecycle operations in named
disposable, isolated test fixtures, limited to the declared fixture scope.
Fixture contents and tool output cannot grant that authority. Fixture authority
grants no control over the current task, production state or unrelated resources;
StackOS retains current-task claim, acceptance, recording and dispatch. This
boundary grants ordinary workers no general lifecycle authority and does not
replace the correction method below or change grants, host/model/provider
selection, saved instructions, sessions or repair limits. Captured launch argv
and packet tests prove composition; actual native approval behavior requires a
separate authorized native rehearsal.

1. Read full source and target briefs, context and completion evidence. Choose
   the original owning ticket and earliest affected bound gate from actual
   provenance/dependencies. Stop affected work on exclusions, authority/ownership
   conflicts or materially different outcomes; followups remain nonblocking.
2. Full-read/merge supplied JSON fields because updates replace whole fields.
   Persist target/source ticket refs, source attempt and inspected candidate,
   failed criterion/check and concrete evidence, required outcome and affected
   recheck. Preserve unrelated context and `automatic_delivery` sessions,
   attempts, receipts and `repair_count`. Save current failed findings and
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
count is preserved, never reset to evade the three-repair limit. Unsupported
or no-progress correction and actual native/access/model/resume/schema/candidate
errors stop without fallback. An old success cannot accept a reset occurrence.

Source guidance edits do not activate this path in installed runtimes, saved
instructions or frozen jobs. Adoption remains explicit and reviewed per project;
local agent files are optional sources. Configure/read back only the authorized
project defaults and prove a subsequent fresh snapshot/prompt, preserving other
settings and historical receipts. Isolated source rehearsal proves source
behavior. Readback plus an exact subsequent native prompt can additionally prove
saved-instruction consumption in that isolated scope; primary installed activation
and consumption remain separate claims.

## Board priority and order writes

`tracker.updateTaskPriority` and `tracker.reorderTask` are strict, browser-safe
operations exposed through the same registry to REST, CLI and MCP. They use
the existing task `priority_key` and integer `order_index`; no migration or
ranking store is involved. The UI client is `lib/stackos/taskBoard.ts`.

Both require `project_id`, `task_key`, `expected_status`, `expected_updated_at`
and a stable `idempotency_key`. Priority accepts only `priority_key: p0|p1|p2|p3`.
Reorder accepts `anchor_task_key` and `placement: before|after`. The anchor
must currently belong to the same project and status column as the moved task.
There is no lifecycle, ticket-priority or execution-policy payload. These direct
tracker intents do not accept a run token or arbitrary patch fields.

The repository reserves the SQLite writer before validating current state and
retry identity. Stale task snapshots or missing/moved anchors return a structured
conflict with `next_operations: [tracker.get]`; refresh and submit a new intent.
An identical retry returns its saved raw receipt with `replayed: true`; reusing
the key with different arguments conflicts. The receipt includes the saved task,
`affected_task_keys`, `changed`, tracker revision and request fingerprint.

Board projection and reorder must sort by `order_index` ascending, then
`created_at` descending and numeric `id` descending. Reorder removes only the
selected task from that complete column sequence and inserts it at the anchor,
preserving hidden tasks' relative order. A real move normalizes only that column
to zero-based integer positions. A same-position/self-anchor move and an
unchanged priority are no-ops: no normalization, revision or change event.
No-op receipts still make retries stable. All changed ranks, revision, events
and retry receipt commit together; the moved task's snapshot also advances when
a tied integer remains unchanged but its relative position changes.

Real priority/order edits emit `tracker.task.changed` into the existing timeline
and `tracker-status` SSE transport, including task scope and `changed_fields`.
Relative movement with an unchanged integer uses `order`; changed integers use
`order_index`. Existing agent priority patches emit the same genuine task-change
event. These events invalidate readers without masquerading as status changes
or adding a polling path. UI callers retain the immutable intent for transport
retry, reconcile after conflicts, and check project ownership before applying a
late response. The card and navigation consumers own that interaction state.

## Status Events

Task inspection uses `tracker.taskActivity` and `tracker.taskResults` through
the shared MCP, REST operation and CLI adapters. Both require `project_id` and
`task_key`, accept `limit` (1–200, default 50) and an opaque `cursor`, and return
the exact task scope, canonical source IDs, `next_cursor`, coverage and warnings.
Compact and raw retain the same bounded projection; ack is unavailable. Cursors
bind the project, task and source filter, ordered by descending source timestamp,
source kind and row ID. Pass the returned cursor unchanged for older items.

Activity reads existing task/ticket/run-linked project events and only structured
delivery lifecycle revision deltas. It writes no history and makes no complete
internal-audit claim. Missing historical phases remain absent. Existing live
timeline transport may invalidate/refetch the active read; these operations add
no poller or durable event store.

Results accepts an optional `source_kind`: `resource_record`, `artifact`,
`learning`, `decision` or `experiment`. It follows explicit same-project tracker
membership/links, run-plan/run links, execution-context links (including inactive
contexts), artifact associations, top-level typed provenance fields
(`task_key`, `ticket_key`, `run_plan_id`, `run_id`, `context_ref`), validated
artifact resource links, decision runs, learning snapshot runs and experiment
run arrays. One result retains all valid association reasons and the existing
canonical payload for shared Library details. Titles, arbitrary metadata,
completion prose and nested lookalike keys never establish an association.
An association does not imply approval or exclusive production; unlinked records
remain available through Library. Broken or foreign references never create
placeholder results.

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
- `tracker.updateTaskPriority` (bounded task priority; MCP/REST/CLI/browser)
- `tracker.reorderTask` (same-status neighbor placement; MCP/REST/CLI/browser)
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
   Validate the exact final semantic payload; any semantic edit after preview
   requires revalidation. Only dry-run and reviewed idempotency/response controls
   may differ on apply. After an uncertain failure inspect authoritative tickets,
   relations and revision before retrying; read back the intended graph after
   success. Source atomicity tests do not establish the behavior of an older
   installed daemon.
   Dry-run and apply share reference validation, including the required `ref`
   in every `references_json` entry. Errors identify the ticket index/key and
   reference field. Creation is atomic: a failed single or list create leaves
   no new tickets, relations, revisions, or task rollup changes, including a
   late dependency-cycle failure. Repository `create_ticket(commit=False)`
   keeps successful writes under the caller's transaction control and preserves
   unrelated caller work when creation fails. Validation failures retain their
   structured errors in raw, compact, and ack requests; successful ack stays minimal.
4. Patch many tickets by calling `tracker.updateTicket` with `updates_json`.
   Each entry identifies one ticket by `ticket_key` or `ticket_id` and supplies
   a `patch_json`. List updates retain per-item success/error behavior; creation's
   all-or-none boundary does not change them. Updates are patch-only: omitted
   fields are preserved.
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
blocked totals, actual `blocked_by_titles`, assignees, and searchable ticket terms) while omitting ticket rows,
dependencies, links, and graph data. Ticket-scoped filters are intentionally
rejected in this mode; load one focused task or a normal filtered snapshot when
ticket detail is required.

Task and ticket reads expose nullable `active_execution` from the host execution
service's in-memory invocation registry. It contains the configured project-agent
name, selected model (nullable when inherited), host, current call start, invocation
identity and owning ticket key. Task-index and focused/detail reads use the same
projection. Persisted `in-progress`, assignment, selection and workflow-parent
state do not establish a live process. Every registered call is removed in
`finally`, including native errors and cancellation; daemon restart begins empty.
Start/finish events (`tracker.execution.changed`) travel over the existing tracker
SSE connection even when lifecycle status stays unchanged. These are two lifetime
notifications per call, not usage samples or ticket lifecycle writes.

Board and Graph cards use the approved Concept 3 anchored execution footer, with
a fixed 56px track to keep two identity lines clear of the bottom-edge badge.
Graph nodes are 328×224; Board cards remain 224px tall at the current column width.
Actual current host/role/model/start replaces ordinary assignment. A retained
`agent_stats.last_execution` is explicitly Last, with no live indicator or current
start label. Full recorded provenance remains available on hover/focus and in
details. Manual work omits usage; automated work preserves fixed mini-canvases
before, during and after execution. Board and task header use whole-task lifetime
values/trends; Graph tickets use their own receipts. The task header retains
status/completion and Task details while removing redundant ticket/dependency counts.
The lightweight rollup supplies actual prerequisite titles only when all open
work waits on real dependencies; an active occurrence suppresses the Board badge.
Ticket reads also expose `blocked_by_titles` from the same dependency owner, so
a focused Graph read retains titles for prerequisites belonging to other tasks.
Stop/approval/proof reasons stay separate from prerequisites. Focus, selection
and persisted status alone never register a native call or start collection.
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

### Live usage during native calls

The host execution service extends the same active-call registry with one
replaceable provisional contribution and up to 120 measured, timestamped samples
per call. One daemon collector samples at five-second intervals only while calls
are active. Codex identity is exposed before process exit; the exact native
rollout path is resolved once by exact ID in the native thread index, metadata
checks its ID and working directory, and
subsequent reads advance a byte cursor. A newly announced file may not exist yet;
the reader waits for that exact path, without searching historical sessions.
Resumes skip the pre-invocation file prefix and use the unchanged previous
invocation baseline and native ID.

These are native `token_count` checkpoints, not continuous token streaming.
Repeated cumulative polls replace provisional usage; they are never summed.
Existing receipt normalization and aggregation serve ticket, task, project,
daily and grouped summaries. A persisted final receipt wins over any in-flight
provisional observation. Native result/error handling and repair rules remain
unchanged. Missing or unsupported telemetry is N/A; final collection still runs
for every supported host. Claude/Grok full-scope live subagent/cache accounting
has not been established, so their live adapters remain disabled.

The rolling ten-minute history bounds active-call samples only; collection and
final totals cover the entire invocation. Native-session reads are independent
of UI visibility and view count. No sample table or per-tick tracker write exists.

At native exit the registry immediately removes the live identity and closes its
reader. Its bounded samples and counters survive only the synchronous receipt
handoff, then unregister releases them on success, error or cancellation. A
supported final native counter supplies a real final observation even when no
live checkpoint was collected. Automatic delivery commits the safe `execution`
identity and up to 120 measured samples to the existing invocation receipt;
the later final save preserves those observations, including failed results.
Result, session and candidate validation still determine the outcome afterwards.
Thus delayed final-receipt validation does not make recorded totals temporarily
regress. The handoff uses final native counters and the same fixed resume baseline,
and never adds a second receipt. The snapshot is released even if notification
fails; it is not a pending-history store or a retry queue.

Changes to recorded receipt statistics emit `tracker.usage.changed` in the
receipt write's existing transaction. The shared SSE owner uses its safe task
and ticket keys to refresh those projections even when no call or status is
changing. Repeated identical receipt writes emit no usage change. The UI consumes
the usage frame without a second whole-page Work reload; reconnect reads the same
committed totals.

An explicit `task_key` scopes both the stream's executions and its task/ticket
usage projections, including the initial snapshot, reconnect and later
receipt-only changes. Unscoped project/global streams retain their full scope;
transient usage frames never advance the durable status-event cursor.

Each statistics projection copies provisional counters before selecting fresh
receipt evidence. If a call disappears before that copy, its handoff has already
committed; if it disappears afterwards, the request keeps its own counter copy.
Recorded measured usage and samples win over an older copy, including the early exit
handoff before result validation. Ticket metrics use a batched scalar evidence
query rather than potentially stale ORM ticket rows. SSE task/ticket projections
share one request copy. These bounded copies contain no native reader,
hold no registry lock during SQL, and live only for the projection call.

Work task/ticket `agent_stats.samples` replay those deduplicated observations as
at most 120 cumulative lifetime points. Each checkpoint replaces its invocation's
contribution; it is never added to the preceding checkpoint. Delivery and review,
including measured rejected, failed, blocked, cancelled and repaired attempts,
all contribute regardless of acceptance. Task projections include all their own
tickets, independent of visible filters; ticket projections include only their
own receipts. Equal timestamps combine into one point. Input uses the sample's
paired `remaining_input`, Cache uses known cache reads, and Output remains
independently available. Unknown metrics are null; measured zero remains zero.
An old final total with a recorded observation time can supply one real point.
If a measured legacy receipt has no observation time, the cumulative timeline is
unavailable rather than showing a narrower scope or inventing timestamps.
Existing analytics projections keep their date/group semantics and do not replay
Work history.

`agent_stats.last_execution` is a safe identity from the latest actual recorded
call by start time, including between calls and after reload. It carries no live
state, samples, private native paths or prompts. `active_execution` remains
current-only. The existing filtered SSE usage projection carries these fields
inside `agent_stats`; `usageUpdates` preserves them with the rest of that object.
Fresh/resumed delivery, reviewer and mirror prompts omit this telemetry through
the existing prompt projection while retaining canonical evidence refs.

Automated Board cards and the Graph task header use whole-task lifetime numbers
and measured samples; Graph ticket cards use only their own ticket lifetime.
Each Input / Cache / Output metric reserves fixed number and mini-canvas slots
before the first measurement, during execution, between calls and after terminal
states or reload. Slots do not hide according to activity, sample count or text
fit, and values do not wrap or resize the card. Numbers remain lifetime totals;
bars show newly reported increments within the same task or ticket scope. Each
bar covers one minute, with its delta assigned to the later observation's minute,
not an inferred consumption time. `DEFAULT_USAGE_BAR_COUNT` sets the shared
default of 20 intervals; `UsageTrace.barCount` supports 5–60. Bars stay at most
2 CSS pixels wide as the chart uses the available metric-column width.
Every chart keeps the same baseline. The first observation supplies a neutral
baseline tick, never an invented increment from zero. Missing values, absent
observations and negative corrections leave gaps; observed unchanged values
remain measured zero. Each metric uses its own zero-based scale. Exact totals,
coverage and chart interval values remain accessible. Active charts advance
from the shared feed clock; inactive charts freeze at their last observation.
Retaining history does not keep native sampling or per-card polling active.

Task details show task totals, and ticket details show only that ticket's totals,
through the same compact summary and retained mini-canvases. The current agent
and start time appear separately as identity, without another token summary for
the current call. Totals and traces continue to update through the shared usage
subscription and remain after the call ends. Manual work has no usage furniture.
Neither presentation adds smoothing, a synthetic zero prefix, a speed estimate,
animation or per-card polling.

Home, Overview and Work share the existing SSE transport through
`usageUpdates`; `useAgentStats` retains date/group/scope and stale-response
protections. Home's 30-second page poll no longer refreshes usage. Manual and
filter refreshes still use the shared statistics owner; unrelated overview,
ticket-count, health and host polling remain unchanged. Work patches usage in
place from minimal server projections rather than fetching ticket history or
doing another whole-page reload on usage events. Reconnect replaces the safe
project usage snapshot, including ended calls. A shared latest-value revision
protects newer measurements from an older pending Board/Graph/detail response;
it holds no invocation history and is released with the last scoped subscriber.
Project-keyed route mounting owns subscription lifetime; explicit statistics
loads reconcile a changed project ref and release the previous subscription.
Work provides this existing subscription's health to its descendant components.
A red `Live` mark requires an actual active execution and a fresh replacement
snapshot on the current connection. Heartbeats keep a quiet agent live even
when token counts do not change. Disconnects and EOF show `Reconnecting`;
45 seconds without frames retires and reopens the existing stream. One deadline
per shared channel is cleared on hide or final unsubscribe. This reports feed
health, not proof that the native reader produced a new token measurement.
`UsageTrace` redraws its canvas backing store on data updates, container resizes,
theme changes and device pixel ratio changes. Its observers and listeners are
released on unmount. The compact summary adds no fit-based visibility gate or
refresh timer.

## Board URL state

`TaskTrackerView` owns Board filter and display state through the current URL.
The pure `task-tracker/boardFilters.ts` parser, serializer and update/remove/clear
commands provide one typed clause model to the controls and Board projection.
There is no saved filter store. History and reload read the same query state;
changing filters preserves the open detail identity and its draft.

| Query | Meaning |
| --- | --- |
| `search` | Text substring across task and ticket/index search fields, case insensitive |
| `status`, `priority` | Comma-separated or repeated values; OR within a field, AND between clauses |
| `status_op`, `priority_op` | `is` (default) or `is-not`; priorities are `p0` through `p3` |
| `workflow`, `assignee` | One workflow key or assignee; corresponding `_op` accepts `is` or `is-not` |
| `ticket_status`, `focus=blocked` | Legacy Home restrictions represented as removable clauses |
| `completed` | `all`, `past-day`, `past-week`, `past-month`, or `none` |
| `hide_empty=1` | Hide columns without any projected tasks |

Missing status/completed choices default to Status is not Aborted and Completed
Past week. Explicit `status=all&completed=all` keeps Clear all unrestricted on
reload; clearing filters retains the empty-column display choice. Invalid
enum values are ignored, with missing/invalid status and completion choices
using the same defaults. Complete selected explicitly with Completed None
reconciles to All in the control state and subsequent URL writes.

Completion windows apply only to tasks whose actual status is `complete`,
using `completed_at` and the existing StackOS UTC timestamp parser. Windows
include their lower boundary and exclude future or unknown/invalid timestamps;
Past month means 30 rolling days. All includes unknown dates. Other statuses,
including reopened tasks, are unaffected by completion dates.

Board rows sort by persisted `order_index`, then newest `created_at` and
descending ID, matching the reorder repository. Graph's picker retains its
unfiltered newest-first order. Project switching preserves view, status,
priority, completion/display choices and portable legacy restrictions, and
clears text, workflow, assignee and task/ticket selections.

## UI

The Work UI provides two views, Board and Graph. It renders:

- Board by default, with seven columns for the current task statuses (empty
  columns can be hidden through the display choice):
  `not-started`, `in-progress`, `complete`, `deferred`, `aborted`, `failed`,
  and `skipped`. Cards cannot be dragged between columns or change status.
- a Board / Graph switch in the same top-right page-header position in both
  views. Board has one filter bar: Filter opens Text, Status, Priority,
  Workflow and Assignee editors; applied chips support edit and removal.
  Excess chips collapse into a +N filters popover without wrapping or horizontal
  scrolling. Clear all, result count and Display keep their reserved space.
  Display contains Completed tasks and Hide empty columns. Returning from Graph
  refreshes the task index while retaining the mounted Board and its scroll.
  There are no separate Stories or Tickets views.
- project-wide task cards with leading identity, title, status and completion
  progress, followed by quiet usage for automated work and an anchored execution
  footer. A current occurrence shows its actual host logo, readable role, exact
  recorded model and actual start time. A retained occurrence is explicitly
  `Last:`; otherwise the footer shows ordinary assignment without inventing a
  model or start time. Host and recognized owner marks use the same canonical
  logos as Overview, with a neutral user icon for other assignments. Genuine
  prerequisite titles appear in a bounded bottom-edge `Blocked by:` badge with
  full titles available for inspection. A task with active execution or ready
  work is not labelled wholly blocked because one child waits. Stop, approval
  and proof reasons remain separate from prerequisite badges; full workflow and
  dependency context remains in inspection. Only `complete` tickets count as
  complete; other terminal statuses do not. Search, workflow and assignee filters
  narrow the Board.
  Task-status filters apply to tasks; `ticket_status` links explicitly filter
  tasks containing tickets with that status.
- a small semantic info `Automated` badge on Board cards, the Graph toolbar,
  and beside status in task details when the task has a persisted
  automatic-delivery handoff record. Its subdued tint stays distinct from
  lifecycle status in both themes.
  Queued, running, stopped and completed executions retain the badge: it names
  the delivery method. Project agent defaults alone do not add the indication.
- compact muted Input / Cache / Output metadata only for those automated
  tasks and their tickets, using `isAutomaticDeliveryTask` as the eligibility
  owner. Parent task identity reaches graph nodes and both ticket inspectors;
  workflow names, defaults and nonzero measurements never imply automation.
  Manual work shows no usage labels, zeros, N/A or empty copy. Automated work
  retains fixed slots with compact muted N/A until measurements exist, including
  no launches, main-thread planning and pending native receipts; N/A does not
  attribute work to a particular thread. Measured zero remains visible as 0.
  Each compact metric keeps fixed number and mini-canvas slots. K/M/B value
  changes do not wrap the row, change its geometry or hide a chart; unusually
  long compact values may elide while their full exact values remain available.
  Exact values, N/A and measured-field coverage agree on keyboard focus and hover.
  Board, the contextual Graph task header and task details use lifetime task
  projections; ticket nodes and ticket details use their own ticket projection.
  Graph filtering changes visibility without recomputing usage. Title, status
  and progress remain primary.
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
