# StackOS Desktop Product and Experience Direction

Status: canonical product contract for the desktop redesign

Screen-level implementation reference:
[`ui-redesign-wireframes.md`](./ui-redesign-wireframes.md) and
[`ui-screen-redesign.md`](./ui-screen-redesign.md). The latter is the source of
truth for first-viewport hierarchy, screen interactions, and cross-screen
handoffs; visual alignment alone does not satisfy this redesign.
Last reviewed: 2026-07-10

This document defines what the StackOS desktop app is, what it is not, which
actors own each action, the user flows the app must support, and the boundaries
that must be proven before implementation resumes.

It supersedes the earlier website-led redesign direction. That direction
incorrectly treated StackOS as an AI assistant that could receive a user request
and perform work. Delivery is paused until the implementation and tracker agree
with this contract.

Authoritative foundations:

- [`architecture.md`](./architecture.md): StackOS is storage, execution, and
  audit for agent-operated tools; it is not a strategy engine.
- [`agent-operating-model.md`](./agent-operating-model.md): an agent is the
  MCP/tool consumer; the agent decides what should happen.
- [`product-direction.md`](./product-direction.md): humans set up, approve,
  inspect, and repair; agents execute.
- [`ui-usability-audit.md`](./ui-usability-audit.md): the UI is an inspection
  and local-admin setup surface, not the workflow engine.
- [`ui-design-system.md`](./ui-design-system.md): the product UI is calm,
  dense, predictable, observer-first, and operational rather than promotional.

## 1. Product constitution

StackOS is a local operating layer used by AI models, agents, automations, and
scripts through MCP, REST, or CLI. It provides:

- project-scoped durable state;
- plugin, capability, provider, workflow, and operation catalogs;
- daemon-held credentials and safe credential references;
- workflow templates, concrete run plans, scoped grants, and approvals;
- tracker tasks, tickets, dependencies, and evidence;
- explicit provider action execution;
- resources, artifacts, decisions, learnings, and context;
- complete redacted audit history;
- local lifecycle, MCP registration, readiness, and repair.

StackOS can validate, persist, resolve credentials for, and execute an explicit
call. It does not reason, choose strategy, invent a plan, select the next action,
or initiate business work on its own.

The desktop app is the human console for this runtime. It makes agent behavior
legible and manageable. It does not replace the agent.

The control loop is:

```text
human intent in an AI host
-> AI model or agent decides what to do
-> agent calls StackOS through MCP
-> StackOS validates, persists, executes explicit calls, and audits
-> desktop shows state to the human
-> human sets up, approves, inspects, or repairs when required
-> agent continues through MCP
```

### Non-negotiable language rule

The desktop must never imply:

- “Ask StackOS to do something.”
- “StackOS created your plan.”
- “StackOS is working on your request.”
- “Start a workflow” when the click would turn the human into the workflow
  operator.
- “AI assistant,” “chat,” or other language that assigns agency to StackOS.

Correct subjects are explicit:

- “Your agent created a run plan.”
- “This run is waiting for your approval.”
- “Claude Code is connected through MCP.”
- “The daemon executed this action using credential ref …”
- “Open your AI tool to request new work.”

## 2. Actors and responsibilities

| Actor | Owns | Does not own |
| --- | --- | --- |
| Human in Codex, Claude, Gemini, or another host | Business intent, judgment, risk decisions, outcome review | StackOS operation sequencing |
| AI model or agent | Strategy, workflow selection, run-plan authorship, tracker lifecycle, next actions, provider payloads | Secrets or daemon state internals |
| Agent host | Conversation, project files, host-local skills/agents, filesystem tools | StackOS project scope or credential custody |
| StackOS MCP bridge | Workspace resolution, project scoping, compact tool routing | Strategy or cross-project choice |
| StackOS daemon | Validation, persistence, grants, credential resolution, explicit execution, audit | Autonomous business decisions |
| Desktop app | Local-admin setup, readiness, observation, approvals, recovery, audit | General workflow execution or agent replacement |
| Provider connector | One validated external operation | Workflow judgment |

## 3. Desktop mutation boundary

The default UI posture is read-first and observer-first.

### Desktop-owned writes

These are valid because they are local administration, human policy, or
lifecycle responsibilities:

- create or connect the first deliberate project/workspace;
- connect, test, refresh, or revoke provider credentials;
- configure static communication profiles, surfaces, targets, routes, and
  ingress when the UI is the approved local-admin surface;
- approve, deny, reject, or cancel where the operation contract explicitly
  requires a human decision;
- configure budgets, schedules, notification preferences, and other explicit
  policy when registered as local-admin operations;
