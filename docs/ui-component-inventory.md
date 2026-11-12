# UI Component Inventory

A flat, honest list of what's shipped, what's coming, and what to migrate.

## Shipped — primitives (`ui/src/components/ui/`)

| Component | Notes |
|---|---|
| `UiButton` | variants: primary, secondary, ghost, danger; sizes sm/md/lg; loading; iconLeft/Right; `align="start"` for menu choices (default/icon-only centered) |
| `UiIcon` | MIT Heroicons-backed icon registry for reusable control icons |
| `UiIconButton` | square; aria-label required |
| `UiButtonGroup` | segmented |
| `UiInput` | label/help/error wired via UiFormField |
| `UiTextarea` | autosize optional |
| `UiSelect` | custom listbox-style select; no native browser select chrome |
| `UiCheckbox` | indeterminate supported |
| `UiSwitch` | role="switch" |
| `UiRadioGroup` | keyboard arrows |
| `UiRange` | tick marks optional |
| `UiSecretInput` | reveal + copy |
| `UiFormField` | label + control + help/error/dirty |
| `UiDialog` | viewport portal; shared dialog/drawer stack; focus trap; H/B/F; `describedBy` links body-owned descriptions; optional `layout="detail"` with bounded 880px frame and scrolling body; centered 32px close target / 16px icon |
| `UiConfirmDialog` | builds on Dialog; accessible explanation and supplemental slot in body above cancel/confirm actions |
| `UiSidePanel` | left/right; 480/720 |
| `UiDropdownMenu` | keyboard arrows; sections; optional `portalTarget` for viewport-clamped actions inside scrolling lists |
| `UiPopover` | trigger or external-element anchor; viewport flip/clamp, scroll/resize placement and focus return |
| `UiReorderList`, `UiReorderHandle` | neutral vertical neighbor intents, handle/menu alternatives, insertion feedback, gesture-scoped edge scroll and cleanup |
| `UiTooltip` | hover + focus |
| `UiCard` | density compact/comfortable; optional padding; no nested cards |
| `UiPanel` | flat alt-surface; for sub-sections |
| `UiCallout` | info/warning/danger/success |
| `UiEmptyState` | icon + title + body + primary action |
| `UiLoadingState` | for async regions |
| `UiSkeleton` | shimmer |
| `UiToast` | live region; 5s auto-dismiss |
| `UiBadge` | status, neutral; sm/md |
| `UiProgressBar` | determinate + indeterminate |
| `UiScoreMeter` | radial 0–100 |
| `UiCodeBlock` | copy button |
| `UiJsonBlock` | folding optional |
| `UiDiffBlock` | unified + side-by-side |
| `UiPageHeader` | title + slug + description + actions |
| `UiSectionHeader` | inside cards/panels; action slots stack below copy on narrow screens |
| `UiToolbar` | sticky action bar; default, sunken, or plain treatment for controls directly on a page |
| `UiFilterBar` | search + chips + filters |
| `UiBulkActionBar` | generic primitive only; not used in observer-mode product views |
| `UiMetricCard` | label + value + delta |
| `UiDescriptionList` | label/value rows |
| `UiFactGroups` | grouped drawer/detail metadata blocks with headings, badges, mono IDs, and wide rows |

The shared reorder primitive accepts arbitrary items through `itemKey`/`itemLabel`,
optional meaningful `itemVersion`, `scopeKey`, `disabled` and an optional
`scrollContainer` (the list by default). Its item slot supplies the item and handle
bindings, including dropdown actions for Move up/down/to top/to bottom. A real move
emits only `{ itemKey, anchorKey, placement: 'before' | 'after' }`; the consumer owns
validation, optimistic ordering, persistence and recovery. The primitive never
imports tracker, store or API contracts. It keeps the source-sized item in place,
uses a restrained text preview and a single insertion line, suppresses visible
no-ops, scrolls only the supplied vertical container, and cleans up its one
animation-frame loop and gesture listeners on every termination path. Movement
announcements and focus/visibility restoration accompany menu and drop intents;
there is no decorative movement animation.

