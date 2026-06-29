# StackOS Desktop UX Redesign — Direction

> Status: design direction + build spec. Goal: turn the operator console from a
> database/admin dashboard into a **calm, agent-first local operations console**.
> Humans are observers, approvers, troubleshooters, and setup owners — not the
> primary operators. Reuse the existing design system (`Ui*` primitives,
> renderers, stores, `status.ts`, `time.ts`); add new code only for the few
> genuine gaps named in §6.

## 1. Problem (what's wrong today)

The current UI is organized around the backend schema, not around what a person
supervising agents needs to do.

- **No real home.** `/` pings `/api/v1/health` and prints a raw JSON blob.
- **~19 routes / ~25 sidebar destinations** mirror tables: Overview, Tasks,
  Runs, Workflow Library, Agent Presets, Agent Requests, Project Data (8 sub-
  tabs), Resources, Connections, Plugins, Capabilities, Operations, Action
  Calls, Setup, Schedules, Cost & Budget, plus 8 plugin lanes.
- **Everything is a flat table of records** keyed by `id`, `kind`, `plugin_slug`,
  `created_at`, with `#123` ids and raw JSON dumps (RunDetail, Project Data,
  Resource Explorer, Action Calls, Operations).
- **Internal jargon** is surfaced to users: "run-plan audit rows", "operation
  contracts", "grant policy", "credential ref", "attention_status", "resource
  records", "claimable queue", "rev 3105".
- **No human action loop.** The product says humans approve/unblock/configure,
  but Agent Requests is framed as a queue for *agents to claim*; approvals, the
  unblock action, and budget caps are buried or read-only.
- **Attention is scattered** across 5 screens (failed runs, unread requests,
  blocked tickets, connection problems, budget overages). Nothing answers
  "what needs me right now?" at a glance.

## 2. Principles

1. **Answer five questions, in order:** What needs me? · What are agents doing
   now? · What changed recently? · Is the system ready/healthy? · Where do I
   inspect or configure deeper?
