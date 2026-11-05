# Independent artifact review — StackOS 2.1.31

**Final recommendation: PASS for publication of the six exact artifacts in
manifest `a613f8e38348645aa7533c6e2faed2f7e033146a0cd75f4f0c5706cf990e8d89`.**
No unresolved source, artifact-identity or macOS trust finding remains in this
review. Main owns publication and public readback; user installation remains
separate.

## App review: pass

Assignment `release-artifact-review-2.1.31-attempt-1`, run plan 488 / execution
483. Reviewer `/root/artifact_uri_check` applies the current project-adapted
delivery-reviewer v0.3.1 contract. The reviewer did not implement, consolidate
or build this release. Main authorized app checks after app signing and Apple
notarization completed, while DMG packaging continued.

Reviewed app:
`desktop/dist/mac-arm64/StackOS.app`.

| Check | Result |
| --- | --- |
| Accepted feature inclusion | All **15** packaged runtime/plugin files match the exact accepted `integration-audit-repair-r4-1.json` after hashes. |
| Current packaged guidance | All **10** protected plugin/guidance files match the preserved current checkout, including branding method and the phase 1 source-owned workflow prose. |
| App/payload version | Both Info.plist version values and payload build-info equal **2.1.31**. Payload build ID: `20260930T041843Z`. Packaged `stackos/__init__.py` equals current source. The separate source review checked all four version owners. |
| `codesign --verify --deep --strict --verbose=2` | Exit 0: valid on disk and satisfies its Designated Requirement. |
| `spctl --assess --type execute --verbose=2` | Exit 0: accepted, `source=Notarized Developer ID`. |
| `xcrun stapler validate` | Exit 0: validate action worked. |
| Apple submission | Root's retained `notarytool info` result reports **Accepted** for `b7285569-3492-4da4-8fc0-ba3b311fd2ee`, created `2026-09-30T04:25:46.432Z`. |

Exact file identities are retained in [independent-app-source-checks.json](independent-app-source-checks.json),
SHA256 `6e1523e40a37e6f2eb626cbaf0dea41925573077a340e121f9c400033f9980bc`.
Apple receipt:
`/private/tmp/stackos-release-2.1.31/app-notarization-info.log` and its command
result file. Verification ran directly against the final app with host macOS
signing services. Initial sandboxed invocations failed with signing subsystem
or app-open errors; the authorized host-access checks above resolved those
failures without changing the app.

No test suite, packaged CLI/app launch, install, restart, source edit,
publication or native lifecycle write was performed. Only review evidence was
written.

## Final distribution artifacts: pass

Final checks started only after main reported the canonical release build had
completed. Its retained result records `pnpm --dir desktop run dist:mac:release`,
exit **0**, elapsed **1,488.36 seconds**. Independent file/metadata inspection
completed with **230 checks passed and zero failures**. No test suite was run.

| Check | Result |
| --- | --- |
| Six-artifact manifest | All six actual files match independently computed size, SHA256, SHA512 and base64 SHA512. Manifest SHA256 matches the main-agent handoff. |
| Updater feed | Version 2.1.31; primary download is the versioned ZIP. Both ZIP and DMG sizes/checksums equal final actual bytes. |
| Stable website download | `stackos-latest-mac-arm64.dmg` equals the final notarized versioned DMG by size and both hashes. |
| Updater ZIP inclusion | The same 15 accepted feature files and 10 protected plugin/guidance files inside the ZIP match their expected hashes. Archived app Info.plist and payload build-info exactly equal the reviewed app. |
| Final source freeze | All 38 accepted feature files, 71 protected files and five release metadata files remain at their reviewed hashes. The packaged app's 25 checked feature/guidance files also remain unchanged. |
| DMG signature | Host `codesign --verify --strict --verbose=2`: exit 0, valid on disk and satisfies Designated Requirement. |
| DMG Gatekeeper | Host `spctl --assess --type open --context context:primary-signature --verbose=2`: exit 0, accepted as Notarized Developer ID. |
| DMG staple | Host `xcrun stapler validate`: exit 0, validate action worked. |
| DMG integrity | Host `hdiutil verify`: exit 0, disk-image checksum valid. The image was not mounted. |
| Apple DMG submission | Retained `notarytool info` reports **Accepted** for `30f610e5-0d0e-495d-9312-efb2c05ced8b`, created `2026-09-30T04:34:36.867Z`. |
| Prior local versioned artifacts | All 48 retain their captured size and nanosecond modification time. The baseline did not contain content hashes, so this is a metadata preservation check. |

### Final identities and evidence

| Artifact | SHA256 |
| --- | --- |
| `stackos-2.1.31-mac-arm64.zip` | `2efd67d04e583b19971bc026dee7a4a95da06d782b55047306e15e5f3bdc88d9` |
| `stackos-2.1.31-mac-arm64.dmg` and stable alias | `f165807ce78f81a96fb81c23d570b04b2f72e79944e0744cf710a96d3da87feb` |
| `stackos-2.1.31-mac-arm64.zip.blockmap` | `e7f7bfd8f876d7bc5de25eee604bb4c685efff76b607c767dd32a0e3bcb25222` |
| `stackos-2.1.31-mac-arm64.dmg.blockmap` | `c88b1cef925090e01b8c9dcbb0ec9daaee4b7fcbe83779e45d70b0624df1a3f3` |
| `latest-mac.yml` | `6890ae43526f0f0bcab4dc872c2616b895ea151ad6e97d77f0e271b422a471f9` |

- [Independent final file checks](independent-final-artifact-checks.json), SHA256
  `a3c578930eb16f5c493c89e2eb18424f30ad415dbaf2d1d7e1ab5db2e6501413`.
- [Independent DMG trust command results](independent-dmg-trust-checks.json).
- Main manifest, build result/log and Apple submission info remain under
  `/private/tmp/stackos-release-2.1.31/`; the independent file checks bind the
  manifest's exact digest.

This is local publication readiness evidence. It does not claim public upload,
successful installation, automatic update/relaunch or a live Google action.
