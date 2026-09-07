# StackOS 2.1.23 — release receipt

Published September 6, 2026 local / September 7 UTC, under the operator's explicit
website and signed/notarized app deployment request. No install, local app launch,
daemon restart, commit, or push was performed.

## Delivered

- Website: https://stackos.flowmonkey.io/library/articles/managing-a-solo-agency-with-ai-agents/
- Stable app: https://stackos.flowmonkey.io/StackOS/stackos-latest-mac-arm64.dmg
- Update feed: https://stackos.flowmonkey.io/StackOS/latest-mac.yml — 2.1.23.
- Release changes are in CHANGELOG.md; package versions and uv.lock agree.
- Canonical editable article: website/content/articles/managing-a-solo-agency-with-ai-agents.md.
  The earlier report is an approval snapshot, not a second editable master.

## Signoff

- `make signoff`: passed, including lint, typecheck, connector/backend tests,
  UI tests and UI build. One agency test file received formatting only.
- Agency plugin/setup focused suite: 16 passed.
- `pnpm --dir desktop check`: passed.
- Release preflight validated the saved signing identity and notarization profile.
- `dist:mac:release`: passed, including packaged CLI checks and final metadata refresh.
- Strict deep codesign verification passed; app staple validated; DMG Gatekeeper
  assessment returned `accepted`, `Notarized Developer ID`.
- Apple accepted app submission `3b2f30c2-4f67-476c-83a3-1bfbaeb1e77e` and DMG
  submission `3d0f8d4c-567c-482b-91bf-0513f7590d6c`; DMG staple validated.
- Feed SHA512 values and sizes match local ZIP/DMG; stable DMG is byte-identical
  to the versioned notarized DMG.
- Website content sync, typecheck, static generation and SEO checks passed.
  Nonfatal Vue plugin/build warnings were not expanded into unrelated repair work.
- Public article/canonical/finance links passed. Sitemap matches the generated
  183-URL output. Public ZIP, DMG and stable alias return HTTP 200 with expected sizes.
- Live update feed matches the verified local feed exactly. Both public blockmaps
  match local bytes. An initial HEAD-size assertion was unsuitable for Brotli
  responses; the bounded GET comparison passed, with no artifact repair needed.

## Publication audit

All transfers used the existing daemon-held FTP account; no credentials were
exported. The reused execution context names some response files `2.1.18`, but
their action requests and completed paths explicitly identify release 2.1.23.

- Website action 8987: 749 completed, zero failed, 25,581,097 bytes.
- ZIP/blockmap action 8988: 2 completed, zero failed, 361,069,999 bytes.
- DMG/blockmap/stable alias action 8989: 3 completed, zero failed, 726,040,253 bytes.
- Feed action 8990: 1 completed, zero failed; switched last after artifact checks.
- Article recovery index: content-piece 996 / artifact 2142; independently read back.
- Existing ownership files, prior app versions and unrelated remote files preserved.

## Rollback and boundary

The previous versioned 2.1.22 ZIP and DMG remain at the same update endpoint.
An operator-authorized distribution rollback can restore the stable DMG from
`stackos-2.1.22-mac-arm64.dmg` and restore 2.1.22 feed metadata. This does not
automatically downgrade clients already on 2.1.23; a corrective higher version
is the normal updater path. No rollback was performed.

This is build/package/signing/notarization/publication proof, not a new installed
app upgrade/relaunch rehearsal or production-finance activation. The installed
app and other active agents were left undisturbed.