2. **Narrative over records.** Lead with human-readable activity ("Agent
   completed *Release closeout*, 24m") not table rows; keep raw audit behind an
   "Technical detail" disclosure.
3. **Calm hierarchy.** A small set of goal-oriented surfaces. Audit/DB tables
   still exist but live in one demoted *Developer / Inspect* area.
4. **Human verbs are first-class.** Approve, respond, unblock, reconnect, set a
   budget — surfaced where the attention is.
5. **Reuse the design system.** New surfaces compose from `Ui*` primitives; only
   §6 items are new.
6. **Local-first & desktop-aware.** Respect the Electron shell (deep links,
   notifications, readiness gating); degrade gracefully in a browser.

## 3. Language map (humanize)

| Current term | Surfaced as |
| --- | --- |
| run-plan / run / "audit rows" | **Run** / "what an agent did" (Activity) |
| agent requests / claimable queue / attention_status | **Inbox** · "Questions", read/unread |
| approval_requests | **Approvals** ("waiting on you") |
| tracker tasks / tickets / rev / priority_key | **Work** · Tasks; "blocked", "in progress" |
| context timeline / project data / knowledge | **Activity** (the project story) |
| schedules / cron_expr | **Automation** · "every day at 9:00" |
| cost / budget / QPS | **Spend** |
| operation contracts / action calls / capabilities / resources | **Developer** (demoted) |
| credential ref / provider_key | hidden; show service + account label |

## 4. Information architecture

**Root `/`** — **Projects portfolio.** Calm list/cards of projects, each with
"agents active · needs you · last activity · ready?". Replaces the health JSON.
Daemon health becomes a small status pill in the shell + a System/Diagnostics
detail (and, in desktop, an in-app System panel — §7).

**Per project — primary nav (5):**

1. **Home** `/projects/:id` — the operations console.
   - *Needs you* band (top attention items, mirrors Inbox).
   - *Agents at work* — live runs + current step ("what it's doing now").
   - *Recent activity* — the timeline feed (human narrative).
   - *Readiness/health strip* — "Ready to run" + one blocking thing if not.
2. **Inbox** `/projects/:id/inbox` — everything that needs a human, ranked:
   approvals (pending gates), questions (unread agent requests), blocked work,
   failed runs, connection problems, budget overages. Human actions inline.
3. **Work** `/projects/:id/tasks` — the tracker (flagship; **route + `?task=`
   preserved** for deep links). Elevate *Blocked* and *In progress*; humanize
   labels. Keep the dependency graph as a secondary view.
4. **Activity** `/projects/:id/activity` — agent work as narrative: runs +
   timeline merged, outcomes and durations in plain terms; raw audit filters
   behind an *Advanced* toggle; drill into a run (narrative-first detail).
5. **Setup** `/projects/:id/setup` — readiness hub: "Is StackOS ready?" plain-
   language story + **Connections**, **Automation** (schedules), **Spend**
   (budgets, editable caps), **Plugins** as sections/sub-routes.

**Secondary — "Developer" (collapsed group, low emphasis):** Runs audit,
Action calls, Operations, Capabilities, Resources, Project-data tables, Agent
presets, Workflow library. The database/inspector surfaces, clearly labeled
advanced. Existing views are reused here largely as-is.

**Plugin lanes:** keep as collapsible plugin-contributed sections below the
primary nav (existing `PluginNavRenderer` mechanism), de-emphasized.

Old routes keep working (alias/redirect) so deep links and tests don't break;
removed top-level *lanes* move into Setup/Developer rather than disappearing.

## 5. Screen-by-screen verdicts

| Screen | Verdict | Becomes |
| --- | --- | --- |
| HomeView (`/`) | restructure | Projects portfolio; health → status pill/System |
| OverviewTab | restructure | **Home** console (needs-you + live + activity + readiness) |
| SetupStatusTab | restructure | **Setup** plain-language readiness story (granular metrics → Developer) |
| AgentRequestsView | restructure | **Inbox** (human approvals/questions first); agent-claim view secondary |
| RunsView / RunDetail | restructure | **Activity** narrative + narrative-first run detail (JSON behind disclosure) |
| TaskTrackerView | keep, elevate | **Work**; elevate Blocked/In-progress, humanize |
| ConnectionsView | keep | lives under Setup; "needs attention" bubbles to Inbox/Home |
| CostBudgetTab | keep, make caps editable | **Spend** under Setup |
| SchedulesTab | merge | **Automation** card/section under Setup; human cron text |
| PluginsView | merge | Setup → Plugins section |
| CapabilitiesView | cut as destination | provider auth types folded into Connections |
| OperationsView / ActionCallsView / ResourceExplorerView / ProjectDataView | demote | Developer area (timeline promoted to Activity) |
| AgentPresetsView / WorkflowTemplatesView | demote | Developer / "How agents work" |

## 6. New code (the only additions; everything else reuses primitives)

1. `ui/src/lib/stackos/events.ts` — **event taxonomy** map (analogous to
   `status.ts`): `event_type`/`source_type` → `{ icon, tone, label }` + a
   human title/summary helper. Covers known types (`tracker.*.status_changed`,
   `learning.*`, `decision.record`, `experiment.*`, `context.snapshot`, ingress)
   and title-cases unknowns to neutral.
2. `ui/src/components/domain/ActivityItem.vue` — one timeline/feed row composed
   from `UiIcon` + title/summary + `UiMetadataStrip` + relative time + tag
   `UiBadge`s; optional run/task drill-down. Reused by Home, Activity, Inbox.
3. `ui/src/stores/attention.ts` — cross-store aggregation producing a ranked
   `AttentionItem[]` (kind, title, detail, tone, when, deepLink, action) by
   fanning out: runs `?status=failed`, `agentRequest.list?attention_status=unread`,
   pending approvals, `tracker` blocked count/items, budgets vs cost,
   connections not `connected`. Powers Home "Needs you" + Inbox.
4. `ui/src/stores/readiness.ts` — normalized readiness model
   `{ key, label, state, tone, hint, action }` + an overall verdict ("ready to
   run") and the single most important blocking item, from `/health` +
   `/auth/status` + `integration.list`. Renders via `UiDescriptionList` /
   `StatusBadge` / `UiProgressBar` / `UiScoreMeter`.
5. `ui/src/lib/desktop.ts` — typed, **feature-detected** wrapper over
   `window.stackosDesktop` (`status`, `installOrRepair`, `restartService`,
   `runDoctor`, update methods) + `isDesktopShell()`. No-ops / hidden in browser.
6. `ui/src/composables/usePolling.ts` — deliberate, visibility-aware light
   refresh (default 20s, pause on hidden tab) for live surfaces. A considered
   deviation from the watcher-free *time* contract — it refetches data, it does
   not tick clocks; "updated Ns ago" is computed at render as before.
7. *(optional, defer)* `UiSparkline.vue` — small trend for Spend/metrics; only
   if the inline SVG in CostBudgetTab proves insufficient.

## 7. Desktop integration (must respect)

- **Deep links / notifications:** preserve the `/projects/:id/tasks` route and
  honor `?task=<key>` on **cold load** (full `loadURL`, not SPA nav). This is
  how task-completion notifications select a task. Do not rename/move that route.
- `lib/desktop.ts` exposes in-app **System actions** (Restart service, Install
  or Repair, Run doctor, Check for updates) and richer health from
  `window.stackosDesktop.status()` — shown **only** when `isDesktopShell()`;
  hidden in the browser. These live on the Projects portfolio / a System panel.
- Keep all API calls **same-origin**; external links must be real `http(s)`
  anchors (the shell denies non-`stackos` in-window navigation).
- The shell only loads the SPA after `/health` 200, so an in-app "connecting"
  screen is optional; if added, it must not fight the shell's native failure
  pages. A lightweight reconnect toast on transient fetch failure is enough.

## 8. Data gaps (explicit, with justification + decision)

- **A. No project-level pending-approvals query.** `ApprovalRequestOut` is only
  reachable embedded in `RunPlanOut`. *Justification:* the Inbox approvals
  section is core to the agent-first loop; iterating run-plans is N+1/fragile.
  *Decision:* add a read-only `approvalRequest.list` operation (and/or REST
  `/projects/{id}/approval-requests?status=pending`) over the existing table —
  pure read, no schema change. Interim fallback: aggregate from started/draft
  run-plans client-side. **(Recommended backend add.)**
- **B. Provider readiness / full doctor health is CLI + desktop only.**
  *Decision:* web readiness uses `/health` + `/auth/status` + `integration.list`
  now; in desktop, surface richer health + repair via `window.stackosDesktop`.
  Flag exposing `provider_readiness`/doctor checks over REST as a future add.
- **C. No cross-cutting attention summary endpoint.** *Decision:* client-side
  aggregation (`stores/attention.ts`) now; a server summary is a later optim.
- **D. Run steps/calls not wired to the UI.** *Decision:* wire `run.listSteps`
  via `callOperation` for the live "what's it doing now" run detail — no backend
  change.
- **E. No polling/staleness anywhere.** *Decision:* `usePolling` + visible
  "updated Ns ago" on live surfaces; `SetupStatusTab`-style silent error
  swallowing replaced by explicit degraded/unknown states.

## 9. Flows

1. **First-time / returning:** portfolio shows each project's readiness; opening
   a not-ready project lands on Home with a prominent "Finish setup" banner →
   Setup's one blocking action.
2. **Ongoing awareness:** Home glance → *Agents at work* + *Recent activity*.
3. **Attention:** Inbox (full), Home *Needs you* band (top), native
   notification → deep link → the task in Work.
4. **Inspection:** activity item → run detail (narrative first; JSON behind
   "Technical detail"); Developer area for raw tables.
5. **Recovery/resume:** after relaunch, Home shows continuity (running jobs, last
   activity, readiness); desktop System panel offers repair/restart.

## 10. Build sequence

1. Foundation: `lib/desktop.ts`, `lib/stackos/events.ts`,
   `composables/usePolling.ts`, `stores/attention.ts`, `stores/readiness.ts`,
   `components/domain/ActivityItem.vue` (+ unit tests).
2. Router + nav restructure: new primary routes, slimmer `nav.ts`, collapsed
   Developer group; alias/redirect old routes; **preserve tasks route + `?task=`**.
3. **Home** console (flagship — sets the visual language).
4. **Inbox**.
5. **Activity** + narrative run detail.
6. **Setup** hub (readiness story + Connections/Automation/Spend/Plugins),
   editable budget caps.
7. **Work** humanization (light touch).
8. Developer area (move audit tables; minimal change).
9. Root **Projects portfolio** + demote health; desktop System panel.
10. Docs (`ui-design-system.md` §4 nav, `architecture.md` UI section, AGENTS
    change checklist) + any backend add from §8A.

## 11. Definition of done (per surface)

- `pnpm test` (vitest), `pnpm lint` (eslint), `pnpm type-check` (vue-tsc),
  `pnpm build` all green.
- Playwright at **360 / 640 / 768 / 1024 / 1280 / 1440**, **axe** clean, and
  **zero console errors**.
- Empty / loading / degraded / offline / stale states handled on every surface.
- No secrets or raw credentials rendered. Language passes the §3 humanization bar.
