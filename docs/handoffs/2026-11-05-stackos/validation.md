# Consolidated PR validation

**Draft handoff only: full signoff failed.** Historical verification is recorded separately in the handoff and must not be treated as validation of this exact integrated checkout.

## Review

A fresh Astra session inspected the actual integrated tracked/untracked source and original requirements. It found no merge-specific required-run regression and accepted an explicitly incomplete draft handoff, not production execution. See [integration review](integration-review.md). Its follow-up accepted the corrected role paragraph and draft framing; CLI/link corrections were applied. No runtime implementation was changed after that review.

## Fresh verification session

Fresh Luna `/root/luna_pr21_verification` ran the existing suites after logic review, without editing implementation/tests or installing dependencies. It used the previously prepared Python 3.14.6 environment at `engineering-process-clarity-current/.venv`, with this checkout selected through `PYTHONPATH`, `UV_NO_SYNC=1`, `UV_OFFLINE=1` and a task-local UV cache. This is a qualified source environment, not a clean lockfile provisioning or installed-app proof.

`make signoff` was invoked once (exit 2). It stopped at Ruff `I001` in `tests/unit/test_engineering_host_diagnostics.py:3`: the import block lacks a blank separator between `pytest` and `stackos.artifacts.checked_output`. The retained accepted test bytes were not silently reformatted. Ruff format, mypy, the complete Python suite, UI tests and UI build were therefore **not reached**. UI dependencies were absent; a guard prevented dependency installation and was not invoked because lint stopped first. No install/build/deploy gate passed.

The focused command used the prepared interpreter with `-m pytest -n0` and these nine files:

```text
tests/integration/test_repositories/test_required_stackos_flow.py
tests/unit/test_checked_output_boundary.py
tests/unit/test_engineering_delivery_regressions.py
tests/unit/test_engineering_host_diagnostics.py
tests/unit/test_engineering_isolation_recovery.py
tests/unit/test_agent_presets.py
tests/unit/test_engineering_tracked_delivery_workflow.py
tests/unit/test_skill_presets.py
tests/integration/test_repositories/test_workflow_templates.py
```

Result: **152 passed, 3 failed, 0 skipped**, exit 1. All failures were in `test_workflow_templates.py`:

| Test | Actual failure |
| --- | --- |
| `test_builtin_templates_can_be_listed_and_described` | Expected delivery/reviewer-only presets; actual includes planning and test-writer. |
| `test_engineering_readiness_and_acceptance_contract_survives_plan_derivation` | Expected version `0.5.0`; actual `0.7.0`. |
| `test_builtin_public_workflows_have_reviewed_experience_contracts` | `engineering.tracked-delivery` had no required reviewed experience contract (`None`). |

The first two assertions are stale relative to the intended four-session workflow. The missing experience contract needs a real contract assessment; do not delete the assertion merely to make tests green. These issues are left visible for the next owner, rather than represented as approved completed integration. Current runtime tests passing do not close source-admission, operational recovery, generic attestation or live-host gaps.

The [focused result projection](evidence/integration-focused-result.json) preserves counts, failed tests/messages and SHA256 of the full local JUnit file. Raw logs and full failure traces remain local; no new host, provider, production database or business run was invoked.

## Publication checks

The evidence archive is an explicit text allowlist. Rejected implementations occur only as `.patch.txt` historical evidence, with no parser/vendor additions in executable source. Local TDLib pin changes are inactive evidence. Existing PR21 commits and current-main ancestry are retained; the push uses the existing PR branch without force.

The publication audit checks selected new/changed files for inherited protected values, canonical JSON spellings, high-confidence key/token patterns, forbidden private/session/database paths and archive-manifest hash consistency. Its result is in [publication-audit.json](evidence/publication-audit.json). It is a scoped publication check, not a complete credential detector or source-admission approval. No credential file is read. New handoff prose/derived records are identified separately from exact copied historical evidence.

No merge, installation, deployment or held-attempt retry is authorized or performed by this handoff. Before merge, resolve the documented failing checks and functional gaps, then obtain appropriate independent acceptance and run the full prepared-environment signoff.
