---
name: operations-console-design
description: Use this skill to generate well-branded interfaces and assets for an operations / admin console — either production code or throwaway prototypes. Contains a full set of design tokens, semantic CSS variables, status mappings, Vue primitives, and domain-shape components for fast prototyping of dense, calm, keyboard-first SaaS dashboards.
user-invocable: true
---

Read the README.md file within this skill, and explore the other available files.

If creating visual artifacts (slides, mocks, throwaway prototypes, etc), copy `colors_and_type.css` and reference it in your HTML; lift the visual rules from `README.md` (Visual Foundations) and `docs/ui-design-system.md`. Use `DesignSystemShowcase.html` as the canonical pixel reference.

If working on production code, copy `ui/src/design/tokens.ts`, `ui/src/design/status.ts`, the `ui/src/components/ui/` primitives, and any `ui/src/components/domain/` shapes you need. Wire `ui/tailwind.config.js` into the host project's tailwind config (extend, do not overwrite).

If the user invokes this skill without any other guidance, ask them what they want to build or design, ask some questions about density, dark mode, and which domain shapes they need, and then act as an expert designer who outputs HTML artifacts *or* production code, depending on the need.

**Hard rules to enforce, no matter the output format:**
- Calm, dense, keyboard-first. No marketing-page tropes.
- No gradients, no decorative blobs, no glass/blur backgrounds.
- No emoji. No hand-rolled icons — use Lucide.
- 8px max radius for most surfaces.
- Subtle borders carry the weight; shadows stay subtle.
- Dark mode is first-class — flip via `data-theme` on `<html>`.
- Components consume semantic tokens (`bg.surface`, `fg.muted`, `accent.primary`), never raw palette values.
- Focus rings are always visible (2px outline, 2px offset, `--color-border-focus`).
- Purple is not a product status or primary accent; keep the system palette neutral, blue, and semantic status colors.
