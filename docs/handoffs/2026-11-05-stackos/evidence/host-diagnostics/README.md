# Bounded host diagnostics — accepted offline outcome

The authorized diagnostic implementation is complete and independently accepted
for offline behavior. It is **not installed**, and it does not establish live
delivery readiness or identify the earlier failed host's cause.

Candidate: `host-diagnostics-candidate`, branch
`codex/host-diagnostics-20261105`. The delta is against the preserved accepted
dirty checked-output baseline, not clean Git HEAD. `candidate-complete.patch`
contains only two changed scripts, the new shared diagnostic module and document,
and the new focused test module; `final-manifest.json` pins those five files.

The helper now retains finite error codes, controller activity stages, bounded
HTTP statuses and exact boolean retry flags through the existing checked-output
stream. Raw error strings/details are omitted. The observation consumer retains
valid diagnostic notifications while still requiring a final completion and
rejecting malformed, denial or RPC-error extras. Diagnostics do not authorize
retry or stand in for completion.

## Current evidence

- `independent-review-r2.md`: fresh Astra logic/security approval after the
  bounded correction to observation consumption and stages.
- `independent-outcome-assessment.md`: independent acceptance of the final
  actual fixture source and recorded results, with precise limits.
- `pytest-results.xml` and `test-output.txt`: the final four-suite execution,
  **87 passed, zero failures/errors/skips**, 7 dependency warnings; Ruff passed.
- `test-session-r2.md`: final replacement Luna fixture session. Earlier reports
  and `prior85-*` files are retained history; their incomplete coverage claims
  are superseded by this session and the final independent assessment.
- `bundled-import-proof.json`: candidate imports succeeded under packaged
  Python 3.12.16 with process launch blocked. This is not installed-entrypoint proof.
- `preservation-final.json`: accepted source, all three held candidates,
  installed delivery code and the MYB artifact remained unchanged at
  2026-11-05 08:02:37 UTC.

The first logic review rejected two observation integration issues; the corrected
candidate passed a fresh review. Test review then found a masked-error fixture,
missing negative completion assertion and vacuous budget comparison. Those
claims were not accepted. A fresh test session corrected them and preserved
the prior records. Production files stayed frozen throughout testing.

## Limits and remaining wider work

The fixture suite proves local event/sink/consumer behavior and sampled authority
and effect invariants. The ledger's retry → final → stop cap is tested separately;
receipt-to-budget integration remains source-reviewed, not demonstrated end to
end. The role-consumer fixture uses the reviewer path, not live ordinary workers.
Host and process boundaries are stubbed where stated in the assessment.

No real host was launched or replayed for this change; no daemon/business run,
installation, push, merge or deployment occurred. The earlier host failure still
has an unknown cause. The rejected source-admission candidate remains held after
two independent rejections. No further correction or business launch is implied
by this diagnostic acceptance. All assigned implementation, test and review
sessions have finished; no worker is stalled.
