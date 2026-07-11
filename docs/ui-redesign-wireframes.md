# StackOS Desktop Operational Reference Frames

Status: implementation reference for the approved desktop product contract

These low-fidelity frames translate
[`ui-redesign-direction.md`](./ui-redesign-direction.md) into screen-level
behavior. They deliberately contain no prompt composer, workflow launcher, or
language that presents StackOS as an agent.

## Reading the frames

- `VIEW` is read-only operational state.
- `ADMIN` is a bounded local-administration action.
- `HUMAN` is an explicit approval, answer, or policy decision.
- `AGENT` identifies state owned by an MCP consumer and is never rendered as a
  desktop action.
- Every row opens a stable detail route or drawer; summary cards do not become
  dead ends.

## 1. Portfolio and first orientation

```text
┌ StackOS ─ Local agent runtime ─────────────────────── System healthy ┐
│ Projects                                           [ADMIN Add project]│
│                                                                     │
│ Search projects…                                                     │
│ ┌ Acme website ─ Ready ───────────────────────────────────────────┐ │
│ │ 2 agents connected · 1 item needs attention · active 8m ago     │ │
│ │ Current: Content refresh / Research sources              [Open] │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│ ┌ Launch campaign ─ Setup required ───────────────────────────────┐ │
│ │ Claude connected · Slack missing · no active work       [Open] │ │
│ └──────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

Empty state explains how an AI host connects through MCP and how a deliberate
project is bound. It does not ask what the user wants StackOS to do.

## 2. Shared project shell

```text
┌ StackOS ─ Local agent runtime ┐ ┌ Acme website             Healthy ┐
│ [Project switcher]            │ │ breadcrumb / page title / scope │
│                               │ ├──────────────────────────────────┤
│ OPERATE                       │ │                                  │
│ ● Home                        │ │        active route content      │
│   Attention              3    │ │                                  │
│   Work                       │ │                                  │
│   Activity                   │ │                                  │
│   Setup                  !    │ │                                  │
│                               │ │                                  │
│ INSPECT                       │ │                                  │
│   Execution                   │ │                                  │
│   Catalog                     │ │                                  │
│   Data & evidence             │ │                                  │
│                               │ │                                  │
│ [System] [Theme]              │ │                                  │
└───────────────────────────────┘ └──────────────────────────────────┘
```

The five operate lanes are always visible. Inspect groups technical routes
without breaking their existing deep links. Plugin routes appear under their
relevant secondary group, not as a wall of primary navigation.

## 3. Home — “What matters now?”

```text
┌ Home ─ Acme website ─────────────────────────────── Updated just now ┐
│ Ready for agents  5/6 checks                       [Open setup] VIEW │
├─────────────────────────────────────────────────────────────────────┤
│ NEEDS YOU (3)                                      [See Attention] │
│ Approval · Publish 14 pages · requested by Claude · 4m       [Open] │
│ Failure  · Slack credential expired · blocks Campaign sync   [Fix] │
│ Question · Choose source policy · requested by Codex         [Open] │
├─────────────────────────────────────────────────────────────────────┤
│ ACTIVE WORK (2)                                           VIEW only │
│ Content refresh     Research sources       running 8m        [Open] │
│ SEO audit           Waiting for approval   blocked           [Open] │
├─────────────────────────────────────────────────────────────────────┤
│ RECENT OUTCOMES                                                     │
│ Product brief completed · 3 artifacts · 24m ago              [Open] │
└─────────────────────────────────────────────────────────────────────┘
```

No “start,” “run,” or request composer appears. Empty Home shows readiness,
how agents create work through MCP, and where completed evidence will appear.

## 4. Attention — “What requires a human?”

```text
┌ Attention ─ 3 items ────────────────────────────────────────────────┐
│ [All] [Approvals 1] [Questions 1] [Failures 1] [Setup 1]           │
├───────────────────────────────┬─────────────────────────────────────┤
│ ● Publish 14 pages            │ Approval requested by Claude        │
│   high impact · 4m            │ Why: external side effect           │
│                               │ Scope: 14 named page refs            │
│ ○ Choose source policy        │ Evidence / plan / audit context      │
│   question · 11m              │                                     │
│                               │ [HUMAN Deny] [HUMAN Approve]        │
│ ○ Slack credential expired    │ Agent resumes through MCP afterward │
└───────────────────────────────┴─────────────────────────────────────┘
```

Questions, approvals, failures, and setup gaps have different language and
actions. The desktop records the human decision; it does not continue the plan.

## 5. Work — “What have agents created?”

```text
┌ Work ───────────────────────────────────────────────────────────────┐
│ Search…  [Active] [Blocked] [Complete] [Agent/host] [Workflow]      │
├───────────────────────────────┬─────────────────────────────────────┤
│ Content refresh       running │ Goal / originating agent / host     │
│ Research sources      8m      │ Run-plan and tracker relationship   │
│ 4 of 9 steps                  │ Current step and blocker            │
│                               │ Dependencies / grants / audit       │
│ SEO audit             blocked │ Artifacts and completion evidence   │
│ Product brief         complete│                         VIEW only   │
└───────────────────────────────┴─────────────────────────────────────┘
```

Work is a master/detail supervision surface. Task and run-plan lifecycle stays
agent-owned; any future operator override requires its own reviewed contract.

## 6. Activity — “What changed?”

```text
┌ Activity ───────────────────────────────────────────────────────────┐
│ [All events] [Human decisions] [Agent work] [Setup] [Failures]     │
│ Search actor, object, or reference…                                 │
├─────────────────────────────────────────────────────────────────────┤
│ 10:42 Claude    requested approval     Publish pages        [Open] │
│ 10:38 StackOS   recorded action call   cms.pages.validate    [Open] │
│ 10:31 Codex     completed step         Research sources      [Open] │
│ 10:24 You       repaired connection    Slack                 [Open] │
└─────────────────────────────────────────────────────────────────────┘
```

Actor labels distinguish the agent, human, StackOS runtime, and provider. A
runtime record never implies that StackOS chose the action.

## 7. Setup — “What prevents safe agent use?”

```text
┌ Setup ─ Project readiness 5/6 ──────────────────────────────────────┐
│ Agent clients  Codex ready · Claude restart required       [Open]  │
│ Project scope  Bound to /Acme/site                         [View]  │
│ Connections    4 ready · Slack needs repair                [Fix]   │
│ Plugins        7 enabled · 1 update                         [Open]  │
│ Policy         Budget and approval rules configured         [Open]  │
│ System         Daemon healthy · backup current               [Open]  │
└─────────────────────────────────────────────────────────────────────┘
```

Setup uses plain language first and technical diagnostics on demand. It links a
blocker to the exact repair surface and explains which agents/work are affected.

## 8. Connections — provider readiness

```text
┌ Connections ────────────────────────────────────────────────────────┐
│ Search providers…  [All] [Ready] [Needs setup] [Needs repair]      │
├───────────────────────────────┬─────────────────────────────────────┤
│ Slack             Needs repair│ Used by: Campaign sync              │
│ WordPress         Ready       │ Credential: stored locally          │
│ Telegram          Ready       │ Last test: failed 12m ago            │
│                               │ [ADMIN Reconnect] [ADMIN Test]       │
│                               │ Secrets never shown to agents        │
└───────────────────────────────┴─────────────────────────────────────┘
```

The page configures what agents may use. It never sends a provider action on
the user's behalf.

## 9. Run detail and evidence

```text
┌ Content refresh / Research sources ─ running ───────────────────────┐
│ Origin: Claude Desktop · workflow: engineering.tracked-delivery     │
│ Goal: Refresh product content using approved sources                │
├───────────────────────────────┬─────────────────────────────────────┤
│ 1 Scope                 done  │ Selected step                        │
│ 2 Sources               active│ Grant snapshot / actor / timestamps  │
│ 3 Draft                 queued│ Inputs and safe refs                 │
│ 4 Human approval        queued│ Output artifacts / audit events      │
│                               │ Failure and repair guidance           │
└───────────────────────────────┴─────────────────────────────────────┘
```

The default level explains state in operator language; raw payloads and grants
remain available in an advanced inspection section.

## 10. Splash, startup, and failure

```text
┌─────────────────────────────────────────────┐
│                 StackOS                     │
│           Local agent runtime               │
│                                             │
│  ● Starting local service                   │
│  ○ Checking project database                │
│  ○ Checking agent-client registrations      │
│                                             │
│  Your projects and credentials stay local.  │
└─────────────────────────────────────────────┘

