# StackOS 2.1.41 release verification

**Public final verification: passed.** StackOS 2.1.41 was published through
the production HTTPS download and update channel on 2026-10-29 UTC
(2026-10-28 Pacific). Packaging, installed verification, all five publication
actions and complete public artifact readback passed.

- Source: `b78a7c39b2f35293597575b57d8b3f7f84c786a8`.
- Pushed tag: `v2.1.41`.
- Payload build: `20261029T025156Z`.
- Distribution: signed and notarized macOS Apple Silicon (`arm64`) DMG and ZIP.
- Target channel: `https://stackos.flowmonkey.io/StackOS/`.

## Delivered behavior

Work presents tasks in seven fixed status columns with canonical status icons,
readable card widths, owner initials, workflow/source chips, creation dates and
real completed-ticket counts. Lifecycle remains agent-controlled; cards open
inspection rather than supporting state changes or drag-and-drop.

Board and graph navigation retain separate task identities. Graph entry selects
a valid task, while node clicks retain graph-local selection and explicit detail
inspection. Task and ticket inspection share a stable centered popup, with a
bounded scrolling body, six visible ticket rows, retained drafts and reading
position, and intentional Back/close focus restoration.

The earlier Work delivery added bounded human description edits and append-only
additional input through `tracker.updateTaskContent`. These persist in the existing
task description and `context_json.operator_inputs`, with revision, conflict and
retry protection. They do not change lifecycle or automatically invoke an agent.
The final board-card refinement changes presentation using existing metadata.

Source and built-preview behavior, light/dark comparisons, desktop overflow and
keyboard checks were recorded at the local, uncommitted evidence path
`reports/design/2026-10-28-work-navigation/board-polish/design-qa.md`.
That historical folder is not part of this release's committed evidence. The integrated candidate
`04fddeae242325e5d82d9dc34b259d5bccd33772df435c5bc442e546dcc368eb`
passed `make signoff`, including Python lint/types/tests, UI tests and production
build. The release preparation review accepted the subsequent version metadata
delta without repeating unchanged broad suites.

## Package verification

The strict canonical release build passed with signing and notarization required,
using the existing Apple API-key configuration and Developer ID identity. No new
credentials, signing bypass or notarization bypass was needed.

Independent reviews matched packaged source and assets to the release commit,
verified the app and ZIP, checked the DMG contents and trust evidence, and accepted
the final metadata. The stable DMG alias is byte-identical to the notarized
versioned DMG. The generated feed, ZIP/DMG sizes and hashes, and both blockmaps
agree with the accepted artifact set.

The release receipts include [artifact hashes](evidence/artifacts.json),
[package content](evidence/review-package-content.json),
[app/ZIP review](evidence/review-app-zip.json),
[final metadata](evidence/review-final-metadata.json), and
[final artifact review](evidence/review-final-artifacts.json).

## Installed update and preservation

The existing signed installed 2.1.40 app discovered and downloaded the exact
accepted 2.1.41 ZIP through a loopback test feed. The ready-to-install prompt was
observed, and the updater replaced the signed app. `/Applications/StackOS.app`
was then explicitly reopened normally; startup prepared the
matching payload and daemon. **Automatic relaunch was not demonstrated.**

Packaged repair, restart and signature checks passed. Doctor passed all 27 checks.
The installed app, payload, install key and daemon agree on 2.1.41 and build
`20261029T025156Z`. Launchd owns the installed daemon, one normal app process
remains, the production update URL is restored, and the temporary feed/debug
ports are closed.

Independent review found exact equality of the recorded project, credential,
account-link and attachment identity sets. Seed, daemon token, backup policy,
key exclusions and schema fingerprints are unchanged. This verifies those
preservation boundaries rather than byte equality of the entire live database.

A fresh encrypted pre-upgrade archive reused the existing recovery key and was
successfully restored into a separate inert folder. The plaintext verification
folder was removed; the encrypted archive and 2.1.40 rollback app remain local.
No backup configuration or key rotation was performed.

See [installed lifecycle](evidence/installed-lifecycle.json),
[independent lifecycle review](evidence/review-installed-lifecycle.json),
[state preservation](evidence/state-preservation-final.json), and
[backup verification](evidence/preupgrade-backup.json) receipts.

## Visual evidence and limits

The normal native StackOS window was observed. OS screenshot capture was
unavailable after bounded attempts, and the original window geometry was
restored. The actual installed-daemon Work Board and task popup were captured
through the authorized StackOS browser and inspected, with no browser console
errors. These establish installed-daemon rendering and native window presence
as separate observations; they do not establish captured native-window pixels
after normal reopen.

An initial splash data-URL `ERR_ABORTED` diagnostic was observed. Subsequent
download, update-downloaded, installed startup and verification succeeded; this
release does not claim to repair that transient diagnostic. The updater's
nonmetadata Electron sources are unchanged from 2.1.40. No new negative-feed
test was run or claimed. The [observation receipt](evidence/installed-ui-observed.json)
retains these limits.

## Public channel

Staging upload actions `12965`, `12966` and `12967` succeeded. All six complete
staged/versioned files were downloaded over public HTTPS and matched the accepted
byte counts and SHA-256 hashes. Immediately before promotion, the live feed was
confirmed byte-identical to the captured 2.1.40 feed.

Action `12968` promoted the stable DMG alias at `2026-10-29T03:46:45Z`;
action `12969` promoted `latest-mac.yml` at `2026-10-29T03:46:59Z`. Final HTTPS
verification completed at `2026-10-29T03:47:54Z`, downloading the entire
485,555,719-byte stable DMG and exact 513-byte feed. Both matched the accepted
release hashes. All five provider actions succeeded, with no failed, skipped or
uncertain outcomes. The pushed `v2.1.41` tag was remotely verified against
`b78a7c39b2f35293597575b57d8b3f7f84c786a8`.

The independent [publication review](evidence/review-publication.json) passed
with no required repairs.

See [staged verification](evidence/public-staged.json),
[channel verification](evidence/public-channel.json),
[publication audit](evidence/publication.json), and [remote tag proof](evidence/tag.json).
The stable [download](https://stackos.flowmonkey.io/StackOS/stackos-latest-mac-arm64.dmg)
and [update feed](https://stackos.flowmonkey.io/StackOS/latest-mac.yml) now serve
the verified 2.1.41 release.

Previous versioned artifacts and the captured prior feed are retained for
rollback. Replacing the app does not roll back data; any data recovery remains a
separate, explicit operation using a verified inert restore.

The bounded [evidence index](evidence/index.json) catalogs hashes for the curated
safe receipts. Private state snapshots, raw
diagnostics, actual-project UI images, backup archives, recovery keys and raw
provider response files remain local.
