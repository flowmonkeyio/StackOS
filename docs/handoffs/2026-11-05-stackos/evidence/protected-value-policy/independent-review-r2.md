# Independent protected-value policy review, r2

Verdict: **REJECT**. The candidate is not approved for the fresh test session. A concrete final-serialization admission gap remains at StackOS-controlled disclosure and file-retention boundaries. This is separate from the documented host-internal output-retention limitation.

## Review basis and integrity

Read `requirements.md` first, then applicable repository/plugin guidance, product direction, engineering orchestrator and relevant workflow documentation. Reviewed actual candidate source and the complete ten-file `candidate-r2.patch` against `before/`. Did not read executor reports, implementation notes, prior reviews, parser-correction evidence or the held parser candidate. No candidate imports, tests, parser/model/business execution, installation, credential/session reads or delegation were performed. The only review deliverable written is this file.

Branch is `codex/protected-value-policy-20261105`. All 13 entries in `review-source-r2/manifest.json` match SHA-256 hashes of both the candidate files and their frozen copies. Reconstructing the unified diff from the actual `before/` and candidate files reproduces `candidate-r2.patch` exactly. Patch SHA-256: `f9522e3ca59e3064db357977955a8cebf72637d1ef1edc38ac68240c90dc8f36`.

Against the 2,235-entry full baseline manifest, eight existing files differ, exactly the existing-file changes in the patch; two patch files are additions. Each available pre-change file in the patch matches its baseline hash. The baseline `website/dist` entry is an unchanged symlink, not a missing source file. The three additional frozen files (`redaction.py`, `engineering_delivery.py`, `pyproject.toml`) are unchanged. Existing dirty role/workflow/recovery-related work remains part of the preserved baseline, not newly introduced policy scope.

## Blocking finding: validate the final serialized representation before emission or retention

`plugins/engineering/scripts/reviewer_isolation.py:203–212` calls `policy().require(value)` and then serializes with `json.dumps` and writes directly to the app-server stdin. `ProtectedValuePolicy._walk` checks individual keys and leaf values; it does not check JSON punctuation and adjacent fields assembled by serialization.

A concrete static counterexample is a captured sensitive environment value equal to the synthetic string `{"id": 1, "method": "initialize"`. This need not occur in any individual field, prompt, command or filtered environment. The first `Server.call("initialize", ...)` constructs precisely that prefix through `json.dumps` and sends it to the host. The structured check passes because its leaves are `id`, `1`, `method`, `initialize`, and the remaining keys/values. This is ordinary raw known-value disclosure produced by StackOS serialization, not an arbitrary computed/encrypted encoding or host-generated answer. The requirement and documentation explicitly include protected values that overlap protocol metadata; accidental assembly does not exempt this boundary.

The same root defect affects local retention:

- `delivery.py:498–501` validates the snapshot manifest structure, then writes unchecked serialized bytes to `outcome-manifest.json`. For example, captured value `{"changed_paths":[` is assembled by its sorted compact JSON serializer even when no field contains that value.
- `controlled_workspace.py:634–640` does the equivalent when replacing the broker manifest.
- `delivery.py:966–970` writes the role schema with `json.dump` before either structured or serialized policy admission. The later helper-side schema check is after file retention.
- `reviewer_isolation.py:138–141` checks the sanitized event structure but emits unchecked serialized JSON. Controller log processing subsequently checks its final text, which is useful defense in depth but does not make the helper's own emission gate complete.

The opted-in API adapter already recognizes and closes this exact class of gap at `stackos/cli/api_client.py:67–71`: it checks the serialized public body before dispatch. Apply equivalent final-byte/text admission at the affected controlled sinks, including bounded schema serialization before file creation/write. Retain existing structured checks, and reject rather than rewrite executable/source input. Serialization failure or unsafe replacement must hold without an unguarded secondary diagnostic. A fresh independent review must approve the corrected frozen candidate before behavior/security tests.

## Other assessed boundaries

The generic owner and opt-in design are appropriate. `ProtectedValuePolicy` is immutable, has a private representation, bounds transport/value counts, text size, aggregate string bytes, nodes/depth and supported JSON types. Its normalization consumes original escapes once, covers the requested JS/JSON escape families and surrogate pairs, and does not recursively decode produced backslashes. A nonempty set rejects malformed supported escape forms; the explicitly documented empty-set behavior still applies type/Unicode/structure limits. Existing label redaction is unchanged.

The controller captures the existing sensitive inherited-value selection once per invocation and reuses a captured filtered environment. Requirements and assembled prompts are checked before their durable/model dispatch boundaries. Observation JSON is rechecked after decoding; both confined and unconfined argv are checked again at execution. Helper structured inputs, model answers and dynamic-tool arguments/results are gated before downstream action or model return. Broker source reads/writes, current and old changed source, snapshot contents and paths retain strict known-value plus label rejection. No parser or executable-source admission exception was introduced.

Private policy transfer is explicitly enabled only for the trusted helper. Ordinary worker, reviewer and confined observation paths all request it. The anonymous socket has bounded payload handling; the supervisor passes only the intended descriptor to the helper and closes its copy. The helper consumes/closes the descriptor before native probes or app-server startup. Policy values are not deliberately placed in argv, environment, prompts, host configuration or files. Missing/malformed transport holds. Existing filesystem/no-network profiles and model/thread checks are not widened by this patch. These are static findings, not live descriptor-isolation proof.

The API opt-in covers final method/path/URL/wrapped body, bounded response reads, decoded response admission, decoded HTTP-error sanitization, connection/missing-token diagnostics and final console text. Default callers retain the existing unbounded/default behavior and call signature compatibility; credential authentication remains a separate existing privileged path, not discovery for the policy. Run-plan secret-key checks remain with the canonical owner. Progress/error/receipt sanitization and final API validation are materially improved; unsafe prior recovery data holds instead of being rewritten. Recovery policy, role/model selection, independent-review-before-tests sequencing, literal thread continuity and consumed-budget logic are unchanged by the patch.

## Limitations and remaining proof

The documented host-internal limitation is valid: `Server.receive` operates after the app-server has produced a message and cannot prove that host-generated answers/command results were never stored internally. It can gate subsequent StackOS action, retention and disclosure. This limitation does not excuse the blocker above, which sends newly assembled protected text into the host from StackOS-controlled code.

Conservative lexical scanning can hold benign prose/source containing escape-like fragments. Arbitrary concatenated/computed/encrypted/compressed/base64/URL encodings and visual or compressed media content are outside the claimed detection scope. Short protected values may also make otherwise benign protocol or diagnostics unusable. The strict JavaScript source-admission hold remains expected and unresolved.

The checkout is not installed, and the documented candidate-versus-installed helper import problem remains an integration limitation. Neither this review nor a future isolated bootstrap proves the installed runtime flow or authorizes installing or relaunching business work.

After correction and fresh logic approval, a fresh test session must establish candidate module paths and exercise synthetic canaries through real decoded input/output boundaries; raw/mixed escapes, one-pass backslashes and malformed/oversized/deep structures; both observation modes and model-requested commands; source/broker reads/writes; final serialized RPC/event/schema/manifest/API bytes; API success/errors/default-call behavior; safe console/log/receipt retention; transport truncation/absence/oversize and descriptor/environment/argv isolation before children; and benign flows with unchanged model identity, reviewer input isolation, approval gating and recovery history/budgets. Include the serialization-spanning counterexamples above. No such behavioral proof was run in this review.
