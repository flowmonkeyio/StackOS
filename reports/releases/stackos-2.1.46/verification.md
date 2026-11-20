# StackOS 2.1.46 release

Released on 2026-11-19 with explicit operator authorization. Source commit
`c939b60503fad584acbff27baf2b58b97b866c5b` and remote tag `v2.1.46` identify
build `20261119T200906Z`. Workflow 886, run 874, owns release closeout.

The release includes saved harness accounts, Codex automated-worker continuation
after account switching, immediate switching feedback, and the other accepted
changes listed in [CHANGELOG.md](../../../CHANGELOG.md). Version declarations in
Python, the desktop package and `uv.lock` are synchronized at 2.1.46.

## Verification

| Boundary | Result and evidence |
| --- | --- |
| Accepted source | Runtime `deba55134ab6c3f14caa28d45e6bb6f62fbb3817`; release delta is four version declarations and changelog, 5 files, +32/-4. Unrelated untracked work and unmerged server/online work excluded. |
| Integrated checks | Reused unchanged accepted runtime proof: 8,749 Python passed, 4 skipped; 1,837 UI tests passed; lint, typecheck and production UI build passed. Independent review accepted reuse; focused version checks and desktop checks passed. |
| Package | One canonical `pnpm --dir desktop run dist:mac:release` execution passed. Source/UI linkage to the app and ZIP verified. App and DMG signatures, Apple notarization, staples and Gatekeeper checks passed. |
| Installed update | Real native check, ZIP download, install and relaunch into `/Applications/StackOS.app` passed. Installed build matches the release; health, Doctor, launchd ownership and served UI passed. Managed upgrade reached `continued`, without a fence or required recovery. |
| Preserved state | No missing project, credential, account, attachment or retained receipt identities; encrypted credential/payload values, seed and daemon token preserved. Expected Telegram runtime metadata, Claude cache timestamps and Grok same-principal token refresh were independently explained. |
| Normal launch | Installed app quit/reopen passed, production update URL restored, temporary feed stopped and debug port closed. |
| Publication | Upload 13220 completed all six files; alias promotion 13221 preceded feed promotion 13222. Full HTTPS downloads matched local hashes for four versioned artifacts, staged files, then the live stable installer and feed. |
| Independent review | `/root/release_review`: final PASS, no required repairs. Independently checked remote tag, artifact identity, installed proof, preservation, publication order and public readback. |

The public installer is
[stackos-latest-mac-arm64.dmg](https://stackos.flowmonkey.io/StackOS/stackos-latest-mac-arm64.dmg).
The [stable update feed](https://stackos.flowmonkey.io/StackOS/latest-mac.yml)
advertises 2.1.46. Exact artifact identities and notarization submissions are in
[receipt.json](receipt.json).

## Evidence limits and retained observations

- There were no active automatic workers or upgrade checkpoints during this
  installation. This occurrence proves native installation and maintenance
  continuation. No live-worker upgrade resumption was exercised in this
  occurrence. Account-switch worker continuation has separate earlier accepted
  proof.
- Current update-prompt tests passed (7/7). The unchanged prompt's earlier
  independently observed native UI evidence was reused alongside this release's
  native IPC and installed bundle proof; no fresh visual walkthrough is claimed.
- The signed source fixture came from a local unsigned 2.1.45 build and initially
  lacked updater metadata. It was repaired using the metadata identical in the
  prior public and final release; the final release artifacts were untouched.
  The initial failed download is retained in scratch evidence.
- A restricted-sandbox signature check failed; the same check with normal macOS
  security access passed. An existing nonfatal splash-navigation cancellation
  was observed; actual page loading, native IPC and health succeeded.
- State preservation does not claim byte identity for legitimately refreshed
  native authentication/cache files. Those changes were independently assessed.

## Recovery and evidence custody

The prior public 2.1.45 artifacts and prior feed were retained. Local rollback
material includes the previous installed app and a restricted, integrity-checked
consistent SQLite copy. No database migration was introduced by this release.
Recovery remains an explicit operator action, not an automatic downgrade.

Detailed command receipts remain in `/private/tmp/stackos-release-2.1.46` and
the accepted runtime proof in `/private/tmp/stackos-account-continuation-20261119`.
These scratch paths may expire; this report and the adjacent receipt retain the
release identities, results and qualifications. Credentials, encrypted database
contents and private rollback material are intentionally not checked in.

This report is an evidence-only follow-up to the release source commit. It does
not change the shipped payload or move the release tag.