`ProjectSwitcher` consumes this primitive for the complete, stored project
catalog. Its closed sidebar trigger retains its geometry; only the open menu is
wider. Project names select through `useProjectNavigation`; separate row actions
request rename/delete targets from that same owner. The project store owns
display-name updates and bounded moves through the browser-safe catalog
operations, reloading all pages after move attempts. `ProjectRenameDialog` owns
the retained draft and saving/failure states. Home and Overview read owners
reconcile strictly newer project metadata into the shared store using the
canonical `updated_at`, retaining sub-millisecond precision. They update only
metadata on already loaded IDs, preserving catalog membership, order and selection.
Displays use shared names with portfolio fallback for unloaded IDs. Equal/older
portfolio responses cannot undo a confirmed rename; newer Refresh results update
the shared labels. Catalog reads and mutation responses also retain newer metadata.
`ProjectDeleteDialog` shows the current name and immutable slug and submits the
explicit permanent confirmation through `project.delete` in catalog scope.
It protects in-flight cleanup from duplicate submission/close, routes blockers
to existing Work controls, and retains incomplete/uncertain cleanup for same-ID
retry. Legacy ownership gaps remain administrator work. Successful or missing
targets leave the shared catalog and Overview immediately; session-local ID
exclusions prevent late reads from restoring them. No deleted project payload
is retained in the backend. `useProjectNavigation` alone chooses the first
survivor in stored order or global Overview, preserves a newer selection and
noncurrent routes, and permits navigation-only retry after cleanup succeeds.
It rechecks route/selection ownership after survivor reads and rejects removed
project destinations at router resolution after asynchronous leave/update guards.
The same owner observes push/replace calls and native history events before
component guards await, so pending direct links also retain navigation intent.
Tracking delegates to Vue Router and is removed with the owning app lifecycle.
The selector remains an intent emitter. Dialog titles wrap long project names;
normal overlay focus returns to the opener, or the stable selector when a
deleted row's opener is gone. Source tests do not claim operator pointer or
visual acceptance.

## Shipped — domain (`ui/src/components/domain/`)

Both Home headers mount the feature-owned `views/home/DeliveryCapacityControl`.
Its compact trigger shows server-reported admitted tasks against the persisted
local-daemon limit, independent of the visible task list or selected project.
The anchored `UiPopover` uses the shared form/input/buttons for a bounded integer
draft and explicit Save. Dismiss discards the draft; failed saves retain it;
success uses the operation's durable readback. Lowering below running work
explains that current tasks continue. Visibility-aware polling and Home Refresh
reload counts through the same local-admin read, with no project override.

| Component | Notes |
|---|---|
| `ProjectPageHeader` | project-aware title, breadcrumbs, utility action slot, and route chrome |
| `ProviderMark` | canonical provider logo/initial mark backed by `provider-presentation.json`; shared by Connections and Plugins |
| `AgentUsageSummary` | Shared fixed Input / Cache / Output renderer; accepts `metrics: AgentStatsMetrics \| null`, `compact?: boolean` and `active?: boolean`. Default mode keeps the analytics headline scale. Compact mode reserves three equal columns with fixed label, number and mini-canvas slots; values never wrap or toggle charts. Numbers retain task/ticket lifetime totals; bars show recent reported increments from that scope. Active charts use the existing shared feed clock, completed charts retain their last window. Missing measurements use N/A; measured zero stays 0. Exact focus/hover values remain available when a compact number is elided. Input uses paired `remaining_input`, Cache uses `cache_read`, Output uses `output`; raw input stays inclusive. No receipt parsing, subtotal calculation or polling. Work callers enforce automatic parent-task eligibility. |
| `UsageTrace` | Shared canvas bar renderer. `lib/liveUsage.ts` owns paired counter mapping and one-minute reported-delta buckets. Shared default is 20 bars; `barCount` accepts 5–60, `endAt` supplies the active clock or defaults to the last observation. Empty and first-reading states retain a fixed baseline; the first reading adds only a neutral tick. Unknown intervals and negative corrections stay gaps. Each metric has a separate zero-based scale; bars remain at most 2 CSS pixels wide. Chart values are accessible by title/ARIA. Resize, theme and pixel-density changes redraw; unmount releases observers/listeners. No SVG, animation, per-card timer or visibility decision based on sample count. |
| `ActiveExecutionLine` / `WorkTime` | Shared actual current / explicitly Last execution identity using canonical `agentIdentity.ts` host marks and readable recorded roles. `WorkTime` formats the canonical `work_duration` interval union for the execution line and Scheduled Runs, independent of lifecycle dates, automation and tokens. Partial coverage shows `Partial`; unavailable history shows `N/A`, and recorded zero shows `0m`. Only an exact matching owned live identity extends the total once after `observed_at` on the existing Work heartbeat; stopped, closed or disconnected observations freeze without per-card timers. Last retains full recorded provenance in its accessible title. Focusable instances disclose the full role/model or fallback owner in a hover/focus tooltip without expanding their layout slot. `owner` is ordinary assignment when no occurrence exists. Card footers reserve 56px and center the 32px mark and text; the blocker badge sits outside the bottom edge. Task and shared ticket details use the same Work time presentation without duplicate Started facts or separate current-call token totals. |
| `UsageLiveIndicator` | Small recording dot and Live label for actual active executions with a healthy shared feed; Connecting/Reconnecting stays neutral. Consumes the Work page's existing subscription through `useUsageFeed`, opens no connection and owns no timer. Hidden for last execution or ordinary assignment. |

