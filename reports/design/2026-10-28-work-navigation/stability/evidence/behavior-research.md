# Work popup behavior research and QA checklist

Prepared 2026-10-28 from official product and design-system sources. This is research and a proposed verification checklist, not evidence that the current implementation passes or contains a defect. The main agent owns the second-pass observations and acceptance.

## Approved StackOS scope

Preserve the selected Work concept and existing design system: a persistent centered task/ticket modal, task → ticket → Back navigation, description editing, additional input, and read-only lifecycle fields. Map node clicks remain local selection; explicit map Details uses the side inspector. View dependencies leaves the popup for the exact task's map.

The current `lg` width, one viewport-clamped fixed height, and maximum of six comfortable ticket rows before list scrolling are StackOS requirements. They are not dimensions prescribed by the external sources. This research selects no new pixel height, modal layout, tab system, or editing authority.

## Evidence and application

1. **Keep editing attached to its content.** Jira gives description prominence and separates secondary context. Atlassian's multiline inline-edit example keeps editing open on blur to avoid accidental save/discard. Keep the description action beside Description; preserve its draft while other controls are used. Renaming **Edit content** to **Edit description** is optional if only description is affected. [Jira work-item details](https://support.atlassian.com/jira-software-cloud/docs/update-a-work-items-details/), [Atlassian inline edit](https://atlassian.design/patterns/inline-edit/).

2. **Separate history from a new submission.** Linear explicitly distinguishes editing description from posting a comment and retains unsent comment drafts. Keep input history read-only and distinct from the composer. **Add input** submits the new input; a combined description/input submission needs a clearly broader label such as **Save changes**. Preserve text on failure and clear it only after confirmed success. This does not propose copying Linear's autosave model. [Linear comments and reactions](https://linear.app/docs/comment-on-issues).

3. **Make action priority and scope predictable.** Atlassian recommends one primary action per area and primary-right alignment in modal footers. Keep **Close / Add input** secondary/primary, **View dependencies** secondary near Tickets, and **Back to task** distinct from dismissal. A read-only ticket does not need an invented primary save action. [Atlassian button guidance](https://atlassian.design/components/button/usage?_bhlid=61aad6207a71374d743ce0c5f63073165c0bb7b3), [Atlassian modal guidance](https://atlassian.design/components/modal-dialog/usage).

4. **Scroll content while preserving orientation.** Material's published dialog guidance pins the title and actions during content scrolling. Atlassian discourages horizontal overflow and unnecessarily complex scrolling. Keep header/footer visible, the body scrollable, editors bounded, and every ticket reachable. Material 2 is cited for this established behavior only; no Material 3-specific claim is made. [Material 2 dialogs](https://m2.material.io/go/design-dialogs/), [Atlassian modal guidance](https://atlassian.design/components/modal-dialog/usage).

5. **Back restores the inspection context.** Linear treats list-scroll restoration after returning from an item as expected behavior. Atlassian recommends one modal at a time and cautions against nested/multi-step modal complexity. Apply this as a shallow task/ticket drill-in within the approved persistent frame, retaining task identity, reading position, and draft. It is not a recommendation for additional nested dialogs. [Linear navigation fix, February 5, 2026](https://linear.app/changelog/2026-02-05-linear-mcp-for-product-management), [Atlassian modal guidance](https://atlassian.design/components/modal-dialog/usage).

Guidance differs on disabled submit buttons: Atlassian discourages them, while Material 2 allows confirmation to remain disabled until a choice is made. An empty disabled **Add input** is not, by itself, a universally established defect. Check whether the next step is understandable. Retain StackOS's existing dirty/in-flight guards.

## Practical second-pass checks

- [ ] Read, edit, save/cancel, and input submission have accurate labels and distinct effects; lifecycle controls remain unavailable.
- [ ] Description, ticket list, input history/composer, and expanded details remain distinguishable. Navigation actions do not compete with the active submission action.
- [ ] **View dependencies** opens the correct task map. Map selection does not open the Work modal.
- [ ] Task → ticket → **Back to task** preserves the modal frame, task identity, draft, disclosures, body/list scroll, and originating row focus. **Close** returns to the original trigger.
- [ ] Cases with **0, 1, 6, 7, and 30 tickets** retain true counts and all rows; the last row is reachable. The long-content fixture's final marked ticket is reachable.
- [ ] Long titles, descriptions, history, and expanded details produce no horizontal overflow or clipped controls. Header/footer stay visible on normal and short desktop viewports.
- [ ] Editors cannot resize over surrounding controls. Wheel/trackpad and keyboard scrolling reach content without trapping the user in an unusable region.
- [ ] Loading, empty, error, edit, expanded, and save-feedback states preserve the approved outer geometry in both themes.
- [ ] Failed/conflicting submissions preserve recoverable input; success does not duplicate input. Dirty or in-flight navigation follows the existing guard contract.

Wrong task identity, misleading action effects, lost drafts, broken Back behavior, and unreachable rows/controls are defects **when reproduced**. Label polish, moving a secondary action, or removing a duplicate Close control are optional design changes unless a concrete usability failure is demonstrated.

## Second-pass observations

Main-agent StackOS browser pass against D2 `fb22c59208f84a14619e6d2a3c14d1e46ceeb5db307ce6d5ae2b98542e722db4`, synthetic project 4, 1280×800 in light/dark themes:

- Task/ticket frame stays 672×752. Ticket drill-in focuses the heading; Back restores the originating row and the 30-row list's scrollTop 960. Description draft survives the round trip.
- Live 0/1/6/7/30 cases retain all records. List height is 40px for one row, 240px for six or more; seventh and thirtieth rows remain reachable. The 30th row was opened by keyboard.
- More details expands inside the same frame. Long content and the short 1024×600 viewport keep header/footer inside the viewport; the 1440×1000 frame caps at 880px. No observed horizontal page overflow.
- Map entry selects the first available task. Node selection opens no popup; explicit Details retains the 640px side inspector.
- The second pass reproduced P2 D1-F1: description-only save with six long input-history entries placed success feedback at y2283, outside visible body y130–719; focus fell to BODY. D2 moves existing progress/error/success and conflict recovery above the fixed actions. Repeating the save leaves body scroll at 0, renders success at y707–727 and focuses its in-dialog feedback region. Independent review also verifies no focus theft into a hidden task pane if saving finishes during ticket inspection.
- Labels now state Edit description, Save description, Add input, Save description and input, or Retry save. The existing atomic combined submission, revision checks, and idempotent retry remain unchanged. Live combined submission cleared its composer only after success; focused controller tests cover conflicts and ambiguous outcomes without duplicate input.

The six-row cap intentionally introduces one bounded list scroll inside the reading body; this is the approved product requirement, not a vendor-prescribed pattern. Input history uses the main body scroll, editors are fixed-height, and secondary technical details remain disclosed. No new tab or navigation layer was introduced.

Evidence: `browser-observations.json`, `after-save-feedback-light.png`, the paired full/focused comparisons, and `modal-D2-review.json`. An additional integration failure in the shared dialog's Teleport-stub lifecycle is recorded separately in the delivery receipts; visual/behavior proof does not replace final repository signoff.

Additional authoritative checks used by the main agent: [WAI-ARIA modal dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/) for focus containment/return and [Carbon modal guidance](https://www.carbondesignsystem.com/building-blocks/core/components/modal/guidelines) for fixed chrome, body scrolling and visible action feedback. Existing StackOS tokens and component styling remain authoritative.
