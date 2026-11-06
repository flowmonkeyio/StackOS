# Independent source-admission review R1

**REJECT. Not approved for fresh tests.**

I did not implement this candidate. I read `requirements.md` first, then actual frozen/candidate and baseline source, root/plugin AGENTS guidance, product direction, the project SDLC orchestrator/reviewer contracts and relevant preset guidance. I did not read the design assessment, implementation narrative/report, held implementations or prior review reports. This was static source reading, hashing and comparison only: no candidate imports, parser execution, tests, native host/model/daemon execution, installation, business run or source changes. The examples below are synthetic static counterexamples, not executed test results.

## Blocking findings

### R1 — Destructuring declarations bypass credential-target admission

`plugins/engineering/scripts/source_classifier.mjs:51–63,79–82` records names introduced by ObjectPattern and ArrayPattern, but `:160` only calls `admit` for a VariableDeclarator whose identifier is a plain Identifier. The final walk never validates sensitive names introduced by ordinary destructuring declarations. AssignmentPattern checking at `:161` only addresses defaults, and the Property check at `:173–176` uses the property key rather than a renamed binding's target name.

Consequently these complete JavaScript programs follow an accepting path:

```javascript
const [token] = ["fixture-credential"];
```

```javascript
const { value: token } = { value: "fixture-credential" };
```

There is no `admit` call for either `token` binding. The collected literal `fixture-credential` has no credential label by itself, and the original text contains no `token:` or `token=` spelling for the fallback scanner (`stackos/artifacts/redaction.py:24–28`). Therefore `source_admission.py:165–182` has no basis to reject it when that synthetic value is absent from inherited OutputPolicy. The policy still rejects protected values; it does not repair classification of other plainly literal credential data.

This violates the required rejection of literal credentials and obvious literal aliases. It is a direct syntactic binding gap, not a demand for whole-program runtime secret analysis. Check every credential-labelled binding in declaration patterns against its initializer/path, or hold unsupported patterns. Do not fix this by omitting source or weakening inherited policy.

### R2 — Unsupported source extensions take an accepting text-only route

`plugins/engineering/scripts/source_admission.py:139–143` returns successfully for every suffix other than `.js`, `.mjs` and `.cjs` whenever the old textual redactor does not change the text. Meanwhile `controlled_workspace.py:195–219` expressly selects `.ts`, `.tsx`, `.jsx`, `.vue`, `.svelte` and other executable source forms. These files reach snapshots, broker reads/edits and complete before/after review through that same accepting fallback.

For example, an admitted `.ts` file containing the following credential literal receives no program parse and no rejection from the raw labelled-text pattern:

```typescript
const token /* ordinary comment */ = "fixture-credential";
```

The intervening comment prevents the scanner's required label/whitespace/colon-or-equals sequence. A typed file can likewise contain an unsupported computed target such as `const value = { ["to" + "ken"]: "fixture-credential" } satisfies Record<string, string>;` without any matching textual credential assignment. The JavaScript path would reject the former through AST analysis and hold the latter; changing to a selected unsupported source suffix bypasses that boundary.

Retaining ordinary prose checks is appropriate, but it is not a fail-closed executable-source admission rule. The requirement says unsupported syntax remains held. Distinguish ordinary non-source text from selected source lacking a supported complete parser, and hold the latter unless an independently reviewed complete-source contract supports it. This does not require adding TypeScript support in this correction. `docs/engineering-source-admission.md:7–9` currently describes the permissive fallback and must not be treated as overriding the original requirement.

## Integrity and preserved boundaries

