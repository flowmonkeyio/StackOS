# Backups — selected option 3

final result: passed

No actionable P0, P1 or P2 finding remains. This report covers the selected visual and the real StackOS components, with synthetic data and native-dialog results in the browser preview.

## Comparison target

- Source: [selected-concept.png](selected-concept.png), the third displayed ideation image selected by the operator's `3`; SHA-256 `a312c681dce933bd4d47a553b077fa1948afc37f1695a2a33031335b538f3f5d`.
- Source raster: 1586 × 992, normalized with contain scaling into 1280 × 800. The raster does not establish a CSS pixel density.
- Implementation: [configured-1280-dark.png](../evidence/browser/configured-1280-dark.png), actual App, BackupPanel and shared primitives. CSS viewport and screenshot are 1280 × 800 at 1× density.
- State: configured backups, dark theme, last backup on October 28 at 09:42, 48-hour schedule, retention 17, folder `/Volumes/Backup drive/StackOS`; menu closed.
- Full combined input: [comparison-full.png](../evidence/browser/comparison-full.png), 2608 × 850. Source and implementation are shown together at matching normalized dimensions.
- Focused combined input: [comparison-content.png](../evidence/browser/comparison-content.png), 1968 × 650. Used for readable typography, row rhythm and copy. Its far-right control edges are cropped by the comparison frame; the full comparison and original implementation capture establish those edges. This is a comparison-artifact crop, not application overflow.
- Final integrated source/bundle candidate: `72935f7792c9a4c8f43db9991a831cec114c2592e26c697ca418fbce5ebc2ad7`.

Both combined images were opened and inspected by root and the independent reviewer. The browser was restored to the configured preview after capture; the final console check reported no errors.

## Fidelity findings

| Surface | Disposition and evidence |
| --- | --- |
| Fonts and typography | Passed. Existing StackOS Inter family, native weights, line heights and compact button sizes preserve the hierarchy: page title, status, recovery section, file rows and quiet metadata. Generated-image text is softer and somewhat heavier; retaining the real design-system typography is intentional. Copy wraps cleanly at 320/390 and the 639/640 breakpoint. |
| Spacing and layout | Passed. Header Restore, compact tinted status band, two file rows, device-loss reminder and separated folder/Manage footer match the selected composition. Native sidebar and page shell remain intact. The status, export and footer controls keep their anchors during asynchronous actions. Settings/history use existing side panels. |
| Colors and tokens | Passed. Existing dark surfaces, muted text, border and lime accent tokens provide the selected hierarchy; light theme also inspected. The raster's slight background texture/lighting is not recreated as new gradients or product styling. State text and icons accompany color. |
| Image and icon quality | Passed. Existing StackOS logo asset and established icon library render sharply. The target contains no new photography, custom illustrations or asset requiring generation. No substitute logo, placeholder image or handcrafted decorative artwork was introduced. |
| Copy and content | Passed. Restore backup, Back up now, Export backup, Export key and Manage match the selected roles. Both required recovery files and the device-loss reminder are explicit. Native locale timestamps replace mock prose dates. Fresh, paused, browser-only, legacy-shell and initial-error states use truthful copy. |
| Interaction and accessibility | Passed within tested scope. Keyboard menu/side-panel use, Escape return focus, settings-save focus, no focus stealing, accessible loading and retained controls were exercised. No settings write occurs on mount or opening/closing Manage panels. |

## Review and repair history

The earlier visual composition was superseded by the selected image. Prior technical behavior proof remains reusable only where covered implementation is unchanged.

| Finding | Repair | Post-fix evidence |
| --- | --- | --- |
| P2: spinner and feedback changed button size or displaced controls | Shared UiButton keeps its footprint; backup feedback occupies a stable region; actions keep their nodes | Focused regressions and the final action geometry summary |
| P2: narrow-window outer scrollbar shifted controls by 11px after feedback | Reserve scrollbar gutters at the document and main scroll-region owners | Historical failed summary retained; 27 action and 9 initial-load scenarios subsequently pass with zero recorded retained-control shifts |
| P2: dropdown Escape lost trigger focus | Shared dropdown restores trigger focus; outside dismissal preserves the user's new focus | Independent reproductions, full UI suite and current menu proof |
| P2: settings Save became disabled while focused | Keep focus on an enabled settings control after successful save; preserve no-steal behavior | Focused tests and browser settings-save receipt; exactly one changed-field write |
| P3: initial failure still said Checking | Show a refresh instruction when the read has failed | Regression and initial-error capture |
| P2: downward Manage menu caused a 2px document scroll at 390px | Use the existing top-end placement | Twelve current menu cases: Escape/settings/history/folder at 1280/390/320; no document or main scrolling, no retained-control shift, focus returns to Manage |

The 27 action and 9 initial-load cases ran after the gutter/focus repairs on the prior integrated candidate. The final change only moves the open menu upward; current full UI/build, all 12 menu cases and final configured screenshot were captured afterward. The independent reviewer accepted reuse of the unaffected closed-menu geometry proof. No unsupported 20px main-scroll claim is made.

## Responsive and state coverage

Inspected configured dark/light desktop; fresh, paused, browser-only, legacy-shell and initial-error desktop; configured 320/390/639/640 widths; settings at 320/390/1280; and no-project app navigation. Browser proof covers setup success/partial/cancel, backup, archive/key export, restore, refresh and failure feedback. The final screenshot and combined comparison preserve the selected visual hierarchy.

No open design question or P3 polish item blocks handoff. Browser native dialogs are synthetic; real Electron and CLI recovery proof is recorded separately. OS-native chooser appearance and an installed-app upgrade are not claimed.

## Implementation checklist

- [x] Resolve the exact third displayed image.
- [x] Use existing StackOS typography, tokens, primitives and assets.
- [x] Capture and inspect a combined source/implementation comparison.
- [x] Repair demonstrated focus and layout movement.
- [x] Inspect responsive, loading, error and alternate-theme states.
- [x] Pass full UI tests, typecheck/build and independent source/design review.
- [x] Leave the verified local preview available.

The preview-root copy is `/tmp/stackos-workflow-718/browser-qa/design-qa.md`. This scoped durable copy preserves the repository's unrelated, pre-existing root `design-qa.md`.
