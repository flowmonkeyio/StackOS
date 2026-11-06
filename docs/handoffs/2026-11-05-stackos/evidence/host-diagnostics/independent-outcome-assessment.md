# Independent offline host-diagnostics outcome assessment

Verdict: **APPROVE the bounded offline diagnostic outcome.** The frozen production source remains identical to the four files approved in `independent-review-r2.md`. The corrected fixtures and recorded machine results support diagnostic capture, checked-output admission, observation receipt handling, and the tested authority/effect invariants. No blocking finding remains for this offline gate. This does not establish runtime delivery readiness or explain the earlier host failure.

## Evidence inspected

Reviewed the complete final `host-diagnostics-candidate/tests/unit/test_engineering_host_diagnostics.py`, the relevant actual checked-output/delivery/isolation-recovery fixtures, original `requirements.md`, `test-output.txt`, `pytest-results.xml`, and `bundled-import-proof.json`. The production logic and its consumers were independently reviewed earlier; fresh hashes establish that review still applies. No executor/test-session prose was used as proof of behavior.

The current `test-output.txt` records the exact command selecting these four candidate suites, candidate `PYTHONPATH`, exit code 0, and captured output. Independently parsing the JUnit XML gives **87 tests, 0 failures, 0 errors, 0 skipped**:

| Suite | Recorded cases |
| --- | ---: |
| `test_engineering_host_diagnostics.py` | 28 |
| `test_checked_output_boundary.py` | 30 |
| `test_engineering_delivery_regressions.py` | 17 |
| `test_engineering_isolation_recovery.py` | 12 |

The XML records 8.802 seconds and timestamp `2026-11-05T00:00:09.861094-08:00`. Captured output reports 87 passed, 7 warnings in 8.80 seconds; the warnings concern pytest-asyncio configuration and an AnyIO pytest compatibility deprecation. These are observed recorded results, not a suite rerun by this assessor.

## What the final fixtures establish

- **Actual projection and event writing:** `test_engineering_host_diagnostics.py:150` supplies synthetic JSON to the real `Server.receive`, then inspects the actual bytes written through `emit` and `CheckedStream`. Exact event assertions cover known codes, an unknown variant, omitted arbitrary message/details/extra values, accepted HTTP 503, rejected boolean and out-of-range HTTP statuses, and exact boolean versus string/integer retry flags. This exercises the production path rather than a separately reimplemented projector.
- **Admission before disclosure:** the test at line 169 rejects a protected value in a raw field that would otherwise be omitted, with an empty event sink. It separately rejects a value formed by final JSON framing through the actual `emit` writer. The unchanged checked-output suite additionally covers escaped input, cumulative stream framing, partial writes/poisoning, console aliases, real pipes, private-policy transfer and helper module provenance. These provide sampled boundary evidence; they are not exhaustive enumeration of every protected string or protocol shape.
- **Failure surfaces:** line 190 exercises typed denial despite `willRetry=True`; line 207 exercises an actual terminal `turn/completed` error, checks the exact completion-stage diagnostic, omits raw detail and retains no retry authorization. Line 245 exercises rejected RPC responses through actual `Server.call`, verifies allowlisted versus unknown method/code values, and preserves `unclassified_host_error`. Generic failure emission tests at line 229 check fixed/unknown stage projection; line 333 checks observation admission, dispatch and result-stage failures through the real main controller with synthetic host dependencies.
- **Observation acceptance and holds:** line 284 includes a positive diagnostic-plus-single-completion case, exact retained stdout and retry telemetry. Negative cases include duplicate completions, arbitrary extra keys, a terminal diagnostic in the notification prefix, typed denial and RPC rejection. Line 344 verifies failed/timeout custody is not overridden. Both `_execute` stubs accept the actual sixth positional prompt argument, record that dispatch occurred, and assert the precise expected hold reason. Negative observation cases also inspect retained rows. The earlier missing-positive-case and wrong-argument false-negative defects no longer mask the intended behavior.
- **Shared role receipt consumer:** line 376 parametrizes actual `_run_role_impl` processing with and without a completion event. The positive case retains diagnostic metadata and parses the answer/accounting; the negative case holds despite successful synthetic process custody and diagnostic telemetry, has no answer, and has no usage counts. Both JUnit parameter instances passed. This fixture uses the **reviewer path** through the shared consumer, not a separately exercised planner/executor/test-writer launch.
- **Authority, effects and budgets:** `test_authority_and_effect_dispositions_ignore_diagnostic_telemetry` uses each receipt in the actual comparison. Clear, unknown RPC authority and typed denial retain their expected states with telemetry; positive reconciled reviewer custody remains settled and missing custody remains unknown. The separate ledger test calls actual `Ledger.consume` and observes retry → final → stop. The unchanged isolation/recovery suite also checks replacement cannot reset an exhausted allowance. The earlier unused-receipt loop is gone. The standalone ledger cap test is correctly described as **without receipt integration**; it is not evidence of an end-to-end telemetry/recovery run.