`AgentUsageSummary` also accepts `inline` with `compact` for the contextual task
header: labels stay above fixed number/canvas columns in the same shared renderer.
This changes arrangement only; reporting intervals, scope and chart ownership
remain unchanged.

Project Automations reuses this inline compact renderer for historical task runs.
`SchedulesTab` owns exactly two shared underline tabs, Automations and Runs;
`ScheduleEditor` uses the existing dialog/form primitives for inert drafts and
validated activation through `schedule.set/toggle/remove`. Removing a schedule
preserves existing runs. The editor offers Workflow / Prompt, a workflow selector
only in Workflow mode, one Goal / instructions textarea, schedule/timezone and
Enabled. Switching to Prompt clears only the workflow reference; goal and saved
input context remain. Saved input context and native settings survive unrelated
edits without schema fields or role selectors. The selected project supplies the
folder; a missing-folder setup link and explicit legacy-folder reconciliation
remain actionable without displaying directories. Incomplete disabled drafts
stay saveable. Leaving an edited form requires discard confirmation.
`ScheduledRuns` reads the paginated `schedule.runs`
request-first projection, displaying actual request state, result, duration and
native session identity. Native usage is measured main-thread Input (including
cache), Cache and Output; absent counters show Not reported. Only actual
`task_key` values link to the Work graph, and only actual plans offer run actions.
The schedules store patches canonical historical task usage through
`usageUpdates`, including updates received during pending history reads, while
keeping native main-thread evidence separate from linked task totals. It never
sums receipts or polls rows.

## Shipped — app-level primitives (`ui/src/components/`)

| Component | Notes |
|---|---|
| `DataTable` | Generic accessible table with sorting, selection, cursor pagination, sticky desktop header, mobile card rendering, row click support, `selected-id` highlighting for master/detail pages, and optional `max-height` for dense ledgers/catalogs. |
| `TabBar` | Accessible grouped tab navigation; currently available for richer route/tab shells. |
| `SubNav` | Accessible section rail/strip for dense goal-oriented console pages; vertical on desktop, horizontal on narrow screens. |
| `KvList` | Compact key/value list for dense read-only metadata. |
| `MarkdownView` | Sanitized markdown display. |
| `StatusBadge` | Domain-aware lifecycle/status tone mapping. Use for project, run, tracker, action-call, step, and connection states. |

## Shipped — StackOS renderers (`ui/src/components/renderers/`)

| Component | Notes |
|---|---|
| `PluginNavRenderer` | renders core/plugin nav sections from static route descriptors or plugin `manifest_json.ui.nav` contributions |
| `TemplateRenderer` | renders reusable workflow template setup, contracts, context requirements, policies, approvals, and learning hooks |
| `RunPlanRenderer` | renders concrete run-plan steps, allowed tools, approvals, run context, and redacted action calls |
| `ActionSchemaRenderer` | renders action input/output schemas and connector config with defensive redaction |
| `ResourceViewRenderer` | renders resource schemas or project resource records using explicit plugin/resource fields |
| `ArtifactRenderer` | renders artifact metadata/provenance without inventing plugin ownership |
| `ContextQueryRenderer` | renders bounded context query items with sanitized fields and provenance |

These renderers are intentionally generic. New domains should contribute
manifests/templates/resources/actions and let the renderer surface them; do not
add workflow-specific UI unless the generic renderer is insufficient and the
exception is signed off.

## View decomposition pattern

Both Home pages mount `views/home/ActiveTaskList.vue`, whose single
`useActiveTasks` scope supplies the lifecycle projection and feed to
`ActiveTaskRow.vue`. Continuous rows use shared lifetime usage, execution,
priority and elapsed renderers, fixed list-wide zones and container breakpoints.
Manual rows reserve the same geometry without automated usage; current/Last
identity and Live retain the canonical feed semantics. Each row links directly
to the selected task graph. Global rows include canonical project names;
project rows omit redundant names. `HomeConnectionsSection.vue` renders a compact
directory after Usage using the existing scoped account owners; it does not probe
providers. Its 48px entries use normalized 24px provider marks, concise account
names and canonical status icons with accessible labels. Identical provider/account
names retain stable account numbers. Hover/focus discloses full identity and state;
Manage connections in the header reaches the existing global or project owner.

Both Home pages share `views/home/AgentActivitySection.vue`: Input / Cache /
Output headlines, date controls, daily chart with input detail, and an accessible
Agent / Model / Agent+model `DataTable`. `useAgentStats.ts` owns one dated,
grouped project/global statistics request, with scope and stale-response
protection. Regrouping retains the same dated chart and totals while the request
loads; the retained rows and chart keep their returned grouping labels until replaced.
Date, project and visibility changes clear the prior scope. Defaults are today plus the previous six local calendar days and
the browser's visibly identified IANA timezone. Global Active/Archived/All
selection remains with the existing page owner.

