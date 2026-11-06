# Independent accepted-baseline runtime assessment

**Verdict: ACCEPT the bounded synthetic HTTP persistence/readback evidence. HOLD any claim of completed planner or end-to-end engineering workflow execution.** The real host attempt failed; supported host readback confirms that failure. These artifacts reveal no decisive checked-output implementation defect, but they do not diagnose the host failure or complete the runtime proof matrix.

## Scope and integrity

I read the original checked-output requirements, the accepted source manifest and relevant actual source, the three requested probe scripts, their captured result artifacts, and the subsequently supplied `read_host_receipt.py` / `host-readback-result.json`. I did not use executor reports or `limitations.md` as proof. I did not execute/import candidate code, run tests, launch a model/server, read auth/session files, or inspect the held source-admission candidate. This assessment is my only write.

All 11 accepted source files match `evidence/checked-output/review-source-r2/manifest.json` in both the frozen source and `checked-output-candidate`. I also independently hashed the synthetic host-probe worktree source, snapshot source and snapshot manifest: all match the digests in `root-host-probe-result.json`.

Reviewed artifact SHA256:

| Artifact | SHA256 |
| --- | --- |
| `daemon_persistence_proof.py` | `385622dbab3e93e470b6e6504284fcfd1b2c86a067f0a40e45eed3aa79fa65ec` |
| `restart_readback_proof.py` | `32972f277fbe314349f19af2acf2d5b44969ca29e5fbccd1ade58f04ec881546` |
| `persistence-result.json` | `9658a3acd2a990456051b8140c4b98df8752560a48cbbb7fc1783f31e3f68787` |
| `root_host_probe.py` | `e3a01dd413fe3ba52d7bb1a91ce0a4a50a95364f98730a7362ad1ee9e63c5138` |
| `root-host-probe-result.json` | `acbdaad96f056731fe76de0004c9d9c933a5d1879de6af3f5657080f7ae6fbe4` |
| `read_host_receipt.py` | `bdc9b44caa0fc4822c0c50a269a68fb5baf13448dc5ddffee94b0f69bda02dc4` |
| `host-readback-result.json` | `8a5e073c5480bf76f74458d7e0a5b9a3f95354377c2ef901a52c025a45e89ddf` |

## Persistence evidence

The fixtures use candidate `create_app`, an owned Uvicorn server on an ephemeral loopback port, explicit fixture data/state directories, and the real `_api_request` / urllib path. They do not replace HTTP with a mock. `prepare_app_database` prepares an independent migrated/catalog database and refuses to replace an existing database. Authentication remains inside the normal API owner against that fixture's state; token contents are not included in the reported results.

`daemon_persistence_proof.py` contains actual project/run-plan creation, start, claim, receipt-write and restart assertions. Its source alone is not proof that every statement completed. The supplied captured successful result is specifically for the later `restart_readback_proof.py` invocation, with exit code zero. That artifact explicitly states it was reconstructed from retained tool output, not produced by a new runtime rerun; I have assessed that recorded observation and the corresponding script, not independently rerun it.

The later script starts an owned server against the already-existing fixture database and asserts through REST that project 1 is the expected synthetic project, plan/run 1 is completed, its step is successful, and its full `result_json` equals `{"receipt":"synthetic-success","proof":"safe"}`. The captured receipt hash matches my independent SHA256 of `synthetic-success`: `53c4cf13645d6b80983a64e734e15d7b53c735ffe8cdfebed20b34611df22d5b`. This is evidence of durable state read by a fresh fixture invocation, not merely a prior write response or agent claim.

The protected request contains the synthetic canary in the wrapped operation body and is caught as `OutputRejected`; plan IDs remain exactly `[1]` and no returned title equals the canary. In the unchanged accepted `_api_request`, body admission occurs before `Request` construction and `urlopen`. That source ordering plus the observed rejection supports rejection before HTTP for this concrete case. Unchanged row count alone would not prove absence of network dispatch, especially because the attempted plan reused the existing key. There is no wire capture or server request-count assertion here.

Safe persistence uses an empty `OutputPolicy`; the negative request uses a nonempty synthetic policy. Consequently this proves a benign durable readback and one protected request rejection, not every protected-value interaction with receipts or recovery metadata. It also does not prove byte identity between checked request bodies and later daemon database serialization: that re-encoding remains outside the client boundary.

Cleanup evidence is exit zero after the fixture's owned server stop/join, whose source raises if its thread stays alive. The separate `ps` check was denied by the sandbox. Do not claim an independently successful external process-table check.

## Host evidence

`root_host_probe.py` verifies the 11 accepted source hashes and exercises one synthetic planner through the real controller/helper/native-host path. It deliberately bootstraps candidate imports inside the scrubbed helper launch. That makes the candidate import provenance explicit, while leaving the installed entry point unproven. The prompt is planning-only and requests a `workspace_read`; no model report is used as completion evidence.

The actual receipt records matching requested/resolved `gpt-6-astra`, the pinned host contract, checked isolation/scope admission and a turn-start identity. It then records broker closure, scoped effects reconciliation and `worker.failed`. Exit code is **2**, `status` is **held**, timeout is false and the original process group is reported cleared. There is no `turn.completed`, planner answer or demonstrated `workspace_read` result. Source/snapshot hashes remain unchanged. The settled effect disposition concerns verified controlled-workspace broker custody; it does not mean the task succeeded or prove every possible host-internal effect was reconciled.

The subsequent diagnostic uses supported `initialize` / `thread/read` RPCs for the same synthetic thread and does not call turn start, thread resume or model execution. It filters raw error text into allowlisted codes/fixed categories before checked encoding and atomic retention. Its result matches the original turn identity and reports **failed**, `error_present=true`, `host_code="other"`, no HTTP status and no recognized message category. The reader process is reported closed. `item_count=1` does not identify a tool call or establish useful work. This corroborates failure but leaves its root cause unknown; neither a network/configuration explanation nor a checked-output bug is established.

## Remaining limits and disposition

This evidence extends the accepted local-adapter assessment with real synthetic HTTP/database readback and a real but unsuccessful host attempt. It does not demonstrate a completed planner, model-requested broker read/edit, tool-result return cycle, independent reviewer/test-writer workflow, or durable recovery-ledger execution. It does not exercise protected response/error canaries against the live fixture, all final framing combinations, HTTP wire-byte identity, arbitrary host-internal log/session retention, or the shipped import/layout path. Runtime summaries expose safe identifiers/statuses rather than credentials or raw host error text; they are not an exhaustive audit proving that all host-internal records contain no protected material.

Retain the checked-output baseline's bounded acceptance. Retain the host execution failure as held with an unresolved cause; do not equate model resolution, turn start, broker closure, a generic error classification or server startup with completion. Do not replay the turn or reset any budget on the strength of this assessment.

The separate source-admission candidate remains out of scope and held after its stated rejections. The original strict JavaScript/MYB snapshot source-admission blocker is not resolved by a clean synthetic Python fixture. No snapshot bypass, production/business run, installation, migration launch, merge, push or deployment is approved.
