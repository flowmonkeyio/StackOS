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

These lifecycle values are separate from the eight visible Board columns and
delivery execution state. Backlog is uncommitted backlog; Todo is waiting for
development. A recoverable delivery stop is not an instruction to set `aborted`.
Use the [Board membership and operation contract](#board-priority-and-order-writes)
for placement and the existing explicit handoff/requeue owners for execution.

## Automated Task Delivery

### Managed upgrade preparation

`maintenance.prepareUpgrade` is an installation administrator boundary exposed
through REST and CLI, unavailable to project MCP agents, workflow grants and the
browser console token. It takes `upgrade_id`, declared `source_identity` and
`target_identity` build references, and `expected_revision` (0 for a new
transaction). These references identify the intended transition; preparation
does not attest an installed build or authorize replacement by itself.
`maintenance.upgradeStatus` reads the active transaction, or one `upgrade_id`.
Both compact and raw responses retain build identity, revision, fence status,
checkpoint refs and per-checkpoint repair context.

The minimal `managed_upgrades` row owns one durable installation-wide fence and
task references, separate from tracker/workflow lifecycle. In one SQLite writer
transaction preparation fences admission/dispatch and checkpoints eligible
authorized queued/running delivery handoffs across projects. Stopped, failed,
aborted, complete and manual work is not captured. Invalid/foreign-generation
ownership refuses preparation before interruption. Tracked setup uses ordinary
delivery eligibility. Legacy setup or other native owners outside those scopes
must retire first; they are not
implicitly checkpointed. The same fence guards shared-capacity setup admissions,
native request admission and scheduled main-agent dispatch. It adds no queue or
new tracker status and startup does not clear it or launch work.

Each task retains `metadata_json.delivery.upgrade_checkpoint`, version 1:

- Upgrade, original handoff/admission/job, run/controller/step and prior delivery
  status; digest of frozen settings, project directory and task-only MCP route.
- Ticket/attempt/phase and candidate references into existing ticket evidence.
  Original native session snapshots, launch settings, selected/launched sources,
  repair counts and attempts remain with that evidence, without a copied queue.
- Exact invocation refs and observed process/reader retirement, callback slot
  acknowledgment, plus the post-retirement source fingerprint using the existing
  redaction-safe format. A non-Git/unborn source has no Git fingerprint.
  `launch_baseline_clean` records actual launch evidence for later dirty-work
  attribution; a missing legacy value never grants implicit adoption.
  `native_never_spawned` records exact handle prevention; a first native launch
  prevented before spawn has no session to preserve. It never permits replacing
  a missing historical/resumed session.
- `status` and `issues`: `quiesced` is confirmed retirement, `held` is uncertain,
  and `settled` preserves an intervening terminal lifecycle winner. A null phase
  means no interrupted native invocation; continuation must resolve the existing
  evidence cursor rather than invent a delivery phase.

Preparation fences process spawn and late native acceptance without aborting or
reopening anything. Already-accepted terminal results win. Returns after the
fence retain observations with `upgrade_interruption`, not cancellation or a new
validated outcome. Only exact registered process handles are stopped; native
exit alone is insufficient while the controller callback retains its slot.
Unknown native outcomes, missing sessions, unconfirmed retirement or changed
frozen settings produce a 409 with durable held state and repair refs. A repeated
successful request returns the same receipt; held retries require the current
revision and never infer retirement from a missing handle. Delayed callback
registration acknowledges the original fence before its slot can retire.

Preparation also checks persisted native request ownership, including failed
terminal persistence and unobserved exits after registry cleanup. A missing
registry entry or terminal request status never proves retirement. Such requests
refuse preparation before interruption with request/invocation refs and repair
context. Native cleanup checks its exact owned process group on every managed
return, including returned adapter errors as well as raised exceptions. It stops
only its owned process group when exit is unknown or unconfirmed; an
unconfirmed group retains its exact handle in the existing registry after readers
retire. Existing cancellation/shutdown owners can retry that handle, but observed
process termination never fabricates a missing native outcome. Pre-existing
deferred/skipped work is terminal under the canonical tracker rules and is not
captured; terminal winners during preparation are `settled`, never resumable.

Preparation is independently usable for inspection and quiescence. It neither
installs a target nor resumes work. The continuation owner must verify target,
health/doctor, current authority, exact frozen settings/session/candidate/source
and the original checkpoint before rejoining the existing queue. A new daemon
generation or an arbitrary restart is not upgrade authorization.

### Managed upgrade continuation

`maintenance.finishUpgrade` is the same installation-admin REST/CLI boundary as
preparation. It is unavailable to project MCP agents, console tokens and workflow
grants. Supply `upgrade_id`, the declared `target_identity`, `expected_revision`,
the target's `daemon_generation` from `maintenance.upgradeStatus.runtime`, and
`doctor_exit_code` plus `doctor_evidence_ref` from the installer/operator's actual
target doctor command. The daemon checks its own database and running scheduler;
the caller cannot assert health. A nonzero doctor exit, wrong target or stale
generation returns structured 409 repair context without releasing the fence.
The doctor receipt is a local-admin attestation; it is not a daemon-executed doctor.

`runtime.running_identity` is frozen at daemon startup. Packaged identity is
`<version>:build-info:sha256:<build-info digest>` using the existing package
identity reader. Clone/unbundled identity is
`<version>:python-source:sha256:<digest>` over sorted package-relative Python
paths and their content hashes. Select the intended target identity from the
staged target before preparation; an arbitrary build label cannot pass finish.
These identities describe runtime inputs, not signing, publication or installed
desktop activation. Startup identifies the generation and leaves all checkpoints
fenced. The upgrade row retains successful postflight even when it has no tasks.

Finish compares each original handoff, project/workspace, retirement, frozen
settings and task-only route bytes, current contract/grants/approvals, native
evidence and source fingerprint. It atomically consumes safe checkpoints into
the existing metadata queue with a new callback identity and the target daemon's
admission identity. The old callback cannot launch, accept results or release the
new slot. The existing project capacity and directory owners still decide when
queued work runs. Unsafe checkpoints remain held with per-checkpoint issues;
`partial` releases the installation-wide fence for safe/unrelated work while
retaining those holds. Retrying partial finish requires its current revision and
rechecks only unconsumed checkpoints. Terminal lifecycle winners are settled.
No stopped/failed/aborted/deferred/manual work is implicitly admitted.

An interrupted invocation resumes its exact delivery, repair, review or workflow
mirror phase with the original native session, candidate, attempt and repair
count. Its interrupted observation is retained in `upgrade_interruptions` before
the current receipt is replaced. Accepted delivery is retained during review
resume. A null phase resolves accepted delivery to its first review, or lets the
normal selector choose work if no invocation began. A proven first spawn that
was prevented can launch its original selection; missing historical sessions
cannot. Dirty Git work needs the unchanged retirement fingerprint, an original
delivery session and a proven clean launch baseline. Its existing owned-work
receipt uses `source=upgrade_checkpoint`; review requires its clean candidate.
Non-Git/unborn work retains the existing absence of an optional Git fingerprint.

The runner rechecks a queued continuation before native launch. Drift holds the
checkpoint and stops that handoff with repair context, preserving ticket and
native evidence. Consumption records `continuation_dispatch_started=false` in
the existing checkpoint; slot reservation sets it to true atomically before job
registration can start. Status exposes `continuation_state` (`pending`,
`dispatched`, `settled` or legacy `unconfirmed`) and `continuation_generation` on
consumed checkpoints. `recovery_required` identifies unresolved holds and
unfinished continuations from a different current daemon generation. A stored
`continued` result alone is not proof that dispatch happened. Queue reservation
can advance the observed continuation state without changing the upgrade revision;
pending work in the current generation still waits on the ordinary queue limits.

Repeated finish does not consume a checkpoint twice. A same-generation retry
asks the existing queue to drain. After generation loss, explicit finish of the
original upgrade requires current target postflight and revision, and revalidates
the exact pending handoff, cursor, authority, source, native evidence and original
retirement. Only a proven never-dispatched continuation is rebound to the current
generation with a fresh callback identity. Accepted work, sessions, phase,
candidate, attempts and repair counts remain unchanged. Drift or missing legacy
dispatch evidence returns a structured hold. A prior dispatch reservation is
conservative evidence that dispatch may have begun: unfinished work from that
generation requires explicit delivery-owner reconciliation, never automatic
replay through the old upgrade. Settled work cannot be replayed. Transaction
failure rolls back admission. Startup alone does not re-admit work or replace
native sessions. Desktop/CLI update callers must explicitly run
the prepare/target-postflight/finish protocol; this operation adds no installer.

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
preparing a task's self-contained tickets and dependencies. Backlog is
unscheduled; Todo placement alone grants no execution authority. Only a prepared,
explicitly enabled automatic handoff enters the queue. Fresh ordinary work must
be Todo before the handoff writes queued metadata; neither that metadata nor a
manually active `in-progress` task manufactures eligibility. A valid running
workflow entry step permits its first handoff without resetting lifecycle or
invocation history. Queued work before its first actual invocation projects to
Todo, even if its workflow is already started; already-invoked work between calls
stays In progress. Stopped work requires explicit authorized recovery and
rehandoff, preserving sessions, receipts, attempts and consumed repair allowance.
Deferred work keeps its named resume condition and explicit resume owner. The
existing terminal-managed path continues to use the normal tracker operations.

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
`projectAgent.*`; generic presets are not the live project list. **Role defaults**
on that same screen configures delivery, review and main launch choices. Its typed
`role_patch: {role, settings}` goes through `tracker.configureDelivery` with the
exact bound `project_dir` and `expected_revision` from `tracker.get`. Omitted
fields are preserved; explicit null removes an optional choice to inherit. The
writer merges one role, preserves unrelated defaults, rejects stale/foreign
drafts, and returns saved plus effective readback. `preview: true` uses the same
launch resolver without saving. Codex roles select native project source refs;
other hosts edit their supported inline names/instructions. Main instructions
remain owned by the setup or automation contract. Source model/effort takes
precedence over fallback fields; the console labels the effective saved values.
Failed saves retain drafts and require an explicit reload/reconciliation. Source
editing, role saves and adoption never call `tracker.startDelivery`; active and
resumed snapshots stay frozen. Source adoption also retains the explicit
preview/write/readback contract below.

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
run/template snapshots stay unchanged. Installed application/runtime replacement is separately
authorized and main-owned, coordinated with the active runtime owner. Workflow/default
activation explicitly included in the current outcome must finish there rather than
being deferred until closure. Adoption does not authorize
restarting the daemon orchestrating the active task; source checks do not prove
saved adoption, native consumption or installed activation.

For ready-to-use Setup/Update, the effective workflow method first reuses actual
completed delivery/review/closeout evidence only for covered claims at the current
project/source/default/native/environment basis. Record covered/missing claims and
run only missing or invalidated harmless bounded checks in the existing owning
setup/task. Managed work uses its admitted `core.project-setup` run, with normal
bounded children only for selected distinct missing role checks. Do not create a
competing proof workflow or nested queue/wait/drain machinery. Existing
product/adaptation tasks retain independent review; standalone setup follows its effective method and genuine evidence.
Aborted, incomplete, simulated or another project's evidence is not completed
activation. Task overrides cannot hide missing project defaults. Explicit
infrastructure-only scope has one preparation mirror; `ready_to_use` adds activation
verification. Existing-agent reporting retains its external session owner. Missing
authority or required proof stays unfinished; source edits neither adopt defaults nor refresh
active native sessions.

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
    "model": "gpt-6.2-sol"
  }
}
```

The workspace bridge supplies `project_id`; REST and CLI callers supply it
explicitly. Fresh Codex admission requires an exact project `source_ref`; legacy
inline Codex choices return `adoption_required`. Other hosts retain intentional
inline contracts. `agents` maps arbitrary safe role keys to selected definitions
and execution fallback choices. Task overrides merge field by field within each role;
an explicit null clears an optional role selection
(model/model_reasoning_effort/provider/profile/host),
while a resolved name or instructions cannot be missing, null or empty.
Source-native model/effort and supported reduction-only feature/skill settings win.
Omitted values inherit task choices, project choices, then the native host. Source
model/effort therefore wins even over an explicit null in a task fallback. To
remove a source override, explicitly edit that source; null in a source patch
removes the field, while omission preserves it. No missing effort is filled with
a guessed host default. Capability-supported effort reaches both delivery and
independent review command arguments and their retained native session snapshots.
Source fields cannot replace provider/profile, permissions, sandbox or MCP routes;
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
Ordinary ticket independence compares the selected implementation executor's
native host/name identity with `review_agent`. A workflow role may reference that
reviewer as an alias without becoming another implementation worker or launching
an extra agent. Assigning the alias to an ordinary implementation ticket still
rejects self-review. Multiple applicable declared roles still require an explicit
assignment, even when their selected definitions match.
An already admitted inline Codex task stopped across the source-adoption upgrade
retains its saved agents and assignments on unchanged explicit rehandoff, including
work that has not launched yet. Current defaults cannot replace that selection;
changed host, agent settings or assignments reject. Existing workspace, MCP-route,
host-preflight and native-session identity guards still apply. This exception does
not admit a new inline Codex task or refresh its prior receipts or repair count.
Each role may override `host_key`, `model`, `model_reasoning_effort`, `provider`, and
`profile` where the host capability catalog permits. The merged role host overrides the task host, then the project host,
then `codex`. Clearing a role host uses that task/project host chain.
Omitting the model preserves native host settings. Explicit selections reach
native CLI arguments and launch evidence; StackOS never substitutes another
host or model after a failure. Saved Hermes provider/model/profile selections
remain inspectable, but managed execution is unavailable because its gateway work
outlives the CLI viewer. New handoffs reject
before submission; saved and historical settings are preserved without substitution.
Codex also supports its configured `model_provider` selector. Other hosts reject
`provider`; their existing native authentication/configuration remains in control.

Role choices also accept optional `execution_requirements: {"filesystem_write": true}`
in saved defaults and task overrides. This declares that verification needs ordinary
filesystem outputs; it grants no permission or approval. Unknown requirement keys
and non-boolean values reject. Omission, null, or explicit `false` allows an
inspection-only assignment. The object follows existing role-field inheritance;
an explicit task object replaces the saved requirement object while preserving
unrelated model, effort, provider and profile choices.

Admission compares those current requirements with the effective mode used by
native launch, including Codex source narrowing. On rehandoff it checks each
pending ticket's retained phase snapshot before scheduling dependent delivery,
even when that older snapshot has no requirements field. A compatible frozen
session remains frozen after source edits. The runner repeats the check before
delivery and each phase launch. A write requirement against actual read-only
execution returns `execution_requirement_mismatch`, naming the role, phase,
required capability, effective mode, selected source/settings and retained
source/settings where applicable, with recovery context. It neither parses command
prose nor broadens authority. A compatible mode still uses normal native approvals;
an actual denial remains a boundary and source edits cannot refresh a retained session.

For a workflow task, pass `run_plan_id` and `step_id` together with the
matching `task_key` (`workflow-<run_plan_id>`). The plan must be started and
the entry step running. `step_id` is entry context; automation traverses the
whole task by canonical dependencies, including nested groups and fork/join.
Groups activate and complete structurally and never launch agents. A dependency
on a containing group activates its children while the group is running, using
the same `activates` semantics as workflow parents.

Mirrors claim their bound step through `RunPlanRepository`, wait for accepted
descendant/bound-child work or valid explicit exclusions, then launch their
remaining frozen contract once.
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

Each launch supplies and records an `occurrence` beside the role's retained
instructions: attempt, consumed repair count, candidate, task/ticket contract
revisions, context digests, selected source, launched source and explicit resume
thread ID. Context digests bind the canonical task/ticket `context_json` at
launch; raw `tracker.brief` and tracker revisions retain the source context,
while the prompt projection omits nonactionable notes. Digests do not establish
approval. The selected source
is the current admission choice; the launched source is the frozen role snapshot
used by this native session. They can differ after a source edit and explicit
rehandoff. Neither identifies an installed runtime upgrade.

Read the occurrence with current task/ticket context and operator amendments
before interpreting historical prompts or frozen step inputs. An authorized
delivery amendment can supersede the same scope's earlier preparation-only
hold while preserving that history. Required builds, tests and UI proof remain
required. A named pointer drag/drop or autoscroll manual exception covers only
that proof; it does not waive other browser evidence. A separately authorized
server OS-return transport simulation is likewise confined to its named scope.
Use the assigned StackOS browser route; these exceptions do not authorize
Computer Use, machine sleep, installation or deployment. Unresolved authority
contradictions stop the affected action for main reconciliation.

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
directories can execute concurrently, within this local daemon's persisted task
capacity for each project (default 3, supported range 1..32). There is no aggregate
global project-task cap. Task jobs use bounded per-project `task-delivery:<project_id>`
executors; backup and other `long` jobs do not consume task slots. Host child/reviewer
slots and the terminal-managed default of three active ticket assignments are
separate from automatic project task limits.
Tickets and delivery/review calls within
one task remain serial until all task work completes or execution stops:

```text
ordinary: ready -> delivery -> independent review -> success -> complete
                         -> scoped repair -> same delivery/review sessions
                            (up to three ordinary repair passes)
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

