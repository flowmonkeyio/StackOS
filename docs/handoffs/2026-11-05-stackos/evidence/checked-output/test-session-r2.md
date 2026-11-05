# Checked-output synthetic test session R2

Fresh test session against branch `codex/checked-output-boundary-20261105`.
Pytest used the existing engineering-process-clarity-current virtualenv and
explicit `PYTHONPATH` to the candidate checkout. The new tests assert candidate
paths for the checked-output module, API adapter, and engineering delivery
controller. Python was 3.14.6. No provider/model, business run, installation,
or real secret/session read occurred. Canaries were synthetic.

## Results

- Final consolidated impacted suites: **155 passed**, no deselections, with only
  dependency deprecation warnings. Suites covered the new boundary tests,
  delivery regressions, isolation/recovery, artifact redaction, CLI API and
  operation responses, tracked-delivery workflow, agent presets, and skill
  presets.
- Ruff on both edited test modules: **passed**.
- Final pytest runtime: **3.78 seconds** (pytest-reported duration).

New local sink evidence includes a real OS pipe, a real regular file accessed
through two separate handles, actual cumulative-stream rejection before the
next pipe write, exact atomic manifest bytes and digest, and mocked short-write
and failure adapters proving byte slicing and stream poisoning. The API test
stubs `urllib.request.Request` and `urlopen`: it proves checked body ownership
and established default JSON encoding, not HTTP wire framing or daemon
behavior. Private-policy evidence uses an actual anonymous pipe and proves
that the received descriptor is closed; the later helper-mode proof below
covers worker, reviewer, and observe startup through the pre-host boundary.

The follow-up added true split-canary checks across successive writes and
across two duplicated descriptors for one real pipe destination (stdout/stderr
alias semantics). It verifies the second fragment is rejected before any
more pipe bytes, then verifies a later benign write through the first alias is
also rejected by shared poison. Reviewer `Server.send` is tested without
launching a host process: safe JSON reaches captured stdin with exact newline
framing, while a protected canary spanning JSON field framing leaves stdin
unchanged. The impacted boundary, delivery, isolation/recovery, and CLI API
tests passed in an intermediate focused run.

Further bounded adapter checks cover reviewer `outcome_command`: protected
decoded argv is rejected before the host stub is called, and a synthetic
protected result is rejected when encoded for the model-facing handoff. The
opted-in API adapter is tested with stub HTTP error, connection error, and
oversized response paths plus captured stderr; each path withholds the
canary/no diagnostic bytes. A synthetic inherited policy exceeding the
platform's 512-byte atomic pipe capacity is rejected before controller
`Popen`. These checks are mock/stub adapter evidence, not daemon or host
execution. The latest focused boundary + delivery suites pass **41 tests**;
this count predates the final consolidated run below.

The isolated helper-startup proof now runs through the real controller
`delivery._execute`/supervisor path for worker, reviewer, and observe modes.
The controller captures one synthetic parent environment value and transports
the policy through its actual anonymous pipe; helper processes use the
controller's scrubbed environment and explicit candidate/script bootstrap,
without `PYTHONPATH` or the synthetic token in child env/argv. Each mode uses
minimal local snapshot/manifest/schema/host fixtures (plus a synthetic empty
auth-reference fixture for review modes). A `native_binary` spy checks `fstat`
on the transported descriptor and confirms it is already closed, verifies
candidate helper/core import paths and safe env booleans, then stops before any
host binary version/features probe or app-server launch. Only safe booleans and
paths are printed; the synthetic transported value is asserted absent from
retained logs. All three checks pass. This proves closure before the
native-host boundary for these controller-launched helper bootstraps; it does
not prove actual host startup or model interaction.

The final consolidated test command, run from `checked-output-candidate`, was:

