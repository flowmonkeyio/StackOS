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

The same status vocabulary applies to tasks and tickets. `deferred` means
postponed and still potentially relevant later. `aborted` means intentionally
stopped, cancelled, or rejected. `failed` means attempted and unsuccessful.
`skipped` means intentionally not executed. Supersession is a relationship or
disposition in metadata/links, not a lifecycle status; do not use `deferred` as
a generic replacement for "moved elsewhere."

## Workflow Mirroring

`runPlan.create` automatically creates a tracker task and one ticket per
run-plan step. Step dependencies become ticket dependencies.

Workflow selection happens before ticket creation. When an operator explicitly
asks to use a workflow, engineering workflow, StackOS workflow, or "the
workflow", agents must create or resolve the workflow-backed run plan before
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

For workflow-backed delivery, graph readiness should be visible:

- the first executable child ticket for a workflow step depends on the mirrored
  step ticket
- the next mirrored workflow step depends on the terminal child ticket or
  tickets from the prior step
- delivery verification, docs, signoff, and release tickets sit inside the
  dependency graph, not beside it
- no detached delivery, test, docs, or signoff branch can become ready
  independently of the workflow order
- completing a mirrored workflow step while attached child tickets remain open
  is an important tracker-truth signal, not an automatic execution blocker

`runPlan.claimStep` and `runPlan.recordStep(success)` enforce run-plan
lifecycle, approvals, and transitive step dependencies. They do not hard-block
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

## Operations

Tracker behavior is registered once as StackOS operations. MCP, REST, CLI, and
UI consume the same contracts.

Agent read operations:

- `tracker.status`
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

Agent write operations:

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
-> plan-tickets
-> design-approach
-> review-design
-> design-tests
-> deliver-tickets
-> verify-delivery
-> review-delivery
-> audit-tracker
-> release-closeout
```

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
When `include_graph=true`, the graph projection may include advisory warnings.
These warnings are produced by core tracker analysis. Generic warnings call out
review issues such as sparse dependency plans, many isolated active tickets,
likely pre-implementation gate direction mistakes, or large dependency removals
surfaced by dry-run preview. Workflow-spine warnings are stronger tracker-truth
signals: missing child bridges, detached child branches, next-step handoff gaps,
and open children under a workflow step should be investigated and may be
blocking for tracker audit or release closeout when they hide required work.

## UI

The Tasks UI is a generic project work map. It renders:

- metrics for tasks, tickets, in-progress work, and blockers
- filters by workflow, status, assignee, and search text
- a task index where each task row shows status, source/workflow, priority,
  done/total ticket count, completion percent, and current detail
- one focused Vue Flow graph at a time so large task graphs remain readable
- dependency-tree graph as the primary visualization, with ticket nodes laid out
  left-to-right by prerequisite/dependent relationships
- optional nested task-box view for compact containment inspection
- dependency edges between tickets, kept visually quiet at large scale
- graph-local status and block filters. Status filters select tickets by
  lifecycle status. Block filters use unresolved dependency state, with open
  `blocker_reason` treated as blocked; terminal notes are not counted as active
  blockers. The blocked view keeps the immediate blocking
  ticket visible so the dependency edge still explains why work is blocked. The
  graph header reports the visible ticket and edge counts when filters are
  active, not the full task totals.
- edge click highlighting for the selected dependency chain. Unrelated nodes
  and edges dim while upstream and downstream dependencies remain readable.
- a minimap and controls for navigating large task graphs
- ticket table for dense scanning
- detail panel for selected task/ticket context

The UI is for humans to navigate and inspect. Agents should use tracker
operations directly for writes.

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

- Do not store secrets in tracker fields. Metadata, context, references, and
  patches are redacted.
- Do not add provider or workflow-specific tracker logic. Domain behavior
  belongs in plugins, templates, run plans, and agents.
- Do not add bespoke REST routes for tracker behavior. Register operations once
  and use the generated MCP/REST/CLI surfaces.
- Do not use the tracker as a replacement for run-plan grants. Workflow writes
  still require active run-plan step grants where applicable.