`AgentActivityChart.vue` uses Chart.js vertical daily stacks with exactly two
views: Tokens and Agents. Tokens shows disjoint Input (including cache write),
Cache and Output, in that order, using the backend's paired measurements. Agents uses
the same reported Input + Output split by the existing Agent / Model / Agent +
model control, using canonical daily groups. Unsupported series are absent;
missing cache never becomes an invented remainder or unknown-cache category.
An incomplete stack uses ordinary available-measurement context, without
claiming a complete total. Both views keep the same 215px plot, fixed axis gutter,
56px responsive wrapping legend below the plot and single-line selection caption.
Legend entries use their label widths and compact gaps: all three token labels
and six short identities stay on one row whenever they fit. Wrapping follows
available width; additional rows scroll within the same bounded legend. Long legend
identities are visually elided, with the unchanged name available on focus/hover;
agent identities are never rewritten. Model and combined labels preserve exact
recorded versions. Group-key colors match bars, legends, tooltips and table
markers across date changes and row sorting. Hover and keyboard focus (Left/Right, Home/End) expose exact
values. Pointer hover uses Chart.js's external-tooltip hook and a bounded panel
inside the unchanged plot: date and exact total remain visible while the series
list scrolls, preserving every measured value even with many agents. Leaving
the plot closes it; canvas keyboard focus uses the existing exact-value live
region instead. Per-series exact text is screen-reader-only rather than a visible
transcript. A bounded vertical daily disclosure has fixed Date/Input/Cache/Output/
Total columns, omitting unsupported fields rather than growing agent
columns. Compact daily values retain exact focus/hover labels. Its existing mount/update/theme/unmount lifecycle
owns the chart; it adds no polling.

`components/domain/AgentUsageSummary.vue` accepts the existing typed summary
metrics shared by Home and Work; consumers pass the appropriate backend
projection, with no client receipt aggregation. `lib/agentActivity.ts` owns its
shared `usageValue` mapping (`input` display → `remaining_input`, Cache →
`cache_read`, Output → `output`), compact/exact formatting, accessible field coverage, cache-share eligibility,
unchanged agent identities and plain review counts. Coverage proves measurement;
numeric zero alone does not. A measured zero stays zero. Fixed summaries retain
Input / Cache / Output with N/A for unmeasured slots; retained breakdown/daily
columns use N/A in missing cells. Wholly unsupported breakdown columns and
optional token details remain omitted. Input availability and coverage follow
the paired cache measurement; missing cache makes the corresponding Input N/A,
preserving supported Output. Table numeric data, daily rows, chart series and
exact disclosures consume that same mapping. Cache share keeps the raw inclusive
input denominator. Only supported cache-write/reasoning appear in token details;
recorded review outcomes appear as compact, plainly labelled summary/row facts.
Ordinary copy omits raw roles, classification and empty review
sentences. Model versions remain exact. Existing Work layout remains owned by
its consumers and the summary's compact mode.
Breakdown rows use plain labels, with exact model and relevant project identity
inline. IDs, project lists, host names, repeated identities and isolated review
counts do not create disclosures or chevrons. Fixed table columns and elided
identity cells keep long names and grouping changes from resizing the chart.
The analytics table reserves its scrollbar gutter so short and scrolling groups
retain the same column widths in browsers with classic scrollbars.
Home and Overview receive usage updates through the shared `usageUpdates`
SSE transport. `useAgentStats` owns statistics requests, date/group/scope filters
and stale-response protections, including manual and filter refreshes. Their
30-second project Home polling retains overview metadata and accounts; it does
not refresh usage. The removed ticket/portfolio summaries no longer poll.
Global Active/Archived/All visibility remains an aggregate Usage control beside
the date controls. Active-task membership and lifetime figures do not depend on
these filters. Work consumers retain the same lifetime task/ticket projections.

