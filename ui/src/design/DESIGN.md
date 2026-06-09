# StackOS Console — Design Language

The reference for every component and view. When in doubt, match an existing
rewritten primitive (`UiButton`, `UiBadge`, `UiCard`, `UiInput`, `DataTable`)
rather than inventing a variation.

## Identity

A refined operator console: calm zinc neutrals, an indigo accent used
sparingly, a dark sidebar anchoring the shell in both themes, generous line
heights at operational density. Surfaces are differentiated by *material*
(app bg vs surface vs sunken), not by stacking borders.

## Hard rules

1. **Semantic tokens only.** Colors come from Tailwind semantic aliases
   (`bg-bg-surface`, `text-fg-muted`, `border-default`, `bg-accent`,
   `text-success-fg`, `bg-sb-bg`…) or CSS vars (`var(--color-…)`, `var(--sb-…)`).
   Never raw hex, never Tailwind palette colors (`text-slate-500` is banned).
2. **No Vue watchers** (`watch`, `watchEffect`…) — enforced by
   `src/no-watch-ui.spec.ts`. Use `computed`, event handlers, lifecycle hooks.
3. **No new write API calls** — enforced by `src/read-only-ui.spec.ts`.
4. **Component APIs stay stable.** Props/emits/slots may gain optional
   additions, never breaking changes, unless every usage is updated in the
   same pass.
5. **Focus:** every interactive element uses `.focus-ring` (or
   `.focus-ring-inset` in tight containers, `.focus-ring-sb` on the sidebar).
   Never remove focus affordances; never use `outline-none` without a
   `focus-visible` replacement.
6. **Icons:** only via `UiIcon` + the registry in `components/ui/icons.ts`
   (Heroicons outline). No inline `<svg>` in components — add missing icons to
   the registry instead. Decorative icons get `aria-hidden="true"`.
7. **Motion:** transitions use `duration-fast`/`duration-base` +
   `ease-standard`. Animate `opacity`/`transform` only. Reduced motion is
   handled globally.

## Scale reference

- **Radii:** controls `rounded-sm` (6px) · cards/menus `rounded-lg` (10px) ·
  dialogs/drawers `rounded-xl` (14px) · status pills `rounded-full` ·
  tiny chips `rounded-xs` (4px).
- **Control heights:** sm 28px (`h-7`), md 32px (`h-8`), lg 40px (`h-10`).
  Table rows ~36px. Sidebar nav items 32px.
- **Type:** body 13px. Page title `t-h1` (19px semibold tracking-tight).
  Section/card titles `t-h3` (14px semibold). Labels `text-xs font-medium
  text-fg-muted`. Overlines `t-overline` only for true grouping labels —
  use sparingly. Mono (`font-mono text-xs`) ONLY for IDs, keys, code, URIs —
  never for prose or dates.
- **Spacing:** card padding `px-4 py-3` (compact) / `p-5` (comfortable).
  Page sections stack with `space-y-5`. Related controls `gap-2`.

## Color usage

- Accent = interaction: primary buttons, active nav/tab/selection, links,
  focus. Not decoration.
- Status tones (success/warning/danger/info/neutral) only for state, via
  `UiBadge`/`StatusBadge`/`UiCallout` — subtle tinted fills with `*-fg` text
  and a hairline `*-border`.
- Hierarchy through fg ramp: `fg-strong` (titles) → `fg-default` (body) →
  `fg-muted` (secondary) → `fg-subtle` (hints). Disabled is `fg-disabled`,
  never opacity on text.

## Component anatomy

- **Buttons:** `rounded-sm`, font-medium, primary = `bg-accent
  text-fg-on-accent shadow-xs hover:bg-accent-hover`; secondary = `border
  border-default bg-bg-surface shadow-xs hover:bg-bg-surface-alt`; ghost =
  borderless, hover tint; danger = `bg-danger` solid. Icon-only square.
- **Cards (`UiCard`):** `rounded-lg border border-default bg-bg-surface
  shadow-xs`; header slot separated by `border-subtle`. `UiPanel` = nested
  quiet surface: `rounded-lg border-subtle bg-bg-surface-alt`, no shadow.
- **Tables:** header `bg-bg-surface-alt text-2xs uppercase tracking-wide
  text-fg-muted`; rows `h-9` with `hover:bg-bg-surface-alt`, selected
  `bg-accent-subtle`; divider `divide-border-subtle`.
- **Badges:** `rounded-full px-2 text-2xs font-medium` tinted pill with
  hairline border; optional leading status dot.
- **Inputs:** `h-8 rounded-sm border-default bg-bg-surface shadow-inset-none`
  hover `border-strong`, focus ring via `.focus-ring`; invalid swaps to
  danger border.
- **Overlays:** dialog `rounded-xl shadow-xl`, drawer slides with
  `shadow-xl`; both scrim `bg-bg-overlay backdrop-blur-[2px]`. Menus/popovers
  `rounded-lg border-default shadow-md p-1`, items `rounded-sm px-2 h-8`.
- **Empty states:** centered, icon in `bg-bg-sunken rounded-full` ring,
  title `t-h3`, hint `text-fg-muted`, optional action.
- **Page header:** eyebrow optional, `t-h1` title row with actions right,
  short `text-fg-muted` description. No raw mono metadata strips.

## Voice

Labels are sentence case ("Workflow library", not "WORKFLOW LIBRARY" except
`t-overline` group labels). Buttons are verbs. Empty states explain what the
surface is for and how rows get created (usually by agents via MCP).
