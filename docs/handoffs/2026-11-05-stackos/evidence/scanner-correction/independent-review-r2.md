# Independent source review — candidate r2

Verdict: **REJECT**. One blocking source-admission regression is sufficient to withhold logic approval. No tests were written or executed.

## Scope and provenance

Read the original `requirements.md` first, repository instructions/product guidance, the frozen candidate, its manifest, `candidate-r2.patch`, and the corresponding baseline bytes. Did not read implementer reports, plans, earlier reviews, parent history, or status narratives. No implementation/test edits, models, business runs, installations, MYB mutations, or live StackOS calls were performed. The optional application fixture was not read.

SHA256 verification: all seven entries in `review-source-r2/manifest.json` match both the frozen files and the corresponding actual files in `build-candidate`. The baseline comparison confirms that `stackos/artifacts/redaction.py` and `stackos/cli/engineering_delivery.py` are unchanged. The review applies to those verified bytes.

## Blocking finding

**P1 — HTML-like JavaScript comments can bypass literal credential rejection.**

Location: `plugins/engineering/scripts/controlled_workspace.py:39-139`, particularly the comment recognition at lines 55-65, generic tokenization at lines 121-124, and the exemption at lines 165-190.

Consider this synthetic complete `.js` source (the value below is invented):

```javascript
<!-- const token = ExampleSecret;
const harmless = 1;
```

In JavaScript script/browser grammar, `<!--` begins an HTML-like single-line comment. The text `token = ExampleSecret` is therefore a labelled literal in comment text, not an executable reference assignment. The existing strict redactor detects it.

The new lexer recognizes only `//` and `/*` comments. It tokenizes `<`, `!`, `-`, `-`, `const`, `token`, `=`, and `ExampleSecret` instead. Consequently, the sensitive match starts on a `word` token, its preceding token is `const`, the operator is `=`, and `_JS_SAFE_RHS` matches `ExampleSecret` followed by `;`. `source_is_safe` masks the entire sensitive match and accepts the file. A line-start `-->` HTML-like comment has the same class of problem.

This is a regression against the original requirement to preserve literal credential rejection and fail closed on syntax that cannot safely be classified. It does not depend on arbitrary runtime data-flow analysis. A value already present in `known_secrets` remains rejected, but an unknown labelled literal in these comments is exposed.

Impact crosses the intended shared boundaries: `_outcome_snapshot` (`delivery.py:412`), broker reads/edits (`controlled_workspace.py:609,751`), and `_actual_code` (`delivery.py:274`) all trust this classifier. `_safe_role_prompt` then intentionally permits the admitted actual-code block unchanged, so later general prompt validation does not repair this admission mistake.

Required correction: recognize these script comment forms as opaque, or conservatively withhold JavaScript expression exemptions when they occur. Keep the strict scan over their contents. Cover both open and close HTML-like comment forms in regression coverage only after corrected logic receives approval.

## Other bounded source findings

- The reference/RHS rule requires an explicit delimiter and a token boundary. Ordinary `null`, identifier/property references, the specified storage lookup forms, and exact runtime Bearer templates have paths to admission. Ordinary quoted credential assignments, ordinary comments/strings, known inherited values (including JSON representations), and remaining independent literal detectors retain rejection paths. This is source reasoning, not execution proof or an exhaustive lexer proof.
- Complete source is checked before snapshot bytes are written unchanged. Broker reads preserve admitted bytes and verify manifest digests. Actual-code construction checks complete current and HEAD versions, includes complete current changed files, disables external diff/textconv, and checks candidate digests again. Prompt exemptions are limited to a freshly reconstructed exact code block; the remainder receives strict prose validation. General redaction itself is unchanged.
- The controller sends the inherited protected-value policy in bounded private stdin rather than argv/environment/model text. The worker validates the envelope, closes both the Python stream and underlying descriptor before host construction, and sends only the extracted prompt into `turn/start`. Host commands have restricted environments, and retained controller output is sanitized. No live descriptor-inheritance or log-exposure proof was performed.
- New-run preflight calls the same `_outcome_snapshot` implementation for both worker and reviewer before `runPlan.create`/`runPlan.start` (`delivery.py:1328-1360`). Worker dispatch captures again (`delivery_recovery.py:209`), as does reviewer dispatch (`delivery_recovery.py:876`); helper manifest verification remains in place. This satisfies the requested shape of snapshot preflight, subject to the classifier blocker. It does not establish every runtime prerequisite.
- The incremental delta does not change provenance selection, role edit limits, model selection, recovery policy/budgets, or run ownership/history transitions. Reviewer prompt construction still receives original requirements and actual code, without executor plan/report or ordinary-role guidance, and retains outcome inspection tools and its separate host/snapshot. These are bounded source-reviewed observations, not installed-runtime certification.

## Limitations and next gate

Review stopped after a decisive security regression and bounded integration tracing. No tests, live fixture admission, worker launch, host conformance check, or installed-app proof occurred. No claim is made that every adversarial JavaScript grammar case has been exhausted. Logic approval remains withheld; the requested test-writing/execution stage must remain pending corrected-source approval.