- install, repair, restart, update, back up, restore, or uninstall StackOS;
- repair MCP registration for supported agent hosts;
- retry a lifecycle or credential diagnostic owned by the human side.

### Agent-owned writes

These must remain in the MCP/operation path unless a separately reviewed
operator override exists:

- choose or adapt a workflow;
- create, validate, start, claim, or record a run plan;
- create and update normal tracker tasks and tickets;
- decide the next step or business strategy;
- execute provider actions;
- create resources, artifacts, decisions, learnings, experiments, or evidence;
- retry or alter agent execution logic.

### Required audit before delivery

Every current UI mutation must be classified against this table. In particular,
the current task-status controls that call `tracker.updateTask` need an explicit
decision: remove them, limit them to valid operator overrides, or document why
the UI is authorized to own that transition. Generic operation transport is not
itself permission for a product view to mutate agent-owned state.

### Audited implementation boundary

The July 2026 UI audit classifies the current write surfaces as follows:

| Surface | Owner | Decision |
| --- | --- | --- |
| First deliberate project creation | Local administrator | Keep as the bounded first-use exception |
| Provider credential store/edit, authorization start, test, and revoke | Local administrator | Keep under Connections/Setup |
| Communication profile upsert | Local administrator | Keep as static communication setup |
| Ingress configure, refresh, and sync | Local administrator | Keep as connectivity setup and repair |
| Tracker task status mutation | Agent or run-plan controller | Removed from the task drawer |
| Tracker, run, request, catalog, and execution-context reads | Observer | Keep read-only |

`ui/src/read-only-ui.spec.ts` keeps raw writes centralized in the project/auth
stores and generic operation client. The daemon then authorizes operation calls
from the operation registry: reads follow `read_only`, while the four reviewed
local-setup writes must declare `browser_safe=True` on their REST surfaces.

## 4. People and their management jobs

### First-time owner

Needs StackOS installed, the daemon healthy, an AI host registered through MCP,
a real project bound, and only the credentials needed by current agent work.

### Business operator

Needs to see what agents are doing, what requires a decision, what failed, what
finished, and where the result is, without understanding internal schemas.

### Team lead

Needs cross-project health, active work, risk, approvals, ownership, cost, and
recent outcomes.

### Technical operator

Needs exact run plans, steps, grants, credential refs, action calls, inputs,
redacted outputs, artifacts, events, and diagnostics.

### Agent-assisted user

Starts work in an AI host. Opens the desktop only to set up a missing
prerequisite, approve or inspect a consequential action, monitor progress,
repair infrastructure, or review evidence.

## 5. Correct end-to-end user flows

Each flow identifies the initiating actor, the desktop job, and the source of
truth. Empty, partial, error, recovery, and deep-link states are required.

### F1. Install and first launch

```text
human opens StackOS.app
-> branded operational splash
-> initialize or verify local state
-> start or connect to daemon
-> verify database, launchd, bundled assets, and browser runtime
-> register supported MCP agent clients
-> open lightweight readiness / project selection
```

The splash reports lifecycle phases. It must not resemble an AI thinking or
chat state. Failure explains the problem and offers the canonical repair path,
not raw JSON.

Source of truth: desktop lifecycle service and structured doctor/readiness
results.

### F2. Register an AI host

```text
Setup -> Agent clients
-> Codex / Claude Code / Claude Desktop / Gemini status
-> healthy, missing, stale, unsupported, or restart-required
-> repair registration when StackOS owns it
-> verify the saved stdio MCP bridge command and runtime
-> explain host restart when required
```

This is neutral connectivity. StackOS does not install global domain behavior
or pretend to own the host’s filesystem/project guidance.

Source of truth: `stackos.host_mcp` lifecycle adapters and doctor diagnostics.

### F3. Connect or resolve a project

```text
agent calls workspace.startSession from a real workspace
-> daemon reuses or creates a deliberate binding
-> desktop portfolio shows the project and bound workspace
```

For a folderless desktop host:

```text
agent receives project identity requirement
-> human deliberately selects an existing project or names a new workspace
-> workspace.connect / workspace.bootstrap establishes the binding
```

The desktop must not silently bind `Resources`, `Contents`, `MacOS`,
`StackOS.app`, `/`, or another app-internal folder as a project.

Source of truth: daemon-owned workspace bindings and project metadata.

### F4. Connect a provider requested by an agent

```text
agent selects a workflow or action
-> readiness.check returns one scoped missing provider
-> agent gives human the exact Connections link
-> desktop explains why the provider is needed
-> human connects and tests the account
-> desktop shows safe account/ref/status only
-> human returns to the AI host
-> agent rechecks readiness and continues
```

