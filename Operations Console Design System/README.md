# Operations Console — Design System

A generic, reusable design system for **operations / admin SaaS** consoles. Built around dense operational tooling: project lists, run logs, plugin cards, resource records, integration cards, status badges, score meters, budgets, and timelines.

This system is **product-agnostic**. It ships semantic tokens, primitive components, and domain-shape components with well-defined props — none of which are stitched to a specific codebase. Drop it into any Vue 3 + TS + Tailwind app.

> **Vibe:** Linear / Vercel / GitHub / Plaid dashboards. Calm. Dense. Keyboard-first. Subtle borders over heavy shadows. Restrained accent. Dark mode is first-class.

---

## Index

| Path | Purpose |
|---|---|
| `colors_and_type.css` | CSS vars (root) — copy/paste into any project. Light + dark themes. |
| `DesignSystemShowcase.html` | Visual reference page: tokens, primitives, patterns, dialog mocks. |
| `ui/src/design/tokens.ts` | Semantic color, spacing, radius, shadow, z-index, typography, layout tokens. |
| `ui/src/design/status.ts` | Canonical status / severity → tone mappings for runs, steps, projects, integrations, and budgets. |
| `ui/src/design/colors_and_type.css` | Same as root CSS file, project-local copy. |
| `ui/tailwind.config.js` | Tailwind preset wiring tokens → classes. |
| `ui/src/components/ui/` | 38 primitives (UiButton, UiInput, UiDialog, UiToast, UiDataTable…). |
| `ui/src/components/domain/` | Generic domain shapes: ProjectHeader, RunTimeline, BudgetMeter, integration cards. |
| `docs/ui-design-system.md` | Principles, tokens, layout rules, a11y, anti-patterns. |
| `docs/ui-component-inventory.md` | Inventory + missing patterns + migration map. |
| `preview/` | Static preview cards rendered in the Design System tab. |
| `SKILL.md` | Skill manifest — drop this folder into Claude Code as a Skill. |

---

## Content fundamentals

**Voice:** operator-to-operator. Direct. No marketing fluff. No exclamation marks. Never breathless.

**Tense / person:** present tense, second person ("Your run failed", "Connect this provider"). The system addresses an operator who knows what they're doing.

**Casing:** `Sentence case` for everything — page titles, section headers, button labels, table headers. Reserve `Title Case` for proper nouns. Reserve `UPPERCASE` for the small overline label class (`.t-overline`, 11px, `letter-spacing: 0.08em`).

**Numbers:** tabular figures everywhere data is compared. Currency with explicit symbol. Durations as `2.4s`, `11.8s`, `1.2m` — never `2400ms` in user-facing copy.

**Errors:** state what failed, then state the action. *"Slugs may only contain a-z, 0-9, and dashes."* Not *"Invalid input"*.

**Empty states:** describe what would be here, then a single primary action. Never "Nothing to see!"

**Emoji:** **no.** Not in copy, not in icons, not as status indicators. Use `lucide-vue-next` or the SVG set in `assets/icons/`.

**Examples**
- Heading: *"Connect WordPress"* — not "Hook up your WP site!"
- Help text: *"Stored encrypted at rest."* — not "Don't worry, it's safe 🔒"
- Toast: *"Schedule saved."* — not "Schedule saved successfully!!"

---

## Visual foundations

### Color

- **Neutral base, restrained accent.** UI is gray scrollkit; one blue accent (`#2563eb`) carries primary intent. Status tones (success, warning, danger, info) appear only when they mean something.
- **Semantic only at component level.** Components consume `bg.surface`, `fg.muted`, `accent.primary` — never `slate-100` or `blue-600` directly.
- **Dark mode** flips the semantic layer; component CSS does not branch.
- Purple is not a product status or primary accent. Keep the system palette neutral, blue, and semantic status colors.

### Type

- **Inter** for everything. Variable font preferred. Falls back to system-ui.
- **JetBrains Mono** for IDs, code, durations, hashes — anything tabular or monospaced.
- **Body sits at 13/18.** Dense. Readable. Smaller than marketing UI. The base scale runs 11 → 32px in tight steps.
- `font-feature-settings: 'cv11','ss01','ss03'` enabled globally for clean disambiguated digits.

### Spacing

- **4px grid.** Spacing scale `0, 1, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24` corresponds to `0, 4, 8, 12, 16, 24, 32, 40, 48, 64, 80, 96`.
- Form rows: 12–16px between rows, 6px between label and control.
- Card padding: 16px (sm), 20px (md), 24px (lg). No 32px cards — too airy.

### Radii

