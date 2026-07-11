# StackOS Desktop Screen Redesign

Status: implementation contract for the reopened desktop redesign delivery.

This document goes below navigation and visual tokens. It defines the job,
information hierarchy, interactions, states, and handoffs for each critical
desktop screen.

## Product boundary

StackOS does not decide or perform work. Connected agents use StackOS through
MCP. The desktop app is the human console for choosing a project, completing
local setup, understanding readiness, supervising agent-created work, resolving
human attention, inspecting outcomes, and recovering failures.

Every primary screen must quickly answer:

1. What is the state that matters here?
2. Why does it matter to me?
3. What, if anything, needs a human action?
4. Where do I go for evidence or technical detail?

Raw object inventory is progressive detail, never the opening composition.

## Cross-screen journey model

```text
Open StackOS
  -> choose a project with meaningful state
  -> understand readiness and the highest-priority condition
  -> resolve human attention OR inspect agent-created work
  -> understand the outcome
  -> expand evidence/audit only when needed

Setup
  -> exact missing prerequisite
  -> Connections / Automation / Spend / Plugins at the relevant section
  -> complete local-admin action
  -> test or recheck
  -> return to an explainable ready/partial state

Supervision
  Home or notification
  -> Attention item / Work story
  -> impact, owner, stage, and next step
  -> exact owning detail surface
  -> evidence and audit trail
```

### Request-to-outcome flow

StackOS is the system of record and execution boundary in this flow, not the
reasoning actor:

```text
Person asks an agent for work
  -> agent or integration records/receives a request in StackOS
  -> agent claims the request through MCP
  -> agent creates or links tracked work and a run plan when needed
  -> person sees the work on Portfolio or project Work
  -> agent advances explicit steps and records evidence
  -> StackOS raises a human-owned issue in Attention when needed
  -> person resolves the approval/setup/blocker at the owning surface
  -> agent continues and records the outcome
  -> person reviews the grouped result in Activity, then expands raw audit if needed
```

The desktop must never imply that a human submits a prompt to StackOS or that
StackOS autonomously decides the work. Agent Requests is an intake and handoff
surface. Work is the supervision surface. Attention is the human-intervention
surface. Activity is the result and evidence surface.

### Connect-a-tool flow

```text
Setup or Attention identifies a missing service
  -> Connections opens on Services
  -> person chooses the service and account/auth method
  -> person enters credentials into the local daemon
  -> StackOS stores the credential and tests it when supported
  -> connection shows healthy or an exact repair action
  -> agent resolves the safe profile reference through MCP
  -> agent explicitly calls the provider action
  -> StackOS records the action and provider result for Activity/audit
```

Connections must say what becomes possible after saving, keep secrets out of
the rendered result, and distinguish “credential stored” from “connection
tested.”

### Project-switch continuity

Changing the project in the persistent switcher keeps the current operating
surface: Work stays Work, Activity stays Activity, Connections stays
Connections, and so on. Portable filters such as view, section, status, and
focus are retained. Object-specific selections such as task, request, run,
credential, or provider ids are cleared because they belong to the old
project. A run-detail route returns to the new project's Runs list. The root
router view is keyed by project id so the preserved surface reloads its data
under the new scope.

## Portfolio `/`

**User question:** “What is happening across all my projects, what is stuck,
and where should I go next?”

**Current failure:** A project directory alone is not an operating overview.
Names and updated timestamps do not reveal active work, delivery load,
blockers, or portfolio progress.

**Target composition:**

1. Compact local runtime health and recovery actions.
2. Portfolio metrics: projects with active work, active top-level tasks,
   in-progress delivery steps, and blocked steps.
3. Every active top-level work stream across active projects, with its project,
   owner when known, child-work count, blocker count, and a direct Work link.
4. An accessible workload-by-project chart with exact text counts and tracked
   completion alongside relative bars.
5. Search and active/archived project directory.
6. A featured current workspace carrying real active-work and completion
   context.
7. Remaining project rows with active/blocked/idle state rather than a generic
   “open to inspect” label.

Portfolio reads use generic `tracker.status` and filtered `tracker.get` calls
per active project with bounded concurrency. Partial failures retain successful
project data and explicitly state how many projects could not be read. Project
rows must never infer health from timestamps.

States: useful create/connect empty state; stable loading rows; runtime health
retained during list errors; explicit archived-only filter state.

## Home `/projects/:id`

**User question:** “Is this project ready, what needs me now, and what are
agents doing?”

**Current failure:** Readiness, attention, active runs, and recent events are
stacked feeds. There is no dominant state, and tracker noise displaces outcomes.

**Target composition:**

1. **Project state banner:** ready, partially ready, attention required, or
   runtime unavailable, explained in one sentence.
2. **Next best action:** highest-impact human-owned issue with one exact route.
   If no human action exists, show the most relevant active work.
3. **Supervision strip:** active work, blocked work, questions/approvals, and
   recent outcomes, with counts that name their meaning.
4. **Active work stories:** at most three, showing agent identity when known,
   current stage, elapsed time, and owning route.
5. **Recent outcomes:** grouped results before raw events.
6. Runtime/version metadata in a disclosure or footer.

Priority: runtime unavailable > setup blocker > approval/question > failed work
> blocked work > active work > recent outcome > ready empty state.

## Attention `/projects/:id/inbox`

**User question:** “What specifically needs a person, why, and what should I
do?”

**Current failure:** The master/detail frame is useful, but detail is too thin.
Items do not consistently explain impact, ownership, evidence, or success after
the handoff.

**Target composition:**