Work reuses the compact `AgentUsageSummary` on Board cards, the graph task
header, individual ticket nodes, task details, and `TrackerTicketDetailContent`
(shared by the task popup and graph inspector). Board/header/task details pass
the backend lifetime task projection; nodes and ticket details pass only their
own ticket projection. Graph visibility never recomputes these totals. Exact
values and field coverage remain available in exact focus/hover labels.
Manual tasks and tickets render no usage furniture, even with measured values.
The existing `isAutomaticDeliveryTask` predicate owns eligibility; graph data
propagates its parent-task result, and both ticket inspectors receive the parent
task. No workflow-name, defaults or token-value inference is used. Missing
parent identity hides ticket usage. Compact summaries have no metric panels,
dividers or missing-field prose. Their three number/canvas slots stay reserved. Automated tasks and their
tickets retain all three slots even with no launches, main-thread planning or a
pending native receipt; N/A describes availability without attributing the work.
Manual tasks and tickets never acquire a token or N/A row. Compact charts read
the retained lifetime samples from the same metrics as their numbers; stopping
or reloading does not remove their canvases. Current-call detail strips retain
their separate active-call scope through the same canvas renderer. Chart samples for
empty/unavailable days or absent agents stay null rather than measured zero.
`Automated` sits with task identity/status and uses the unchanged persisted
delivery predicate, including stopped and complete executions, with the existing
subtle info badge tone on Board, Graph toolbar and task details. Ticket geometry
is centrally defined as 328 × 224px in `lib/task-tracker/graphModel.ts`, including
layout, CSS variables and minimap consumers. Titles and status lead the compact
card; owner/agent labels and a quiet provenance footer organize its metadata.
Clamped titles, context and identities retain their full values on hover.
Usage remains secondary within it.

`TaskDeliveryStop` renders the shared `taskDeliveryState` projection in task and
owning-ticket details, separately from canonical tracker/workflow status. Compact
cards/header use `executionStatus` from the existing execution presentation owner:
Stopped is a state badge, with the actual reason in card context/header inspection.
It never becomes a fictional prerequisite. Resuming clears the stopped presentation.
Graph focus remains available; only the recorded stopped ticket gets its marker.

Route views are composition roots, not feature implementations. Use the
following ownership split for new work and when a touched view has accumulated
multiple independent lifecycles:

| Layer | Owns | Must not own |
|---|---|---|
| Route view | route/query synchronization, page-level loading order, feature composition, cross-feature dispatch | provider-specific forms, large pure projections, repeated API lifecycle state |
| Feature component | one visible region or form, accessible interaction, local presentation state | route access, direct catalog duplication, unrelated mutations |
| Feature composable | one coherent async lifecycle and its state/messages, such as ingress setup or one provider profile editor | markup, unrelated provider branches, hidden route navigation |
| `viewModel.ts` helper | pure projection, grouping, filtering, labels, and merge rules | refs, network calls, timers, router access |
| Shared primitive/composable | behavior used by multiple features with the same contract | speculative abstraction for one call site |

Split a touched route view when it owns more than three independent async
lifecycles or when its script can no longer be reviewed as one responsibility.
Treat 700 view lines and 400 feature-component lines as review triggers, not
automatic failure thresholds: extract only a named ownership seam, never a
pass-through wrapper made solely to lower a count.

Keep feature-only pieces beside the feature (`views/<feature>/`). Promote them
to `components/domain` or `composables` only after another feature uses the
same contract. A feature composable should receive explicit refs/callbacks,
return its public state/actions, and keep one mutation/reload policy. The route
view remains responsible for coordinating multiple composables.

`AgentsView` composes `agents/ProjectAgents` and `agents/useProjectAgents`.
They read and edit actual project-local native definitions through the canonical
source operations. Saved task defaults are adopted explicitly with digest
readback; editing a definition does not execute work or rewrite frozen runs.
Generic presets remain reusable guidance, separate from the live project list.
The shared shell uses direct primary, Setup and enabled-domain links; see the
[route and wrapper map](./ui-page-layout-map.md).
`lib/stackos/pluginPage.ts` owns supported generic page sections for both
`nav.ts` and `PluginDomainView`. Only a nonempty supported page declaration
creates a generic sidebar destination; section-only navigation metadata does
not. Finance retains its dedicated console. Integration-only packages and
Publishing stay in Plugins/Connections, with their saved information and files
in Library. Direct plugin routes without a page offer setup and scoped Library
links.

Connections is the reference implementation:

- `ConnectionsView.vue` owns project routing, section composition, and
  cross-provider bot dispatch.
- `useAccountCredentials` and `AddAccountPanel` own the global Account
  lifecycle: schema-driven creation and editing, inline validation,
  verification, and revoke state. The same panel is composed by Accounts and
  Connections.
- `useConnectionCredentials` and `AttachAccountPanel` own only project
  attachment selection and attach/detach state.
- `useCommunicationTopology` owns profile/target/surface/route loading.
- `useIngressEndpointEditor` and `useSlackProfileEditor` each own one mutation
  lifecycle. Telegram bot and user setup is Account-bound and uses the generic
  Account editor.
- `AccountSetupFlow` renders declarative progress for staged Account drawers;
  Account state and provider challenges remain in their existing owners.
  `TelegramProxySetupSection` groups the manifest-backed Telegram proxy fields
  by enabled state and proxy type inside that same drawer.
