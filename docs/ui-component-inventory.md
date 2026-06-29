# UI Component Inventory

A flat, honest list of what's shipped, what's coming, and what to migrate.

## Shipped — primitives (`ui/src/components/ui/`)

| Component | Notes |
|---|---|
| `UiButton` | variants: primary, secondary, ghost, danger; sizes sm/md/lg; loading; iconLeft/Right |
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
| `UiDialog` | trap focus; H/B/F |
| `UiConfirmDialog` | builds on Dialog |
| `UiSidePanel` | left/right; 480/720 |
| `UiDropdownMenu` | keyboard arrows; sections |
| `UiPopover` | floating-ui placement |
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
| `UiToolbar` | sticky action bar |
| `UiFilterBar` | search + chips + filters |
| `UiBulkActionBar` | generic primitive only; not used in observer-mode product views |
| `UiMetricCard` | label + value + delta |
| `UiDescriptionList` | label/value rows |
| `UiFactGroups` | grouped drawer/detail metadata blocks with headings, badges, mono IDs, and wide rows |

## Shipped — domain (`ui/src/components/domain/`)

| Component | Notes |
|---|---|
| `ProjectPageHeader` | project-aware title, breadcrumbs, utility action slot, and route chrome |

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

Removed action-oriented demo components. They were removed from `ui/src`
because they exposed or demonstrated domain mutations that now belong to the
agent/MCP path.

## Design-system showcase

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
| Project schedules tab | UiPanel, UiBadge, DataTable | ProjectPageHeader |
| Project budget tab | UiPanel, UiMetricCard, DataTable | ProjectPageHeader |
| Runs list | UiFilterBar, DataTable, UiBadge | (none) |
| Run detail | UiPageHeader, UiCodeBlock, UiJsonBlock, RunPlanRenderer, ArtifactRenderer | (none) |
| Plugins | UiPanel, UiBadge, UiButton | ProjectPageHeader |
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