1. Filters: All, Questions/approvals, Blocked, Failures, Setup, Spend.
2. Rows show impact, age, owning agent/run when known, and a short reason.
3. Detail shows what happened, why it matters, who owns execution, what the
   human can do, what happens afterward, and evidence/technical context.
4. One primary action routes to the exact selected object or setup section.
5. Related work/activity links are secondary.

Query state preserves `item`; destination queries preserve source context where
supported.

## Work `/projects/:id/tasks`

**User question:** “What are agents working on, what is stuck, and what has
been verified?”

**Current failure:** The three Work modes feel like different pages because
each mode owns different page chrome, filters, and width behavior. The graph's
selected state also competes with the information it is meant to reveal.

**Target composition:**

1. Default **Dependency map** mode: show the sequence and blockers immediately.
2. One task selector, view switcher, and filter surface remains in the same
   position across Dependency map, Stories, and Tickets.
3. Stories offers Active, Needs attention, Recently completed, and All. Rows
   show goal, stage, outcome/next step, terminal/total progress,
   agent/owner when known, and last activity.
4. Selection opens plain-language story first: goal, current stage, blockers,
   expected transition, latest outcome/evidence.
5. Ticket list remains a precise inventory without introducing another page
   header or content wrapper.
6. A selected graph node uses a stable, rounded selection ring. Active state
   must not pulse or blink.
7. Global totals remain compact and subordinate to the task in focus.

The UI remains read-only for agent/run-plan lifecycle state.

## Workflow templates `/projects/:id/workflow-templates`

**User question:** “What will this workflow help an agent accomplish, what do
I need to prepare, where will a person be involved, and what do I get?”

**Current failure:** The detail drawer opens on metadata, project JSON, preset
references, and a linear technical step list. A non-technical user cannot see
the route through the work or distinguish what StackOS stores from what the
connected agent does.

**Target composition:**

1. A wide workflow guide begins with purpose and good-fit/not-a-fit guidance.
2. A Vue Flow journey shows the stage order and dependencies. Selecting a
   stage explains what happens, who handles it, what it needs, what it produces,
   the human checkpoint, completion condition, and next handoff.
3. Readiness groups information, connections/tools, and people. It explicitly
   says the connected agent performs the work through MCP while StackOS keeps
   state, grants, approvals, and progress.
4. Named outcomes and project-specific setup are visible without reading JSON.
5. Exact IDs, refs, policies, presets, and overrides remain available in one
   technical-contract disclosure.

The journey has a readable list alternative on narrow screens and does not use
animated or simulated “work in progress” states.

## Activity `/projects/:id/activity`

**User question:** “What changed, what was the result, and can I inspect the
audit trail?”

**Current failure:** A newest-first stream is dominated by repetitive tracker
transitions. Audit is visible, but outcomes are obscured.

**Target composition:**

1. Filters: Outcomes, Work, Connections/setup, Messages, System, Audit.
2. Default to outcomes and grouped work episodes.
3. Group adjacent transitions for the same task/run into one episode with
   start/current/final summary.
4. Master/detail: human summary left; selected actor, refs, and audit fields
   right.
5. Raw audit is explicit progressive detail.

Grouping must retain failures, side-effect identifiers, and retry guidance.

## Setup `/projects/:id/setup`

**User question:** “Can connected agents use this project safely, and what is
the next setup step?”

**Current failure:** A numeric score and “ready” coexist with a vague “Fix.”
Available action counts look like requirements even when providers are optional.

**Target composition:**

1. State: Ready, Ready with optional setup, Needs setup, or runtime unavailable.
2. Ordered checklist by human goal: runtime/binding; agent clients; services
   required by selected work; automation/spend guardrails; optional capability.
3. Every partial/failed check explains impact and has one exact repair route.
4. Optional setup is visually separate from blockers.
5. Technical runtime/project inventory is a disclosure.

Avoid a synthetic percentage unless each weighted component has stable,
user-explainable meaning.

## Connections `/projects/:id/connections`

**User question:** “Which services are connected, which need repair, and how do
I safely add one?”

**Current failure:** Services, bots, channels, destinations, handoff rules,
connectivity, and diagnostics are peer tabs rather than one setup journey.

**Target composition:**

1. Default **Services** view with provider groups, status, safe account label,
   last tested time, expiry, and exact repair/test actions.
2. Add connection flow: provider -> auth method -> credential input -> test ->
   success. Secret input is never echoed after submission.
3. Services needing repair precede healthy services.
4. Messaging becomes a secondary grouped area: identities, places,
   destinations/handoffs, and inbound connectivity.
5. Diagnostics is a technical disclosure, not a peer primary tab.
6. Query `section` lands users on services, messaging, connectivity, or
   diagnostics from Setup/Attention.

## Secondary inspection screens

Runs, agent requests, action calls, templates, presets, capabilities,
operations, resources, and project data remain generic object views. Their
shared rule is list/master-detail with bounded scrolling, summary first, exact
refs/payloads second. They do not compete with the primary operating routes.

Agent Requests additionally exposes its four-stage handoff in plain language:
request arrives, agent claims, tracked work becomes visible, and outcome is
recorded. Its detail view tells the operator what happens next and links to
Work, Runs, or Activity when those destinations exist.

## Verification contract

The pass is not complete when colors, labels, or navigation change. Evidence
must demonstrate:

- a different first viewport and hierarchy on every critical screen;
- fewer competing primary actions;
- exact setup and recovery handoffs;
- progressive disclosure of graphs, raw events, diagnostics, and refs;
- useful empty, loading, partial, and error states;
- keyboard and screen-reader operability;
- browser proof at desktop and compact widths;
- packaged app startup, restart, and doctor proof.
