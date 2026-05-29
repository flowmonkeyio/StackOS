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

## Workflow Mirroring

`runPlan.create` automatically creates a tracker task and one ticket per
run-plan step. Step dependencies become ticket dependencies.

Lifecycle mirroring is mechanical:

| Run-plan event | Tracker effect |
| --- | --- |
| `runPlan.create` | creates `workflow-{run_plan_id}` task and `workflow-{run_plan_id}-{step_id}` tickets |
| `runPlan.start` | marks workflow task `in-progress` and links the run id |
| `runPlan.claimStep` | marks the matching ticket `in-progress`, assigns the claimer, and stores run id |
| `runPlan.recordStep(success)` | marks the ticket `complete` and stores the step outcome |
| `runPlan.recordStep(skipped)` | marks the ticket `deferred` |
| `runPlan.recordStep(failed)` | keeps the ticket open with `blocker_reason` |

StackOS only mirrors state. It does not invent tickets beyond the concrete
run-plan steps and it does not decide how to repair failures.

For `engineering.customer-support-investigation`, the tracker boundary is
intentionally later than investigation. The support workflow may mirror its own
run-plan steps, but delivery tasks/tickets are created only after the operator
replies with task-creation instructions in the canonical Slack thread. Those
delivery tasks must link back to the Slack thread and include the investigation
summary, validated root cause or bounded uncertainty, fix direction, affected
surfaces, acceptance criteria, and verification expectations. They must also
preserve source refs, canonical Slack thread/message refs, clarification refs,
support conclusion refs, instruction message refs, task handoff refs, source
media refs, forwarded media message refs or an explicit media waiver, and
tracked-delivery refs in task context/metadata or ticket `references_json` so
`tracker.brief` gives the delivery agent enough chat context to continue
without re-investigating. Implementation then proceeds through
`engineering.tracked-delivery`.

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

Lifecycle timestamps follow the visible state. Moving a task or ticket into
`complete` or `deferred` sets `completed_at`; reopening it to `in-progress` or
`not-started` clears `completed_at` so audits do not treat active work as
historically complete.

## Operations

Tracker behavior is registered once as StackOS operations. MCP, REST, CLI, and
UI consume the same contracts.

Agent read operations:

- `tracker.status`
- `tracker.get` (full snapshot; use for UI/debug/list review audits, not routine
  agent context)
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
- `tracker.pick`
- `tracker.release`
- `tracker.linkRunPlan`

REST callers use the generic operation adapter:

```http
POST /api/v1/operations/tracker.get/call
POST /api/v1/operations/tracker.patch/call
POST /api/v1/operations/tracker.rejectTask/call
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
6. When an operator rejects, parks, or supersedes a task or workflow run, call
   `tracker.rejectTask` with either `task_key` or `run_plan_id` plus a reason.
   It is a task-level operation: the task is marked deferred/rejected and every
   child ticket is closed deferred with rejection outcome and metadata.

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
-> runPlan.create
-> tracker.status or tracker.brief
-> runPlan.start
-> runPlan.claimStep
-> do the step
-> runPlan.recordStep
-> tracker.verify
```

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
If the evidence is missing, the ticket should remain open or explicitly
deferred.

For direct/manual work:

```text
tracker.createTask
-> tracker.createTicket
-> tracker.next
-> tracker.pick
-> tracker.brief
-> do the work
-> tracker.updateTicket or tracker.patch
-> tracker.rejectTask when the operator rejects or parks the task
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
dependencies. Historical blocker notes on `complete` or `deferred` tickets are
kept for context, but they do not make the project look actively blocked.

Use `tracker.get` only when the caller needs the full project tracker snapshot
or the UI graph projection. It can be intentionally large because it includes
all requested tasks, tickets, dependencies, links, and optional graph nodes.
Normal agents should prefer `tracker.status`, `tracker.next`, `tracker.brief`,
`tracker.verify`, `tracker.search`, and `tracker.changed` to keep context small.
When `include_graph=true`, the graph projection may include advisory warnings.
These warnings are produced by core tracker analysis and are intentionally
non-blocking. They call out low-confidence review issues such as sparse
dependency plans, many isolated active tickets, likely pre-implementation gate
direction mistakes, or large dependency removals surfaced by dry-run preview.
Agents should review them before implementation, but normal tracker writes still
use the existing hard invariants for missing tickets, self-dependencies, and
cycles.

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
  `blocker_reason` treated as blocked; terminal deferred/complete notes are not
  counted as active blockers. The blocked view keeps the immediate blocking
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
