# StackOS 2.1.31 corrective release

Date: September 29, 2026, America/Los_Angeles. Run plan **488**, execution
**483**, following the accepted connector work in **477/479**.

**Status: signed, notarized, published and verified.**
The update feed switched to 2.1.31 at `2026-09-30T04:52:11.221954Z`
(September 29, 9:52 PM Pacific). The operator owns local installation.

[Download 2.1.31](https://stackos.flowmonkey.io/StackOS/stackos-2.1.31-mac-arm64.dmg)
or use the website's
[stable download](https://stackos.flowmonkey.io/StackOS/stackos-latest-mac-arm64.dmg).

## Purpose and scope

The installed release advertised the Google Search Console sitemap submission
action, but its connector rejected `sitemaps.submit` during validation. The
already implemented and tested connector work was still in its separate
worktree. This release consolidates that accepted source into the release
checkout and increments the version from 2.1.30 to **2.1.31**.

The consolidated work includes single and batch sitemap submission, Search
Console HTTP batch reads, and the separate Google Indexing API connector with
single and batch operations. Its action registration, authentication contracts,
transport, tests and documentation move together. No additional connector
behavior was introduced during consolidation.

## Accepted source

- Source worktree: `/private/tmp/stackos-seo-477`.
- Source release base: `3121f4af370fbad273d08a9469d8961de2278534`.
- Accepted integrated patch SHA-256:
  `2bd39145f9accf44f5b32aff2dfc96e5a61ca04082f9824dec2260d9fdf33592`.
- Exactly **38 files** were copied. Their destination preimages and source
  postimages were checked before copying; every destination postimage matched
  the accepted manifest afterward.
- **71 protected files** remained byte-identical, including current project
  guidance, branding sources and phase 1 website work.
- `pyproject.toml`, `stackos/__init__.py`, `desktop/package.json` and the editable
  StackOS entry in `uv.lock` all contain 2.1.31. The changelog gained a factual
  release entry while preserving its previous content.

The [consolidation receipt](stackos-2.1.31/consolidation-receipt.json) contains
the complete file hashes and preserved dirty baseline. Its SHA-256 is
`5d920ed0c2162a12e71cd2ea19255c55da8591ccbc8cc2aea56e05d927f139f0`.

The [independent source review](stackos-2.1.31/independent-source-review.md)
recommended **PASS for release packaging**. It independently checked all 38
accepted files, all 71 protected files, the four version edits, the changelog
prepend and retained proof. The main agent accepted that review before starting
the release build. Source is frozen during packaging.

## Reused test evidence

The operator explicitly requested release without rerunning test suites. This
release reuses the existing evidence whose hashes are retained in the
consolidation receipt:

- `/private/tmp/stackos-seo-477-evidence/signoff-r4-3-receipt.md`: broad
  `make signoff` passed on the pre-audit-repair candidate. No aggregate test
  count is claimed because the full raw summary was not retained.
- `/private/tmp/stackos-seo-477-evidence/integration-audit-repair-r4-1-receipt.md`:
  the final accepted two-file audit-redaction repair had **224 affected tests
  pass**, plus clean Ruff, formatting and mypy results. Its raw affected and
  lint/typecheck logs remain alongside the receipt.

These are historical results on the accepted feature bytes. The combined
2.1.31 checkout has not been retested. The new artifact inclusion and public
download checks are recorded below. The canonical build's intrinsic integrity
checks also passed.

## Build and release evidence

Canonical `pnpm --dir desktop run release:preflight` **passed with exit code
0**, recorded in
`/private/tmp/stackos-release-2.1.31/release-preflight-result.json` and its linked
log. Signing is pinned to `SERGEY RURA (TSHN26FR48)`, notarization uses the
`stackos-notary` keychain profile, and the public update URL is
`https://stackos.flowmonkey.io/StackOS/`.

The canonical `pnpm --dir desktop run dist:mac:release` build completed with
**exit code 0** in **1,488.36 seconds**, execution session `30900`. Build log:
`/private/tmp/stackos-release-2.1.31/signed-release-build.log`.

The six-artifact manifest is
`/private/tmp/stackos-release-2.1.31/candidate-manifest.json`, SHA-256
`a613f8e38348645aa7533c6e2faed2f7e033146a0cd75f4f0c5706cf990e8d89`.

| Evidence | Current result |
| --- | --- |
| Canonical release preflight | Passed, exit code 0; receipt above |
| Signed release build completion | Passed, exit code 0; 1,488.36 seconds |
| New artifact names, sizes and hashes | All six match independent size, SHA-256 and SHA-512 checks against the manifest above |
| Packaged version and accepted connector source inclusion | Version 2.1.31; all 15 accepted runtime/plugin files and 10 protected guidance files match in both app and updater ZIP |
| Signing identity and signature verification | Pinned identity above; app and DMG signature checks passed |
| Apple notarization submission and acceptance | App `b7285569-3492-4da4-8fc0-ba3b311fd2ee` and DMG `30f610e5-0d0e-495d-9312-efb2c05ced8b` both Accepted |
| Stapling and Gatekeeper assessment | App and DMG passed; DMG integrity also passed |
| Update feed and artifact consistency | Local feed version, primary ZIP, sizes and checksums match; stable DMG alias is byte-identical to the versioned DMG |
| Native publication action receipts | Actions 9604 (ZIP and blockmap), 9605 (DMG and blockmap), 9606 (stable alias), 9607 (feed): all successful; six files, 1,469,270,014 bytes, zero failures or skips |
| Public download URLs and retrieved-byte verification | Full ZIP, DMG and stable-alias size/SHA-512 match; both blockmaps and final 2.1.31 feed are byte-identical to accepted local files |

The [independent artifact review](stackos-2.1.31/independent-artifact-review.md)
recommended **PASS for publication** with **230 checks passed and zero
failures**. Its [final file checks](stackos-2.1.31/independent-final-artifact-checks.json)
bind the exact six artifacts. The reviewer also confirmed that all 38 accepted
feature files, 71 protected files and five release metadata files remained at
their reviewed hashes. The main agent accepted the review and published those
exact artifacts in run 488.

| Main artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `stackos-2.1.31-mac-arm64.zip` | 492,350,159 | `2efd67d04e583b19971bc026dee7a4a95da06d782b55047306e15e5f3bdc88d9` |
| `stackos-2.1.31-mac-arm64.dmg` and stable alias | 487,950,163 each | `f165807ce78f81a96fb81c23d570b04b2f72e79944e0744cf710a96d3da87feb` |

Publication preserved the previous feed until the versioned files and stable
alias passed public verification. The feed was uploaded last. Directory audit
9608 found all six expected files and 106 previous entries unchanged by type,
size and modification time; only four new versioned files were added.

Approved StackOS artifact **3147** retains the [release evidence packet](stackos-2.1.31/release-evidence.json):
26 source and receipt files, 314,627 bytes, SHA-256
`3a551c9ebabb86108b877c8c6b4b7fbf0e96744d9ad1ba41ac9bfcb47401a572`.
Its durable URI is `/generated-assets/stackos-release-488/release-evidence.json`.
The packet includes the exact artifact manifest, Apple acceptance, independent
reviews, four successful upload receipts, public checksum checks and recovery
baseline. The artifact record was read back after creation.

## Recovery

The prior 2.1.30 update-feed snapshot and existing versioned release files are
retained. A rollback would require a separate explicit operation restoring the
previous stable download alias and update feed from the retained 2.1.30
release. No rollback is performed by this work.

## Installation and follow-up

The operator owns local installation and restart. This release work does not
install the application or restart the running daemon. After installation,
native connector validation and the already requested sitemap resubmission can
resume against the updated runtime. No sitemap request reached Google during
the earlier failed validation, and no new indexing or ranking result is claimed.
