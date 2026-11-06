# Host diagnostics fixture correction — revision 2

Date: 2026-11-05 UTC  
Candidate: `host-diagnostics-candidate`  
Scope: bounded test-quality correction in `tests/unit/test_engineering_host_diagnostics.py`; no production edits.

The normal-role receipt fixture now executes `_run_role_impl` in two cases using the real positional `_execute` contract and a closed process-custody result. A diagnostic plus agent message without `turn.completed` remains held, retains the diagnostic, and has no answer or usage counts. The same receipt with exactly one real completion becomes complete and retains the parsed answer and usage counts.

Removed the prior allowance comparison that iterated over `_receipt` without using it. Recovery authority is now checked against actual clear (`turn.completed`), unknown (`host.error`), and denied (`authorization.denied`) receipts, each both with and without retry telemetry. Reviewer effect disposition is also checked against a valid positive reconciliation receipt and a negative missing-evidence receipt, each with and without telemetry. The actual `Ledger.consume` API is verified once for retry → final → stop. The budget API accepts no receipt, so unchanged budget integration with diagnostic telemetry is source-reviewed; this suite does not claim end-to-end receipt-to-budget proof.

Before the new run, the prior 85-test XML, output, and full session report were preserved as `prior85-pytest-results.xml`, `prior85-test-output.txt`, and `prior85-test-session.md`. The new JUnit file is `pytest-results.xml` and records 87 tests, zero failures/errors/skips. The same four approved offline test files were run once with the command and exit code recorded in `test-output.txt`; result: `87 passed, 7 warnings in 8.80s`. Warnings are existing `pytest_asyncio` and AnyIO deprecation notices. After the run, Ruff identified one 102-character test assertion; only line wrapping changed, then Ruff passed. No behavioral test changes followed the run.

Ruff command (exit 0):

```text
TPF_LLM_TOOL=codex tpf --passthrough-tty /Users/adilevinshtein/Documents/Codex/2026-10-24/task/engineering-process-clarity-current/.venv/bin/ruff check --config host-diagnostics-candidate/pyproject.toml host-diagnostics-candidate/tests/unit/test_engineering_host_diagnostics.py
```

Output: `All checks passed!`

All four production/documentation hashes still match `review-manifest-r2.json`: `reviewer_isolation.py` `92a92f5ce77b0af2ea27fe4f5efd848b121ec28b1e389297f8064289007c9c28`; `delivery.py` `218e5542b09241409826a77f6b5590087a82839690d981626f041ca5200caf85`; `host_diagnostics.py` `247cd09198fba291c742adb54f0a2d4786f3342c91192c74a7ed60f6d303bed6`; `engineering-host-diagnostics.md` `021a0b6acd1371bb6b3e34f0df246a09582d3437c0f82a0f78e4addcdc42a1c5`. No native host, model, daemon, business task, or network call was made. These synthetic fixtures establish local behavior only and do not prove real host/model delivery or retrospectively identify a prior host failure.
