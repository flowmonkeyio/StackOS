# Independent source-admission review R2

**Verdict: REJECT. Do not proceed to fresh bounded tests.**

I did not implement this candidate. I read the original `requirements.md` first, then frozen source, the accepted baseline, and applicable repository/product/orchestrator/reviewer guidance. This is a static logic/security review. I did not run candidate code, the parser, tests, hosts, models or daemon operations, and did not read credentials or session files. I did not consult designs, implementation-delta narratives, prior review reports or held implementations. The small runtime provenance record was read, but the byte-equivalence conclusions below were independently checked against the archives and installed binary rather than accepted from that record.

## Blocking finding: a directly written credential default bypasses admission

Location: frozen `plugins/engineering/scripts/source_classifier.mjs`, particularly lines 52–64, 173–175, 195 and 207–210. The matching fallback is frozen `plugins/engineering/scripts/source_admission.py:167–184` and unchanged `stackos/artifacts/redaction.py:26–31`.

Minimal synthetic source, suitable for a selected `.js` file:

```javascript
function f({["password"]: value = "synthetic"}) {}
```

Expected: hold this directly written literal default for a credential-labelled property, or conservatively hold the unsupported pattern. Actual static control flow admits it when the pinned parser/runtime are available and the inherited policy does not already contain the synthetic marker.

The complete Acorn parse does not solve the classifier omission:

1. The parameter is an `ObjectPattern`. Its computed property has the statically known string key `password`, and its value is an `AssignmentPattern` whose left identifier is `value`.
2. `pattern()` follows the property's value and binds `value` as ambiguous (lines 58 and 63). Only a sensitive **leaf identifier** enters `patternBindings` (line 56). `value` is not sensitive, so the later ambiguous-origin check never runs for it.
3. The `AssignmentPattern` check only rejects when its left identifier is sensitive (line 195). Again, `value` is not sensitive.
4. The property check explicitly excludes every property whose value is an `AssignmentPattern` (line 207). Consequently it never checks this known sensitive property key or its literal default.
5. The collected strings are individually `password` and `synthetic`, neither of which matches a labelled secret assignment on its own. No label range is admitted; the projection is the original text. The text filter requires an optional closing quote followed by whitespace and `:` or `=`. Here the closing quote is followed by `]`, so the `password` property is not recognized. `value = "synthetic"` is not credential-labelled. Thus both literal scanning and projected-text scanning pass.

The equivalent variable declaration also follows the missed path:

```javascript
const {["password"]: value = "synthetic"} = config;
```

This is not an arbitrary computed unknown secret or a demand for whole-program runtime-secrecy inference. The credential key, literal fallback and rename are explicitly present in the same syntax node. Checking the node or holding it requires no candidate evaluation. It violates the required literal/pattern handling and fail-closed unsupported-source rule. The comment at classifier lines 171–172 claiming defaults conservatively remain held is broader than the implementation.

The inherited protected-value policy correctly rejects values it already knows, but the original requirements separately require source credential-literal checks. Requiring this synthetic literal to have been discovered elsewhere would remove that independent guarantee. Missing runtime assets can hold every input; they do not repair the classifier's logic when its declared runtime is available.

The common admission integration makes this consequential in every required lane: `controlled_workspace.py` routes `admit_source()` to this classifier and calls it from broker reads and edits; `delivery.py` calls it from `_actual_code()` for complete before/after source and from `_outcome_snapshot()` for snapshot source. Therefore the defect is not isolated to an unused helper.

## Evidence identity and complete baseline delta

- Candidate branch: `codex/source-admission-checked-20261105`.
- `candidate-r2.patch` SHA256 verified: `170926e50d86b7e42591accc74609f19c46f25fd2229b21a2b9f71c2e2636a5e`.
- `implementation-manifest-r2.json` SHA256 verified: `ea05fd4061eb64900108189e758912019dacaa78dab474b0c3bb6ed5409339e4`.
- All 16 entries in `review-source-r2/manifest.json` match the frozen files.
- All 2,244 candidate regular files and all 2,237 baseline regular files match their respective full manifests. Each inventory also contains the same `website/dist` symlink, for totals of 2,245 and 2,238 entries. Its link text was checked without following its unavailable target.
- The manifest delta is exactly three changed files (`controlled_workspace.py`, `delivery.py`, `delivery_recovery.py`) and seven added files (the admission document, adapter, classifier and four Acorn vendor files), with no removals. A complete directory comparison also found an unmanifested `.ruff_cache` directory. I notified the parent and did not inspect it; source conclusions use frozen files only.
- Archived Acorn `package/dist/acorn.mjs` hashes to `b4c8c70200e72bae33cf1085e0ecb1e792c1b6924ed50cab817caf14f51bb249`, matching the frozen vendor bytes and pin. The Bun archive hashes to `c240d7f0ccb90c263a849fcdb09b7ee62630d921348ed6aafda86b9d577bc510`. Its contained binary and the installed pinned binary both hash to `0a2237cdcfa0d16ef501d9acca9f0026309ef348bea7393a64e8d1e3609565cc`. These were read/hash operations only, not runtime execution or fresh upstream authenticity checks.

## Other bounded review conclusions

The patch uses Acorn's complete-program parse rather than a handwritten lexer or candidate evaluation. It parses only stdin data, pins classifier/parser/runtime bytes, suppresses parser diagnostics and applies input/result/traversal/deadline bounds. These are source observations; runtime flags and actual process behavior remain untested.

The accepted `OutputPolicy`, redaction, CLI output adapters and reviewer host adapter are byte-identical to baseline. Original source and decoded parser output remain subject to the inherited policy. Existing private-pipe transport and final-byte checks remain in the traced paths. The private label projection does not rewrite disclosed source. This preservation does not compensate for the blocking literal-classification gap.

Complete before/after capture, broker operations and snapshots use the same admission entry point. Source prompt accommodation is limited to controller-created captures, revalidates those captures, checks surrounding prose strictly and checks the complete prompt against the inherited policy. Durable prompt records retain sanitized text plus a digest. The reviewer prompt still contains requirements and actual source, without the plan or executor report.

Both worker and reviewer snapshot routines execute in `_source_preflight` before `runPlan.create`. Snapshot identity/digest checks and dispatch-time capture remain present; source metadata is frozen, and resume without matching metadata holds before lifecycle mutation. Existing per-task/project model selection, separate role sessions, review-before-test gate and bounded recovery/history machinery are preserved by the inspected delta. I found no additional decisive issue in those bounded paths; none of these statements certifies end-to-end execution.

The explicit compatibility limits are material and accurately stated: only JavaScript has the complete-parser lane; selected Python, TS/TSX, embedded HTML/SVG, shell, Makefile/CMake and unknown formats hold. The exact installed Bun path/version is required. This candidate cannot be called generic StackOS or MYB readiness, and this review does not unblock MYB, upgrade an older run, authorize installation, or prove host/model outcomes.

Stop this implementation under the supplied bounded-attempt rule. No fresh test session or further repair attempt is approved by this rejection. The concrete admission defect remains unresolved; no source files were edited during review.