The desktop does not ask users to connect every possible provider. It does not
send secrets back to the agent.

Source of truth: auth provider contracts, credential-account state, scoped
readiness, and auth test diagnostics.

### F5. Agent starts workflow work

```text
human asks the AI host for multi-step work
-> agent resolves a workflow template and project guidance
-> agent checks readiness and adapts required presets
-> agent validates, creates, and starts a run plan through MCP
-> workflow steps mirror into tracker state
-> desktop Home / Work / Activity update
```

Desktop role: observe the concrete state, explain it, and link to technical
truth. There is no desktop request composer and no generic “Start workflow”
button.

Source of truth: workflow template/version, run plan, run, tracker, grants, and
audit.

### F6. Agent performs a direct action

```text
human asks the AI host for one explicit action
-> agent describes/checks readiness for the action
-> agent confirms intent and calls action.run through MCP
-> daemon resolves credential and executes
-> action call and cost/audit records appear in desktop
```

Desktop role: show what happened, the target, safe credential ref, result,
idempotency/retry status, and any repair guidance.

Source of truth: action spec/version and action-call audit row.

### F7. Monitor active agent work

```text
Home compact active-work section
-> Work queue
-> select task or run plan
-> see goal, source agent/host when known, current step, elapsed time,
   blockers, approvals, grants, and recent evidence
-> open run / tracker / action detail for technical depth
```

The UI must say “the agent” or name the host/actor when known. It must not say
“StackOS is doing …”.

Source of truth: tracker, run plans, runs, steps, approval requests, links, and
evidence records. Human summaries may join durable relationships but must not
invent intent or outcomes.

### F8. Human approval or rejection

```text
agent reaches an approval gate or high-risk action
-> durable approval request + native notification
-> Attention opens exact context
-> human sees requested action, reason, scope, target, risk, and consequence
-> approve or deny through the registered approval operation
-> agent observes the decision and continues or stops
```

Source of truth: approval request, policy/grant snapshot, related run step, and
decision audit.

### F9. Agent request or question

```text
agent creates a request for human input
-> Attention groups it as a question, not a failure
-> human reads full context and responds through the supported channel/path
-> request status changes durably
-> agent resumes through its host/session
```

Source of truth: agent request and related project/run/tracker refs.

### F10. Failure and repair

```text
run, credential, action, daemon, or MCP registration fails
-> notification and Attention item
-> desktop classifies ownership:
   human setup / lifecycle repair OR agent execution repair
-> show exact evidence and safe next action
-> correct actor performs repair
-> original state remains auditable
```

The desktop may repair daemon, registration, credentials, or static setup. It
must not silently rewrite an agent plan or retry an external side effect without
the operation contract and human intent permitting it.

### F11. Completed work, result, and evidence

```text
agent completes work
-> completed notification
-> Activity milestone and Work result state
-> human opens outputs, artifacts, decisions, affected resources, action calls,
   cost, and verification evidence
-> technical audit remains one click away
```

Source of truth: terminal run/tracker state, resource/artifact links, action
calls, decisions, and completion evidence. The UI does not synthesize a success
claim that is absent from durable evidence.

### F12. Browse what agents can use

```text
Catalog
-> plugins / capabilities / workflow templates / operations / presets
-> availability, required provider, readiness, grants, example request,
   expected outputs, and contract detail
-> open exact setup surface OR copy/open a starter request in the AI host
```

This is discovery and inspection. It is not a second workflow engine. Labels
are “Available to agents,” “Setup required,” “Inspect contract,” and “Open in
AI host,” not “StackOS will do this.”

### F13. Multi-project supervision

```text
root portfolio
-> projects with binding, readiness, active work, attention, and last outcome
-> select a project
-> project-scoped Home
```

System health is global and compact unless it blocks entry. Project activity
and readiness must never leak across project scope.

### F14. Update, repair, backup, and uninstall

```text
desktop reports update or lifecycle issue
-> explain impact and preserved state
-> optional backup before risk
-> canonical update/repair/uninstall operation
-> restart daemon / refresh registrations
-> postflight doctor
-> return to prior project context
```

Source of truth: canonical lifecycle service. Default uninstall preserves the
project database and user-owned state unless destructive cleanup is explicit.

## 6. Information architecture

The primary project navigation answers the five highest-frequency human
questions:

1. **Home** — Is the project ready, what needs me, what are agents doing, and
   what finished recently?
2. **Attention** — Which approvals, questions, failures, or setup problems need
   a human now?
