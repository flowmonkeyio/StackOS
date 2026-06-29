# StackOS UI Consistency Spec

> Canonical component rules derived from two full internal audits (tables ·
> scroll · cards · filters/drawers, and badges · headers/tabs · key-value ·
> buttons/forms · feedback/icons/format · renderers/blocks/meters). This is the
> contract every page is rebuilt onto. Baseline reference: HomeConsoleView,
> InboxView, ActivityView, SetupStatusTab, HomeView.

## 1. Scroll — one model

The only routine vertical scroller is App shell `<main overflow-y-auto>`.
**Every list/registry/detail/dashboard page is page-scroll.** Remove all
`DataTable :max-height` caps (14 pages used ad-hoc `calc(100vh-{16,22,24,31}rem)`
producing double/triple-scroll traps). Tables flow to natural height; cursor
`load more` lives at the bottom. Capped internal scroll is reserved only for the
genuinely special vue-flow graph canvas. Drawer/dialog bodies scroll at the
container level (`scrollBody`); no per-block JSON `max-height` inside a drawer.

## 2. Tables

- One primitive: `DataTable`. No custom wrapper divs / scoped flex CSS (fix
  `TrackerTicketTable`). Page-scroll, no `maxHeight`.
- Master/detail pages always pass `selected-id` (add to `RunsView`).
- **Status always via `StatusBadge`** (status.ts) — never a raw enum in a bare
  `UiBadge`, never a per-page tone function.
- **Identity, not raw ids:** humanize column labels (`plugin_slug`→Plugin,
  `parent_run_id`→Parent run, `credential_ref`→Credential, `source_type`→Source,
  `event_type`→Event, `cron_expr`→Schedule…). Raw `#id`/mono key is a muted
  secondary, never the primary cell. `credential_ref` leaves the table (detail
  only).
- Empty state flows through `DataTable` `emptyMessage` (don't gate the table
  behind a separate `v-else UiEmptyState` like SchedulesTab); one contextual
  sentence style. Raw JSON never interleaved beneath a ledger.

## 3. Cards

- Never nest `UiCard`. List-of-rows = `padded=false` + `class="overflow-hidden"`
  + inner `divide-y divide-border-subtle`, rows `px-4 py-3`, page-scroll (fix
  CommunicationSetupPanel/ConnectedServicesPanel inner `max-h` lists).
  Narrative/summary = padded default.
- Header always via `#header` slot: title + one count chip (left), at most one
  ghost/secondary action (right). One heading level rule (see §5).
- **Raw payload disclosure rule:** the ONE primary payload renders expanded via
  `UiJsonSection`; every secondary/raw blob (metadata, provenance, run context,
  schema, connector config, credential refs, sha256/path/namespace) renders
  COLLAPSED via `UiAdvancedJsonPanel` / `<details>`. Never a JSON dump as a
  card's default body on a primary surface.

## 4. Status & counts

- `UiBadge` = low-level pill. `StatusBadge` = the only status pill (composes
  UiBadge from status.ts, now **renders the StatusDef icon**).
- Extend `status.ts` with the missing domains: `connection`, `agentRequest`,
  `attention`, `system`, and `enabled/disabled` (disabled = neutral, not
  warning); wire the existing `budgetState` + `integrationHealth`. Delete the ~8
  hand-rolled tone helpers (connections/formatters, attentionTone, policyTone,
  domainTone, budgetTone, HomeView statusTone).
- Delete `TrackerStatusBadge` → `StatusBadge domain="tracker"`.
- One numeric count chip: **`UiCountBadge`** (replaces the two divergent bespoke
  medallions in HomeConsole/Inbox and the `<UiBadge>{{ n }}</UiBadge>` section
  counts). `tabular-nums`; tone neutral default, danger for alerts.
- ProjectPageHeader slug/domain/locale chips → `UiBadge variant="outline"`.

## 5. Headers, sections & tabs

- `ProjectPageHeader` for project routes; raw `UiPageHeader` only for
  non-project (HomeView). Header action buttons are **size `sm`** everywhere.
- One section-title path. Heading levels: page `h1` → card/section title `h2`
  (visual `t-h3`) → sub-section `h3`. Stop the h2/h3/h4-all-at-t-h3 drift; fix
  PluginsView title scale. Drawer titles stay `t-h2` (a formal `UiSidePanel`
  convention).
- **Tabs:** adopt the existing `TabBar` (underline, accent, count badge,
  keyboard) for CONTENT switching — ProjectData (8-way), Connections (4
  sections) — rendered via the `UiPageHeader #tabs` slot. Reserve
  `UiSegmentedControl` for in-toolbar FILTER/mode toggles only.
- Drop section titles that duplicate the page title (Capabilities).

## 6. Key-value displays

