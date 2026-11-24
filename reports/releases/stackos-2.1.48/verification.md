# StackOS 2.1.48 release

Published on 2026-11-24 with explicit operator authorization. Source, final
artifacts, Apple trust and public HTTPS readback are verified. Independent
artifact and final release reviews passed with no required repairs.

Candidate `c2a4979adcc0014af9bfc6ce0fe344c4bf879b28` contains ten commits since
the previous release source. Main is pushed, and remote annotated tag `v2.1.48`
resolves to that exact candidate. The signed public build is
`20261124T182416Z`; the separately installed unsigned local build remains
`20261124T180722Z`.

This release makes Home reads smaller and cheaper: delivery statistics use a
covering scalar revision index, Home connection status avoids model-list probes,
and Project Home reads a bounded setup summary. It also repairs recovery of a
clean committed candidate returned by blocked delivery. The existing retry flow
retains and validates that candidate, including older receipts missing the
canonical SHA, while preserving sessions, original base, history, repair allowance
and independent review.

## Verification

| Boundary | Current result |
| --- | --- |
| Source | Exact candidate passed `make signoff`: 9,275 Python tests, 4 skipped, 1,902 UI tests, lint, formatting, types and UI production build. Desktop checks passed. |
| Earlier gate failure | The unrelated timeline fixture now observes stream-owned connection cleanup rather than an instantaneous shared-pool total. The complete final gate passed; prior failure evidence remains retained. |
| Local package and installation | Unsigned 2.1.48 build `20261124T180722Z` was installed. Health and Doctor passed; managed upgrade continued and is unfenced with no recovery required. Installed recovery-module bytes match the reviewed source. |
| Installed recovery | Existing stopped work resumed the unchanged committed candidate through retained delivery and independent-review sessions. Current review accepted the child. The workflow subsequently passed verification, review and audit and reached active closeout; related workflows completed combined integration. No claim is made that the active closeout finished. |
| Preserved state | Project/account identities, encrypted credential/payload values, saved host-account identities, seed/token and 785 terminal receipts were retained. Telegram generation/timestamp changes and the Claude global-file limitation are qualified below. |
| Public package and Apple trust | One canonical release build passed. Independent review matched 110 source/UI/template entries across the app, ZIP and DMG, verified all six final artifact hashes and the production update URL, and passed signatures, staples, Gatekeeper and packaged CLI/TDLib checks. App and DMG notarization are Accepted. |
| Publication | Upload 15205 completed six-file staging. Full HTTPS reads matched the four versioned artifacts and both staged files. Installer promotion 15206 preceded feed promotion 15207. Final full HTTPS reads matched the live installer and feed. |
| Tag and final review | Remote annotated `v2.1.48` object `69f41c6ea21954a4e5a5378d6dda47ad45bbfe39` resolves to the exact source candidate. Independent artifact and final release reviews passed with no required repairs. |

## Publication

Exact source, build, artifact, notarization and publication identities are retained
in the adjacent [receipt.json](receipt.json). Versioned artifacts and staged files
matched their accepted local hashes before the installer and then
`latest-mac.yml` were promoted. Final live installer/feed hashes matched afterward.

The public installer is
[stackos-latest-mac-arm64.dmg](https://stackos.flowmonkey.io/StackOS/stackos-latest-mac-arm64.dmg),
with the [update feed](https://stackos.flowmonkey.io/StackOS/latest-mac.yml).
The feed advertises 2.1.48. Previous versioned artifacts remain available.

## Evidence limits and custody

- Local activation used an unsigned build from the accepted source. It establishes
  installed payload behavior; it is not an installation of the final signed and
  notarized public artifact. No further local restart or replacement was performed
  for publication.
- No fresh signed 2.1.48 native updater occurrence was performed. The updater
  controller, build wrapper, signing hook, artifact-alias logic and notarized
  metadata-refresh sources are unchanged from 2.1.47. Prior signed native
  download/install/relaunch proof is reused only for those unchanged mechanics;
  the current local installation separately exercises the new payload and
  migration. This does not turn an unsigned occurrence into signed-update proof.
- Migration 0053 adds the covering `ix_tracker_revisions_delivery_stats` expression
  index. It preserves retained revision/receipt JSON. A consistent private
  preinstall SQLite backup passed integrity checking; restoring it remains a
  separately authorized recovery action.
- Two Telegram configurations changed only their generation and update-time
  fields, established by private comparison against that backup. Encrypted values
  and the remaining configuration fields were unchanged.
- The global Claude configuration was captured as a hash only. Its exact
  field-level difference and cause cannot be established. Current stable identity
  matches the unchanged saved profile. Neither a cache-only difference nor blanket
  preservation of native settings is claimed. Private credential values and
  identity hashes are excluded from this report.
- No fresh computer-use visual walkthrough is claimed. Related combined
  integration completed, but the recovered workflow's closeout was still active
  in the recorded release-owner snapshot.
- Optional connector `direct_url.json` metadata was not present in the
  package. Connector provenance is supported by the accepted immutable source/lock
  pins and canonical build log, with matching app/ZIP package metadata and records.
- Private logs and detailed receipts are under
  `/private/tmp/stackos-release-2.1.48`. Committed evidence retains public
  source/build/artifact identities and actual outcomes, excluding sensitive state
  snapshots. This evidence-only report does not change the shipped payload or
  move its release tag.