### Project task capacity

Managed project Setup/Update uses real `core.project-setup` runs and mirrored tasks,
admitted through normal task delivery and the project's capacity preference.
Queueing, callback retirement, cancellation and usage have the same owners as
other tracked work. The protected setup admission row is not a second slot.
Genuine legacy request-owned setup still holds capacity/directory ownership until
retirement is established; an elapsed timeout is insufficient. Completed setup
receipts and possible-edit evidence survive cancellation. Setup grants no business
workflow execution authority.

`TaskTracker.config_json.delivery.main_agent` stores typed project launch choices
(`host_key`, `model`, `model_reasoning_effort`, `provider`, `profile`)
independently of specialist contracts.
Authenticated local-admin REST `tracker.configureDelivery` accepts `main_agent`
alone to merge those choices without replacing other defaults. This affects future
managed setup and scheduled main-agent admissions; queued/running native snapshots
and setup continuations remain frozen. Setup and automation reuse
`project_setup_execution.resolve_preferences`: omitted launch fields inherit saved
main choices, explicit null clears a choice for that launch, and an explicit host
change does not carry another host's saved model/effort/provider/profile. Scheduled
host and validated MCP-route constraints remain authoritative. Each fresh schedule
occurrence reads current preferences; an already admitted occurrence keeps its
snapshot and is not replayed. This introduces no retry/resume lifecycle. Model choices
and the optional recommendation come from the host capability catalog. An explicit
unavailable host or invalid choice rejects without substitution.

`tracker.deliveryCapacity` reads `scope: "project"`, supported bounds and a
`projects` array. Each row contains `project_id`, `project_name`, `project_slug`,
effective `configured_limit`, admitted `running_count` and waiting `queued_count`.
An optional `project_id` filters the read to the current project; omission reads
all projects. `tracker.setDeliveryCapacity` atomically saves 1..100 unique project
updates, for example `{"updates": [{"project_id": 1, "max_concurrent_tasks": 6}]}`,
and returns the all-project readback in its write envelope. Each limit is an integer
from 1 through 32. Both are local-admin REST/CLI operations; ordinary MCP and
workflow/run-token authority cannot use them.
For example: `stackos ops call tracker.setDeliveryCapacity --input capacity.json`.

Overview exposes all project rows through the shared **Task queues** control;
project Home and Board show only their current project. Rows show Project,
Running, Waiting and Run at once. Edit limits with the inputs or steppers, then
Save changes. The effective values update from server
readback; a failed/invalid save keeps the draft and error. Cancel, Escape or
outside dismissal discards unsaved edits. A limit below the running count leaves
current work running and delays new admission. Opening the control, refreshing,
or changing projects only reads configuration.

One row per permanent project ID in the existing data-directory database owns
each limit. Migration preserves the former saved value for existing projects;
new projects default to 3. Reads do not create preference rows. Limits survive
ordinary restart/install/upgrade and are covered by the existing encrypted
database backup/export; there is no separate
preference file or backup path. Different data directories remain independent.
This is not account isolation for people sharing one daemon or a cross-device quota.

A successful save commits before admission changes. A failed commit leaves the
previous limits effective. Raising drains existing queued task metadata;
lowering affects future admissions only. Every admitted/running task continues
unchanged, even above the new limit; admission waits for room. Capacity edits
never stop, abort, cancel, restart or evict work and do not affect other projects.
`running_count` includes admitted callbacks starting or retiring, so it can exceed
a newly lowered limit. Backup workers and tasks merely waiting for a slot are
excluded. The existing launch lock serializes slot reservations across projects.
No priority policy is added; native sessions, candidate evidence and repair
allowance are retained.
Abort keeps its reservation until native termination/prevention is confirmed or
the original callback retires. Queue registration failures cannot skip native
cancellation. A stopped handoff with an outstanding reservation in this daemon
cannot be replaced or share its working directory; inspect its delivery state
and retry after that reservation is released. Retired handoffs release only
their own reservation, so a late return cannot release a replacement's slot.
Historical handoffs without continuation intent retain explicit recovery/rehandoff
requirements. Newly authorized handoffs follow the bounded continuation below.

### Ordinary restart and missed launch continuation

New `tracker.startDelivery` handoffs persist versioned continuation intent in the
existing delivery metadata. Each native phase retains its frozen settings,
authority and source fingerprint, attempt, candidate, and observed native session.
The existing invocation receipt records the actual owned PID, process group and
process start identity before consuming native output; it contains no argv or
environment. No historical admission or stopped/failed task is migrated.

The existing scheduler startup entrypoint considers eligible prior-generation
queued/running handoffs once. A missed queued launch after sleep uses the same
validation. Valid unstarted work re-enters the existing queue. An interrupted
phase requires its exact retained session and read-only confirmation that both
its original process and group are absent. A surviving descendant, reused PID,
unknown owner, missing session, changed frozen settings/authority or source,
unresolved result/failure, denial, exhausted repair allowance, Stop or abort
holds the work with a reason. Recovery never signals a reconstructed PID.
Uncertain owners retain capacity and directory exclusion until reconciled.

Continuation rotates the handoff/callback fence before registration, preserving
original refs in metadata and interrupted receipts. Repeated startup or an old
missed-job notification cannot dispatch that cursor twice; late callbacks cannot
replace the new decision. Project capacity, prerequisites, accepted delivery,
phase/session and consumed repairs remain authoritative. Review continuation
does not replay delivery. Attributed dirty delivery work may resume from its
recorded clean launch baseline; unattributed source changes hold.

This is bounded admission at existing entrypoints, without a watchdog, polling
loop, new scheduler/table or retry-all-failures policy. Completed scheduled
occurrences are not replayed. Managed-upgrade checkpoints and their explicit
finish/postflight protocol retain their own authority and retry refs. General
stopped-work recovery still requires explicit authorization and rehandoff.

### Fresh assignments and exact recovery

Native preparation-context reuse remains unsupported. New assignments use the
existing compact fresh launch, and independent review receives no delivery
history. Recovery retains the exact native session; missing identity, denial,
partial execution or failed resume never triggers a fresh retry.

### Task-local native tool routing

For a task in a separate working tree, pass optional task-only
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
before native execution.

The shared host capability catalog declares `mcp_routing`: `per-call` for
Codex/Claude Code, `workspace` for Antigravity/Grok, or `unsupported` for
Hermes and MCP-only hosts such as Claude Desktop. This declares a
supported configuration mechanism; it does not assert a successful connection.
Claude translates the same fragment over existing effective stdio entries into
`--mcp-config` with `--strict-mcp-config`, for fresh and exact resumed calls.
Its invocation-scoped `--system-prompt-snapshot off` applies the supplied role
instructions on exact resume while `--append-system-prompt` retains the native
default prompt. This does not change the selected frozen assignment settings.
It retains commands and existing environment references, rejects unsafe entries
instead of copying credentials, and preserves native permission rules, including
disabled project servers. Worktree checks include the common repository's native
project disable settings.

Antigravity reads its native `.agents/mcp_config.json` workspace overlay; Grok
reads `.grok/config.toml`.
Their fragment is an expectation to compare against native configuration, never
an arbitrary external override. Different route leaves reject before queueing or
spawn with an actionable unsupported-override reason. StackOS never rewrites
these files, replaces their executables, or changes global settings/trust.
Fresh non-Codex admissions also verify their native route when no fragment was
provided. Native child processes receive the selected project directory through
invocation-local `STACKOS_WORKSPACE_ROOT`, so ordinary registered bridges need no
global project pin. Explicit server root overrides must still match that directory.
Required StackOS tooling, host runtime, daemon context, actual root and
the existing project binding must agree; supplied Serena routes must target the
same root through the canonical wrapper. The admitted native route fingerprint
is frozen in existing task/session receipts, rechecked before fresh/resumed
launches and retained on rehandoff. A changed route or project binding stops the
affected launch. Legacy admitted sessions retain their original contract.