Collapse five overlapping primitives to two; retire `KvList` + hand-rolled
`<dl>`s:
- `UiMetadataStrip` = inline horizontal fact strip for detail HEADERS / dense
  disclosures (align its `dt` token to `text-xs font-medium text-fg-muted`).
- `UiDescriptionList` (layout=grid, density=compact, numeric for numbers) = block
  fact display in card bodies / panels; 2 columns in drawers, 3–4 in full-width.
- `UiFactGroups` only for genuinely grouped/titled multi-section facts.
- Fold `UiDescriptionItem` into the list's slot form. Null → primitive's em-dash.

## 7. Buttons & forms

- Variants: `primary` = single main CTA per surface; `secondary` = neutral
  defaults incl. Refresh/Test/Load-more/Back; `ghost` = low-emphasis/nav-out;
  `link` = inline text; `danger` = solid destructive confirm. Add a
  **danger-ghost** variant for quiet destructive row actions → delete the
  duplicated `.btn-danger-quiet` CSS.
- Sizes: `sm` for header/section/filter/row/callout actions; `md` only for
  form-footer + standalone page primaries.
- **Refresh canonical:** `<UiButton variant="secondary" size="sm"
  icon-left="refresh" :loading>` (never rotate-ccw, never disabled+"Refreshing…"
  swap). Fix ActionCalls/TaskTracker/Schedules/CostBudget.
- `DataTable` load-more → `UiButton` (drop the raw `<button>`).
- Forms: every control in `UiFormField` with explicit `#default="{id,describedBy,
  invalid}"`; form-section grid `gap-4`; no `pt-6` spacers to align actions.

## 8. Feedback, icons & formatting

- Empty = `UiEmptyState` bare (add a `framed` prop; delete the dashed-border
  wrappers). Tables: `emptyMessage` for filtered-zero, `UiEmptyState` for true
  zero-data.
- Loading = `UiSkeleton` shaped to content. Replace literal "Loading…" divs and
  the UiEmptyState-as-loader. `UiLoadingState` only for inline/button spinners.
- Error = inline `UiCallout` bound to `error`; toasts only for transient action
  results (move RunDetail load error to a callout).
- **Medallion scale (one):** round `h-7 w-7` + icon `h-4 w-4` (tone map
  `bg-*-subtle text-*-fg`); square tile `h-8 w-8 rounded-md` + icon `h-4 w-4`.
  Eliminate every `h-9 w-9` / `h-[18px]`. Extract **`UiMedallion`** (shape, tone,
  icon). Default icon `h-4 w-4`, secondary/chevron `h-3.5 w-3.5`.
- Formatting: all dates/durations via `lib/stackos/time.ts`. Add
  `lib/stackos/format.ts` (`formatUsd`, `formatPercent`) → route CostBudget
  through it (drop inline `$${x.toFixed(2)}`).

## 9. Renderers, JSON & meters

- One renderer shell: `UiCard section` + `#header` (h3 `t-h3` + badges). Migrate
  ArtifactRenderer/ResourceViewRenderer/ContextQueryRenderer off `UiPanel` and
  ActionSchemaRenderer off bare `<details>` (collapsibility = a shell prop).
- New **`UiJsonSection`** (heading `h4 text-xs font-medium text-fg-muted` +
  `UiJsonBlock` density=compact, wrap). Apply the §3 disclosure rule.
- `UiScoreMeter` linear composes `UiProgressBar` (stop duplicating the track).
- New **`UiSparkline`** (points, ariaLabel, tone, height) — extract CostBudget's
  inline SVG. `UiDiffBlock` stays parked (no consumer) until a diff surface.

## 10. New / extended primitives (the only new code)

`UiCountBadge`, `UiMedallion`, `UiJsonSection`, `UiSparkline`,
`lib/stackos/format.ts`; extend `status.ts` (+domains, icon), `StatusBadge`
(render icon), `UiButton` (danger-ghost), `UiEmptyState` (framed), `DataTable`
(UiButton load-more), `UiScoreMeter` (compose UiProgressBar). Delete
`TrackerStatusBadge`, `KvList`, and the per-page tone helpers.

## 11. Build order

A. Primitives (above) + status.ts + format.ts — bottom layer.
B. Renderers onto the shell + UiJsonSection + disclosure rule.
C. Detail surfaces (RunDetail, ActionCallDetailDrawer, tracker panels, list
   drawers) — de-tech + disclosure + time/format helpers + UiDescriptionList.
D. List pages — drop maxHeight, humanize columns, StatusBadge, selectedId,
   UiToolbar/UiFilterBar filter bar, UiCountBadge, UiEmptyState.
E. Tabs (ProjectData, Connections) → TabBar via #tabs slot.
F. Connections panels — medallion, full-bleed lists, StatusBadge, danger-ghost.
G. Forms — form-row + gap alignment.

Verify after each layer: type-check, eslint, vitest, e2e (6 viewports + axe
light/dark + zero console errors), and screenshots of changed surfaces.
