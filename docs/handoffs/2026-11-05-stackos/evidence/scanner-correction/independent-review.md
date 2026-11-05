# Independent logic review — reject

Reviewed the original `requirements.md` first, the frozen `review-source/` candidate, its actual incremental patch and baseline, applicable repository instructions/product contract, and source consumers. No implementer reports, plans, prior reviews, execution narratives, or scratch status logs were read. No tests, candidate code, models, business runs, installation operations, or MYB operations were executed. Only this review artifact was intentionally written.

All seven frozen file hashes match `review-source/manifest.json` and the corresponding current `build-candidate` files. Compared with `before/`, changes are limited to `controlled_workspace.py`, `delivery.py`, `delivery_recovery.py`, and `docs/workflow-templates.md`; reviewer isolation, canonical redaction, and the CLI adapter are byte-identical. Findings below are source-derived counterexamples, not executed reproductions. Example values are invented placeholders.

## Blocking findings

### 1. P1 — Regex literal contents can receive executable-reference exemptions

`plugins/engineering/scripts/controlled_workspace.py:83–93`, with the exemption applied at `:158–180`.

Consider this valid JavaScript source, with a line terminator after `break`:

```javascript
while (flag) { break
/{token: opaqueCredential}x/; }
```

JavaScript inserts a semicolon after the restricted `break` statement; the next statement contains a regular-expression literal. The scanner discards the line terminator, treats `break` as an ordinary operand because it is absent from the keyword set, and classifies the opening slash as division. It consequently exposes `{`, `token`, `:`, and `opaqueCredential` as executable tokens. The trailing `x` likewise makes the closing slash look like division, so the scan does not fail. The labelled-value match has a permitted preceding `{`, its RHS is a reference ending before `}`, and it is masked. No remaining detector rejects the source.

Thus literal credential-labelled text inside a regex, which canonical redaction would reject, is admitted and subsequently disclosed unchanged. The documented opaque-literal and fail-closed properties are violated. This affects snapshots, broker access, actual-code capture, and exempt source blocks in prompts. Slash classification must retain enough lexical context or reject ambiguous statement boundaries; adding only this one keyword would not establish the claimed general guarantee.

### 2. P1 — A newline is treated as proof that the RHS reference is complete

`plugins/engineering/scripts/controlled_workspace.py:34–35`, `:171–180`.

```javascript
const token = String
("opaqueCredential");
```

This is one call expression, `String("opaqueCredential")`; JavaScript does not insert a semicolon before its argument list. `_JS_SAFE_RHS` nevertheless matches `String`, backtracking its trailing whitespace so that the newline satisfies the terminal lookahead. The token-end check only proves the end of that identifier, not the end of the expression. The scanner masks `token = String`, and the remaining unlabelled string literal is not rejected.

The supposedly complete reference exemption therefore admits a credential-bearing call outside its declared subset. Newline continuations with operators, indexing, or tagged templates have the same underlying problem. This is a local lexical-admission failure, not a request for arbitrary runtime data-flow analysis: the implementation must not certify a prefix of an unclassified RHS as a complete safe expression.

### 3. P1 — The actual broker does not receive the parent's known inherited credentials

`plugins/engineering/scripts/controlled_workspace.py:744` and `:602`; supporting consumer chain: `delivery.py:109–125`, `:623–658`, and `reviewer_isolation.py:450–465`.

The parent discovers inherited credential values, then `_execute` launches the helper with only `_WORKER_ENV_KEYS`. The helper reconstructs `known_secrets` from that already scrubbed environment. For a normal parent `API_TOKEN` variable, the original value is therefore absent from the `Workspace` validator. The helper also has different name and minimum-length rules from the parent.

For example, if the parent's invented `API_TOKEN` value is `opaqueCredential`, a broker edit creating `src/new.js` containing `const token = opaqueCredential;` now passes source admission and is written to both repository and view. A subsequent broker read also accepts and returns it. The parent would reject the exact same bytes with its known-secret tuple. Before this change, the generic labelled-value filter rejected this particular edit even with the empty tuple; the new exemption exposes the consumer mismatch.

A later parent snapshot or actual-code rejection is too late to preserve rejection at the write/read boundary. Trusted broker validation needs the same complete protected-value policy as parent admission, conveyed without giving credentials to the model or its command environment.

## Other invariant assessment

- **New-run ordering:** `delivery.py:1307–1313` performs both worker and reviewer `_outcome_snapshot` calls and rechecks the clean base before `runPlan.create` and `runPlan.start`. A failure of those checks precedes durable run creation. The existing-run branch does not recreate a run or reset policy/models.
- **Repeated admission:** `delivery_recovery.py:216` recaptures worker snapshots, `:877` recaptures reviewer snapshots, and `delivery.py:1443` recaptures the final outcome. Broker reads and writes call the shared scanner; fresh actual-code prompt matching is enforced when the strict filter would change source. The three findings prevent these paths from being substantively equivalent and safe.
- **Byte handling:** snapshot copying uses original bytes and hashes; broker source reads return decoded original UTF-8 bytes rather than redacted replacements; `_actual_code` checks complete HEAD/current sources and includes current full-file bytes; `_safe_role_prompt` preserves its exactly matched source block while checking surrounding prose. `_execute` writes the original prompt bytes to stdin. General retained prompt logs are sanitized and gain an original-prompt hash. These are source observations, not live transport proof.
- **Canonical redaction:** the general redaction module is unchanged. Ordinary quoted credential assignments remain rejected by the new path when they are correctly recognized as literals, and known-secret checks precede exemptions when supplied. That does not cure the lexical or broker-policy failures above.
- **Isolation and provenance:** selection exclusions, component-wise no-follow snapshot opens, manifest digests, isolated reviewer hosts, source/outcome tools, and role write limits remain in place. Initial reviewer construction still uses original requirements and actual code without the implementer's plan/report. The patch does not change model selection, retry budgets, same-run ownership checks, or historical-event preservation.
- **Additional pre-existing admission gap:** preflight invokes snapshot capture but does not instantiate the worker's manifest/path validator. A clean tracked POSIX filename such as `src/a\\b.js` passes snapshot path checks but is rejected by `Workspace._path` (`controlled_workspace.py:541–554`) after run creation. Snapshot/broker size and binary-content limits also differ. This is not introduced by the scanner patch, but the strong claim that preflight establishes all downstream source readability remains unproven; it should be explicitly reconciled with the intended scope of “same substantive snapshot admission checks.”

## Verification limits and disposition

Reject. Do not proceed to test writing/execution under the stated approval gate until the blocking logic is corrected and independently approved.

No live proof was performed for the unchanged MYB source fixture, packaged application, actual model prompt transport, host confinement, retry recovery, or durable business-run history. I did not read or modify the MYB fixture. Source reasoning and file-identity checks must not be described as those live proofs. Existing dirty work was left untouched.

No requested source read or review-artifact write was denied. The required `tpf` wrapper emitted `tpf: learn warning: Operation not permitted (os error 1)` while source-inspection commands themselves succeeded; no escalation or retry of that ancillary learning write was attempted. No remaining access blocker prevents this review verdict.
