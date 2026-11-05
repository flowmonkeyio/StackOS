# StackOS 2.1.40 release

Published on 2026-10-28 through the production HTTPS download and update channel.
The signed installed app was updated from 2.1.39 to 2.1.40 and reopened normally.

- Source: `958f554223a438b404e72c67a568d573f4653ae5`; pushed tag `v2.1.40`.
- Payload build: `20261028T164835Z`.
- Engineering: run plan 718 / run 712; publication: run plan 721 / run 715.
- [Download](https://stackos.flowmonkey.io/StackOS/stackos-latest-mac-arm64.dmg)
  and [update feed](https://stackos.flowmonkey.io/StackOS/latest-mac.yml).

## Delivered behavior

The sidebar separates app-wide setup, accounts and backups from project work.
Native backup setup saves a portable recovery key, protects a convenience copy,
and creates the first archive. Existing backup settings and recipients are
preserved. The selected Backups design provides stable status, file exports and
a Manage menu; recovery verifies into a separate inert folder.

The [source verification](../2026-10-28-streamlined-backups/verification.md)
contains the behavioral, native integration and visual evidence, including
5,459 Python tests and 752 final UI tests. The release version delta was
independently reviewed without repeating unchanged broad suites.

## Release proof

The strict canonical release build completed with signing and notarization
required. It reused the existing Apple API-key configuration; no password,
credential replacement or notarization bypass was needed. The earlier missing
keychain-profile diagnostic checked the wrong authentication method and remains
preserved as history in [the resolution receipt](notarization-auth-resolution.json).

Independent review verified all 533 first-party StackOS files, 89 UI assets,
101 bundled plugin/skill assets and 9 Electron source files against the release
commit. The app, ZIP-contained app, DMG and DMG-contained app passed the applicable
signature, Gatekeeper and staple checks. The stable DMG alias is byte-identical
to the signed, notarized versioned installer. See
[package review](review-package-content.json), [ZIP review](review-app-zip.json)
and [final artifact/lifecycle review](review-final-artifacts.json).

The installed updater discovered and downloaded the exact accepted ZIP, displayed
the ready-to-install prompt, and completed the 2.1.39 to 2.1.40 update. The new
app, payload, install key and daemon agree on version/build identity. Packaged
repair and restart passed, launchd owns the installed daemon, and Doctor passed
all 27 checks. Normal relaunch displayed the new App/Backups navigation. Temporary
feed/debug ports were closed. See [installed lifecycle](evidence/installed-lifecycle.json).

Existing project/account/credential identities, seed, daemon token, backup policy
and key exclusions were preserved. A one-off encrypted pre-upgrade archive reused
the existing portable recovery key and was successfully restored into a private
inert folder for verification. The temporary plaintext copy was then removed;
the encrypted archive and rollback app were retained. No backup setup, recipient
or schedule was changed for release testing.

All six staged/versioned files were downloaded completely over public HTTPS and
matched their accepted byte counts and SHA-256 hashes. The stable alias was
promoted first and the feed last. Final HTTPS readback confirmed the exact new
feed and alias. All five provider actions completed successfully, with no failed,
skipped or uncertain outcomes. See [artifact hashes](evidence/artifacts.json),
[staged verification](evidence/public-staged.json),
[final channel verification](evidence/public-channel.json) and
[publication audit](evidence/publication.json). The
[independent publication review](review-publication.json) accepted the public
release and the complete staged evidence set with no unresolved blockers.

## Recovery and known limitation

Previous 2.1.39 versioned artifacts and its captured feed remain available for
rollback. The local signed rollback app and verified encrypted pre-upgrade
archive are retained. App replacement does not roll back data; recover into an
inert folder and verify it before any explicit state adoption.

The deliberately missing-feed test used synthetic data and a disabled installer.
Its visible error dialog was expected, and fixture hashes remained unchanged.
The existing native error body still contains verbose upstream diagnostics; this
release does not claim to improve that presentation. The test was announced,
closed, and the normal app restored. See [process notes](evidence/release-process-notes.json).

The [evidence index](evidence/index.json) records copied receipt hashes. Private
identity snapshots, raw diagnostics and real project UI captures remain local;
reviewed summaries are included here.
