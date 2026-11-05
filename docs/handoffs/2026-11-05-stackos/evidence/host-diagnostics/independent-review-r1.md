# Independent host diagnostics review — r1

**Verdict: REJECT — two blocking findings.** This is the first independent rejection of this approach. Test writing and execution remain blocked pending a corrected candidate and fresh independent approval.

I did not implement this candidate. I reviewed the original `requirements.md`, actual candidate source and exact delta, project/product/security guidance, the adapted delivery reviewer/orchestrator contracts, checked-output owners, and downstream delivery/recovery consumers. I did not read an executor report or runtime-probe summary, execute candidate code, run or write tests, invoke a host/model/daemon, read raw authentication/session files, or modify a candidate.

## Blocking findings

1. **P2 — additional diagnostic events change confined-observation acceptance and recovery disposition.** `plugins/engineering/scripts/reviewer_isolation.py:261` now emits `host.diagnostic` for every admitted non-null notification/terminal error, including a non-denial error that the existing receive loop returns and the RPC loop queues rather than treating as failure (`:293`, `:337`). The downstream confined-observation consumer still requires the entire helper stream to contain exactly one row at `plugins/engineering/scripts/delivery.py:836`; it does not count completion rows separately. Thus an admitted non-denial error notification followed by a successful `command/exec` response produces a diagnostic plus `observation.completed`, and the previously accepted observation is now held with `confined observation broker did not return a complete receipt`. This is a static control-flow finding, not a claim that an actual host emitted that sequence. The helper accepts that sequence, and adding telemetry changes its outcome. It violates the requirement to keep diagnostics contextual and preserve existing recovery decisions. Repair the consumer to retain bounded diagnostics while enforcing exactly one valid completion and preserving all existing refusal, custody, timeout, unknown-authority and malformed-event checks. Do not simply ignore arbitrary extra rows or use a retry flag to admit success.

2. **P2 — generic observation failures retain a stale controller stage.** `plugins/engineering/scripts/reviewer_isolation.py:619` sets `DIAGNOSTIC_STAGE` to `host_admission`, but the `--observe` path at `:755`–`:785` never advances it after successful host admission. The call at `:759` can fail during command dispatch, receive, or command-result validation (`outcome_command`, `:439`–`:472`), and the generic handler at `:1112` emits `review.failed` using the unchanged `host_admission` stage (`:1085`). That is not the last main-controller activity. Set an appropriate fixed stage before observation request admission/dispatch and before its result emission, without changing execution or recovery behavior. The documentation's caveat that a stage does not prove root cause does not make an earlier completed activity the current activity.

## Checks that passed by source inspection

- `error_metadata` (`reviewer_isolation.py:151`–`:168`) projects only finite host codes, integer HTTP statuses in 100–599, and exact booleans; unknown values become fixed `unknown`/`null`. It does not stringify arbitrary diagnostic inputs or copy messages/details/stacks. RPC method and code retention is finite (`:317`–`:325`).
- The host record is admitted through `policy().decode_json` before projection (`:245`); helper event emission uses `encoded_json` and the cumulative `CheckedStream` (`:147`–`:148`). `stackos/artifacts/checked_output.py:164`–`:177` and `:203`–`:216` retain decoded, final-byte, cumulative-framing and poisoned-stream checks. There is no unchecked error-output fallback (`reviewer_isolation.py:1091`–`:1095`).
- The new field names survive existing retention sanitization (`delivery.py:191`–`:245`) and role event capture (`:1034`, `:1084`). Typed denials remain separate host-refusal events (`reviewer_isolation.py:272`–`:292`). The explicit RPC unknown-authority marker remains. `delivery_recovery.py:1137`–`:1245` does not use diagnostic stage, host code, HTTP status or `will_retry` to authorize recovery; its existing denial/unknown checks remain intact.
- Model checks, permission profiles, private policy transfer, broker custody, no-launch proof, recovery budgets and append-only history have no source changes in this candidate delta.

## Candidate integrity and limits

Read-only SHA-256 comparison against all 2,238 baseline-manifest entries found only `plugins/engineering/scripts/reviewer_isolation.py` changed; no baseline entry is missing. The existing `website/dist` symlink target is unchanged. The new product file is `docs/engineering-host-diagnostics.md`; `.ruff_cache` files are also present as generated cache additions. Both review-manifest hashes match the actual files:

- helper: `9420990ffe30e711334d8ae47ae0d38f371db3854c4ae9f2407aa6da6146edf0`
- diagnostics doc: `48e39f90e183ee1d448eafb461092d48127ad1a807518002984ba42537b1d706`

No acceptance of runtime behavior, successful delivery, or retrospective cause identification is made. No permission/automatic-approval action was denied; the blocker is this independent logic review.