## Integrity and imports

Freshly computed production SHA-256 values all match `review-manifest-r2.json`:

| File | SHA-256 |
| --- | --- |
| `plugins/engineering/scripts/reviewer_isolation.py` | `92a92f5ce77b0af2ea27fe4f5efd848b121ec28b1e389297f8064289007c9c28` |
| `plugins/engineering/scripts/delivery.py` | `218e5542b09241409826a77f6b5590087a82839690d981626f041ca5200caf85` |
| `plugins/engineering/scripts/host_diagnostics.py` | `247cd09198fba291c742adb54f0a2d4786f3342c91192c74a7ed60f6d303bed6` |
| `docs/engineering-host-diagnostics.md` | `021a0b6acd1371bb6b3e34f0df246a09582d3437c0f82a0f78e4addcdc42a1c5` |

The three existing regression suites and `delivery_recovery.py` still match their baseline hashes. The prior 85-case records remain present as `prior85-pytest-results.xml`, `prior85-test-output.txt` and `prior85-test-session.md`; this assessment relies on the current 87-case result, not the earlier incomplete assertions.

Final test/evidence SHA-256 values:

| Artifact | SHA-256 |
| --- | --- |
| `tests/unit/test_engineering_host_diagnostics.py` | `ee667f47d4e386bc389f6c28fae0ff252b217eda14f6f9744b780f753a7f775b` |
| `test-output.txt` | `bb5521197073426d3d99a012b85f6b6cc15ec2b1e243fdd6ae24bda5c39ca4a5` |
| `pytest-results.xml` | `976683756d66f450e6858f4c93ed1c2e40f648a07558a37d3e0f43ed16c1a794` |
| `bundled-import-proof.json` | `5b2553057c887b733ac2686723cbfb9c6a6aecd9943bd6fd0363499f3fd0ac68` |

The new fixtures explicitly resolve their helper/controller files inside the candidate. The passing existing provenance fixture also checks candidate core, API adapter and CLI-loaded controller locations. The import-proof record reports a separate guarded Python 3.12.16 import, four asserted candidate module locations, bytecode writes disabled and subprocess launch blocked. This is supplementary recorded import evidence, not an independently repeated import here. The suite command used a Python 3.14 environment; neither that suite nor the guarded 3.12 import proves installed-entrypoint selection. The prior static review found recursive package inclusion and fixed sibling import ownership intact.

## Exact acceptance limits

This approval combines inspected production logic, unchanged source integrity, meaningful synthetic fixtures and recorded passing results. Recovery budget integration, ordinary worker-specific launch branches, model/session selection, scope/isolation admission and full custody lifecycle remain **source-reviewed**, with existing focused regression coverage; they were not exercised as a live integrated delivery run. The reviewer-path consumer fixture does not prove all roles launched successfully. Host/config responses and process execution are stubbed where needed; no actual Codex host or model behavior is established. The guarded import does not establish a built package, installed entrypoint, successful host launch, deployment or business outcome.

Diagnostics remain context only. Retry telemetry grants no authority or retry instruction. The earlier failed host's root cause is still **unknown**, and discarded details cannot be recovered from these fixtures. Prior failures and the earlier independent source rejection remain history. No source relaxation, automatic retry, failed-session replay, installation, push, merge or deployment is authorized by this assessment.

This assessor performed only bounded source/evidence reads, static parsing and hashing, and wrote this assessment. No tests, candidate imports, host/model/daemon/business calls, auth/session reads, held-candidate reads or broad preservation audit were performed during this outcome assessment. The parent's wider preservation report is outside this independent assessment's claims. There is no remaining blocker to the narrowly defined offline diagnostic acceptance; broader runtime readiness remains unverified.