- **8px max** for surfaces. Radii scale `2 / 4 / 6 / 8 / full`.
- Buttons & inputs: 4px. Cards & panels: 6px. Dialogs: 8px. Pills: full.

### Borders & shadows

- **Borders carry the weight.** `1px` subtle border separates surfaces 90% of the time.
- Shadows are **subtle**: `xs` for cards, `sm` for dropdowns, `md` for popovers, `lg` for dialogs. Nothing larger.
- No glow shadows. No colored shadows.

### Backgrounds

- **No gradients.** No decorative blobs. No mesh. No imagery in chrome.
- One flat surface color per layer. Layering happens via 1px borders + 4–6% lightness deltas.

### Animation

- 120ms (fast), 180ms (base), 260ms (slow).
- `cubic-bezier(.2 0 0 1)` standard, `cubic-bezier(0 0 .2 1)` enter, `cubic-bezier(.4 0 1 1)` exit.
- **No bounces. No springs.** Just fades, slides, and color transitions.
- Spinners only when an operation is genuinely indeterminate.

### Hover / press

- Hover: surface goes one step lighter (or darker in dark mode), border one step stronger.
- Press: no shrink. No scale. Just a deeper background tone.
- Focus: **always-visible 2px outline** in `--color-border-focus`, 2px offset.

### Cards

- 1px border + `xs` shadow + 6px radius. **Never nested.** A card inside a card means you should be using a panel + section, not two cards.
- Cards are for *real things* — projects, plugins, runs, resources, and integrations. Not every layout block.

### Layout

- App max width: `1536px` (`max-w-content-wide`). Reading max: `768px` (`max-w-content-narrow`).
- Sidebar: 240px. Top bar: 52px. Bottom bar: 48px when present.
- Tables and forms go full-width within the content column.

### Transparency / blur

- Tooltips: solid inverse background, no blur.
- Dialogs: scrim at `rgba(0,0,0,0.45)` light / `rgba(0,0,0,0.65)` dark, no backdrop blur.
- Sticky headers: solid `bg.surface`, no glass. Operational tools shouldn't fight contrast.

---

## Iconography

- **Lucide** (`lucide-vue-next`) is the canonical icon library. 16/20/24px sizes, 2px stroke.
- Inline SVG for one-offs only. Never hand-roll an icon if Lucide ships it.
- **No emoji** in chrome. Ever.
- Status indicators: a 6px filled `.dot` next to a `UiBadge` — not an icon.

The showcase HTML uses inline SVGs to stay self-contained; in production code, use Lucide.

---

## Component coverage

**Primitives (38)** — all in `ui/src/components/ui/`:

`UiButton`, `UiIconButton`, `UiButtonGroup`, `UiInput`, `UiTextarea`, `UiSelect`, `UiCheckbox`, `UiSwitch`, `UiRadioGroup`, `UiRange`, `UiSecretInput`, `UiFormField`, `UiDialog`, `UiConfirmDialog`, `UiSidePanel`, `UiDropdownMenu`, `UiPopover`, `UiTooltip`, `UiCard`, `UiPanel`, `UiCallout`, `UiEmptyState`, `UiLoadingState`, `UiSkeleton`, `UiToast`, `UiBadge`, `UiProgressBar`, `UiScoreMeter`, `UiCodeBlock`, `UiJsonBlock`, `UiDiffBlock`, `UiPageHeader`, `UiSectionHeader`, `UiToolbar`, `UiFilterBar`, `UiBulkActionBar`, `UiMetricCard`, `UiDescriptionList`.

**Domain (5 shipped, more sketched in inventory)** — `ui/src/components/domain/`:

`ProjectHeader`, `ProjectStatusSummary`, `IntegrationProviderCard`, `RunTimeline`, `BudgetMeter`.

See `docs/ui-component-inventory.md` for the full list, including what's deferred.

---

## Quickstart

```ts
// main.ts
import './design/colors_and_type.css'
```

```vue
<script setup lang="ts">
import { UiButton, UiCard, UiBadge } from '@/components/ui'
import { ProjectHeader, BudgetMeter } from '@/components/domain'
</script>

<template>
  <ProjectHeader name="acme-content" slug="acme-content" state="active" />
  <BudgetMeter :spent="425" :cap="500" period="MTD" />
</template>
```

Theme switching is `data-theme="light"` / `"dark"` on `<html>`.

---

## Iteration

This is v1. The next thing to do is wire the rest of the domain components (RunStepAccordion, IntegrationSetupDialog, SchemaEditorPanel, LinkSuggestionCard, ScheduleRuleCard, MarkdownSectionEditor) — see the inventory for the full list and priority order.