3. **Work** — What work have agents created, what is active or blocked, and
   where are the results?
4. **Activity** — What changed, in chronological and auditable order?
5. **Setup** — Are the runtime, MCP clients, plugins, providers, policies, and
   schedules ready?

The root route is a lightweight portfolio and system/lifecycle surface. It is
not a marketing landing page.

Secondary groups remain addressable and searchable:

- **Setup:** Agent clients, Connections, Automation, Spend, Plugins, System.
- **Execution inspection:** Runs, run plans, agent requests, action calls,
  approvals.
- **Catalog:** Capabilities, Operations, workflow templates, agent presets.
- **Data and evidence:** Resources, artifacts, project data, context, decisions,
  learnings, experiments.

“Inspect” may be a secondary entry or contextual action, but it is not a reason
to hide important execution evidence. “Explore” is not a primary project lane;
catalog discovery belongs in the secondary Catalog group and Setup readiness
links.

Deep links remain stable. Existing task, run, action-call, agent-request,
resource, provider, and setup URLs must continue to resolve.

## 7. Screen contracts

| Surface | Primary inspection question | Valid human actions |
| --- | --- | --- |
| Portfolio | Which project needs me or has active work? | Select, deliberate create/connect, lifecycle repair |
| Home | What matters in this project now? | Navigate, refresh, open attention/setup/detail |
| Attention | What requires a human and why? | Approve/deny, answer, open setup, lifecycle repair |
| Work | What agent-created work exists and what state is it in? | Inspect, filter, navigate; explicit operator override only if authorized |
| Activity | What changed and where is the evidence? | Filter, search, inspect |
| Setup | What prevents agents from using this project safely? | Local-admin setup and repair |
| Connections | What providers can agents safely use? | Connect, test, revoke, repair |
| Agent clients | Which MCP hosts are registered and healthy? | Repair StackOS-owned registration, explain restart |
| Catalog | What contracts are available to agents? | Inspect, open setup, copy/open host request |
| Runs | What executed and what is its exact state? | Inspect; approval/rejection only through explicit contract |
| Action calls | What explicit call happened? | Inspect, copy safe refs, follow repair guidance |
| Resources/artifacts | What durable outputs exist? | Inspect/export where authorized |
| System | Is the local runtime healthy? | Install, doctor, restart, repair, update, backup, uninstall |

Every page should answer one inspection question in its first viewport. Use
master/detail for large inspectable collections. Keep metrics compact and do not
push the list or selected object below promotional content.

## 8. Visual and interaction direction

The website and app share identity, not layout grammar.

The public website may be editorial and promotional. The product UI remains a
desktop operations console:

- calm, dense, predictable, and keyboard-readable;
- current semantic tokens remain the implementation source of truth;
- 13/18-style operational body density and approximately 32px controls/rows;
- subtle borders before heavy shadows;
- restrained radii, with 8px as the normal maximum for product surfaces;
- blue as the primary interaction accent;
- semantic green, amber, red, blue, and neutral only for real state;
- compact headers and metric strips;
- no decorative grid background behind operational data;
- no oversized website hero sections inside project pages;
- no lime decorative signal that could be confused with readiness or success;
- no promotional workflow-card wall as a primary route;
- visible labels, statuses, timestamps, sources, and selected states;
- reduced motion, visible focus, zoom support, and WCAG AA contrast.

Brand alignment should prioritize:

- consistent StackOS name, icon, app bundle icon, favicon, and splash;
- compatible typography and voice, tested at operational density before any
  font migration;
- shared core accent identity without importing website composition;
- native startup, failure, update, notification, and repair surfaces that feel
  like the same product.

### Splash contract

The splash is a lifecycle surface, not an AI state:

- show the StackOS identity and the current startup/repair phase;
- use real progress states when available;
- avoid “thinking,” assistant, conversation, or autonomous-work metaphors;
- transition directly into readiness or the last valid project;
- provide a human repair screen if startup fails;
- render crisply at normal and Retina scale;
- match the installed app icon and native failure/update visuals.

## 9. Human-facing read models

New projections are justified only when they join durable StackOS truth for
more than one consumer. They must not invent strategy, intent, or outcomes.

Potential generic reads:

- project supervision summary: binding, readiness, active runs, attention, last
  terminal outcome;
- attention list: approvals, agent requests, blocked/failed runs, credential and
  lifecycle problems with exact refs;
- work queue: tracker tasks/tickets joined to run plans/runs and terminal
  evidence;
- activity timeline: existing events grouped only where provenance remains
  complete;
