# Independent parser security and logic review

Verdict: **REJECT**. The frozen candidate needs corrections and a new independent logic review before test writing or execution.

Reviewed against `evidence/parser-correction/requirements.md`, read first, and the repository's AGENTS/product contract. Review source is `evidence/parser-correction/review-source-complete/`, with the complete authored delta in `candidate-complete.patch` and the supplied `before/` baseline. No executor reports, implementation plans, or earlier reviews were consulted. At review entry, SHA256 verification of the actual `build-candidate` files matched **all 13 entries** of the frozen manifest. The parent subsequently reported changes beginning in the live candidate; this verdict applies only to the original frozen snapshot, not those later changes.

All file/line references below are relative to the frozen source root.

## Blocking findings

### R1 — Escaped inherited protected values can bypass admission

**High priority.** `plugins/engineering/scripts/source_admission.py:25–32`, consumed at lines 41–47.

The protected-value normalization does not implement JavaScript escape semantics completely and can also decode the same characters twice:

* The line-continuation substitution removes only backslash + LF or CRLF. JavaScript also permits backslash + CR, U+2028, and U+2029. With the synthetic protected value `ABCDEFGH`, a source string containing `ABCD`, a backslash followed by a literal CR, and `EFGH` has the protected value as its JavaScript string value. Neither the raw source nor `_decoded` contains contiguous `ABCDEFGH`: the generic substitution removes the backslash but leaves CR. An ordinary field such as `const note = ...` triggers no label-based detector, so a complete successful parse does not prevent disclosure. The vendored parser itself confirms the accepted behavior at `vendor/acorn/acorn.mjs:6064–6067` and `6103–6107`.
* The hex/Unicode substitution runs before the generic escape substitution. A synthetic protected value whose characters are `A`, backslash, `n`, `B123456` can be represented in a JavaScript string as `\x41\x5c\x6e\x42\x31\x32\x33\x34\x35\x36`. JavaScript produces the protected value, but `_decoded` first produces backslash+n and then turns it into a newline. Raw and JSON-escaped substring checks do not recover the missed value.

These are static data-flow counterexamples, not executed tests. The same decision is used by snapshots (`delivery.py:412`), broker reads/writes (`controlled_workspace.py:454,596`), and actual-code capture (`delivery.py:273`). A missed protected value can therefore enter model-readable source and subsequent prompt/retained data. Strict prose redaction does not repair these particular escaped forms.

Required correction: preserve exact inherited-value rejection for the complete supported escape representations, without sequentially reinterpreting newly decoded characters. Keep this check independent of executable-expression exemptions and retain strict behavior on non-JavaScript text. Do not revive the stopped handwritten syntax lexer.

### R2 — Regex literals can be mistaken for the null keyword

**High priority, conditional on host regex construction failure.** `plugins/engineering/scripts/source_admission.mjs:23`, propagated through lines 57–59 and `source_admission.py:98–105`.

`reference()` accepts any `Literal` whose `value === null`. Acorn explicitly uses that same representation for a syntactically valid regex literal when host `new RegExp(pattern, flags)` throws: `vendor/acorn/acorn.mjs:5795–5804`. Its AST preserves the regex identity in `node.regex` at lines 3007–3011, but the candidate does not inspect it.

Consequently, a secret-labelled assignment whose RHS is such a regex can receive the executable-reference range. The Python side then masks the secret field label, removing the detector that would reject the literal credential content. Host construction failure is possible independently of Acorn syntax validity; this branch must fail closed, not confer a null-keyword exemption. No concrete triggering regex was run against the pinned Bun binary, so this review does not claim a reproduced runtime exploit. The invalid AST discrimination and fail-open branch are directly present in the reviewed code.

Required correction: distinguish the actual null keyword from regex and other literal kinds using the parser's syntax identity. A regex must never become an executable-expression exemption, including when its runtime `value` is null.

## Other reviewed boundaries

The materially different architecture is appropriate: the helper uses complete Acorn `parse`, not the stopped handwritten lexer, and passes candidate source only as parser input. Script/module failures hold admission. Comment overlap prevents exemptions across comments, and ordinary string/regex literals receive no exemption except for R2. Input/output limits, timeout, integer offsets, UTF-16 boundary conversion, duplicate-key checks, and generic failure returns are present.

The Python admission path pins Acorn and Bun bytes, verifies the existing managed runtime, uses a private empty runtime configuration, clears ambient environment variables, disables install/env-file/addon/macro behavior, discards parser stderr, and does not evaluate candidate programs. This is source inspection of the launch contract, not a runtime hermeticity proof. The helper and controller remain trusted installed code; no installation was performed.

The controller's protected-value tuple reaches the trusted worker broker through a bounded private stdin envelope; the helper validates the envelope and closes the inherited descriptor before starting the model host (`delivery.py:628–643`; `reviewer_isolation.py:439–490`). Raw protected values are not added to model prompts, argv, or subprocess environments by that transport. R1 prevents approval of the policy's effective enforcement.

Actual-code capture validates complete before/after sources, disables external diff/textconv, and checks changed-path digests again. Source exemptions in reviewer/test prompts are limited to an exact freshly recaptured code block; surrounding prose remains strictly checked. Recovery dispatch and final receipt prompt records use redaction plus the original prompt digest. These routes retain the source bytes rather than rewriting them.

New-run preflight invokes both existing worker and reviewer snapshot paths before run creation/start (`delivery.py:1327–1357`); dispatch still materializes current snapshots. The preflight is source admission, not host/command readiness or an end-to-end success proof.

No change in this delta removes the four configurable role defaults, fresh reviewer boundary, actual-code review, thread/model identity checks, approval-before-test gate, test-writer implementation protection, or bounded recovery history. Reviewer prompts omit executor plans/reports. Existing host isolation, restricted outcome tools, manifest checks, and recovery reconciliation remain in place. `stackos/artifacts/redaction.py` and `pyproject.toml` were included in the review scope; the patch does not change their general redaction or asset-packaging rules. Vendored parser/license/provenance files were inspected separately from the authored patch.

## Limits and remaining proof

This review executed no parser, tests, candidate code, worker, business operation, installation, or migration. It changed only this review artifact. It did not inspect executor evidence or mutate run/stop/retry history, installed application state, migration owners, or unrelated dirty work. No actual credential values were read or printed.

Admission of the complete frozen MYB source, including `chrome-extension/bricklink-content.js`, has not been established by this static review. Runtime behavior, distribution inclusion, full-source admission, broker round trips, and end-to-end four-session delivery still require evidence after a corrected candidate receives independent logic approval. Existing runtime/provenance failures intentionally hold admission; additional whole-source blockers must be reported rather than excluded.
