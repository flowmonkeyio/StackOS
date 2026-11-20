# StackOS 2.1.30 release

## Scope and identity

- Assignment: `release-2-1-30-package-a1`, contract revision 1.
- Ticket: `release-2-1-30-package` (11556), engineering plan 479,
  execution 474, `deliver-tickets` step 5054.
- Status: independently accepted, signed, notarized, and publicly deployed.
  Public feed verification completed on 2026-09-29 at 06:58 UTC.
- Source HEAD: `3121f4af370fbad273d08a9469d8961de2278534`.
- Source patch SHA256:
  `cc46127e4f2f1a8e90107363885a8d53369f4b615d5822c0628777478e7e90ab`.
- The 23 changed source paths matched their pre-build hashes after packaging.
  This receipt is separate from that source snapshot. No tracked UI bundle
  changes resulted from canonical signoff.
- Source manifest: `/tmp/stackos-release-2.1.30/final-source.json`, SHA256
  `0d196d0a5ee4f16dbb73127fd17589b88a270e5481878f527756c9260c3dd46e`.
- Exact source diff: `/tmp/stackos-release-2.1.30/final-source.patch`.
- Build ID: `20260929T060958Z`; release date uses the operator's local date,
  2026-09-28. Apple receipt timestamps use UTC on 2026-09-29.

The worker changed the four version values in `pyproject.toml`,
`stackos/__init__.py`, `desktop/package.json`, and the editable StackOS entry
in `uv.lock` to 2.1.30, and added a factual `CHANGELOG.md` entry. Existing
reviewed branding, project guidance, and website edits were preserved. Main
also authorized a formatting-only repair to `tests/unit/test_skill_presets.py`.
No runtime, installer, or provider behavior was changed by this packaging ticket.

## Shipped guidance

The packaged branding content-production workflow and orchestrator are both
0.6.2. The packaged writing reference, workflow, and orchestrator files match
their source bytes. Reference SHA256:

`4e22e48a9d92b8a8ce90e73c53f87a27b2fef9080dc579894f96cf70c521d760`

Evidence: `/tmp/stackos-release-2.1.30/packaged-branding-proof.json`.
The shipped addition is reusable editorial guidance. Project-specific voice
and selected personal examples remain with their existing project owners.

## Verification

All final checks below exited 0. Shell commands used the project TPF wrapper;
network and keychain checks used the normal approved sandbox escalation.

| Check | Result |
| --- | --- |
| Version alignment test | 1 passed |
| Bundled writing-reference test, exact node ID | 1 passed |
| `make signoff` | Passed in 2026.4 seconds |
| Ruff lint and format | Clean; 750 files formatted |
| mypy | Clean; 458 source files |
| Transfer slice | 188 passed |
| Broad regression slice | 3008 passed, 4 skipped |
| UI tests | 427 passed across 101 files |
| UI production build | Passed; no tracked bundle drift |
| `pnpm --dir desktop check` | Passed, including desktop doctor |
| Strict release dry-run | Signing required, public update URL required, notarization configured |
| `pnpm --dir desktop run release:preflight` | Passed for 2.1.30, arm64, configured identity/profile |
| `pnpm --dir desktop run dist:mac:release` | Passed in 1600.41 seconds |
| Packaged CLI smoke | Passed for StackOS 2.1.30, before and after app packaging |
| App codesign deep/strict, staple, Gatekeeper | Passed; Notarized Developer ID |
| DMG codesign strict, staple, Gatekeeper, `hdiutil verify` | Passed; Notarized Developer ID |
| Feed sizes and SHA512 | Match final versioned ZIP and stapled DMG |
| Stable DMG alias | Byte-identical to final versioned DMG |
| Previous versioned outputs | 44 files retained with unchanged sizes and modification times |
| `git diff --check` | Passed |

The four skips are existing skills-frontmatter checks with no matching
`skills/**/SKILL.md` entries. The new packaged-reference test passed. Existing
dependency/test-harness warnings and the Vite large-chunk advisory did not fail
the gate. The payload emitted a platform-library-path warning before its
successful isolated CLI and TDLib verification.

Initial attempts are retained: sandboxed signoff stopped at uv cache access;
the first authorized attempt found one Ruff formatting issue. After main
authorized the formatting-only repair, the exact reference test and full
signoff passed. An initial `-k natural_writing` test selector matched no tests;
the corrected exact test node passed and is the evidence used above.

## Signing and Apple receipts

The canonical build used `CSC_NAME=SERGEY RURA (TSHN26FR48)`, the existing
`stackos-notary` keychain profile, and public update URL
`https://stackos.flowmonkey.io/StackOS/`. It used the external TDLib proof at
`/private/tmp/google-sa-tdlib-proof.dylib`, verified before payload replacement:

`c397db26e7596eaf4a37ffb6d9400f2a34d764a26f388e4f4b9bfd41b070d5af`

No unsigned or notarization-skip fallback was used. Developer ID identity hash:
`CC8682C2109680F2D4AC15CC81B0010EB20D749D`.

