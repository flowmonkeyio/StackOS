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
| `ProviderMark` | canonical provider logo/initial mark backed by `provider-presentation.json`; shared by Connections and Plugins |

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

Connections is the reference implementation:

- `ConnectionsView.vue` owns project routing, section composition, and
  cross-provider bot dispatch.
- `useConnectionCredentials` owns provider visibility, the schema-driven
  credential form lifecycle, inline validation, verification, and revoke state.
- `useCommunicationTopology` owns profile/target/surface/route loading.
- `useIngressEndpointEditor`, `useTelegramProfileEditor`, and
  `useSlackProfileEditor` each own one mutation lifecycle.
- `ConnectionServiceSelect`, `ConnectionMetadataFields`,
  `ConnectionCredentialFields`, and `ConnectionCredentialField` own form
  regions and schema rendering; `AddConnectionPanel` composes them and owns
  provider/setup feedback plus its action footer.

Plugins follows the same composition rule: `PluginsView.vue` owns route loading
and catalog controls; `plugins/viewModel.ts` owns cross-plugin/provider/action
search and sorting; focused plugin/provider card components own presentation.
Both Plugins and Connections use the shared `ProviderMark` so website catalog
logos and operator UI logos cannot drift.

Graph surfaces intentionally have two different sizing contracts. Workflow
guide journeys are document previews: their canvas grows to the complete stage
layout and leaves wheel/pinch gestures to the containing page. The task tracker
dependency map is a bounded, page-sized exploration canvas: node geometry and
spacing may extend beyond the viewport, while pan, wheel/pinch zoom, minimap,
and controls expose that off-canvas context. Do not share viewport behavior
between these surfaces merely because both render with Vue Flow.

Partition integration tests by user flow before moving risky async ownership.
Use focused unit tests for pure view models/composables and keep one route-level
test for cross-feature wiring, loading, and error-state truthfulness.

### Current decomposition map

| File | Current ownership decision | Next safe seam |
|---|---|---|
| `views/ConnectionsView.vue` | Route/query synchronization, section composition, loading order, and cross-provider dispatch remain. Credential, topology, ingress, Telegram, and Slack lifecycles are extracted. | Keep router access and cross-feature dispatch here; do not fold lifecycle state or provider editors back into the page. |
| `views/connections/useConnectionCredentials.ts` | Provider visibility, credential form state, save-and-verify, test, and revoke form one lifecycle with field-level validation. | Keep routing out. Split only if credential discovery and mutation acquire separate reload/error policies. |
| `views/connections/AddConnectionPanel.vue` | Cohesive panel/form shell; service selection, account metadata, and schema-driven credential rendering are subcomponents. | Keep provider/setup feedback and the action footer here; promote field rendering only if another feature adopts the same schema contract. |
| `views/TaskTrackerView.vue` | Route/query synchronization, page-level snapshot loading, and cross-feature dispatch remain. `useTrackerExecutionContexts` owns context/artifact pagination; `useTrackerGraphSession` composes focused graph loading and selection with dedicated viewport and live-update lifecycles. Pure projections and filters remain in `task-tracker/viewModel.ts`. | Keep new graph interaction state inside the graph session. Extract command/filter orchestration only if it gains an independent async lifecycle; do not move route/query access into a feature composable. |
| `views/connections/credentialPresentation.ts` | Provider/auth grouping, labels, connection status, and account presentation. | Keep provider catalog knowledge here; do not mix in communication topology facets. |
| `views/connections/formatters.ts` | Communication profile, route, surface, Telegram, Slack, and ingress presentation, plus a compatibility re-export of credential presentation. | Rename/split another provider-specific facet only when it gains a distinct contract or test lifecycle. |
| `views/AgentRequestsView.vue` | One cohesive queue/master-detail flow. | Extract the request detail region only if it gains independent mutation/loading state. |

Large generated catalogs, API types, and declarative plugin manifests are not
decomposition targets. Change their generators or source manifests instead of
splitting generated output.

Provider logo paths and presentation kinds are an adjacent one-brain contract:
edit [`../provider-presentation.json`](../provider-presentation.json), not a
view-local map. Website and desktop UI keep separate Vue mark components, while
the website catalog sync validates both public asset trees against that shared
manifest.

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
