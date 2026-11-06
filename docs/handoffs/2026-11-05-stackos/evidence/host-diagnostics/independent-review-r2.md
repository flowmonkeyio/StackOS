# Independent host diagnostics review — revision 2

Verdict: **APPROVE for the fresh Luna offline synthetic-fixture gate only.** No blocking logic or security finding was identified in the frozen four-file candidate. This is static review approval, not test acceptance, runtime verification, installation approval, or evidence of successful model/business delivery. The earlier independent rejection remains history; this verdict does not erase it.

## Scope and method

Reviewed `requirements.md`, the actual `host-diagnostics-candidate` source, and `candidate-r2.patch` against the accepted baseline manifest. No executor report was provided or read. Read the candidate root and plugin AGENTS guidance, product contract, documentation router, checked-output contract, and the engineering delivery/recovery section of `docs/workflow-templates.md` (287–358). Source was inspected without importing or executing candidate modules. No tests, host/model calls, daemon/business calls, auth/session reads, installation, or source edits were performed. This report is the only intentional file write.

## Findings and evidence

1. **The new diagnostic fields are finite and typed.** `plugins/engineering/scripts/host_diagnostics.py:3` defines fixed stages, host codes, HTTP variants, denial codes, RPC methods and standard JSON-RPC codes. `error_metadata` at line 31 copies no raw message, details, exception representation, or arbitrary field value. Unknown/malformed codes become `unknown`; invalid HTTP statuses become `null`; only exact integers from 100 through 599 survive; only exact booleans survive in `will_retry`. Boolean-as-integer status values cannot pass. RPC method/code projection at `reviewer_isolation.py:271` uses the explicit finite sets and maps other values to `unknown`/`null`.

2. **Decoded admission and final-byte admission remain on the actual path.** `reviewer_isolation.py:200` decodes the complete host record with the existing policy before diagnostic projection. Notifications and terminal errors emit through the same `emit` function (`:127`, `:223`); rejected RPC responses also use it (`:271`). `emit` uses `policy().encoded_json(..., suffix="\n")` and the lifetime `EVENT_STREAM`. The unchanged boundary at `stackos/artifacts/checked_output.py:114`, `:164`, `:169`, and `:203` checks structured input, encoded bytes, decoded JSON and cumulative stream bytes before writes; failure poisons the stream. Thus an omitted raw field containing a protected value still rejects at decoded admission, and a protected value formed by output encoding/framing is not exempted. There is no new output sink or unchecked fallback.

3. **All requested failure paths have bounded context.** `reviewer_isolation.py:213–231` covers host error notifications and terminal-turn errors, with distinct fixed stages. `:267–284` preserves rejected/missing-result RPC handling and `refusal_state="unclassified_host_error"`. Main-controller stage assignments cover preparation/admission, observation admission/dispatch/result, turn start/receive, tool admission/dispatch/response, agent output, completion and thread close (`:430`, `:573`, `:710–719`, `:821–984`). `emit_failure` at `:1038` admits only a stage in `STAGES`, otherwise `unknown`; its generic exception caller at `:1067` retains a fixed message. The stage is last-activity context, not root-cause proof, as documented in `docs/engineering-host-diagnostics.md:15`.

4. **Observation acceptance is preserved for diagnostic-only notifications.** The real `--observe` branch at `reviewer_isolation.py:709–739` still requires host admission and the existing confined command result. `delivery.py:834–861` now bounds the whole retained stream, requires dictionary rows, requires one final `observation.completed` row, and accepts preceding rows only through the exact-key schema at `host_diagnostics.py:51–71`. Only non-denial notification telemetry passes; a duplicate completion, terminal diagnostic, RPC error, extra key, malformed value, or other event holds the observation. `_recovery.denied` is checked before acceptance. Exit/timeout checks and extraction of the actual completion receipt remain; exact expected stdout and program exit checks still occur at `delivery.py:900–910`. Telemetry is retained separately from command stdout. This prevents newly emitted benign telemetry from turning a previously valid command receipt into a row-count failure without treating telemetry as completion evidence.