- agent-client readiness: supported host registration and restart/repair state;
- scoped setup/readiness: missing provider, plugin, client, or lifecycle
  prerequisite for a known workflow/action.

Each projection must be:

- operation-backed and domain-neutral;
- read-only unless its operation explicitly represents a human admin decision;
- project-scoped;
- safe-ref only;
- available through allowed adapters consistently;
- tested for missing, partial, stale, error, and large-history states;
- traceable to source objects.

Do not use page-specific client fan-out to infer critical meaning when a shared
read model is required. Do not add a projection merely to rename internal state
or create a marketing story.

## 10. Delivery re-entry and cleanup

Implementation must not resume directly from the current worktree. The safe
sequence is:

1. Freeze this product constitution and flow map in docs and tracker.
2. Inventory every file changed by the invalid implementation slice and
   distinguish it from pre-existing user work.
3. Revert or rewrite only the invalid slice: request composer, human workflow
   launch controls, primary Explore lane, promotional shell, website-like
   density, and incorrect splash language.
4. Audit every existing UI mutation against Section 3 and create explicit
   tickets for violations or approved exceptions.
5. Restore a green baseline: UI build/tests, desktop checks, and route smoke.
6. Produce low-fidelity reference frames for Portfolio, Home, Attention, Work,
   Activity, Setup, Connections, run detail, and startup/failure.
7. Review the frames against the actor boundary and one-page/one-question rule.
8. Implement in small vertical slices with tracker evidence.

Recommended waves after re-entry:

- **Wave A — cleanup and constitution:** remove the invalid slice, mutation
  audit, route/source-of-truth matrix.
- **Wave B — lifecycle and first use:** splash, runtime readiness, agent-client
  registration, project binding, recovery.
- **Wave C — supervision core:** Home, Attention, Work queue, Activity,
  notifications, deep links.
- **Wave D — setup:** Connections, scoped readiness, Automation, Spend, Plugins,
  System.
- **Wave E — inspection:** Runs, action calls, agent requests, catalog, data,
  evidence, master/detail consistency.
- **Wave F — packaged verification:** accessibility, performance, notifications,
  deep links, lifecycle, installed-app restart, and release proof.

## 11. Verification contract

### Product-boundary proof

- No screen addresses StackOS as an AI actor.
- No primary UI control creates/starts agent work without an explicitly reviewed
  operator contract.
- Every mutation is classified as local admin, human approval/policy, lifecycle,
  or agent-owned.
- The existing restricted-write test is extended to cover operation names and
  view-level intent, not only raw HTTP verbs.

### Flow proof

- F1-F14 each have happy, empty, partial, error, recovery, and deep-link cases.
- First launch proves MCP client registration and real workspace binding.
- Provider setup proves credentials stay daemon-side and the agent can resume.
- Workflow and direct-action flows start in an AI host and appear in desktop
  only after durable StackOS state exists.
- Approval and failure flows prove the correct actor owns the next action.
- Results prove traceability to artifacts, action calls, decisions, and
  completion evidence.

### Visual and accessibility proof

- 1280x800 and 1440x900 operational desktop captures;
- compact-window behavior without hiding labels or state;
- keyboard navigation, visible focus, screen-reader names, reduced motion,
  zoom, and WCAG AA contrast;
- first-viewport main-object review for every route;
- normal and Retina splash/startup capture;
- no promotional composition inside operational pages.

### Engineering proof

- UI build and complete unit/component suite;
- focused operation/repository tests for new read models;
- browser route and deep-link smoke;
- desktop doctor and focused lifecycle tests;
- packaged unsigned development build;
- install/repair smoke and manual restart from the installed app before desktop
  signoff;
- tracker graph with no detached implementation or verification branch;
- docs, implementation, tracker status, and evidence agree.

## 12. Definition of done

The redesign is complete only when:

- a first-time user can install StackOS, understand its relationship to their AI
  host, connect a real project, and repair MCP registration without developer
  vocabulary;
- a returning operator can identify active agent work, human attention, setup
  problems, recent outcomes, and runtime health within seconds;
- the desktop never presents StackOS as the agent;
- agents remain the owners of strategy, run-plan execution, tracker lifecycle,
  and provider action intent;
- humans can safely set up, approve, inspect, and repair;
- technical operators retain complete generic audit and registry access;
- the product UI is visually aligned with the StackOS identity while remaining
  dense and operational rather than promotional;
- splash, startup, failure, notifications, deep links, update, repair, and
  installed-app lifecycle feel like one product;
- all required tests and packaged evidence pass;
- the tracker tells the same story as the code and this document.