| Submission | Apple ID | Final status |
| --- | --- | --- |
| App (`StackOS.zip`), 06:16:58.521 UTC | `907248c8-f17e-4892-b277-49ffec40ec36` | Accepted |
| Final DMG, 06:27:45.051 UTC | `bae883f7-a3de-4040-8ea0-3691102edfb4` | Accepted |

Apple readback: `/tmp/stackos-release-2.1.30/apple-submissions.json`.
The canonical wrapper stapled and validated the DMG, refreshed its blockmap and
feed checksum, then refreshed the stable alias. Independent read-only checks
followed that final mutation.

## Frozen artifacts

All files are under `/Users/sergeyrura/Bin/content-stack/desktop/dist/`.
The machine-readable manifest records absolute paths, sizes, SHA256, SHA512,
base64 SHA512, and the accepted Apple receipts:

`/tmp/stackos-release-2.1.30/candidate-manifest.json`

Manifest SHA256:
`98e89835dfea20f0bb87e0754da20c6961e7a2b0cf693a8739635b3e0dd3b6ea`

| Artifact | Bytes | SHA256 |
| --- | ---: | --- |
| `stackos-2.1.30-mac-arm64.zip` | 492334931 | `013ad889a133687d3584f8c9d8d73be792fe40b3eaac7bf9280668a203e796fb` |
| `stackos-2.1.30-mac-arm64.dmg` | 487963050 | `d7a86f1ee9d344175cef419d6ae7727f567a7d13c3e5b000b34a8fa088815a12` |
| `stackos-2.1.30-mac-arm64.zip.blockmap` | 511433 | `5b4c123045fcaddc78e7849983ce51c0a204964b59f7ca9695bb31aeaa66bdb8` |
| `stackos-2.1.30-mac-arm64.dmg.blockmap` | 507011 | `7ba748851ffc3d640fd3ffaac7493ff874be945f42e6d9b8e0ac6602f2daf7d1` |
| `stackos-latest-mac-arm64.dmg` | 487963050 | `d7a86f1ee9d344175cef419d6ae7727f567a7d13c3e5b000b34a8fa088815a12` |
| `latest-mac.yml` | 513 | `368de269dfc0aba16ca0b585707c829f4d2d76e7d0278c9794a1c3cd9b8637a1` |

Full command logs and exit receipts are under
`/tmp/stackos-release-2.1.30/`, including `make-signoff-final.log`,
`signed-release-build.log`, and the app/DMG verification logs.

## Public deployment

The main agent accepted the independent source, signoff, app, and final artifact
reviews before publication. All six artifact sizes and SHA256/SHA512 values
matched the frozen manifest. The reviewer also compared the ZIP against the
verified app: 20,900 regular files and 31 symlinks matched, with no missing or
extra files. There were no required repairs.

Published through the existing StackOS FTP connection to `/StackOS/`:

- ZIP and ZIP blockmap: action 9593.
- Versioned DMG, DMG blockmap, and stable download: action 9595.
- Update feed, published last: action 9597, success, 513 bytes, one completed
  file and no failures or skips.

Public verification read the complete ZIP and both DMG URLs, checking their
sizes and SHA512 values against the accepted release. Both blockmaps matched
their local bytes. Before feed publication, the old public feed was still
byte-identical to the saved 2.1.29 baseline. After publication, the 2.1.30 feed
and both blockmaps matched byte-for-byte.

- [Download the notarized installer](https://stackos.flowmonkey.io/StackOS/stackos-latest-mac-arm64.dmg)
- [Public update feed](https://stackos.flowmonkey.io/StackOS/latest-mac.yml)
- Public ZIP proof: `/tmp/stackos-release-2.1.30/public-zip.json`, 06:54:03 UTC.
- Public DMG and stable-download proof:
  `/tmp/stackos-release-2.1.30/public-dmg.json`, 06:57:49 UTC.
- Final feed proof: `/tmp/stackos-release-2.1.30/public-feed.json`.
- Feed SHA256:
  `368de269dfc0aba16ca0b585707c829f4d2d76e7d0278c9794a1c3cd9b8637a1`.

### Upload audit incident

Actions 9593 and 9595 retain failed/unknown audit status with
`daemon-restart-orphan`. The serving daemon had not restarted: another launchd
process repeatedly entered startup against the same database, reconciled live
action rows, then failed to bind the occupied port. The original FTP threads
continued. Native directory reads 9594 and 9596, followed by the complete public
file checksum checks above, establish external delivery. Neither upload was
retried, and the failed audit rows were not rewritten as success.

An attempted local restart was rejected by automatic approval review before
execution. The operator reaffirmed deployment only. No restart, launchd repair,
local installation, or app replacement was performed. This release does not
claim to fix the existing startup reconciliation issue.

## Installation boundary

The worker did not install or launch the app, restart the local daemon, mount
the DMG, use a browser, push Git, or change native StackOS lifecycle state.
Installed-app repair/restart and in-app update checks were not run under the
explicit no-install/no-restart constraint. Packaged CLI and desktop lifecycle
contract tests passed as listed above.
