# Work inspection polish — Product Design QA

final result: passed

Visual, Work behavior and final signoff checks pass. D3 repaired the shared lifecycle regression and passed independent review and the complete signoff rerun. Prior QA is preserved at `reports/design/2026-10-28-work-navigation/stability/evidence/prior-design-qa.md`.

## Target and evidence

The accepted StackOS Work concept and operator task/ticket screenshots remain the visual truth. This delivery preserves the application shell, inline Board/Dependency map navigation, tokens, cards and shared controls.

Evidence folder: `reports/design/2026-10-28-work-navigation/stability/evidence/`.

| State | Source visual truth | Rendered implementation | Combined comparison |
| --- | --- | --- | --- |
| Task, dark | source-task-1280.png | after-task-dark-1280.png | compare-task-dark-full.png and compare-task-dark-focus.png |
| Ticket, dark | source-ticket-1280.png | after-ticket-dark-1280.png | compare-ticket-dark-full.png and compare-ticket-dark-focus.png |
| Long task, light | source-long-1280.png | after-long-light-1280.png | compare-long-light-full.png and compare-long-light-focus.png |

Originals: 1280×800 pixels, 1280×800 CSS viewport, density1. Full comparisons render both intact screenshots at equal640px widths. Focused comparisons show the same x290/y16/690×784 region at1:1. Browser HTML places both images in one input; no screenshot pixels were edited. All six combined captures were opened and inspected. The prior ticket source was opened over the map; the implementation ticket is opened from the Board task. This intentional background difference follows restored map selection behavior. Ticket typography/content is compared in the focused view; shell fidelity is established by the matching task/long-task views.

Source preview: http://127.0.0.1:5175/projects/1/tasks . Authorized StackOS profile `work-inspection-qa`, session `work-stability-20261028`, tab3. Writes used synthetic project4 only. Existing operator tabs and drafts were preserved. Exact image hashes/dimensions are in `visual-evidence-manifest.json`.

## Findings and comparison history

1. **Resolved P1 — changing frame.** Original task/ticket/expanded heights varied. Existing UiDialog now provides an opt-in reusable detail layout. Task and ticket measure672×752 at1280×800,672×552 at1024×600 and672×880 at1440×1000. Header/footer stay fixed; More details scrolls inside the frame.
2. **Resolved P2 — unbounded tickets.** Six40px rows fill a240px viewport. Live0/1/6/7/30 cases retain all records. The30th row opens by keyboard; Back restores scrollTop960 and exact row focus. Draft retention also passes.
3. **Resolved P2 — distorted close target.** Shared close button is32×32 with16×16 glyph, measured center offset0 and no long-title shrink. Long headings clamp to two lines while retaining the full accessible name/title.
4. **Resolved P2 — backdrop strip.** The original overlay inherited16px PageShell spacing. Shared body Teleport now measures origin0,0, margin0 and full viewport height. The light full comparison shows the removed strip.
5. **Resolved P2 — hidden feedback/focus loss.** The requested second behavior pass found success at y2283 outside visible body130–719 and focus on BODY. D2 moves existing feedback/recovery above pinned actions. Repeating the save shows success y707–727, in-dialog focus, unchanged frame and bodyScroll0. `after-save-feedback-light.png` and `modal-D2-review.json` prove the correction, including delayed completion while inspecting a ticket.
6. **Resolved integration blocker.** Full signoff found recursive updates in Connections detach confirmation with Teleport stubbing. D3 removes reactive render/lifecycle coupling in UiDialog, following the existing side-panel pattern. The unchanged failing consumer and mixed-overlay tests pass independently, and the complete signoff rerun passes. The two-file repair changes lifecycle handling, with no visual-token/layout change. Final D3 source and built-bundle captures were opened; geometry remains unchanged.

## Required fidelity surfaces

- **Typography:** Existing family,16px heading and14/12px content hierarchy retained. Weights and wrapping match. List titles truncate within the fixed row; details expose full content.
- **Spacing/layout:** Existing672px width, token spacing/radii and surface hierarchy retained. Approved differences are fixed outer height,40px rows, Back separated from dismissal, bounded heading and full backdrop. Persistent controls remain visible in short desktop viewports.
- **Colors/tokens:** Existing light/dark surfaces, borders, status colors, focus ring and shadows remain unchanged; no separate Work palette.
- **Images/assets:** Existing app mark and shared icon library retained. No generated decoration, replacement logo or custom icon drawing. Close is sharp and centered.
- **Copy/content:** Description, Tickets, Additional input and More details retain their hierarchy. Edit description and intent-specific save labels clarify effects. Status remains agent-managed; no status editing or drag/drop.

## Behavior and proof

Browser: task/ticket/Back, draft retention, final-row keyboard access, More details, long history, save feedback, both themes, short/large desktop frames, map default task, node selection without popup and explicit side inspection. Automated proof covers loading/errors, invalid URL identity, project/async scope, disclosures, focus trap/return, dirty/in-flight guards, conflicts/idempotent retry, hidden-pane focus, and mixed overlay cleanup.

The historical QA console buffer contains an initial401 before authenticated fixture browsing and an existing Vue Flow deprecation warning. After preserving those observations, the buffer was cleared for final built-bundle smoke. Task/ticket/Back succeeded with no console errors. Built asset `index-Bz5kSfwx.js` matches the verified bundle; task/ticket both measure672×752 and backdrop origin0,0 covers the viewport. `final-built-task.png` records the final light-theme popup. Final keyboard proof confirms Shift+Tab/Tab wrap inside the dialog; Back preserves More details and exact body scroll153; Escape returns focus to the original board card and unlocks page scrolling.

Official sources and observed second-pass results are in `behavior-research.md`. The six-row cap and dimensions are StackOS decisions. Vendor guidance informs behavior without replacing the selected design.

## Completed checklist

- D3 independent review approved candidate `facaba3f908ed954f3b8c3d8eef96887346d19947563e1f5eebb99ba7661d804`.
- Frozen integrated candidate `09c506038cc3c8f755e9887659694fef3c4f5942a5042f61131f3ca12527f75d` passed `make signoff`: lint, types, Python suite, UI suite and production build.
- All89 built files copied and hash-verified against the tested output; final built UI verified at http://127.0.0.1:5185/projects/1/tasks . Installed app/runtime was not upgraded.

No further visual P0/P1/P2 findings remain. No optional redesign is required.