- All 16 frozen files match their recorded SHA256 values and the candidate. The complete patch hash is `b9c3ca3f2015eed858d104af14a5800880591950930af0cfdba19af6036dd7c9`. The implementation manifest hash is `f2296d0e15bb48345b3b2e49aeb1084c9befff72f8138070c34473b4e1e09e42`.
- Independently hashed all regular-file entries in the 2,238-entry accepted baseline and 2,245-entry candidate manifest; all matched. The single symlink entry, `website/dist`, has the same recorded link target in both. The manifest delta is exactly the ten files represented by the supplied patch. I found no unrelated source changes in that delta.
- Acorn archive SHA512 matches the recorded published integrity value. Its `dist/acorn.mjs`, LICENSE and README match the vendored files byte for byte by SHA256. The Bun archive SHA256 is `c240d7f0ccb90c263a849fcdb09b7ee62630d921348ed6aafda86b9d577bc510`; its binary and the exact declared installed macOS binary both hash to `0a2237cdcfa0d16ef501d9acca9f0026309ef348bea7393a64e8d1e3609565cc`. These are independent local hash comparisons, not execution or a new network provenance verification.
- The JavaScript route does use complete Acorn Program parsing with script/module selection (`source_classifier.mjs:26–40`), rather than token-only or partial parsing. There is no candidate module import/evaluation. The source classifier and Acorn imports are pinned, and subprocess input/output/time and traversal sizes are bounded. The exact macOS Bun path is an explicit compatibility bound, not a portability claim.
- `checked_output.py`, `redaction.py`, `reviewer_isolation.py`, CLI adapters and packaging configuration match the baseline. Original-source policy checks precede classification; decoded results/literals, prompt bytes, broker replacements and model transport retain inherited policy enforcement. The parser receives source, not policy values. Private policy transport is unchanged.
- Snapshot capture and broker admission call the same source adapter (`delivery.py:493`, `controlled_workspace.py:462,612,619`). Complete before/after review sources also use it (`delivery.py:351–393`). `_SourcePrompt` permits only controller-owned capture, independently checks surrounding prose, recaptures before dispatch and checks the whole prompt (`:680–701`). Durable prompt records retain sanitization and an original prompt hash. Source capability subclasses are converted to exact strings before the final OutputPolicy boundary (`:774–790`).
- Model-facing worker `workspace_read` returns admitted exact content and digest (`controlled_workspace.py:544–560`, `reviewer_isolation.py:896–920`). Reviewer outcome commands remain scoped to the admitted snapshot and policy-checked transport (`reviewer_isolation.py:380–412,844–856`); the reviewer has complete changed before/after source in its prompt. This is a code-path assessment, not proof of a running native host.
- Both worker and reviewer snapshots are produced with dispatch's `_outcome_snapshot`, then rechecked before `_repo_head` clean/root/base confirmation (`delivery.py:553–570`). New-run preflight precedes `runPlan.create` and `runPlan.start` (`:1498–1524`); preceding daemon calls in this function are lookups. Resume requires matching frozen parser/adapter/runtime identity and base, and valid manifest digests before subsequent mutation (`:1448–1456`). Old runs without this metadata remain held. This preserves the source-contract boundary statically; it does not establish cross-process exclusion of unrelated external writers.
- Four role/model selection, fresh review, approval before tests, reviewer-input isolation, recovery policy, consumed budgets and historical-stop logic are preserved by the focused delta. No approval to resume a held business run follows from this review.

## Remaining proof after correction and fresh independent approval

No behavior has been demonstrated by this review. Future fresh tests must cover the corrected declaration-pattern and unsupported-source cases, ordinary/direct/aliased runtime references, literals, comments, regexes (including escaped labelled text), Unicode/escape and template forms, unknown syntax, inherited protected values at decoded and final-byte boundaries, exact source bytes/digests and strict surrounding prose. The existing regex/comment scanning bounds must be stated accurately; complete parsing alone does not establish arbitrary runtime secrecy.

Verify the pinned Bun flags, empty configuration/environment behavior, startup and timeout/output limits using the existing declared runtime without installation. Verify actual installed packaging/import paths, not merely source-tree imports. Then prove snapshot/broker/review parity and zero daemon mutations on failed preflight with bounded isolated local fixtures. Keep mocked transport, real isolated persistence, native-host startup and actual model outcomes separate in evidence. Review/model end-to-end behavior remains entirely unproved here.

The real held MYB artifact was not executed, edited or excluded. Its benign read-only admission probe remains post-approval work, and any other held dependency must be reported. Both earlier implementation stops, existing recovery allowances and the current test gate remain intact.
