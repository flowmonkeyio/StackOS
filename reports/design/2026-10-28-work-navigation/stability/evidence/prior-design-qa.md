# Work board implementation — Product Design QA

final result: passed

No actionable P0/P1/P2 findings remain. This review covers the delivered Work UI, not a general product or accessibility certification.

## Visual truth and evidence

- Source: `reports/design/2026-10-28-work-navigation/concepts/01-board.png`, `02-task-popup.png`, `03-dependency-map.png`.
- Latest operator amendment is authoritative: Board / Dependency map belongs inline with the other controls. Tickets is removed as a main view. The existing StackOS shell, components and design tokens remain authoritative over generated concept details.
- Implementation: `http://127.0.0.1:5175/projects/1/tasks`; production bundle smoke: `http://127.0.0.1:5185/projects/1/tasks`. Both use isolated synthetic data. The installed app on5180 was not replaced.
- Candidate: `4df1ab8ebdd011a335d82a0915591298b092d897008bedbfcd7bb24905ceabe8`; exact source and generated-bundle manifests are in `reports/design/2026-10-28-work-navigation/implementation/evidence/`.
- Source and primary implementation captures are1630×965 pixels for a1630×965 CSS viewport, 1:1 density. Additional desktop captures are1280×800 and1440×900, in dark and light themes. No density resampling was used to judge fidelity.
- Primary state: project Board with20 tasks/seven statuses, task popup with6 tickets/3 complete and empty input, and that task’s5-edge map. Fixture IDs, owners, ordering and workflow labels differ from the illustrative concept. Those differences are disclosed rather than treated as pixel errors. The later live-event test deliberately changed counts from4/4 to3/5.
- Full combined comparisons, each opened with source and implementation together: `board-comparison.png`, `map-comparison.png`, `popup-comparison.png` in the evidence directory.
- Focused combined comparisons: toolbar region within the full comparison files, plus `map-focus.png` and `popup-focus.png`. Full original captures remain alongside them; focused crops are for typography and alignment, not evidence that cropped content is absent.
- Eighteen viewport/theme/state screenshots were inspected by main and a bounded independent visual reviewer. Additional evidence includes `empty-project.png`, `disconnected-map.png`, and `production-board.png`.

## Findings and iteration history

1. **Resolved P2 — first map node clipped.** Initial fitting occurred before graph node dimensions were ready. Fitting now follows the actual graph mount and nodes-initialized lifecycle; all six nodes fit. The final lifecycle repair also protects stale and disposed requests.
2. **Resolved P2 — small map unnecessarily zoomed out.** Six-node cards initially rendered209×124px despite available space. Small graphs now use64px rank spacing and0.08 fit padding; the wide capture renders258×153px cards. Existing large-graph and saved-viewport behavior is preserved. `map-comparison.png` and `map-focus.png` were recaptured and opened after the fix.
3. **Resolved P2 — narrow minimap covered a node.** At1280px the overview overlay obscured the bottom-right node footer. The minimap is now hidden at widths below1400px; zoom/fit controls remain. Compare `map-dark-1280-before-minimap-fix.png` with `map-dark-1280.png`; light-theme equivalents were also recaptured and opened. No node/footer is obscured.

The no-watch project-contract repair, async stream cleanup and stale source-test fixes were engineering verification repairs, not claimed as visual QA iterations. They changed no stable layout beyond the narrow minimap correction; affected route, save and graph journeys were rerun.

## Required fidelity surfaces

| Surface | Assessment |
| --- | --- |
| Fonts and typography | Uses existing Inter Variable/Inter UI and JetBrains Mono metadata tokens. Titles, body copy and controls retain the app hierarchy; wrapping is contained and long workflow labels truncate. Graph nodes preserve the existing12px source title scale and zoom controls. Generated concept lettering is not substituted for the design system. |
| Spacing and layout | Full available Work area, seven columns, one desktop toolbar. Measured control tops112–114px share a line. Narrow Board overflow is contained; the document stays within the viewport. Dialog body scroll retains header/footer. Map fitting and minimap overlap findings are resolved. |
| Colors and tokens | Existing semantic status badges, neutral segmented control, surfaces, text and focus ring are reused in both themes. The concept’s stronger lime/glow treatment is intentionally replaced by current canonical component tokens, as requested. |
| Image quality and assets | Existing StackOS logo and Heroicons remain crisp and unchanged. The edit icon is a canonical PencilSquareIcon registry entry. No custom imagery, CSS artwork or placeholder replacement was introduced. |
| Copy and content | Board and Dependency map are the only modes. Description, tickets and Additional input follow the selected concept; status remains agent-managed. Goal, evidence and metadata remain inspectable under More details. Fixture content is identified as sample data. |

## Interaction and state proof

- Exact task opens a centered popup; ticket inspection uses one active dialog and Back to task. View dependencies opens that task’s map.
- Board search persists through map selection, mode changes and browser Back/Forward. The map selector contains all project tasks independently of the Board filter.
- Description and appended input survive refresh. A real concurrent update preserves the draft, shows the latest content for review and permits a fresh save. Persisted lifecycle, ticket/run bindings and dependencies remain unchanged.
- Unsaved close preserves text when declined. Keyboard Enter opens a card, Tab stays inside the popup, Escape closes it and returns focus to the originating card.
- A project-wide event moves an unrelated task to its new column without navigation. Empty project, no results, missing task, no tickets and disconnected nodes have explicit usable states.
- Fresh final browser diagnostics contain no new runtime exception. An existing Vue Flow API deprecation warning remains. Earlier intentional conflict and isolated-daemon restart responses are historical test evidence.
- Production bundle smoke opens the Board and exact task popup using the built asset. Full verification:5470 Python tests,777 UI tests across117 files, lint/format, mypy and UI typecheck/build passed. See `verification.json` for provenance and historical failures.

## Open questions

None blocking delivery. Product choices intentionally follow the user’s inline-control amendment and the current design system.

## Implementation checklist

- [x] Preserve app shell and canonical components/tokens.
- [x] Keep seven read-only status columns and only two inline views.
- [x] Inspect/edit content in centered dialogs without lifecycle controls.
- [x] Verify full and focused source/render comparisons.
- [x] Resolve all P0/P1/P2 findings and recapture affected states.
- [x] Verify three desktop widths, both themes, keyboard, error/empty states and production bundle.

## Follow-up polish

P3: very long workflow keys truncate on compact cards; richer display labels can be considered separately. At narrow widths, the map fits the full chain and provides zoom plus full-size detail inspection. Existing Vue Flow deprecation and build chunk-size warnings remain outside this UI change.

The prior root QA report is preserved at `reports/design/2026-10-28-work-navigation/implementation/evidence/prior-design-qa.md`.