- `ConnectionServiceSelect`, `AccountNameField`,
  `ConnectionCredentialFields`, and `ConnectionCredentialField` own Account
  form regions and schema rendering.

Plugins follows the same composition rule: `PluginsView.vue` owns route loading
and catalog controls; `plugins/viewModel.ts` owns cross-plugin/provider/action
search and sorting; focused plugin/provider card components own presentation.
Both Plugins and Connections use the shared `ProviderMark` so website catalog
logos and operator UI logos cannot drift.

Workflow details use `TemplateRenderer`'s compact stage list, declared inputs,
outputs and approval gates, with the sanitized effective contract in Details.
`WorkflowDetailView` owns both plugin-scoped details and Home's exact configured
project/repository/user workflow inspection. Non-plugin workflows read the same
effective-template API directly and return to Home; no global list store remains.
They use ordinary page scrolling. The task tracker dependency map remains a
bounded, page-sized exploration canvas: node geometry and spacing may extend
beyond the viewport, while pan, wheel/pinch zoom, minimap and controls expose
that off-canvas context.

Partition integration tests by user flow before moving risky async ownership.
Use focused unit tests for pure view models/composables and keep one route-level
test for cross-feature wiring, loading, and error-state truthfulness.

### Current decomposition map

| File | Current ownership decision | Next safe seam |
|---|---|---|
| `composables/useProjectNavigation.ts` | Sole app-shell project selection lifecycle: route scope, navigation fallback, navigate-before-commit ordering, scoped plugin navigation, and project-keyed route remount state. | Keep feature data loading and route-query interpretation out. The route remains authoritative on project pages; the Projects store selection is only a global-page/navigation fallback. |
| `composables/useProjectRouteScope.ts` | Canonical project-route identity for views: strict project id parsing, project/global classification, and the route-view scope key. | All project views consume this contract; do not parse `route.params.id` locally or add page-specific project-switch handlers. |
| `composables/useProjectScopedLoader.ts` | Mount/manual-refresh lifecycle for project views keyed by `useProjectRouteScope`. | Keep feature-specific request/query logic in the owning feature composable or store. Root project-key remounting owns scope changes. |
| `lib/stackos/projectRequestGate.ts` | Latest-wins commit boundary for project-scoped Pinia requests, including cross-project invalidation and parallel operation loading. | Use only in shared stores whose state survives routed component unmounts; component-local state relies on the keyed route lifecycle. |
| `components/ProjectSwitcher.vue` | Presentational project dropdown with local open/close interaction and one `select` intent. | Keep router, store, catalog, and project lifecycle access in `useProjectNavigation`. |
| `views/AccountsView.vue` | Global Account inventory, filtering, edit/test/revoke dispatch, and reusable creation-panel composition. | Keep project attachment concerns out; those belong to Connections. |
| `views/accounts/useAccountCredentials.ts` | Global Account create/edit/test/revoke lifecycle with schema-driven fields and field-level validation. | Keep routing out and preserve the reusable panel contract. |
| `views/accounts/AddAccountPanel.vue` | Reusable Account creation shell used by Accounts and project Connections; service selection, Account naming, auth method, and schema-driven credential fields stay cohesive. | Keep provider challenge state in the existing Account controller and native authorization panel. |
| `views/accounts/AccountSetupFlow.vue` | Declarative drawer-local progress and stage content slot for multi-step Account setup. | Presentation only; no provider calls, auth state machine, or persistence. |
| `views/accounts/TelegramProxySetupSection.vue` | Conditional disclosure of Telegram's manifest-backed proxy fields. | Keep proxy validation and normalization in the daemon's Account proxy contract. |
| `views/ConnectionsView.vue` | Route/query synchronization, Account attachment, section composition, and project-bound communication/ingress dispatch remain. | Keep Account secret lifecycle in the reusable Accounts panel; do not fold it back into the project page. |
| `views/connections/useConnectionCredentials.ts` | Lists attached Accounts, filters reusable unattached Accounts, and owns attach/detach confirmation state. | Keep global Account mutation and project communication topology out. |
| `views/TaskTrackerView.vue` | Owns Board/map route history, independent map-task and popup identities, project-local Board preferences, task-index loading and focused detail dispatch. `boardFilters.ts` guards one localStorage record per project and owns explicit update/remove/clear commands plus one-shot Home entry intents. `TrackerBoardPanel` renders seven fixed status columns, 224px anchored-footer cards and API-projected whole-task usage; `TaskTrackerCommandPanel` renders the unified `TrackerBoardFilterBar` on Board and groups task identity/Automated, status/completion, inline shared usage and Task details on Graph. The shared page-header switch stays top right in both views. `TrackerBoardFilterBar` measures only the chip region with a cleaned-up ResizeObserver, reserves controls and +N overflow space, and uses `BoardFilterEditor` for visible and overflow clause edits through the preference owner's commands. `TicketGraphNode` renders its own projected ticket usage; `graphModel.ts` owns its 328 × 224 geometry and prerequisite-title resolution. `TrackerTaskDetailDialog` owns one persistent `UiDialog layout="detail"` for task/ticket inspection and a six-row scrolling ticket list. `TrackerTicketDetailContent` is shared with the map's `TrackerTicketDetailPanel` side inspector. `useTrackerExecutionContexts` owns context/artifact pagination; `useTrackerGraphSession` composes focused graph loading, viewport and the existing project/task event stream. Pure projections and filters remain in `task-tracker/viewModel.ts`. | Keep Board filters independent from map selection. Task state and ticket-status containment filters have separate meanings. Graph visibility never changes whole-task values/trends. The task-index rollup supplies actual prerequisite titles only when all open work waits on dependencies; active execution suppresses the Board badge. No per-card requests or receipt summing. Shared current/Last identity stays independent of lifecycle status. Preserve task drafts, disclosure state and scroll during ticket inspection. No standalone Stories or Tickets modes. |
| `views/connections/credentialPresentation.ts` | Provider/auth grouping, labels, connection status, and account presentation. | Keep provider catalog knowledge here; do not mix in communication topology facets. |
| `views/connections/formatters.ts` | Communication profile, route, surface, Slack, ingress, and shared Account presentation exports. | Split another provider-specific facet only when it gains a distinct contract or test lifecycle. |
| `views/finance/FinanceRequestDialog.vue` | Contextual safe request read, original source and status at the Finance origin. | Preserve exact request/project/workspace identity, stale-read rejection and explicit unavailable/retry states. Never infer a tracker task or execute recovery from inspection. |