┌ StackOS could not start ────────────────────┐
│ The local service did not become healthy.   │
│ Database: available · Port 5180: occupied   │
│ [ADMIN Try again] [ADMIN Run repair]        │
│ [Show diagnostic details]                   │
└─────────────────────────────────────────────┘
```

Splash progress is deterministic lifecycle state. There are no typing dots,
thinking language, prompt fields, or claims that StackOS is doing user work.

## State and deep-link matrix

| Surface | Empty | Partial | Error/recovery | Stable deep link |
| --- | --- | --- | --- | --- |
| Portfolio | Explain MCP host + deliberate project binding | Show readiness per project | System repair without inventing a project | `/` |
| Home | Ready/needs-setup orientation | Prioritize attention and active work | Link exact repair owner | `/projects/:id` |
| Attention | “Nothing needs you” | Group by human job | Preserve failed decision context | `/projects/:id/inbox?item=…` |
| Work | Explain agent-created work | Master/detail queue | Show blocker and repair owner | `/projects/:id/tasks?task=…` |
| Activity | Explain future audit trail | Chronological actor-labelled events | Preserve partial/failed action state | `/projects/:id/activity?event=…` |
| Setup | Guided readiness sequence | Per-area readiness | Exact diagnostic and recovery | `/projects/:id/setup?section=…` |
| Connections | Provider discovery | Ready/setup/repair filters | Preserve credential-safe diagnostics | `/projects/:id/connections?provider=…` |
| Run detail | Not applicable | Step/evidence inspection | Failure, grant, and repair context | `/projects/:id/runs/:run_id` |

## Implementation invariants

1. The shell never promotes Catalog/Explore above Home, Attention, Work,
   Activity, or Setup.
2. Home contains no agent-work creation control.
3. All work rows name the originating actor/host when known.
4. Human actions are limited to setup, explicit decisions, policy, and repair.
5. Dense tables use master/detail; dashboards do not become equal-weight card
   walls.
6. Existing object routes remain addressable throughout migration.
7. Splash and failure screens expose lifecycle truth and actionable recovery.
