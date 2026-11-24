# StackOS 2.1.47 release

Published on 2026-11-23 with explicit operator authorization. Release source,
final artifacts and the user-performed installed update are verified. The public
installer and update feed were promoted in order, and final HTTPS readback
matches the accepted artifacts. Remote tag `v2.1.47` identifies the candidate.

The operator authorized release and a version bump. Candidate
`d3f4a5599f1f0351489d2a8d3c25e40ead830fdc` produced version 2.1.47, build
`20261124T002643Z`. Python, desktop and lockfile versions agree. The candidate
contains the 95 declared commits since `v2.1.46`, including the accepted runtime
`2acdd44d1beec83091d7486c95a9ce0752d701b9`.

The release includes tracked project setup/update, native harness execution and
project role settings, Antigravity integration, worker continuation and usage
corrections, faster homepage reads, Finance cleanup and recovery, optional-host
handling, and daemon startup exclusion. See the candidate's
[CHANGELOG.md](../../../CHANGELOG.md) for the user-facing changes.

## Verification

| Boundary | Current result |
| --- | --- |
| Accepted source | Candidate is clean and synced. Declared source inventory matches Git ancestry. Unmerged branches and disposable setup work are excluded. |
| Integrated checks | Reused accepted runtime signoff: 9,245 Python passed, 4 skipped; full lint, types, UI tests and production build passed. The release delta adds synchronized versions, changelog and a bounded setup role-ID clarification. Its 77 focused checks and desktop checks passed; independent review accepted the bounded delta and proof reuse. |
| Package | One canonical `pnpm --dir desktop run dist:mac:release` execution passed. The app and ZIP match 115 source, UI and template entries from the candidate. All six final artifact hashes were independently rechecked. |
| Apple trust | App and DMG notarization are Accepted. Signatures, staples, Gatekeeper and packaged CLI/TDLib checks passed. Final DMG metadata and stable alias match the stapled bytes. |
| Native installed update | The user performed the native update and relaunch from the signed current-code 2.1.46 fixture. Native logs show final ZIP download and Squirrel.Mac delivery. Automated postflight confirms the exact final 2.1.47 build, valid signature/staple and running installed app. No fresh CUA visual walkthrough is claimed. |
| Preserved state and runtime | Installed health and Doctor passed. One launchd-owned listener serves the matching UI; the production update URL is restored. Managed continuation is unfenced, continued, and requires no recovery. Project/account identities, protected credential/payload values, seed/token and 780 terminal ticket receipts were retained; explained native refreshes are qualified below. |
| Publication | Upload 14905 completed six-file staging. Full HTTPS reads matched all four versioned artifacts and both staged files. Stable installer promotion 14906 preceded live feed promotion 14907. Final full HTTPS reads matched the live installer and feed. |
| Independent review | Source/artifact, installed/preservation and final public-readback review passed with no required repairs. The release owner verified the remote annotated tag points to the exact candidate. |

## Publication

Exact source, build, artifact, notarization and publication identities are retained
in the adjacent [receipt.json](receipt.json). Versioned artifacts and staged files
matched their accepted local hashes before the installer and then `latest-mac.yml`
were promoted. Final live installer/feed hashes matched afterward.

The public installer is
[stackos-latest-mac-arm64.dmg](https://stackos.flowmonkey.io/StackOS/stackos-latest-mac-arm64.dmg),
with the [update feed](https://stackos.flowmonkey.io/StackOS/latest-mac.yml).
The feed advertises 2.1.47.

## Evidence limits and custody

- There were no active automatic workers or upgrade checkpoints during this
  installation. This occurrence proves native installation and maintenance
  completion; it does not provide fresh evidence of live-worker resumption.
- The disposable setup test exercised preparation and reached verification; its
  report used one incorrect role identifier. The release clarifies the existing
  exact-ID contract and retains focused regression coverage. No fresh successful
  update-mode behavior is claimed from that test.
- The local updater source is a newly signed current-code 2.1.46 fixture. It is
  not a claim that the previously published 2.1.46 runtime was installed for this
  proof. It requires no second public release build or extra notarization.
- The user performed update/install/relaunch. Native logs and automated
  postflight establish the installed result; no agent-driven visual walkthrough
  or second normal quit/reopen is claimed. The temporary local update feed was
  stopped after the installed production URL was verified.
- The local test feed did not support HTTP ranges, so differential download
  fell back to a successful full ZIP download. This occurrence does not verify
  production differential-download behavior.
- Telegram configuration differences were limited to runtime generation and
  update timestamps. An exact baseline Claude backup showed only six cache
  fields changed; OAuth identity, settings and MCP configuration were unchanged.
  Current Grok identity matches an unchanged baseline saved profile, with only
  token/time fields differing from that profile. The pre-update global Grok file
  was retained as a hash only, so an exact before/after field-level comparison
  of that global file is not claimed. No blanket native-auth byte identity is
  asserted.
- Previous public artifacts and feed remain available for rollback. Replacing
  the app alone does not roll back project data; recovery remains explicit.
- Detailed command receipts are under `/private/tmp/stackos-release-2.1.47`;
  accepted runtime signoff is under `/private/tmp/stackos-tracked-setup-911`.
  Scratch evidence can expire. Final committed receipts must retain public
  source/build/artifact identities and actual outcomes, excluding credentials,
  private database contents and state-preservation hashes.

This evidence-only report does not change the shipped payload or move the release
tag.
