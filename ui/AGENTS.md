# StackOS UI Agent Guide

This file scopes work under `ui/`. It is a router, not a second design system.

## Read first

| Work | Source of truth |
|---|---|
| Product/UI principles, tokens, accessibility, responsive behavior | [`../docs/ui-design-system.md`](../docs/ui-design-system.md) |
| Component inventory, migration map, and view decomposition pattern | [`../docs/ui-component-inventory.md`](../docs/ui-component-inventory.md) |
| Local token/class usage and component implementation notes | [`src/design/DESIGN.md`](src/design/DESIGN.md) |
| Product direction and primary human flows | [`../docs/ui-redesign-direction.md`](../docs/ui-redesign-direction.md) |
| Generic StackOS object and operation model | [`../docs/architecture.md`](../docs/architecture.md), [`../docs/operations.md`](../docs/operations.md) |

Read only the references relevant to the change, but treat them as binding.
Update the owning document when a new durable pattern is introduced; do not
copy the same guidance into another file.

## Implementation boundaries

- Route views are composition roots. They own routing, page-level loading order,
  and cross-feature dispatch; they do not own several provider forms or async
  lifecycles inline.
- Colocate feature-only components, composables, types, and pure view models
  under `src/views/<feature>/`. Promote code to shared `components/` or
  `composables/` only when another feature uses the same contract.
- A feature composable owns one named lifecycle, its loading/error/message
  state, and its reload policy. It receives explicit refs/callbacks and does not
  access the router or render markup.
- Put pure grouping/filtering/projection logic in a feature `viewModel.ts` and
  test it without mounting the route.
- Treat 700 route-view lines and 400 feature-component lines as mandatory
  decomposition reviews. Split by coherent ownership, not by arbitrary line
  count or pass-through wrappers.
- Use generated API types and the canonical store/operation client. Do not add
  a second UI-only interpretation of server lifecycle, permission, provider,
  or readiness rules.
- [`../provider-presentation.json`](../provider-presentation.json) is the single
  reviewed provider logo/kind map for both the website catalog and this UI.
  Use the shared UI `components/domain/ProviderMark.vue`; do not add another
  provider asset map or another feature-local mark component.
  The website catalog sync validates that `website/public` and `ui/public`
  contain byte-identical mapped assets.

`ConnectionsView.vue` and `views/connections/` are the current reference: the
route coordinates project sections and cross-feature dispatch;
`useConnectionCredentials`, topology, ingress, Telegram, and Slack composables
own their named lifecycles; form regions live in focused subcomponents.

## Interaction requirements

- Every pointer interaction must have a keyboard path and announced state.
- Loading, empty, error, partial-success, and destructive confirmation states
  must be truthful. Do not render zero/ready/setup conclusions before required
  data has resolved.
- Preserve normal page/panel scrolling. Interactive canvases must not capture
  the wheel for zoom unless the surface is explicitly a dedicated graph mode.
  Workflow-guide previews are page content: they grow to their stage layout and
  disable wheel/pinch zoom. The task-tracker dependency map is a dedicated,
  bounded canvas: keep pan, wheel/pinch zoom, minimap, and controls available.
- Check responsive behavior at 320px, 390px, around the component's layout
  breakpoint, and desktop width. Avoid fixed graph/card geometry that only fits
  on first mount.
- Secret fields belong in semantic forms, remain daemon-only, and must never be
  rendered back through messages, logs, test snapshots, or API payload display.
- Reuse existing primitives (`UiConfirmDialog`, `UiFormField`, `UiSelect`,
  `UiSidePanel`, empty/loading/callout components) before adding local controls.

## Verification

From the repository root, use the smallest relevant checks first:

```bash
pnpm --dir ui test -- <focused spec files>
pnpm --dir ui exec eslint <touched .ts/.vue files>
pnpm --dir ui type-check
pnpm --dir ui build
```

For interaction changes, add a focused regression and inspect the live Vite UI
at `http://127.0.0.1:5173/`. Keep route-level tests for cross-feature wiring;
partition large suites by user flow and share only test builders/mount support.