Large generated catalogs, API types, and declarative plugin manifests are not
decomposition targets. Change their generators or source manifests instead of
splitting generated output.

Provider logo paths and presentation kinds are an adjacent one-brain contract:
edit [`../provider-presentation.json`](../provider-presentation.json), not a
view-local map. Website and desktop UI keep separate Vue mark components, while
the website catalog sync validates both public asset trees against that shared
manifest.

Agent host marks have their own existing owner in
`ui/src/lib/stackos/agentIdentity.ts`, shared by Overview connection cards and
Work board owners. `HomeAgentHostStatus` renders the backend's host list and
uses three columns on standard desktops and six on wide desktops. The Grok
mark in `ui/public/images/grok.png` is the unchanged 192px icon published by
[grok.com](https://grok.com/images/android-chrome-192x192.png), retrieved
2026-10-29 (SHA-256
`768cbbf7a7528d6e7b3af8112d7cde43eb6c9c8989ad9b06be8047dae1227463`).

Removed action-oriented demo components. They were removed from `ui/src`
because they exposed or demonstrated domain mutations that now belong to the
agent/MCP path.

## Design-system showcase

Work Board card edits use `useTaskBoardEdits` for bounded priority or
same-column neighbor intents, with one pending edit per conflicting column,
optimistic presentation, confirmed-rejection rollback and canonical index
reconciliation. Save errors, uncertain outcomes and saved-but-stale reads stay
distinct. `TaskTrackerView` owns project-index revision coverage shared by save
responses and live batch events. Covered responses and in-flight reads are
reused; stale responses obtain the required revision. Unversioned events and
explicit refreshes retain one trailing latest read. Background reconciliation
keeps the board interactive without toggling global loading; initial/manual
refresh still shows loading. Structured batch members scope focused reads and
highlights; graph/detail readers reuse their own revision coverage. `TrackerBoardPanel`
scopes disabled edit controls to pending columns and consumes `UiReorderList`
with `UiReorderHandle` for each column. The shared primitive accepts arbitrary
items through `itemKey`/`itemLabel`, optional meaningful `itemVersion`,
`scopeKey`, `disabled` and an optional `scrollContainer` (the list by default).
Its item slot supplies the item and handle bindings, including the existing
dropdown action descriptors for Move up/down/to top/to bottom. A real move emits
only `{ itemKey, anchorKey, placement: 'before' | 'after' }`; the consumer owns
validation, optimistic ordering, persistence and recovery. The primitive never
imports tracker, store or API contracts. It keeps the source-sized item in place,
uses a restrained text preview and a single insertion line, suppresses visible
no-ops, scrolls only the supplied vertical container, and cleans up its one
animation-frame loop and gesture listeners on every termination path. Movement
announcements and focus/visibility restoration accompany menu and drop intents;
there is no decorative movement animation.

Board supplies project/status/rank/priority eligibility revisions and the current
project/filter context. Unrelated title, usage and `updated_at` refreshes retain
the gesture; source/anchor eligibility changes cancel. The existing neighbor API
preserves hidden tasks' relative order. The body open target remains separate
from header controls. Header dropdown wrappers align on the same center line;
run actions occupy space only when the shared action actually renders.
`TaskPriorityBadge` shares P0 danger / P1 warning / P2 info / P3 neutral
labels and tints across Board, task details and existing task Graph nodes;
ticket priorities retain their own presentation. The 224px card height, usage
slots and 56px footer stay fixed.

Applied Board chips reuse `BoardFilterEditor` in a popover anchored to the clicked
chip, including Completed's existing choices. Overflow stays mounted while its
editor is open; responsive repartition re-anchors to the visible chip or surviving
overflow trigger and preserves draft/focus. Filter/Display controls call the
existing project-local preference owner's explicit commands. The primitive specs use a plain non-task
list; pointer feel, edge-scroll feel and rendered alignment remain operator-owned
manual checks.

`RunAbortAction` provides the shared run menu trigger on Scheduled Runs, Board and
the Graph task header. `RunAbortDialog` stays at the page composition root so a
task moving to the Aborted column cannot dismiss a stop error or its retry.
`useRunAbort` owns the single pending request/confirmation and calls `runPlan.abort`
with the recorded project/run-plan link. Page callbacks refresh existing projections;
there is no abort poller. Board menus are siblings of card buttons, preserving
the fixed 224px card and keyboard access.

The `/__design-system` route was removed from the shipped app. Keep any future
component demos outside the production router, and do not ship action-demo
components into `ui/src/components/domain`.

## Inline duplication patterns to remove

These are the common copy-paste patterns I'd expect to find in views — when migrating, replace each with the listed primitive:

| Inline pattern | Replace with |
|---|---|
| `<button class="bg-blue-600 text-white px-3 py-1.5 rounded">…` | `UiButton variant="primary"` |
| Ad-hoc badge spans with lifecycle/status colors | `StatusBadge` or `UiBadge` with a tone from `resolveStatus()` in `status.ts` |
| Sectioned drawer/detail facts built from repeated custom grids | `UiFactGroups` |
| Simple ungrouped metadata rows | `UiDescriptionList` or `KvList`, depending on density |
| `<input class="border rounded px-2 py-1">` with separate label/error divs | `UiFormField` + `UiInput` |
| Custom modal with backdrop + dialog box | `UiDialog` |
| Custom dropdown built from `<select>` styled to look rich | `UiDropdownMenu` |
| `<table>` with hand-styled headers | upgraded `DataTable` consuming tokens |
| Hand-rolled tab bars | `TabBar` (upgraded) |
| Inline copy-to-clipboard buttons | `UiCodeBlock` / `UiSecretInput` |
| Hand-rolled empty placeholders | `UiEmptyState` |

## Migration map (representative views)

| View | Primary primitives | Domain components |
|---|---|---|
| Projects list | UiPageHeader, UiFilterBar, UiBadge, UiButton | (none) |
| Project overview | UiPageHeader, UiDescriptionList, UiMetricCard, DataTable | ProjectPageHeader |
| Project Automations / Runs tabs | TabBar, DataTable, UiSwitch, UiDropdownMenu, UiDialog, UiConfirmDialog, UiFormField | ProjectPageHeader, AgentUsageSummary |
| Project budget tab | UiPanel, UiMetricCard, DataTable | ProjectPageHeader |
| Runs list | UiFilterBar, DataTable, UiBadge | (none) |
| Run detail | UiPageHeader, UiCodeBlock, UiJsonBlock, RunPlanRenderer, ArtifactRenderer | (none) |
| Plugins | UiPanel, UiFilterBar, UiSegmentedControl, UiSelect, UiBadge, UiCard, UiEmptyState | ProjectPageHeader, ProviderMark |
| Capabilities | DataTable, UiBadge, UiButton | ProjectPageHeader |
| Connections | SubNav, UiMetricCard, UiSectionHeader, UiCard, UiEmptyState, UiCallout, UiFormField, UiInput, UiSecretInput, UiSidePanel, StatusBadge | ProjectPageHeader |
| Workflow templates | DataTable, TemplateRenderer | ProjectPageHeader |
| Project Data | UiSegmentedControl, DataTable, ArtifactRenderer | ProjectPageHeader |
| Resource Explorer | DataTable, ResourceViewRenderer, ArtifactRenderer | ProjectPageHeader |

## Refactor priority

If you're carving up the work:

1. Keep the observer-mode route audit green.
2. Keep `read-only-ui.spec.ts` scanning product code for writes outside the
   explicit provider-auth setup store.
3. Add new domain components only when they display state or delegate to a
   generic StackOS setup surface rather than owning workflow-specific mutations.