5. **Denial, unknown authority, completion and recovery remain separately owned.** The notification/terminal denial set at `reviewer_isolation.py:233–242` is exactly the baseline set, and still emits a typed refusal and raises `HostRefusal`. RPC rejection remains unclassified; no numeric code or retry flag grants authority. `_execute` retains typed refusal markers before sanitization (`delivery.py:740–750`). Normal-role consumers still identify completion, thread identity, model identity and isolation evidence by their existing event types and checks (`:1049–1134`), and retain partial failure history (`:1135–1169`). The unchanged recovery owner checks denial first (`delivery_recovery.py:1137`), keeps `host.error` unknown (`:1202–1245`), and requires clear authority plus settled effects and a valid designer decision before recovery (`:583–605`). Observation effect safety still requires verified read-only/no-network confinement and cleared custody (`:1355–1375`). No added diagnostic field is used by these decisions. Retry allowances and semantic recurrence history remain in the unchanged recovery module; the change introduces no retry execution or budget reset.

6. **Runtime loading and package inclusion have an existing owner.** `delivery.py:85–93` loads the new module from its own fixed sibling path, as with its existing helper modules. The helper imports its shipped sibling at `reviewer_isolation.py:20`, and controller helper commands still point to that sibling script for observations, workers and reviewers (`delivery.py:821`, `:968`, `:1001`). `stackos/cli/engineering_delivery.py:7–17` selects the fixed bundled controller or source controller. `pyproject.toml:80–82` recursively includes `plugins` in the wheel, and `:84–88` includes it in the source distribution. No packaging change is needed to include the added sibling. This is a source-level packaging conclusion, not a built-package or installed-runtime test.

## Source integrity

All four actual candidate SHA-256 values match `review-manifest-r2.json`:

| Candidate path | SHA-256 |
| --- | --- |
| `plugins/engineering/scripts/reviewer_isolation.py` | `92a92f5ce77b0af2ea27fe4f5efd848b121ec28b1e389297f8064289007c9c28` |
| `plugins/engineering/scripts/delivery.py` | `218e5542b09241409826a77f6b5590087a82839690d981626f041ca5200caf85` |
| `plugins/engineering/scripts/host_diagnostics.py` | `247cd09198fba291c742adb54f0a2d4786f3342c91192c74a7ed60f6d303bed6` |
| `docs/engineering-host-diagnostics.md` | `021a0b6acd1371bb6b3e34f0df246a09582d3437c0f82a0f78e4addcdc42a1c5` |

The full baseline has 2,238 entries: 2,237 regular-file hashes and one symlink. All baseline regular files except the two intentionally changed scripts match; no baseline regular file is missing. The `website/dist` symlink itself and its target text match the baseline (its target was not followed). Two source additions are exactly the new diagnostic module and documentation. Three additional unmanifested `.ruff_cache` files were present; these are outside the source delta and were not executed or changed by this review.

Reverse-applying every patch hunk **in memory** to actual candidate text reproduced baseline hashes for both changed files, and empty prior contents for both additions. No checkout was changed by that comparison:

| Reconstructed baseline file | SHA-256 |
| --- | --- |
| `reviewer_isolation.py` | `2a47587e81ae6231cd7ceeb3f81ea27eb225c58101837d592aabcdfc1a2d28f2` |
| `delivery.py` | `f8bfcb9396fc594fb71438d5089c8a2b755154f718e93a3acfccfe4149f4ed5f` |

Evidence SHA-256 values:

- `requirements.md`: `a6a556ce888ee2c7f42a2324f6d9bf17efa641e49e45bedd443d22c1ee1fc240`
- `candidate-r2.patch`: `2804e0c731abf5ead6e655907a2a2d1ff5d7f1dfc30614a42fd31ca3838585f4`
- `review-manifest-r2.json`: `639222c7deb8468e8ea1ae36c72f51e39fb61662a97cc18ce92f6ce5127ca540`
- `baseline-manifest.json`: `e4b78b1af78f2d74970d87a80af6e41f588e86c730e8f97ae4dd070cb37e8e48`

## Review limits and next gate

Fresh Luna offline fixtures must still prove actual event encoding and sink behavior, omission of raw/arbitrary values, malformed/unknown values, exact boolean and bounded-integer handling, protected-value rejection before projection and after encoding, typed-denial and unknown-authority preservation, observation acceptance/holds, normal-role consumers, unchanged recovery decisions/budgets, and candidate module provenance. No fixture was authored or run during this review. No end-to-end host result or cause of an earlier host failure is established.

Audit limitation: an initial filename inventory was broader than the candidate. Later, one incorrectly piped TPF navigation command consumed stdin and caused the trailing text search to run at workspace scope, returning incidental matching snippets from held candidates/evidence. That command was stopped as a navigation route and the parent was informed. Those incidental results were not used as review evidence; no held file was opened separately or modified. Subsequent reads were explicitly candidate-scoped. TPF also emitted local learning-permission warnings; successful source reads and integrity outputs above were still returned. No automatic approval rejection blocked the review.