Only canonical path/digest identity and native route fingerprints enter snapshots, session
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

Current stops may include one optional `metadata_json.delivery.stop_context`.
`TaskDeliveryStateOut` projects it in full tracker, lightweight board/active
task reads and existing task activity revisions. Legacy `reason` remains
readable. Known producers supply a mechanical `code`, optional delivery/review
`phase`, at most four no-ready `facts` and eight scoped `evidence_refs`, and
known `next_owner`, `next_action` and `resume_condition`. These are closed
mechanical values, not a classifier of agent prose or a project root cause.
Unknown exceptions remain `unknown`; arbitrary exception data and rejected
reports never enter this object. Malformed optional context falls back to the
legacy reason. Public projections allowlist fields and bounded ticket/attempt
references (`tracker-ticket:<project-id>:<ticket-id>[:attempt:<n>:<phase>]`),
excluding native paths, frozen instructions and arbitrary metadata.
Stop reasons retain legacy prose rendering, with secret redaction, local-path
masking and a 500-character public bound. Local POSIX paths, file URIs (including
localhost/host forms), Windows drive paths with either separator and UNC paths
are masked in public readers and the retained UI fallback, including
colon-delimited file/drive diagnostics and complete quoted paths with spaces.
Ordinary HTTP(S) links remain readable. Raw historical metadata is not rewritten
by these reads.

The no-ready producer reads current explicit blockers, unresolved dependencies,
non-pending unfinished work and containment state through their existing
owners. Explanation does not requeue, accept, wake another project or change
readiness. Current context is cleared on queued/running/complete and replaced
on every new stop; existing revisions retain historical facts. The existing
stop details show concise cause, responsible next action, resume condition and
evidence references, using current task/ticket navigation without recovery
controls. Inspect retained ticket evidence before explicit authorized recovery.

A successful workflow report rejected by `runPlan.recordStep` may receive one
report-only continuation in the same retained native role session. Eligibility
requires exit zero, a valid outer envelope, one strictly parsed inner JSON object,
valid frozen output/proof contracts, settled actions, an intact candidate and
complete trusted key-name-only diagnostics. Duplicate JSON keys, non-JSON numbers,
trailing content, value-pattern findings and incomplete/unknown classification
remain failures. Key-only classification never establishes that content is safe.

Before consuming the allowance, reporting prepares a usable comparison basis
through the existing admitted workspace semantics. A committed Git workspace
retains its candidate and staged, unstaged and untracked work fingerprints.
Admitted non-Git and unborn workspaces need no synthetic commit or filesystem
snapshot; current run, ticket, authority, candidate and session comparisons still
apply. Failed preparation stops without spending the opportunity.
The existing `automatic_delivery.reporting_corrections` receipt then durably
consumes the allowance before continuation and retains the safe original invocation
receipt and diagnostic context, omitting the rejected body. The producer may rename
rejected descriptive labels only; status, summary, candidate, values, proof refs,
followups and limits must remain identical. The prompt prohibits tools, actions,
tests, code changes and lifecycle writes. Runtime does not rewrite the report or
permit credential relabeling to bypass the guard. This is a cooperative reporting
contract, not a new host sandbox or a universal guarantee of model obedience.

The corrected return passes strict parsing, schema, secret, native identity,
candidate and current lifecycle/authority checks again. Applicable Git work
fingerprints and assignment state must remain unchanged, including the absence
of a committed baseline when none was admitted. Only the existing run-plan owner
accepts the result; accepted predecessors and ordinary `repair_count` remain
intact. Original and corrected safe receipts retain their invocation identities.
A second invalid return stops. Restart or explicit rehandoff retains the consumed
allowance and stops unresolved reporting recovery before a broad work assignment;
it cannot mint another correction. Duplicate/late predecessor callbacks cannot own the
continuation receipt. Missing retained comparison evidence requires explicit
reconciliation rather than reexecution of accepted work.

The canonical receipt reader includes each reporting invocation once, deduplicating
current and archived copies. Continuation normalization reads the original session
counter baseline through that reader. Existing statistics, lightweight usage and
duration projections consume the same inventory, including failed continuations.

