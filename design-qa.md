# Work board card polish — Product Design QA

final result: passed

## Visual truth and scope

The operator selected displayed option 2, then requested the same agent owner
logos used on Overview and a Graph task selector that fills available toolbar
space. This modifies the existing production UI and preserves its project shell,
seven lifecycle columns, actual records, filters and task inspection behavior.

Evidence is in `reports/design/2026-10-29-board-card-polish/`:

- `selected-option-2.png`: selected visual target, 1633 × 963 pixels.
- `owner-icon-refinement.png`: shown refinement with existing logos, 1632 × 963.
- `current-board.png`: original live application capture, 1527 × 900.
- `final-dark-wide.png`, `final-light-wide.png`: source implementation at
  1633 × 963 CSS pixels, density 1.
- `final-dark-1280.png`, `final-light-1280.png`: 1280 × 800 desktop checks.
- `comparison.png` and `comparison.html`: the source and implementation in
  one comparison input, with full views at 50% and native-scale card crops.
- `built-board.png`, `built-focus.png`, `built-graph.png`: the tested production
  bundle at 1633 × 963, served at `http://127.0.0.1:5174/projects/1/tasks`.
- `graph-wide.png`, `graph-1280.png`, `last-card-focus.png`,
  `task-inspector.png`: additional behavior/layout evidence.
- `prior-design-qa.md`: preserved previous root QA report.

The visual comparison uses the same dark theme, viewport and unfiltered board
state. The mock uses illustrative data; the implementation retains live titles,
dates, counts and owners, including this task. The original sidebar and native
Inter typography remain intentionally smaller than the generated mock. There is
no claim of pixel identity with invented mock data or shell proportions.

## Findings and comparison history

No actionable P0/P1/P2 findings remain.

1. **P2 — page overflow from hidden accessibility text.** The first rendered
   implementation let absolutely positioned `Created` text expand the outer
   main scroll area to 30,620px. Each card now establishes its own positioning
   context. Post-fix page and main scroll heights equal the viewport (963px and
   800px in respective checks); vertical scrolling remains inside columns.
2. **P2 — known agent owner variants rendered initials.** Live records include
   `codex-main-agent`, `codex-main-orchestrator`, `codex:/root/...` and
   `codex:article-...`. The shared resolver now recognizes these machine IDs.
   Post-fix DOM verification confirmed all nine observed Codex owner forms use
   `/images/openai.webp`; only human/unknown role names retain initials.
3. **P2 — partially visible focused card.** Browser focus on a card in the next
   column could leave it clipped at the board's edge. Focus now reveals the card
   with nearest scrolling and a 4px margin. Post-fix built check moved the board
   to scrollLeft 238, putting the card's right edge at 1575 inside the board's
   right edge at 1579. `built-focus.png` shows that full card. At 1280px the final
   column also remains reachable, with its focus ring fully inside the board.

The combined final comparison and final built captures were opened and reviewed.
Independent code and visual reviews found no additional required corrections.

## Required fidelity surfaces

| Surface | Assessment |
| --- | --- |
| Fonts/typography | Existing Inter retained. Titles lead at 14px/20px, weight 500, two-line clamp; full text remains in the accessible name, tooltip and inspector. Footer is quieter at 12px/18px. |
| Spacing/layout | 300px column tracks with 12px gutters; compact title/footer cards, 12px padding, 8px gaps/radius; no shadows or column divider walls. Toolbar is flat on Board. Graph picker grows into all remaining space without the former 640px cap. |
| Colors/tokens | Existing semantic surfaces, foregrounds and status colors; dark/light both reviewed. Status colors are concentrated in column headers. Neutral fallback initials replace bright accent circles. |
| Images/icons | One shared asset map now serves Overview and owner marks. Actual OpenAI, Claude, Claude Code, Gemini and Hermes assets are reused without recoloring. UiIcon uses the existing registry. No generated replacement logos or custom drawn icons. |
| Copy/content | Actual IDs, dates, owners and completed/total counts. Only complete tickets count as completed. No source/manual or blanket Blocked labels on cards; underlying source/dependency details remain in inspectors. Older dates retain the year. |

## Behavior and verification

- Board filters narrowed the live list to one matching task.
- Enter opens task inspection; Escape closes it and restores the originating
  card's focus. The built bundle repeated this path successfully.
- All seven status columns remain reachable by horizontal scrolling and focus;
  vertical columns retain independent scrolling. App controls stay in viewport.
- Graph picker expands to approximately 858px in the wide populated-graph
  check. Search and task selection work. It remains usable at 1280px.
- Real owner images load successfully; zero broken card images were observed.
- Final console capture had no JavaScript errors. Existing Vue Flow deprecation
  warnings remain when entering Graph.
- The full repository `make signoff` initially stopped on 124 pre-existing lint
  errors in unrelated untracked report scripts. An isolated snapshot of current
  tracked files plus the two new identity files passed the complete gate.
- TPF removed successful suite summary lines; collection-only recovery found
  5,479 Python tests and 846 UI tests across 118 files. Gate exit was 0; UI
  execution cache contained no failed files. After alias corrections, 66 affected
  tests, lint and build passed; after the focus correction, 47 affected tests,
  lint and TypeScript/production build passed. Python sources were unchanged.
- Final source parity was independently checked before copying all 89 tested
  bundle files into `stackos/ui_dist/`. Evidence is in `receipt.json` and
  `inputs.json`; bundle digest is
  `699530c87bec2eec2f3dd988ee4ada23f0b6b259c2a8bb6887be13d408b0189a`.

## Delivery boundary and checklist

- [x] Selected option 2 implemented in existing board components.
- [x] Misleading card labels removed without changing tracker lifecycle data.
- [x] Shared Overview owner logos, including observed Codex role IDs.
- [x] Graph picker fills available toolbar width.
- [x] Dark/light desktop visual, interaction and independent review passed.
- [x] Full isolated gate plus affected final checks passed; bundle verified.

The installed daemon at port 5180 still serves its packaged older UI. The new
built UI is running at port 5174; source preview remains at 5173. No installed
desktop upgrade or public deployment was part of this change. Mobile and a
screen-reader session were outside this desktop refinement. No P3 follow-up is
required for the requested scope.