```sh
PYTHONPATH=/Users/adilevinshtein/Documents/Codex/2026-11-03/task/checked-output-candidate \
/Users/adilevinshtein/Documents/Codex/2026-10-24/task/engineering-process-clarity-current/.venv/bin/python \
-m pytest -n 0 \
tests/unit/test_checked_output_boundary.py \
tests/unit/test_engineering_delivery_regressions.py \
tests/unit/test_engineering_isolation_recovery.py \
tests/unit/test_artifact_redaction.py \
tests/unit/test_cli_api_client.py \
tests/unit/test_operation_responses.py \
tests/unit/test_engineering_tracked_delivery_workflow.py \
tests/unit/test_agent_presets.py \
tests/unit/test_skill_presets.py
```

It reported **155 passed** in 3.78 seconds, with one dependency deprecation
warning and no deselections. Ruff command:
`.../.venv/bin/ruff check tests/unit/test_checked_output_boundary.py tests/unit/test_engineering_delivery_regressions.py`;
result: all checks passed. Frozen source verification reported 11 entries,
zero mismatches.

The separate `local_file_sink_probe.py` ran six actual local snapshot/schema/
broker probes successfully using a disposable linked Git worktree. It verified
exact snapshot bytes and manifest digest, rejected an unsafe encoded snapshot
manifest before manifest creation, rejected an unsafe broker manifest before
source mutation, verified exact broker edit bytes/digests, and tested schema
rejection before temporary-file creation plus exact benign schema bytes before
a stubbed native-host boundary. Native discovery and dispatch were stubbed.
The probe touched only temporary local Git/workspace files.

## Corrections during test setup

The first draft treated strings in separate fields as if they were
concatenated without JSON separators. That was not an actual serialized
canary and did not represent the stated contract. The corrected test uses a
synthetic canary equal to bytes genuinely spanning the encoded JSON field
separator (`\", \"right\": \"`): decoded field admission succeeds, final JSON
encoding rejects, and the real sink remains empty.

The first direct `_execute` and `_sanitize` calls reused the imported delivery
module's per-invocation cached policy after a prior test had captured a
different environment. The delivery regression module now resets its three
invocation globals for each direct-helper test, matching the actual wrapper
lifecycle. With fresh capture and a synthetic `FAKE_API_TOKEN`, argv rejection
occurs before child dispatch, and retention removes raw and JSON-escaped
canary forms. These initial failures were test setup errors, not candidate
defects; the assertions were preserved.

The one-pass escape regression uses protected value `A`: raw `\\u0041` is
accepted once as literal escaped text, while `\u0041` is rejected after one
decode. Identity escapes, line continuations, and a UTF-16 surrogate pair each
have synthetic rejection cases.

## Proof limits

The root's additional `local_process_sink_probe.py` passed four checks with the
test virtualenv: real supervised Python child environment scrubbing and canary
log redaction, byte-identical benign retained stdout/stderr, rejection of unsafe
joined event-log framing before either retained file write, and withholding an
unsafe second helper event on a real pipe with stream poisoning. No native host,
daemon or model was involved.

An extra runtime-compatibility check used the installed app's bundled Python
3.12.16 with explicit candidate imports and `-B`, without starting the app or
daemon. All six file-sink probes passed. The process probe passed its first
canary-redaction check, then failed its fixed benign-stderr expectation because
the packaged interpreter prints `Could not find platform dependent libraries
<exec_prefix>` on startup. The later process-probe checks were not reached under
this interpreter. A separate `bundled_python_diagnostic.py` directly demonstrated
one warning from the child and one from the supervisor, with those actual bytes
preserved by the controller, ordinary stdout intact, and exit zero/group cleanup.
No production or installed-runtime correction was made for this pre-existing
packaging behavior. These receipts are retained in `local-sink-results.json`.

All 11 frozen production-source entries in `review-source-r2/manifest.json`
still match their reviewed SHA256 values. No installed app, daemon, host model
flow, real HTTP daemon, or provider process was exercised. API evidence is
mocked. Local OS sink evidence does not establish host stdout/stderr
aggregation, actual TTY aliasing, or daemon wire behavior. Snapshot/schema and
broker evidence uses local fixtures with host discovery/dispatch stubbed; the
helper proof stops before native host probing and app-server launch.