For Codex, `thread.started` commits the exact native ID and frozen role identity
into `automatic_delivery.sessions[phase]` before native exit. The write requires
the current handoff, ticket, attempt, phase and invocation receipt; stale or
terminal receipts cannot be replaced. Persistence runs outside the invocation
registry lock and emits no extra start event. A failed identity commit stops the
native process and remains failed proof. Explicit recovery reuses this session;
early identity alone grants no continuation authority. New handoffs also require
the [ordinary continuation intent](#ordinary-restart-and-missed-launch-continuation).
Hosts without an early identity event
retain their existing terminal session handling.

After native, session, schema and applicable candidate validation, an ordinary
delivery or review `blocked` result keeps the ticket `in-progress`, with a safe
summary (or a phase-specific reason for a blank summary) in `blocker_reason`
and `outcome` and no terminal
`completed_at`. Its receipt retains `validated_outcome=blocked`, outputs,
candidate, followups, attempts, native sessions and consumed repair allowance;
task delivery stops. Existing blocked counts and Work/detail views read these
canonical ticket fields. Native, malformed-result, candidate-integrity and
explicit `failed` outcomes retain failure behavior; exhausted ordinary repair
allowance still stops delivery. Historical attempts are not rewritten.

There is no automatic retry or blocker clearing. Main must resolve the actual
prerequisite, explicitly clear `blocker_reason` through `tracker.updateTicket`,
requeue the ordinary ticket as `not-started`, then explicitly hand off delivery
again. Neither an unresolved blocker nor an `in-progress` ordinary ticket can
launch. This resumes the saved role sessions and preserves the counted repair
pass; an interrupted repair does not consume another pass merely on requeue.

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
delivery. Each source-repair pass requires a separate new descendant commit,
then the same reviewer rechecks that exact SHA. The runner increments the durable count before
launching a repair; explicit requeue preserves it and cannot bypass the limit.
Legacy evidence with `repair_pass_used=true` and no count means one consumed
repair, not a fresh allowance. Rejection after the third repair stops the task.
This counts ordinary review rejections. An integration-origin correction after
child approval preserves that count and may requeue at `repair_count=3`.
Its corrected descendant still needs independent review; a subsequent ordinary
rejection at three permits no fourth ordinary repair. This is not a total cap
on corrections from all origins and does not reset sessions or history.
An explicitly admitted evidence-only correction can retain the unchanged clean
candidate, including after one or three consumed repairs. Before requeue, the
authorized correction owner merges `context_json.correction` with typed
`work_intent: "evidence_only"` (or `"source_repair"`), the full lowercase
`candidate_commit_sha`, and a positive integer `target_attempt` equal to the
ticket's next automatic attempt. Preserve source/check/outcome evidence and
all existing context, sessions, attempts and counters. `tracker.updateTicket`
and the other ticket mutation paths validate this record through the repository;
MCP, REST and CLI share that contract. This field adds no mutation authority.
The intent is captured in each launch's `occurrence.correction_intent`. Only
the matching attempt may use it, and the admitted SHA must match both its
observed base and the immediately preceding delivered candidate. Evidence-only
consistency is checked before native invocation, launched-phase persistence or
charging the next ordinary repair pass. Missing prior delivered SHA or a
current-target SHA/base mismatch stops admission without creating a native
invocation or session; retained receipts and consumed repair allowance survive.
The prior delivered candidate need not have been approved. Stale target intent
does not authorize evidence-only reuse and retains the conservative descendant
rules rather than adding an unconditional admission rejection. Final validation
still rejects candidate or intent mutation after admission. Evidence-only
delivery and independent review must return that same clean current HEAD.
Changed source requires explicit source-repair admission and a descendant
commit. A changed correction during proof invalidates the observation. Current
independent review remains mandatory; old approval never supplies missing proof.
Missing/legacy/prose intent and intent targeting another attempt retain the
conservative descendant rules. Neither a result string, zero diff, historical
repair count nor a no-op commit selects evidence-only work. An ordinary review
rejection still consumes the existing allowance; the one-attempt intent cannot
carry into the next repair. No sessions or repair allowances are reset.
An earlier source-change obligation also survives a successful proof-only
occurrence, so a later ambiguous correction cannot use that approval to bypass
descendant validation even when no ordinary repair has been consumed.
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

### Diagnosing interrupted delivery

#### Explicit review-only replacement

After diagnosis and explicit operator authorization, call the existing
`tracker.startDelivery` operation with `replace_review` to replace one
quiescent stopped ordinary review. The operation retains normal tracker-write
authority; the reason records the authorization context and grants no permissions.
It does not enable browser-console writes. Ordinary rehandoff keeps its existing
session behavior and never implicitly selects this recovery.

```json
{
  "project_id": 1,
  "task_key": "prepared-task",
  "project_dir": "/path/to/clean-candidate",
  "replace_review": {
    "ticket_key": "implementation",
    "expected_attempt": 1,
    "expected_review_thread_id": "exact-stopped-native-thread",
    "expected_candidate_commit_sha": "<40 lowercase hex>",
    "expected_reviewer_snapshot_digest": "<64 lowercase hex>",
    "reason": "Operator authorized replacement after the recorded capability diagnosis"
  }
}
```

The snapshot digest is SHA256 of the current canonical resolved `review_agent`
object encoded by `json.dumps(snapshot, sort_keys=True)`. It pins current saved
selection and source resolution, including inline-host settings; it does not
invent a source file for inline hosts. Do not supply raw `agents`, `review_agent`,
host/settings or assignment overrides alongside recovery. Current saved defaults
select the reviewer. Optional
`reviewer_source: {binding_id, source_ref, expected_digest}` selects an existing
approved source from a directory already bound to the same project. Existing
registration, no-follow, path and source-setting checks apply. This reads execution
configuration only: the task directory and immutable task-only MCP route stay
retained, and reviewer host/provider/profile and normal approvals are preserved.
No bootstrap, rebind, default adoption or permission expansion occurs.

The task must be stopped with no outstanding callback/capacity or native
invocation owner. The ordinary child must have a successful candidate-ready
delivery, accepted prerequisites and a known failed, blocked or interrupted
review/session. Attempt, thread, resolved snapshot and delivered SHA must match.
Accepted reviews, ordinary repair rejections, mirrors, unknown identities, live
work and foreign or stale targets reject before mutation with structured repair
context. Git HEAD must remain clean and equal to the exact delivered candidate.
Editing tracked reviewer configuration changes that candidate; use separately
admitted execution configuration or normally admit a new complete candidate.

Admission retains the retired session/snapshot and reason in
`completion_evidence_json.automatic_delivery.review_replacements`. Its
`target.expected_attempt` references the complete retired review and candidate in
`automatic_delivery.attempts`, without duplicating those payloads. It preserves
the successful delivery and queues one explicit review-only transition.
The replacement uses a distinct fresh native session, persisted as
soon as announced. Delivery is not invoked again and consumed `repair_count`
is unchanged, including at three. Replaying the same request returns the same
recovery refs; changed intent against that retired target conflicts. Retired
callbacks/results cannot overwrite or advance the new handoff. Replacement
failure stays stopped with its evidence; a real repair rejection still obeys the
retained repair limit. If the controller is interrupted before a terminal review
result is saved, the admitted review-only continuation remains recorded. After
explicit stopped-work resolution, ticket requeue and rehandoff, it resumes the
announced replacement session directly on the same clean candidate. It retains
the interrupted receipt in attempt history and never invokes delivery or consumes
another repair pass. Missing session identity or a changed candidate stops the
continuation; request replay alone does not schedule it. A terminal review result
ends this continuation and retains normal rejection/repair rules.
`tracker.get`/`tracker.brief` raw inspection shows both
retired and current review truth; statistics count the retained delivery once.
This source behavior does not establish installed or live recovery proof.

Retain the ticket, attempt, native thread/invocation and request IDs that are
actually available, the candidate, launch/exit times and their time zones, the
initiating observation, and the next action's owner. Mark missing history
explicitly. A stopped task or invalid final result alone does not identify why
the native call ended; an unexplained signal does not establish cancellation.
Keep the initiating cause separate from a later result-validation or
error-persistence failure, while preserving credential sanitization.
Positively identified managed Codex cancellation retains its native exit,
session and usage evidence without an extra invalid-result diagnostic solely
because the final file is absent. A present malformed final, initiating stream
or process failure, unresolved native error or failed turn still retains its
failure diagnostics, including when cleanup sets a cancellation flag.

| Observed boundary | Meaning and safe recovery |
| --- | --- |
| Explicit operator cancellation or managed cancellation | Preserve the cancellation reason and observed termination separately. Quiesce only owned processes and fence late results. Cancellation supplies no authority to resume. Main reconciles the current scope, blockers and retained sessions before an explicitly authorized rehandoff. |
| Approval review transport error, including `400 Bad Request` | A failed review request is not an unsafe-action verdict. Retain the exact safe error and request identity; distinguish a receipt explicitly saying the action was not executed from unknown execution. If effects are uncertain, read authoritative target state before considering a retry. A permitted retry uses normal approval review after reconciliation; changing tools to evade review is not recovery. |
| Actual approval denial | Stop the denied action and dependent work. Continue unaffected authorized checks; do not retry the denied action through another route. |
| Required access or capability unavailable | Retain the precise missing boundary and owner as blocked proof. Another host, model or fresh session cannot silently replace the assigned execution. |
| Suspected sleep/wake or lost runtime connection | Correlate existing OS suspend/wake observations with the affected runtime and attempt. Token silence, a heartbeat gap or a stopped label alone proves neither sleep nor a hang. Reconnect and controlled-clock tests prove only their exercised recovery boundary, not physical sleep effectiveness. |

Before retrying an uncertain write, inspect the owning repository/provider's
authoritative record and its operation-specific idempotency receipt. An absent
acknowledgment is not evidence of no effect. If readback cannot settle the
outcome, keep the write unresolved rather than risking a duplicate side effect.
See [bridge replay boundaries](./bridge-failure-diagnostics.md#narrowed-replay-policy).

Use existing physical observations without suspending or restarting a shared
machine for diagnosis. When upstream or physical history cannot establish a
cause, return a bounded diagnosis with known facts, missing evidence, the safe
recovery rule, and a named followup owner and condition. Do not invent a runtime
fix or claim a physical pass. Main owns any later authorized physical exercise
and installed activation; source tests do not update the running daemon.

### Native usage receipts

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

Managed upgrade, account-switch and ordinary runtime continuation retain
`automatic_delivery.upgrade_interruptions`, `account_interruptions` and
`runtime_interruptions` observations. Those invocations keep their original
attempt and phase. The shared
receipt inventory includes each distinct interrupted invocation once alongside
the resumed invocation; duplicate copies of an already current/archived invocation
do not add work. The existing derived statistics index uses `observation_key` to
distinguish these records, using the actual invocation ID when available and a
source-list locator for incomplete observations. A locator never supplies a native
ID, missing time or usage. Ordinary current/archive precedence remains unchanged.
Known native start times order retained observations across interruption modes so
resume selects the latest preceding invocation. Missing times stay missing and
retain stable legacy observation order.
The additive index migration rebuilds derived rows from canonical ticket evidence
without rewriting attempts, sessions, candidates, usage or repair allowance. A
downgrade refuses while distinct retained observations require the newer reader.

Duration, task/ticket statistics, dated reports and delivery trends consume that
same inventory. Interrupted outcomes remain unclassified rather than becoming a
quality decision. Resumed usage uses the immediately preceding receipt of the
phase, including its latest interrupted observation. The shared usage normalizer
still requires the same native session/unit/scope/basis; an intervening missing
snapshot blocks subtraction from an older attempt. Repeating continuation or
projection rebuild adds neither another invocation nor its measured usage twice.

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

Current native usage contracts (and retained historical Gemini provenance):

| Host / scope | Native basis and arithmetic |
| --- | --- |
| Codex / `codex.turn` | Native `exec --json` emits cumulative thread usage on `turn.completed`, verified with CLI 0.167.0 fresh/resume captures and the [native producer](https://github.com/openai/codex/blob/rust-v0.167.0/codex-rs/exec/src/event_processor_with_jsonl_output.rs) (`usage_from_last_total`). Normalize as session snapshots: 18442 then 36925 contribute 18442 then 18483, totaling 36925. Input plus output gives total; cached input/cache writes and reasoning output are already included subsets. `last_token_usage` covers only the latest model request and is not a substitute for a multi-request invocation. No historical version boundary is inferred from the captures. |
| Claude Code / `claude.model-usage.v2.1` | The [Agent SDK cost-tracking contract](https://code.claude.com/docs/en/agent-sdk/cost-tracking#accumulate-costs-across-multiple-calls) defines a version boundary for `claude -p`: stable 2.1 releases before 2.1.277 report per-invocation `modelUsage`; 2.1.277+ restore session totals on resume. A bounded `--version` probe of the same selected executable establishes the basis. Unknown, prerelease, unsupported-series or failed version probes leave usage unavailable and preserve execution. Sum models' input, cache-read and cache-creation counts for input, plus output for total. The narrower main-loop `usage` is not a fallback. Missing counters and potentially zeroed crash results are unavailable. |
| Antigravity / `antigravity.headless` | The [native headless contract](https://antigravity.google/docs/cli/headless/) and owned agy 1.5.14 fresh/resume capture (2026-11-22) supply per-step counters and cumulative terminal counters. Captured native input/total exclude cache reads (a step reports input 2184 and cache 9821); full input is `input_tokens + cache_read_tokens`, and total adds output. Require valid native input/output/cache counters and native total equal to input plus output; thinking is already included in output. Fresh processes prefer valid terminal usage, otherwise sum distinct completed step indexes once; never add step and terminal totals. Resumed usage remains unavailable without a verified exact-session pre-invocation native baseline; neither the first post-work event nor an old StackOS receipt supplies one. Historical `antigravity.fresh-process` receipts retain their original accounting provenance. |
| Gemini CLI (historical) / `gemini.models` | Retained receipts from the retired host used [headless JSON](https://google-gemini.github.io/gemini-cli/docs/cli/headless.html) `stats.models` and [telemetry hydration](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/telemetry/uiTelemetry.ts) on resume. Input is prompt (already includes cached), output is candidates plus thoughts plus tool; total must match their sum. No active Gemini runner remains and historical usage is not rewritten. |
| Hermes (historical) / `hermes.oneshot.main.v0.23.3` | Retained main-loop session counters use full input `input_tokens + cache_read_tokens + cache_write_tokens`, plus output for total. Reasoning is included in output. Existing session-delta rules apply only with a matching baseline; auxiliary usage remains excluded. No new Hermes invocation or usage report is produced. |
| Grok / `grok.headless` | Native `--output-format json` reports invocation usage, verified with CLI 1.0.77 fresh/resume captures and the [headless contract](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/14-headless-mode.md#json). Full input is `input_tokens` + `cache_read_input_tokens` + `cache_creation_input_tokens`; output already includes `reasoning_tokens`. Require all counters and a matching `total_tokens`. The captured calls contribute 15709 and 15796 (31505 total). Do not add `modelUsage` again or apply this arithmetic to persisted session `inputTokens`, which already includes cache. Missing, malformed or explicitly incomplete native usage remains unavailable; agent-authored usage is ignored. |

Native detail mappings (same scope and basis as their parent counters):

| Host | cache_read | cache_write | reasoning |
| --- | --- | --- | --- |
| Codex | `cached_input_tokens` | `cache_write_input_tokens` | `reasoning_output_tokens` |
| Claude Code | Sum `modelUsage.*.cacheReadInputTokens` | Sum `modelUsage.*.cacheCreationInputTokens`; TTL partitions are not added again | Sum `modelUsage.*.thinkingTokens`, only when every model reports a valid subset of its output |
| Grok | `usage.cache_read_input_tokens` | `usage.cache_creation_input_tokens` | `usage.reasoning_tokens` |
| Antigravity (fresh only) | `usage.cache_read_tokens` | Unavailable | `usage.thinking_tokens`, already included in output |
| Gemini CLI (historical) | Sum `stats.models.*.tokens.cached`, only when every model reports a valid subset of its prompt | Unavailable | Sum `stats.models.*.tokens.thoughts`, already included in output; nested roles are not added |
| Hermes (historical main loop) | `cache_read_tokens` | `cache_write_tokens` | `reasoning_tokens`, already included in output |

Hermes execution is unavailable until StackOS can retire the exact owned gateway
turn. A client exit or detachment alone remains insufficient evidence during
Stop, restart and upgrade. Historical receipts keep their recorded protocol and
accounting provenance; cleanup does not rewrite them or launch new Hermes work.
Upgrade preparation recognizes settled historical requests only when retained
daemon-owned protocol provenance, exact session identity and successful terminal
facts agree. Unknown provenance, interrupted viewers and failed retirement remain
unconfirmed.

Claude and Grok managed calls consume native Messages streams (`stream-json
--verbose` and `streaming-messages-json`, respectively). Only the native
`system/init` establishes early session identity; startup hooks, requested IDs
and a terminal ID without init do not. The shared process/observation boundary
persists that identity before exit and retains it on interruption. Exact resume,
ownership and persistence failures remain failures, with no replacement session.
Native init/assistant models provide early observation. A single terminal
`modelUsage` key identifies the model for its aggregate; multiple models remain
ambiguous. This preserves native alias differences without changing selection.

Claude aggregate usage becomes observable when its native result event arrives,
before process exit, retaining its versioned cumulative basis. Grok aggregate
usage is withheld until process exit and complete stdout/stderr observation:
its Messages format zero-fills missing/incomplete usage and warns on stderr,
which can arrive after the result event. Any stderr diagnostic conservatively
withholds Grok usage without turning a warning into execution failure. Early
Grok session and model observation remains available. Failed output decoding
also leaves Grok accounting unknown. Earlier partial/message
counters cannot establish complete session or invocation totals for either host
and remain unknown. Grok Messages totals sum the three disjoint input buckets
and output once per invocation, including on resume. This format omits reasoning
detail; it stays unknown. All-zero Grok Messages counters are unknown. Captured
Claude 2.1.330/Grok 1.0.92 fresh/resume projections are maintained in
`tests/fixtures/claude_grok_streams_20261122.json`; their completed calls do not
claim native first-call interruption or installed StackOS activation.

Claude, Grok and current Antigravity headless accounting require their native
cache buckets to compute valid full input; an absent bucket cannot be assumed
zero. For Antigravity, an incomplete usage-bearing completed step invalidates the invocation
aggregate and its live/provisional measurements; valid authoritative terminal
counters can restore measurement. Drained output after cancellation must also
clear invalidated usage from final receipts. Stop continues to fence positive
live updates and outcome acceptance. Surviving steps alone cannot supply a full
invocation total. Missing optional thinking or reasoning never erases valid base usage.
Codex and historical Gemini missing optional cache detail likewise preserves
their already-inclusive input. Native arguments,
version checks, session identity, scopes and bases are unchanged. Grok fresh and
resumed invocations each contribute their entire map once, even with a previous
receipt; they are never differenced. Historical Gemini mappings retain their
original provenance; Antigravity does not inherit their resume basis. The
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

### Recorded work time

Task and ticket reads expose `work_duration`, including task-index, filtered
snapshots, graph nodes, briefs and the existing usage-refresh stream. Compact
responses preserve this typed projection, including nulls. `total_seconds` is
the union of recorded managed invocation wall intervals. Delivery, review and
repair receipts use the existing ticket-local attempt/phase identity, with
current receipts replacing duplicate archived observations. Task totals union
all their tickets, regardless of visible filters; overlapping work counts once.

Closed intervals use `launch.started_at` and the native exit's canonical
`finished_at`. The execution owner records that finish during the synchronous
receipt handoff, including failures and cancellation. A transient registry copy
can supply the same closed endpoint until the durable receipt is committed.
Reads capture one bounded timing copy before selecting fresh SQL receipts;
retirement after that copy cannot erase the last supported observation. Open
intervals stop at the copy's observation time, and a freshly selected canonical
finish replaces it. Shared task/ticket stream refresh uses the same request copy;
no registry lock is held across SQL and no second invocation history is retained.
Only an exact currently owned invocation matching project, task, ticket,
attempt, phase and recorded execution identity supplies an open interval,
bounded at `observed_at`. Final receipts supersede that copy once. No token
measurement is required. Queued, stopped, complete and reopened gaps add nothing.

`observed_at` is the supported observation endpoint: the current registry
snapshot time while live, the latest canonical finish when closed, or null
without a valid interval. Closed/unavailable projections remain stable across
reads. `endpoint_at` is the latest measured endpoint. `live` lists only the exact owned
identities that can extend the union after `observed_at`; once none remain,
clients must freeze the total. `coverage` is `complete`, `partial` or
`unavailable`, with `recorded_intervals`, `unavailable_intervals` and explicit
`issues`. Missing/invalid/offset-free historical endpoints, reversed clocks,
future endpoints and stale execution state are omitted and disclosed. An
entirely unrecorded scope has a null total with `no_recorded_invocations`;
a recorded zero-length interval has measured zero. Lifecycle dates, usage
checkpoints and token silence never fill gaps. This is invocation wall time,
including any unobserved machine sleep, and does not claim CPU-active time.
Every included ticket without receipt history retains `no_recorded_invocations`
in a task aggregate, so a measured sibling total has partial coverage. Unknown
history adds no interval to `unavailable_intervals`; that count describes
retained receipts with unusable endpoints, not a guessed number of missing calls.
The existing `tracker.usage.changed` notification also invalidates timing-only
receipt changes, with unchanged durable timing/usage inputs suppressed. Existing
connected streams receive corrected work time without a poller or reconnection.

Legacy task-linked `schedule.runs` rows carry canonical task `work_duration`
and its measured `total_seconds` as `duration_seconds`. Native main-thread rows
keep their own process duration and return null `work_duration`, even when linked
to a task; child work time is not substituted. Lifecycle dates retain their
original meaning. The deterministic independent expectations and real managed
exit/handoff scenarios live in `test_work_duration.py`. Backend acceptance does
not require downstream UI delivery.

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
null. Agent labels come from recorded launches. Model attribution prefers an
unambiguous native `model_observation` for that exact invocation/session, then
falls back to the recorded `launch.model` selection only when observation is
absent. Requested `launch.model` and execution `model` remain unchanged and
separately traceable. Row `model_basis` is `observed`, `selected`, or `mixed`
when a group combines those bases; it is null for unknown identity. A selected
model is intent, without a claim of native observation. Conflicting, malformed
or mismatched observation leaves attribution unknown even with a selection.
No current defaults, role names, neighboring runs or transcript prose supply
identity. Missing historical identity stays null/unrecorded; historical receipts
are not backfilled or rewritten, and their known selections remain intact.

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
record known validation failure unless the receipt carries `cancelled=true`.
That positive fence evidence keeps the attempt and measured usage but leaves its
decision unclassified; it contributes neither `validation_failed` nor a reviewer
reject. Null outcomes, unexplained signals and truthy non-boolean cancellation
values do not establish cancellation. Absent outcomes or roles remain unclassified.
`coverage.fields` maps each of `input`, `output`, `total`, `cache_read`,
`cache_write`, `reasoning` to `measured_attempts`, `unavailable_attempts`, and
`status` with the same coverage enum. Each denominator is all eligible attempts
in that scope. A partial cache sum describes only known measurements; it does
not imply zero cache for the others. `review_outcomes` counts explicit
review-role validated `success`, `repair`, `blocked`, `failed`, or
`validation_failed` (explicit null without evidenced cancellation); absent/unknown role or outcome remains
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
filters reject before the query. UI owns the last-30-calendar-days default
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

Statistics reads use the existing receipt owner's derived
`tracker_receipt_projections` index. It contains only the inputs to usage,
classification, source-time history and recorded duration, keyed by ticket,
attempt and phase. Raw results, prompts, configuration and recovery packets
remain in the authoritative ticket evidence and its complete detail reads.
The usage inventory requires a recorded launch; duration retains its existing
unfiltered inventory. A malformed current launch therefore cannot erase an
otherwise eligible archived usage contribution or fabricate a duration endpoint.

The index is backfilled during migration or explicit lost-schema-stamp recovery.
Canonical ORM receipt writes and
the explicit usage-repair script update it in the source transaction. Finalization,
repair/requeue and clearing evidence replace or remove only changed contributions;
ticket/task/project deletion cascades. Project visibility and tracker membership
are joined from current source rows, rather than cached. Date requests select the
UTC bounds of the requested local calendar range before receipt decoding, plus
the deliberately lifetime-scoped undated disclosure. Dated and undated ranges
use separate index searches. A short report never decodes unrelated raw history.

`ReceiptProjection` belongs to one read request. Task/ticket projections in a
snapshot or usage refresh share its selected rows, provisional copy and normalized
facts. The live copy is captured before fresh scalar SQL; persisted final usage
and closed timing endpoints still win. Later requests create a new projection.
At the same statistics owner, bounded in-process LRU reuse (16384 source entries)
keys pure decoding/normalization and per-receipt history preparation by the complete
projected source value and ticket identity where needed. Changed source values
cannot reuse an older contribution. This cache contains no scoped report result,
authorization decision or live registry state; eviction only recomputes a fact.
Outputs own their mutable models. Reporting fans each normalized receipt into
period/day/agent/model accumulators in one pass; history replaces changed cumulative
contributions rather than resumming every invocation at each checkpoint.

The reusable disposable performance workload builder is
`tests/helpers/agent_statistics.py`. Local proof covers repeated terminal reads
and a seven-day selection over its longer archive; the assembled delivery gate
owns the single large-load campaign and useful-page timing. No statistics read
seeds production data or performs an implicit index backfill.

#### Shared delivery trends

Both statistics operations additionally return `delivery_trends.totals` and
`delivery_trends.daily`, using the same authorized scope, inclusive dates and
timezone. The six widgets consume this single projection; they never read raw
receipts or recompute history. Existing usage, identity groups and launch-date
daily series retain their semantics. Without dates, trend totals cover retained
lifetime evidence and the trend daily list is empty.

- `completed_tasks.total` and `completed_tickets.total` count the first recorded
  successful complete transition per stable entity ID from `TrackerRevision`.
  Reopen/recompletion and repeated same-state writes add nothing. Ordinary
  tickets and workflow mirrors each count as tickets; tasks are separate.
  The event's UTC instant determines its reporting day. If retained evidence
  already begins complete, a later completion cannot invent the missing first
  event. Imports without recoverable completion history remain unknown.
- `first_review_acceptance` exposes `accepted`, `reviewed` and nullable `rate`
  (0 to 1). Each ticket's first explicit review-role success or repair in the
  canonical receipt occurrence order supplies at most one verdict, regardless
  of storage phase. Earlier blocked, failed and validation failures do not
  enter this denominator. Its finish instant determines the reporting day;
  unknown occurrence identity or finish time stays unavailable. Period rate
  divides period accepted by period reviewed, rather than averaging daily rates.
- `changes_requested.total` counts distinct explicit review-role repair
  receipts once by ticket/attempt/phase, on the verdict finish day. It reuses
  statistics eligibility/classification, including exact operator-reset
  exclusion; native failures, blocked calls and amendments add no rejects.
- `stops.total` counts task revision transitions from a non-stopped
  `metadata_json.delivery.status` to `stopped`. Repeated stopped metadata writes
  add nothing; a real resume and another stop count again. Missing active
  execution and blocked ticket/step attempts are not stops. Imported histories
  without creation provenance disclose retained-only coverage.
- `work_time.total_seconds` uses the canonical invocation endpoint validator,
  deduplication and final-over-live precedence. It clips recorded intervals to
  each reporting bucket, unions overlaps within each task, then sums distinct
  tasks once. Parallel tasks can contribute concurrently; this is **summed
  task work time**, not elapsed scope age or CPU time. It never adds task totals
  to ticket totals. A known empty period is zero; unsupported history is null.

Each trend metric carries `coverage` (`status`, `recorded`, `unavailable`,
`issues`). Coverage is deliberately lifetime-scoped, also on daily buckets:
a date filter cannot repair missing earlier defining history. Completion
coverage counts entities with recoverable history versus unknown entities;
review coverage counts quality verdicts with usable identity/time versus
unavailable verdicts, disclosing unclassified receipt history separately in
`issues`; stop coverage counts tasks with creation provenance
versus retained-only histories; work coverage counts validated intervals versus
unusable receipts or included tasks/tickets without recorded invocations.
No missing receipt count is inferred. Counts/rates describe known observations,
with partial/unavailable coverage explicit rather than fabricated zero events.

The receipt index also retains validated finish bounds, closed-state presence
and explicit review classification, updated in the canonical source transaction.
Migration (or explicit lost-stamp recovery) backfills these derived fields.
Short reports select overlapping intervals before receipt JSON decoding and
reduce first-completion/first-review provenance before date filtering. Scalar
coverage reductions and selected timing rows share one SQL snapshot, so a native
final receipt committed during a report cannot erase supported work time or its
coverage. These reads preserve lifetime gaps without decoding unrelated history.
One bounded query path supplies all six trends; no report cache or analytics store
is introduced. Completion and stop reads use the covering
`ix_tracker_revisions_delivery_stats` expression index instead of fetching full
revision snapshots. Its fixed JSON paths are shared with the reader; SQLite
maintains the index in the source write transaction. Migration `0053` indexes
retained rows once without rewriting history. `test_delivery_trends.py` provides
independent reopens, stops, review, overlap, DST, scope, missing-history and
read-budget ledgers.

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

Project Home and the main Overview share a **Usage** section backed by
these operations. Daily tokens stacks Input / Cache / Output segments in one bar
per date in All mode,
with individual metric inspection and an Agent / Model / Agent+model breakdown.
Displayed Input is the existing paired `remaining_input`, Cache is `cache_read`,
and Output is `output`. Raw API `input` remains inclusive. Summary, breakdown
and chart use the same selected date range, defaulting to today plus the
previous 29 local calendar days in the visibly identified browser IANA timezone.
All retains the reported period total independently of its split datasets.
Input uses the repository's paired Input-minus-Cache projection,
never an aggregate subtraction that invents a remainder for missing cache.
The shared UI mapping also owns summary values and exact hover/keyboard values.
Input availability and coverage follow
paired cache measurements. For receipts 100/80/5 and 50/unknown/3, the known
display is Input 20, Cache 80, Output 8; those 108 tokens are not the full raw
total of 158. Complete 11,545,485/11,232,768/46,397 counters display Input 312,717,
Cache 11,232,768, Output 46,397, preserving total 11,591,882. A measured zero
remains zero; no second subtraction or historical counter mutation occurs.
The breakdown retains reported totals by the selected grouping. Incomplete
composition stays explicit in exact chart details. Tiny positive components
receive a two-pixel painted mark only in All mode, without changing values or
giving zero/missing usage a mark. Home omits Token details and Daily values
expansions. Exact hover/keyboard values follow the
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
and repair commits; a supplied source-repair candidate must be a new descendant.
Only the explicitly admitted evidence-only occurrence described above may keep
its unchanged exact candidate after earlier repairs.
Git presence alone does not require a commit or clean worktree: noncoding work
may execute in a dirty Git directory and return non-Git outputs. Baseline HEAD
is an observation, not a source-candidate requirement. A valid repository with no
initial commit supplies no baseline SHA; noncoding work needs no initial commit.
When prior results explicitly require a source candidate, an unowned dirty
delivery baseline rejects before native invocation. Exact validated owned-work
resumes retain their existing exception below. First coding-tree preparation
without an explicit candidate requirement remains engineering admission's
responsibility; the runtime does not infer coding intent or impose a universal
clean-tree gate.
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
New snapshots use `version: 1`, fixed integrity fields, sorted path SHA-256
digests, and an ordered list of untracked records (`path_digest`, `sha256`,
`mode`). Filenames in `paths` are diagnostic labels subject to normal redaction;
they never authenticate ownership. Resume compares the versioned integrity
fields, original ticket/base and exact retained role session before launch.
Sensitive or assignment-like filenames therefore survive tracker writes,
revision history and process reopening without storing file contents or exempting
any fields from the shared redactor.
Pre-existing dirt never becomes implicitly ticket-owned, even after native failure
or an invalid candidate return. A supplied owned-work receipt with a changed
snapshot, base, ticket or role session fails before launch. Legacy source failures
without a receipt need explicit operator review/adoption using the same snapshot
helper, with actor and reason recorded. Intact unversioned snapshots normalize
in memory for comparison without rewriting old receipts. Redacted or malformed
legacy data, explicit null receipts and unknown versions fail closed. Recovery
must review the current files, original base and retained session explicitly;
never reconstruct missing hashes from current files as implicit ownership. The
reviewed adoption captures a new versioned snapshot while retaining revisions,
attempt history and consumed repair allowance. Owned source work requires clean Git before
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
IDs, reused by host discovery and launch validation. Choose current models from
the resolved `hostMcp.status` `agent_execution` catalog, including its dated sources,
capability notes and observation fields; see [host capability guidance](setup.md).
A native model-list observation applies to the provider/account that produced it
and does not guarantee a successful model invocation. Packaged suggestions do not
establish entitlement. Preserve saved and historical model IDs as recorded;
catalog refresh must not rewrite pins, drafts or frozen session settings.

| Host | Model selection source | Delivery / review |
| --- | --- | --- |
| Codex | Resolved catalog and recommendation; explicit `gpt-6.1-sol` and `gpt-6-astra` remain supported, with configured provider models following the catalog's capability flags | Automatic approval review for delivery and test-capable review |
| Claude Code | Resolved `agent_execution.models` and capability notes | Native `auto` permissions for delivery and test-capable review |
| Antigravity | Exact IDs from `agy models`, projected into the resolved catalog for the observed account | Session-local terminal sandbox and `proceed-in-sandbox`, explicit StackOS MCP tool permissions, saved ask/deny rules preserved; delivery `accept-edits` / normal native diff review with review-only instructions |
| Hermes | Saved explicit provider's model ID; no Hermes model family | Managed execution unavailable: gateway lifetime cannot be controlled through its viewer |
| Grok | Exact IDs from `grok models`, projected into the resolved catalog; configured aliases follow its custom-model validation policy | `workspace` sandbox, `auto` for delivery and test-capable review |
| Claude Desktop | None for headless execution | Unsupported |

Review modes permit focused shell-based tests under native permissions; they
do not enforce reviewer source immutability. Antigravity has no
established strict read-only execution contract. It supports independent
review through normal native sessions with instructions to avoid changing files
or commits; these instructions provide no enforced read-only isolation. The shared
host capability owner selects the mode (`review_sandbox`); launch receipts record
`sandbox: "normal"` for these reviews. Antigravity review permits sandboxed checks
and explicit StackOS MCP tools through session-only settings; strict tool approval
or unavailable required sandbox/permissions fails rather than widening authority;
Hermes managed execution rejects before scheduling, preserving its saved selections.
Native permissions may deny required work, including committing; a structured
blocked result stops the task even if the process exit code is zero.

The Codex catalog was checked on 2026-11-17 against the [Codex model guide](https://learn.chatgpt.com/docs/models) and the model list advertised to Codex CLI 0.169.0. Both 6.2 models offer low, medium, high, xhigh, max and ultra. The setup dialog uses the recommendation for a fresh selection. Saved choices and admitted runs keep their explicit model IDs.

Source contracts checked 2026-11-05:
[Codex models](https://learn.chatgpt.com/docs/models),
[Claude CLI](https://code.claude.com/docs/en/cli-reference) and
[models](https://platform.claude.com/docs/en/models/overview),
[Antigravity headless](https://antigravity.google/docs/cli/headless/) and
[CLI reference](https://antigravity.google/docs/cli/reference/) (checked 2026-11-21, agy 1.5.13),
[Hermes CLI](https://hermes-agent.nousresearch.com/docs/reference/cli-commands),
[Grok CLI](https://docs.x.ai/build/cli/reference) and
[headless](https://docs.x.ai/build/cli/headless-scripting),
[Claude Desktop](https://code.claude.com/docs/en/desktop).
Grok's installed help and bundled headless documentation confirm its native
JSON envelope (`text`, `sessionId`, `stopReason`), distinct from its Messages
stream format. All adapters enforce the same caller-supplied result schema.

Installed/MCP-connected status establishes neither credential presence nor
model access. Catalog observation fields distinguish native model lists from
unverified suggestions; unavailable checks remain `not_checked`. Documented
native auth-status commands are provided where available. `codex login status`,
`claude auth status`, and `hermes auth status` (for the selected provider/profile)
only report native authentication state, not entitlement to every model.
Antigravity/Grok auth-status support is unverified. Actual launch failures, including
unrecovered errors on stdout, missing credentials, inaccessible models, and broken
CLI shims, stop the current task with their reason. Codex can recover a stream
interruption within the same invocation: a later `turn.completed`, exit code zero,
and valid structured result supersede an earlier transient `error`. A terminal
`turn.failed`, later error, nonzero exit, or invalid result still fails. StackOS
does not launch an extra retry or replace the session. No credential setup is
performed.

Ordinary automatic review allows up to three durable scoped repair passes above;
dependency-driven corrections preserve that consumed allowance. Delivery
resumes after daemon restart only under the
[new-handoff continuation contract](#ordinary-restart-and-missed-launch-continuation).
Historical handoffs and unresolved failures still require explicit recovery.
`tracker.execute` remains a read-only
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

The task's current `completion_evidence_json.summary` follows the final accepted
run-plan step result. `run_plan_outcome` retains that result with its run, step,
and completion refs. A completed status without a successful final step and
nonempty result does not manufacture a success summary; a skipped final step
does not promote an earlier result to closeout evidence. Structured results
remain intact even when they have no top-level summary.

At that accepted completion write, the mirror also reconciles a valid current
delivery handoff after its controller has retired: current status becomes
`complete`, reason and ticket are cleared, and no slot remains reserved. Frozen
settings, identities, sessions and prior stop/rejection evidence remain intact
in their existing fields and tracker revisions. A reserved callback or active
native invocation retains ownership; canonical completion does not release its
capacity or replace its state. Missing or skipped final results, failure and
abort do not manufacture delivery success. This is write-time reconciliation,
not a read-side override or a sweep of historical records.

Abort, reopen, recovery, and completion update only mirror-owned current outcome
fields. Earlier summaries and abort reasons remain in append-only tracker
revisions (`tracker.history` and task activity); unrelated context and nested
automatic-delivery attempts, usage, sessions, and receipts remain intact.
Reopen/recovery removes the prior current summary while work is active.
Existing stale records require explicit, selected-record reconciliation through
normal operations after current canonical results have been reviewed. Read the
exact task and run-plan state, preserve the complete evidence object, patch only
the current outcome fields, and read back both current evidence and history.
Do not reopen completed work or rewrite historical receipts to repair a summary.

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

Main can record an explicit scope exclusion through the existing tracker
metadata patch. Merge other metadata fields when updating it:

```json
{
  "status": "deferred",
  "metadata_json": {
    "delivery_scope": {
      "disposition": "excluded",
      "parent_ticket_key": "workflow-1-verify-delivery",
      "reason": "Operator moved optional work outside this parent's current scope",
      "source_ref": "operator-decision:scope-2",
      "resume_owner_ref": "team:main",
      "resume_condition": "Operator admits the later scope"
    }
  }
}
```

The shared tracker mutation validator requires the exact attached parent in the
same task, a nonempty reason and decision source reference, and skipped, aborted
or deferred status. Deferred exclusions also require both resume fields. Preview
and apply enforce the same contract; single and list creation validate it too.
Bare terminal status, failed/pending work, malformed historical markers and
unresolved nested obligations still hold the workflow verifier. Each nested
exclusion names its own immediate parent. A valid exclusion settles containment
only: the child retains its status, attachment, evidence and revision history.
Explicit `blocks` edges stay success-only until main reconciles obsolete edges
through existing dependency mutation. Exclusion is neither accepted source nor
permission to change scope or perform an action.

Remove `delivery_scope` (or set it to null) to reinstate the obligation even
before changing status. Reactivation or reparenting clears a retained marker;
an explicitly supplied replacement must validate against the new state. The
prior decision remains in append-only tracker revisions. No status migration
guesses historical exclusions. A group with children auto-completes only when
at least one immediate child succeeded and every descendant is complete or
validly excluded; all-excluded groups remain active. Workflow verifier
packets carry relevant `child_dispositions` separately from `accepted_receipts`,
including current metadata, decision/resume refs and canonical evidence refs.
The verifier independently judges the current requirements; eligibility does
not complete a step or waive missing outcomes.

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

Board membership is the repository's `TrackerTaskOut.board_column` projection.
It does not replace the task/ticket lifecycle or run-plan authority. The columns
are exactly Backlog, Todo, In progress, Stopped, Done, Aborted, Failed and Skipped:

| Existing facts, evaluated in order | Column |
| --- | --- |
| `complete` | Done (`done`) |
| `aborted`, `failed`, `skipped` | Aborted, Failed, Skipped |
| `deferred`, retaining its resume owner/condition | Backlog |
| Nonterminal, validated canonical delivery `stopped` | Stopped |
| Delivery `queued`, no actual invocation recorded | Todo |
| Delivery `queued` with a prior actual invocation, or delivery `running` | In progress |
| Remaining lifecycle `in-progress` | In progress |
| Remaining `not-started` with `lane_key=backlog` | Backlog |
| Remaining `not-started` in any other lane | Todo |

Task revision snapshots retain source lifecycle, lane and delivery facts and
omit the derived `board_column`; live reads resolve it with invocation evidence.
Board intent receipts retain the accepted projection for stable retry/replay.

Absent live execution never means stopped. Queued membership uses recorded
actual invocation events or canonical current/archive invocation receipts;
workflow `started_at`, a pending launch or a saved session alone is insufficient.
Already-started delivery waiting between calls stays In progress. Terminal
lifecycle wins over retained delivery metadata. Deferred records retain their
condition and map to Backlog even though their internal terminal lane is `done`.

The existing `lane_key` owns explicit Backlog/Todo placement. Normal tracker
setup includes a generic `backlog` lane; existing catalogs acquire a missing
Backlog entry on the next setup write, preserving their other lanes and order.
Legacy task rows are not rewritten: existing nonstarted lanes stay Todo until
explicitly moved. No lifecycle enum, placement table or data migration is added.

`tracker.placeTask` accepts only `board_column: backlog|todo` plus the same
snapshot and retry fields as other Board intents. It changes only lane and
arrival rank on `not-started` work without an open validated delivery handoff.
Todo preserves an existing non-backlog lane; leaving Backlog selects the normal
`implementation` lane. Deferred work requires its existing explicit resume
authority first. Placement never launches, stops, resumes, reopens or finishes
delivery or a workflow. Repeated same-column placement preserves manual order.

`tracker.placeTask`, `tracker.updateTaskPriority` and `tracker.reorderTask` are strict, browser-safe
operations exposed through the same registry to REST, CLI and MCP. They use
the existing task `priority_key` and integer `order_index`, with no separate
ranking store. The UI client is `lib/stackos/taskBoard.ts`.

All three require `project_id`, `task_key`, `expected_status`, `expected_updated_at`
and a stable `idempotency_key`. Priority accepts only `priority_key: p0|p1|p2|p3`.
Reorder accepts `anchor_task_key` and `placement: before|after`. The anchor
must currently belong to the same project and projected Board column as the moved task.
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

New tasks and genuine projected-column entries prepend to the full destination
column using that same persisted rank. The repository reserves the SQLite
writer before allocating a rank, so concurrent arrivals serialize. Direct
patches, delivery stop/resume metadata, child-ticket invocation receipts,
rollups and workflow mirroring use the existing rank owner. Same-column writes
and title, usage or priority updates retain manual order. Arrival placement does
not change priority or ticket scheduling.

Migration `0039_tracker_board_arrivals` normalizes existing untouched columns
once. Complete tracker revisions, creation receipts and rank snapshots establish
which columns have no saved manual order. A reorder's recorded `expected_status`
identifies its column, which is preserved. The latest same-project task status
event supplies entry order; tasks without historical entry events fall back to
creation time, then numeric ID. Unrelated `updated_at` changes never determine
placement. Incomplete or contradictory history preserves the affected column
(an unidentified reorder preserves the tracker), with task keys, reasons and
manual revision refs in the existing `board-arrivals-normalization` revision.
That receipt also records creation fallbacks and makes retries idempotent.
Legacy `create_all` schema adoption runs the same repair before stamping head,
since schema stamping otherwise skips data migrations.
Upgrade leaves statuses, priorities, workflow snapshots and tracker settings
unchanged; downgrade does not reverse saved operator order.

Real priority edits emit `tracker.task.changed` into the existing timeline and
`tracker-status` SSE transport. A reorder emits exactly one
`tracker.tasks.changed` event, regardless of how many ranks change. Its source
is `tracker` with the tracker ID; typed metadata contains `tracker_id`, the
accepted tracker `rev`, `affected_task_keys`, the union of `changed_fields`, and
`affected_tasks`. Each structured member contains `task_id`, `task_key`,
`changed_fields` and `url_path`. Relative movement with an unchanged integer
uses `order`; changed integers use `order_index`. The event, all ranks, one
revision and the intent-bound retry receipt commit in the same transaction;
independent readers see the notification only after commit. A rollback, no-op
or replay produces no new notification. Existing agent priority patches and
single-task history retain `tracker.task.changed`.

Task-scoped timeline/latest-event readers match exact keys in the structured
batch members; task activity also checks the persisted task ID. Membership uses
an existence query before pagination so each batch appears once per affected
task. Top-level lookalikes, nested keys, display text and the key summary array
do not establish batch membership. Project scope applies independently. A batch
remains one SSE frame within the normal 50-event page cadence, and subsequent
single-task events retain their own IDs and visibility. Consumers use the batch
revision and member keys to invalidate relevant projections and coordinate
save responses with SSE.

These events invalidate readers without masquerading as status changes or
adding a polling path. UI callers retain the immutable intent for transport
retry, reconcile after conflicts, and check project ownership before applying a
late response. The card and navigation consumers own that interaction state.

Work's existing project-index loader owns revision coverage for save responses
and live notifications. A response already covering the accepted `rev`, including
an in-flight read started before the save, is reused; only an older response
requires another read. Delayed notifications for that revision do not reload
the index, while later revisions still reconcile. Unversioned invalidations and
explicit refresh share one in-flight read and one queued latest read.
Structured batch members scope selected-task refreshes and graph highlights;
unrelated tasks/projects do not refresh focused readers. Graph and detail reads
also reuse their own covered or in-flight responses.
Initial and explicit refresh show loading; background reconciliation retains
the current board and does not toggle the global loading control. Focused readers keep
their own loading state and do not hold that queue open. Board writes and index
reads have 15-second abort deadlines; scope exit aborts them and ignores late
outcomes. Pending edits block only conflicting edits in the same status column,
leaving other columns, inspection, filters and navigation usable.

A confirmed rejected save removes its optimistic edit and reads current truth.
A successful save followed by a failed read retains the optimistic order and
reports that the task saved. A timeout, lost response or server error leaves the
save outcome uncertain: reconcile by reading, without automatically repeating
the mutation. Refresh retries only the read and clears settled optimistic state
after a successful current-project index response.

## Status Events

### Home active-task reads

The Home bulk responses include an opaque nullable `read_token` equality fence,
also present in the lightweight UI stream baseline when its sources remain
stable through that poll. It covers the existing event position (including
project identity), project/tracker revisions and bounded native live counters.
Only the initial baseline may acknowledge an already-started matching read;
later updates still invalidate it. It conveys no authority or cursor ordering.
Absent or unequal tokens retain the ordered replacement path. Existing fields,
daily groups, coverage and operation permissions are unchanged.

`tracker.activeTasks(project_id)` and local-admin-only `tracker.activeTasksAll`
return bounded pages of tasks whose lifecycle is exactly `in-progress`.
Queued and stopped execution remain members; complete, aborted and backlog
tasks do not. Global scope includes active projects without incomplete cleanup;
project reads require an available project. Bound MCP callers cannot use the
global read or override their project binding.

Both accept `limit` (1–200, default 100) and `after_id` (the returned
`next_cursor`), in stable ascending task-ID order. Identity is the composite
`(project_id, key)`, never the task key or project display name alone. Pages
include `scope`, `project_id`, `as_of`, project metadata and whitelisted task
facts from the existing task-index projection: ticket rollup, whole-task usage,
actual active/Last execution, canonical `work_duration` and lifecycle dates.
Home uses the same measured interval union and complete/partial/unavailable
coverage as Board, Graph and details; lifecycle dates never substitute for
missing recorded work. A minimal
`delivery` object retains validated handoff status, ticket key and recorded stop
reason without returning frozen instructions, native paths or raw receipts.
Selected agents alone do not establish automation. Missing usage retains its
canonical coverage and unknown values. Compact and raw preserve the same full
bounded projection; ack is unavailable. Reads create no tracker or other state.

The shared `useActiveTasks` Home reader loads pages atomically and subscribes
once through `usageUpdates`. Lifecycle, execution and usage frames invalidate
that scope's bulk read, including global frames with no replaceable project
metrics. A current successful read and healthy shared transport are both needed
for fresh presentation; errors retain previous rows and expose the error.
Disconnects, hidden surfaces and reads crossed by invalidation cannot restore
Live from stale facts. Canonical project-store metadata and removed-ID fences
continue to govern row names and visibility. No per-row read or token aggregation
is added; focused Board/Graph subscriptions keep their existing scope.

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
row id. Ticket status events remain available for audit and UI timelines;
they do not produce native per-ticket notifications.

### Desktop actionable task notifications

The existing desktop controller reads `tracker.changed` with
`projection="notifications"`. This is a typed scalar projection of atomic
`TrackerRevision` before/after snapshots, not another event store or scheduler.
The default full revision contract remains unchanged. The projection includes
project ID/name, stable task ID/key/title, revision and before/after lifecycle
and validated delivery status. It never returns prompts, raw outputs or stop
reason prose. The task graph retains the actual reason and failure details.

| Type | Canonical transition | Native title and body |
| --- | --- | --- |
| `complete` | Task lifecycle changes to `complete` | Task complete; `{task title} · {project name}` |
| `stopped` | Nonterminal task delivery changes from non-stopped to validated `stopped` | Task needs attention; `{task title} stopped · {project name}` |
| `failed` | Task lifecycle changes to `failed` outside an already-notified stop | Task failed; `{task title} · {project name}` |

`update_task`/`_record_revision` own lifecycle snapshots; automatic delivery's
`_update_delivery` uses the same mutation owner. Independent child-ticket
aggregation records its task transition in that same transaction; workflow
terminal mirroring retains its existing task snapshot. A repeated stopped write is
quiet; resume followed by stop is another incident. Failure plus stop in one
revision selects failure; failure during a retained stop or a later stop of a
failed task does not duplicate that incident. Progress, review chatter, task
creation and unchanged lifecycle are quiet. Service/auth/update alerts retain
their existing owners. There is currently no standalone actionable approval or
input-needed producer: pending future approval rows and prose do not create
notifications. Adding one remains a separate extension of that existing owner.

Omitting `since_rev` returns an empty baseline at `current_rev` without scanning
history. Subsequent reads inspect at most `limit` revisions (1–200, default 50),
including quiet writes and removed targets; `next_cursor` continues a partial
page and otherwise `current_rev` is the checkpoint. Compact and raw preserve
this entire bounded contract. Scalar SQL projection avoids decoding full
revision/task evidence or counting history. Missing, foreign and replaced task
identities cannot appear as valid targets.

The controller follows project catalog pages and reads at most four 50-revision
pages per project per poll. Remaining catch-up continues on the next poll. It
checkpoints each processed revision/page atomically, so a later project/page
failure or a normal app restart cannot replay already-processed work. Version 2
`projectRevisionCursors` in `notification-state.json` are independent of old
`projectEventWatermarks`; migration preserves those event IDs as history and
baselines the revision cursor quietly. A first historical load never replays.
The OS display and local checkpoint are separate operations; an abrupt process
failure precisely between them cannot provide transactional exactly-once OS
delivery. Normal restart/reconnect and partial-read retries retain deduplication.

Clicks use the existing task deep link, first re-reading that exact project/key
through `tracker.get`'s lightweight UI projection with empty metric demand and
checking its stable task ID. Missing/deleted/reused/foreign targets and failed
reads perform no navigation, rather than selecting another task by default.
The native macOS adapter/visuals remain the existing owner; presentation proof
belongs with changes to the presentation surface.

The same controller forwards its bounded presentation to the trusted desktop
renderer's existing toast queue, without another read loop or notification
history. Both surfaces use the catalog wording above and retain project/task
identity; each name is normalized and limited to 100 Unicode characters with
an ellipsis. Missing names use “Task”/“Project”, never internal keys or IDs.
Native macOS notifications use the packaged StackOS app identity/icon and system
layout. New notices replace a retained native/toast notice for the same task;
the active presentation is bounded to three notices. In-app completion is
success-toned and expires after five seconds; stopped is warning-toned and
failure is danger-toned, both retained until dismissed or replaced. `UiToast`
owns the accessible severity, wrapping and “Open task” action. That action uses
the same exact-target revalidation as a native click, via main-frame-only IPC.
An unavailable task produces a readable in-app message without changing target.
The fixed in-app stack stays within the viewport and scrolls when needed; keyboard
focus can reach every retained notice and action without dismissing other notices.

Native support is not proof of permission or visible delivery: macOS notification
settings, Focus and system policy control display. The controller reports native
support/failure separately from polling, keeps in-app delivery available when the
native adapter is unsupported or fails, and does not retry historical events to
work around system settings. It cannot detect a silently disabled macOS banner.
Plain browser and older desktop shells without the subscription remain unchanged.
There is no dedicated approval notice until an authoritative actionable source
exists; a future pending approval is never presented as a current request.

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

Admission, initial/reconnect cursor reads and each poll's synchronous database
and usage projection run in the existing thread pool. Each operation creates,
uses and closes its own Session in that worker; no live Session crosses an
await. A stream awaits one read at a time and discards a late result after
disconnect. Cursor polling skips the unused lifetime event count and seeks the
existing event table by ID (project-scoped reads use the project/ID index).
Repository callers that need a total retain the default counted page contract.
A private read watermark also covers unmatched events, so quiet task streams
do not repeatedly scan other tasks' newer history. It never becomes an emitted
status ID or heartbeat/reconnect cursor; paged matching events remain ordered
and are fully drained before that private watermark moves past them.
Global streams retain the watermark event's owning project identity. Project
purge can free event IDs for reuse, so a missing/replaced anchor resets only
private read progress to the last emitted status cursor, even when new events
have already regrown past the old watermark. Durable cursors never retreat.

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
   Preview and apply use the same read-only ticket validation for status
   transitions, workflow mirror ownership, field values, references and dependency
   changes. A skipped ticket cannot move directly to aborted; both paths return
   the canonical transition error with the current, attempted and allowed
   statuses. Preview validates list items in order against earlier successful
   items, including their dependency changes; failed items leave that projected
   state unchanged. Apply retains per-item savepoints and outcomes. List errors
   preserve structured repair context in `results[].error_data`,
   including in compact and ack responses. This does not change create-list
   atomicity or make list updates all-or-none. Revalidate after any intervening
   state change before applying the exact previewed payload.
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
The runner accepts required attached children and accounts for valid explicit
exclusions before dispatching the verification agent, so children must not block
their own mirror. Terminal-managed delivery reconciles the same scope and
evidence before running integrated verification. Delivery
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

Routine supervision starts with compact activity and `tracker.changed` from the
last observed revision. Without a known revision, use a scoped current read to
establish the baseline. Expand changed or unknown relevant tickets, receipts,
blockers and guidance; an unchanged tracker revision does not prove that host
execution or guidance is unchanged. Judge progress from actual invocation,
tool and receipt evidence, never token silence alone. Before final claims,
refresh the relevant state rather than reporting an earlier observation as current.

Keep no-change observations in the current occurrence summary. Update task
context only for changed decisions, actions or blockers and only under the current
writer contract; after automatic handoff, observers do not compete with StackOS
lifecycle writes. Read/merge complete supplied JSON fields before an authorized
write, preserving unrelated context and history. Measure actual reads/writes and
available usage over a named scope; smaller response payloads alone do not prove
less work or token savings. This uses existing operations and receipts, with no
new monitor, scheduler, persistent cache authority or ledger.

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

Workflow gate diagnostics use the declared child attachment, gate lane
(`verification`, `review`, `release`, `docs`, or `qa`), and dependency paths.
The `done` lane is a lifecycle display state, not a role change. Diagnostics
recover the latest non-`done` lane declaration from ticket revisions, preserving
gate identity on completion and requeue. Explicit later lane declarations
supersede earlier ones. Initially terminal creations record their supplied lane
in the same revision history. Legacy tickets with no retained declaration remain
unknown: graph diagnostics advise checking the role and omit it from gate-role
containment comparisons. Activation, reachability and handoff checks still
apply; names do not fill the role gap.
An ordinary implementation ticket can discuss review, tests, verification, or
release without acquiring a gate role. Unrecognized lanes do not imply a gate;
missing activation, reachability, and terminal-child handoffs are still checked
for every attached child. The generic pre-implementation direction advisory is
limited to declared gate lanes and asks for review rather than asserting a
dependency defect. Diagnostics do not change runtime dependency eligibility or
replace run-plan lifecycle checks.

### Live usage during native calls

The host execution service extends the same active-call registry with one
replaceable provisional contribution and up to 120 measured, timestamped samples
per call. One daemon collector samples at five-second intervals only while calls
are active. Codex identity is exposed before process exit; the exact native
rollout path is resolved once by exact ID in the native thread index, metadata
checks its ID and working directory, and
subsequent reads advance a byte cursor and retain up to 120 source checkpoints
from each read, rather than collapsing them into the last timestamp. A newly
announced file may not exist yet;
the reader waits for that exact path, without searching historical sessions.
Resumes skip the pre-invocation file prefix and use the unchanged previous
invocation baseline and native ID.

The same bounded reader also allowlists Codex `turn_context.payload.model`
and checks its `cwd` against the owning directory. Optional `model_observation`
in the native result and safe execution receipt carries `status`, nullable
`model`, `source="codex.turn_context"` and exact `session_id`. A single consistent
model is `observed`; conflicting models, invalid or unreadable context, loss of
the reader, a record still incomplete at native exit, context records exceeding
its 64 KiB bound, or known usage preceding the first model context make attribution
`ambiguous` for the whole invocation. Oversized `response_item` records (which
carry neither `turn_context` nor `token_count`, such as initial tool definitions
or large messages) are discarded without changing model attribution both before
and after the first `turn_context`. Oversized `event_msg` and `compacted`
records are discarded without changing model attribution only after this
invocation has already observed a `turn_context`. Before the first context,
oversized `event_msg` records (which can hide `token_count` usage) and any
`compacted` record (oversized or bounded, because Codex 0.171 pre-turn
compaction can run on a previous model and cannot be attributed solely from a
following `turn_context`), as well as oversized `turn_context` or unrecognized
headers at any point, remain ambiguous; nested payload text never establishes
the record type or model.
Without per-model counters, mixed totals cannot be allocated to one model.
Resumes require new context after their cursor rather than inheriting model
evidence from the old prefix. Other hosts supply no model observation through
this reader. Claude/Grok use the existing stream callback boundary instead,
with `source="claude.messages"` or `source="grok.messages"` and the same exact
session/host validation. Final handoff, retained receipt indexing and index rebuild preserve
this evidence without changing counter normalization or deduplication. This
adds no native-session scan, poller, ledger or historical repair.

The newest source instant owns provisional totals independently of retained
history. Previously unseen in-window checkpoints arriving later are retained in
source-time order without rolling those totals backward. Identical timestamp
and counter observations are deduplicated; conflicting same-time counters reach
the shared projection as uncertainty. The bounded tail keeps the newest 120
source observations, not the last 120 arrivals.

These are native `token_count` checkpoints, not continuous token streaming.
Repeated cumulative polls replace provisional usage; they are never summed.
Existing receipt normalization and aggregation serve ticket, task, project,
daily and grouped summaries. A persisted final receipt wins over any in-flight
provisional observation. Native result/error handling and repair rules remain
unchanged. Missing or unsupported telemetry is N/A; final collection still runs
for every supported host. Claude/Grok Messages callbacks expose early session
identity and native model observation. Full-scope per-message subagent/cache
checkpoint accounting is unproven for both hosts and remains unavailable.
Claude can publish aggregate usage at its result event; Grok publishes it only
after process exit and complete diagnostic validation, as described above.

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

UI-demand replacements additionally report `covered_event_id`: the highest
durable event read in that same poll, or null for a transient-only frame.
This is coverage metadata, not an SSE `id` or a tracker revision. Home's shared
read owner coalesces those same-poll status companions even for existing writers
without a revision, while later event IDs still invalidate. Graph retains the
structured events for highlights. The legacy unscoped and explicit task-key
contracts are unchanged; initial/reconnect replacements still revalidate data.

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
the temporal projection pairs checkpoints within each invocation before combining
task or ticket scope. `agent_stats.samples[].intervals` retains each change's
kind, start boundary and paired counters. First retained cumulative contributions
are `baseline`, never activity from another call. Native changes spanning at most
one minute are `checkpoint` and use the source event's minute, including events
read late in one collector poll. Longer gaps and final-only changes are `batched`;
counter decreases are `reset`. Optional field gaps are not bridged. A final save
does not move an existing native observation to receipt-save time.

Bars show source checkpoint increments and interval averages for known sparse
deltas, not per-token consumption times. A sparse delta contributes to each
one-minute bucket in proportion to its overlap with the measured interval;
clipped windows receive only their overlap, without an endpoint spike. Legacy
counter-only frames preserve the first baseline and use the same projection for
valid differences spanning more than a minute. Existing hover/accessible text
identifies interval averages and retains exact observation amounts and bounds.
Baselines, resets, conflicting timestamps and missing counters cannot supply
activity. Unknown bounds remain observations without bars or overlays.
`DEFAULT_USAGE_BAR_COUNT` sets the shared
default of 20 intervals; `UsageTrace.barCount` supports 5–60. Bars stay at most
2 CSS pixels wide as the chart uses the available metric-column width.
Every chart keeps the same baseline and one metric color: Input blue, Cache lime,
Output green. The first observation never supplies an invented increment from
zero. Missing values, absent
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

## Board local preferences

`TaskTrackerView` owns the existing `BoardFilterState` through explicit control
commands in `task-tracker/boardFilters.ts`. One small localStorage record per
project, `stackos:work-board-filters:<project_id>`, saves clauses, completed
visibility and hide-empty columns. Plain Work navigation, reload and app
reopening restore the same preferences. Project switching restores the target
project's record instead of carrying preferences from the previous project.
Storage access and parsing are guarded; absent, malformed or invalid records
use the existing defaults. Storage failure leaves controls usable in memory.
There are no Vue watchers or server/account preference settings.

Saved version-2 task Status clauses use `board_column` identities in the same
eight-column order. Ticket status clauses still use lifecycle values. On reading
an older unversioned inclusion, Not started expands to Backlog/Todo/In progress/
Stopped, In progress expands to Todo/In progress/Stopped, Deferred becomes
Backlog and Complete becomes Done; other terminal values are retained. A legacy
exclusion hides a column only if all its possible lifecycle cohorts were excluded.
Ambiguous filters deliberately broaden so retained queued, deferred or stopped
work is not silently lost when columns combine lifecycle values. The next explicit
preference save records version 2. Per-project scope and hide-empty choices remain.

| Preference | Meaning |
| --- | --- |
| Text | Substring across task and ticket/index search fields, case insensitive |
| Status, priority | OR within a field, AND between clauses; `is` or `is-not`; priorities are `p0` through `p3` |
| Workflow, assignee | One workflow key or assignee with `is` or `is-not` |
| Ticket status, blocked | Removable ticket-status and blocked clauses |
| `completed` | `all`, `past-day`, `past-week`, `past-month`, or `none` |
| `hideEmptyColumns` | Hide columns without any projected tasks |

Missing status/completed choices default to Status is not Aborted and Completed
Past week. Clear all saves empty clauses and Completed All, retaining the
empty-column display choice. Done selected explicitly with Completed None
reconciles to All in controls and storage readback.

Filter edits do not navigate or create history entries. URL `view`, `task`,
`detail` and `ticket` continue to own Board/Graph placement and inspection;
changing a filter preserves an open detail draft. Old filter query parameters
are no longer read or written. Home ticket-count links use a one-shot
`work_entry=tickets:<status>` navigation intent, generated/interpreted by the
same board helper. It replaces conflicting saved clauses with the existing
default view plus the requested ticket-status clause, saves the result and
removes the intent with route replacement while preserving navigation identities.
Plain links restore saved choices. Graph task selection and graph-only filters
retain their existing owners and do not depend on Board preferences.

Completion windows apply only to tasks whose actual status is `complete`,
using `completed_at` and the existing StackOS UTC timestamp parser. Windows
include their lower boundary and exclude future or unknown/invalid timestamps;
Past month means 30 rolling days. All includes unknown dates. Other statuses,
including reopened tasks, are unaffected by completion dates.

Board rows sort by persisted `order_index`, then newest `created_at` and
descending ID, matching the reorder repository. Graph's picker retains its
unfiltered newest-first order. Project switching preserves Board/Graph view
placement and restores the destination project's local Board preferences.
Task/ticket selections and one-shot entry intents are cleared.

## UI

The Work UI provides two views, Board and Graph. It renders:

- Board by default, with eight projected columns (empty columns can be hidden
  through the display choice): Backlog, Todo, In progress, Stopped, Done,
  Aborted, Failed and Skipped. The [membership contract](#board-priority-and-order-writes)
  owns their mapping; labels are not lifecycle values. Explicit Backlog/Todo
  placement uses `tracker.placeTask`; ordering uses `tracker.reorderTask` within
  the projected column. Neither changes lifecycle or execution authority.
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
  Task-status filters apply to tasks; Home ticket-count entry links explicitly filter
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
  Board task cards open that task's Graph in one click. Their existing card menu
  and the map's explicit Task details action open a task popup independently of
  the map selection. Ticket details inside it offer
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

`tracker.get(projection="ui")` is an additive summary contract. Default `full`
and raw agent/detail reads retain every primary field. UI discovery uses
`task_index_only=true, include_graph=false`; it returns every matching task's
identity, lifecycle, projected Board column, rank, workflow/owner/search facts,
dates and complete ticket rollup. The task scope is complete independently of
metric demand. Existing Board clauses and completed-display preferences select
demand from these facts; no new hidden-column controls are needed.
Summary `metadata_json` contains only the string-valued
`delivery_scope.resume_condition`, preserving the existing deferred Board
subtitle. All other metadata stays with full inspection; no full task metadata
or receipt JSON is hydrated for that fact.

`metric_demand: {task_keys: [], ticket_keys: []}` names at most 200 identities,
including any caller overscan, in one batch. Empty demand means no metrics.
The backend adds **zero overscan**: only exact demanded keys enter receipt reads,
decoding, duration and usage aggregation. Unrequested `agent_stats`,
`work_duration` and `active_execution` are null, meaning **not requested**, never
zero, unavailable measurement coverage, or Live. Demanded values use the same
canonical receipts, partial coverage, invocation identity and final precedence
as full reads. `projection`, `scope_complete`, `metric_scope="demand"` and echoed `metric_demand` describe
that distinction explicitly; compact mode preserves the complete UI projection.
UI consumers own a measured viewport allowance within the 200-identity cap,
coalescing Board/filter/completed-display or selected Graph demand at the shared
reader. Hiding a column or selecting Graph removes its Board metric keys.

A selected UI graph requires `task_key` and reads that task directly, without
hydrating unrelated task JSON. It retains lightweight ticket hierarchy,
dependencies, counts and safe external link navigation; graph node data contains
record identity references instead of a second copy of context/evidence or card
metrics. Task/ticket summaries omit primary instructions, context and evidence;
full focused inspection remains the owner of those fields. A current full
focused graph may supply the detail dialog's primary records directly when its
project/task/revision covers the current index and requested revision. Summary
graphs, foreign scope and older revisions require a full detail read. Secondary
context/results/history panes retain their existing reads and hierarchy.

The existing project/global timeline stream accepts `ui_demand`, JSON encoding
`{"projects":{"1":{"task_keys":["core-tracker"],"ticket_keys":[]}}}`.
Composite project/key identity and a combined 200-identity cap apply. Project
streams reject foreign project demand; missing/foreign tasks or tickets reject
before streaming. UI frames retain complete lightweight selected scope and
revision/dirty facts, but metrics only for explicit demand. Each frame is a
current replacement (`replace=true`), including cleared demand and hidden
entities' null metrics. Global scope includes active available projects, with
per-project revisions; the project store remains display-name/deletion truth.
Admission rejects unavailable or foreign demand. Once admitted, a global stream
reconciles its frozen demand with the current active/available projects: archive,
cleanup or deletion removes that project's metrics and transient executions from
the next complete replacement, while surviving status events keep their durable
cursor. A scope change itself triggers a transient replacement without advancing
that cursor. Reconnecting performs strict admission again.
Omitting `ui_demand` preserves legacy unscoped and task-scoped feed semantics,
durable timeline cursors, and transient usage-frame identity. This option does
not change stream execution-thread or polling ownership.
`ui_demand` and legacy `task_key` cannot be combined: the shared UI transport
keeps complete project/global discovery and expresses selected-view demand only
through explicit metric keys.

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
