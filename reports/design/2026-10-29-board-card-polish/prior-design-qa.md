# Work board visual refinement — Product Design QA

final result: passed

## Scope and visual truth

The requested direction is the operator's Linear board reference, adapted to the existing StackOS design system. This is a refinement of StackOS, not a pixel clone of Linear. Seven existing statuses, read-only lifecycle, the inline Board/Graph control, shared app shell and existing task inspection behavior remain canonical.

Evidence directory: `reports/design/2026-10-28-work-navigation/board-polish/`.

- Direction source: `linear-direction-reference.png` (1958 × 1023 pixels).
- Existing application source: `before-dark.png` and `before-light.png` (1440 × 1000 pixels each).
- Implemented source preview: `after-dark.png` and `after-light.png` (1440 × 1000 pixels each).
- Built implementation: `built-light.png`, served at http://127.0.0.1:5185/projects/1/tasks.
- Previous popup QA is preserved in `prior-popup-qa.md`.

All app captures use CSS viewport 1440 × 1000 and device pixel ratio 1 unless specified below. Before/after comparisons use the same route, 20-task synthetic dataset, initial scroll position and corresponding theme. Full-view images are rendered side by side at equal 50% scale. Focused images show the identical x280/y172, 768 × 790 region at 1:1 via CSS clipping of intact screenshots. Linear's different viewport/content is explicitly labeled in its directional comparison; no pixel-fidelity claim is made.

## Comparison evidence and findings

The following combined inputs were browser-rendered, opened and inspected:

- `compare-dark-full.png` and `compare-light-full.png`: complete app shell and board composition.
- `compare-dark-focus.png` and `compare-light-focus.png`: title/icon alignment, card spacing, owner marks, source chips, counts and date hierarchy at readable scale.
- `compare-reference.png`: Linear direction and StackOS result together.
- Reproducible comparison sheet: `comparisons.html` (1600 × 1000 browser viewport).

Initial audit found narrow 172px columns, repeated truncated workflow metadata, low-contrast owner initials and unnecessary 0/0 counts. The implementation uses 256px minimum columns, canonical colored status icons, stronger titles, accent owner initials, source and blocker chips, and a creation/progress footer. Zero-ticket tasks omit the completion badge. Actual completedCount is used; failed/deferred/skipped tasks are not presented as completed tickets.

Post-change comparison found no actionable P0/P1/P2 issues. Wider columns intentionally show fewer statuses at once; the visible horizontal scrollbar and keyboard focus scrolling retain access to all seven. This is the requested readable column treatment, not viewport overflow. No additional visual repair was needed after this comparison pass.

## Required fidelity surfaces

| Surface | Result |
| --- | --- |
| Fonts and typography | Existing Inter Variable/Inter system fallback retained. Titles are 14px, weight 500, line-height 20px; secondary metadata remains quieter. Long titles wrap and clamp at three lines, with full title available on hover and in the task dialog. |
| Spacing and layout | 256px minimum columns, 12px card padding, 8px card spacing and 24px owner marks create a consistent rhythm. Header icons and labels align. Shared radii and shadow tokens separate cards without changing the shell. |
| Colors and tokens | Status icons use canonical trackerStatus tones. Owner initials use the existing accent subtle/foreground pair. Light and dark surfaces, borders and focus rings remain shared design-system tokens. |
| Images and icons | Existing central UiIcon/Heroicons registry used throughout. No new bitmap, invented avatar, custom drawn icon or substitute logo. Icons remain sharp at native scale. |
| Copy and content | Graph wording retained. Owner, workflow/source label, created date and completed/total counts come from actual task data. Blocked appears only with a real blocker. No invented priority, labels or profile identity. |

## Behavior and responsive verification

- Desktop captures at 1280 × 900 (`after-1280.png`), 1440 × 1000, and 1920 × 1080 (`after-1920.png`) were opened and inspected. App controls remain inside the viewport; board overflow stays contained.
- Project 4 fixtures cover zero, one, six, seven, thirty tickets and long content (`long-1280.png`). Titles, chips, empty columns and counts remain contained.
- At 1280px, focusing the final card scrolls the board to scrollLeft 846; its right edge is 1218px and the full focus ring remains visible (`last-focus.png`). Enter opens the task dialog. Escape restores focus and board scroll position.
- Built preview card click opens the shared stable popup (`built-popup.png`). Capture was taken after opacity reached 1. Escape removes the dialog and restores the original task card focus.
- Graph entry preselects a task. A zero-ticket task correctly has an empty graph. Selecting improve-work displays six nodes; clicking a node leaves one selected node and zero dialogs, preserving earlier graph behavior.
- Browser console errors: none in built Board → popup → Graph interaction checks.

The preliminary source popup capture was taken during its transition and was excluded from accepted evidence. The built popup capture above replaces it. An initial comparison file URL was outside the native browser's allowed local roots; the unchanged evidence sheet was copied into /tmp and rendered successfully. These were capture corrections, not product fixes.

## Code review and verification

- Changed production owner: `ui/src/views/task-tracker/TrackerBoardPanel.vue`; corresponding existing spec updated. No shared status registry, graph or popup logic changed in this refinement.
- Worker candidate: `88266d9b819a71e171fadea47c715d99a88c1ad29341e77db6c317b11d7b1afd` (two paths).
- Independent read-only review: PASS, no required findings; four Board tests independently passed.
- Worker Board/composition checks: 11 tests passed; TypeScript, targeted lint and diff checks passed.
- Isolated integrated candidate: `04fddeae242325e5d82d9dc34b259d5bccd33772df435c5bc442e546dcc368eb`.
- `make signoff` passed with exit code 0 in `/tmp/stackos-board-polish/signoff`: Python lint/types/tests, UI tests and production build. Existing router-test, Alembic and chunk-size warnings remain non-failing.
- Frozen source hashes were verified before copying the tested bundle. All 89 built files match in the repository bundle and isolated preview. See `bundle-receipt.json`.
- No new or reopened StackOS workflow; no installed runtime update or commit was performed.

## Implementation checklist

- [x] Preserve canonical shell, status semantics and interactions.
- [x] Refine icons, card hierarchy, owner marks and useful metadata.
- [x] Inspect full and focused before/after views in both themes.
- [x] Check desktop widths, long content, keyboard access and graph behavior.
- [x] Pass independent review and required verification; update built preview.

Residual scope: this is a desktop board refinement. No new mobile interaction, screen-reader session or touch-device audit was added. No unresolved finding in the requested scope remains.
