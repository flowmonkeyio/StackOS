# StackOS Desktop Distribution

The macOS desktop app is named `StackOS`. It wraps the existing local daemon
and UI instead of replacing them.

## Architecture

The desktop app has three layers:

1. Electron shell in `desktop/`.
2. Packaged StackOS Python payload in `desktop/payload/stackos/`.
3. Existing loopback daemon and UI at `http://127.0.0.1:5180/`.

Electron owns app launch, menus, service status, repair commands, and update
checks. The Python daemon still owns API/MCP, provider credentials, database
migrations, the committed UI bundle, plugin/skill assets, browser automation,
and audit.

The app resolves the `stackos` CLI in this order:

1. `STACKOS_DESKTOP_CLI`
2. packaged `resources/stackos/bin/stackos`
3. repository `.venv/bin/python -m stackos`
4. `stackos` on `PATH`

## Build Commands

Install desktop dependencies:

```bash
make desktop-install
```

Run the Electron shell from the repo:

```bash
make desktop-dev
```

Build the Python payload for the app resources:

```bash
make desktop-payload
```

Build macOS artifacts:

```bash
STACKOS_UPDATE_URL="https://updates.example.com/stackos/macos" make desktop-dist
```

`STACKOS_UPDATE_URL` is optional for local packaging, but release builds should
set it so electron-builder emits generic-provider update metadata for the
custom endpoint.

Local desktop builds intentionally do not require Apple signing or notarization
parameters. A release-intent build is detected when `STACKOS_REQUIRE_SIGNING=1`
is set or when `STACKOS_UPDATE_URL` points at a non-localhost HTTPS endpoint.
Release-intent builds require signing and notarization inputs unless an
operator explicitly sets `STACKOS_ALLOW_UNSIGNED_RELEASE=1` for a non-release
smoke. The release wrapper signs the generated DMGs, notarizes the app bundles
through electron-builder, then notarizes and staples the current-version DMGs.

## Signing and Notarization Environment

Do not commit Apple credentials or paste them into agent chat. Pass them through
the shell environment, a local secret manager, or CI secrets.

Signing accepts one of these inputs:

```bash
# Local keychain identity.
export CSC_NAME="Example Org (ABCDE12345)"

# Or CI/imported certificate.
export CSC_LINK="/absolute/path/to/developer-id-application.p12"
export CSC_KEY_PASSWORD="..."

# Or deliberate local auto-discovery for release smoke only.
export STACKOS_ALLOW_SIGNING_AUTO_DISCOVERY=1
```

Notarization accepts one of these methods. App Store Connect API key is preferred:

```bash
export APPLE_API_KEY="/absolute/path/to/AuthKey_XXXXXXXXXX.p8"
export APPLE_API_KEY_ID="XXXXXXXXXX"
export APPLE_API_ISSUER="00000000-0000-0000-0000-000000000000"
```

Apple ID app-specific password is also supported:

```bash
export APPLE_ID="developer@example.com"
export APPLE_APP_SPECIFIC_PASSWORD="xxxx-xxxx-xxxx-xxxx"
export APPLE_TEAM_ID="ABCDE12345"
```

Or store credentials in the macOS keychain with `xcrun notarytool
store-credentials`, then pass the profile:

```bash
export APPLE_KEYCHAIN_PROFILE="stackos-notary"
export APPLE_KEYCHAIN="login.keychain"
```

For a strict release dry-run that validates the env contract without invoking
electron-builder:

```bash
STACKOS_DESKTOP_BUILD_DRY_RUN=1 \
STACKOS_REQUIRE_SIGNING=1 \
node desktop/scripts/build-mac.mjs
```

For an unsigned local smoke where release metadata should still be generated,
make the bypass explicit:

```bash
STACKOS_ALLOW_UNSIGNED_RELEASE=1 \
STACKOS_SKIP_NOTARIZATION=1 \
STACKOS_UPDATE_URL="https://updates.example.com/stackos/macos" \
make desktop-dist
```

That bypass is not valid public release evidence.

## Installer Flow

The macOS app is distributed as a DMG and ZIP from electron-builder. On first
launch for an app version or a newly built packaged payload, the shell runs:

```bash
stackos install --launchd --force
stackos restart
```

That keeps one installer contract. The desktop shell passes `--force` only for
the app-managed launchd plist repair path, so replacing an older clone-mode
plist with the packaged-app plist does not interrupt first launch. `stackos
install` remains idempotent and
creates or repairs local state, migrations, plugin and skill mirrors, MCP
registration, Playwright Chromium runtime setup, and launchd autostart. It does not rotate
`auth.token` or `seed.bin`.

Generated payloads are ignored by git. `desktop/scripts/build-stackos-payload.sh`
builds a wheel, installs it into `desktop/payload/stackos/.venv`, writes
`build-info.json`, and writes a small `bin/stackos` wrapper that the packaged
Electron app can run. The app records a composite install key from its app
version plus packaged payload build info, so replacing a locally built app with
the same public version still reruns install/repair once.

The macOS bundle icon is generated from the high-resolution desktop PNG:
`desktop/assets/stackos-icon.png`. `desktop/scripts/build-icons.mjs` builds
`desktop/assets/stackos-icon.icns` from that PNG for electron-builder. Keep
`ui/public/favicon.png` visually aligned with the desktop PNG; `desktop doctor`
checks both assets are present.

## Update Flow

The app uses a custom generic update endpoint through `electron-updater`'s
generic provider. The endpoint is a static HTTPS URL prefix served by the
website, for example:

```text
https://example.com/stackos/macos/stable/
```

FTP or another deploy tool may upload files into that website directory later,
but FTP is release infrastructure only. The desktop app never stores FTP
credentials and never speaks FTP; installed apps read update metadata and
artifacts over HTTPS.

At runtime, the endpoint comes from:

1. `STACKOS_UPDATE_URL`
2. packaged `resources/update-config.json`
3. development `desktop/update-config.json`

Release builds should set `STACKOS_UPDATE_URL` so `desktop/scripts/build-mac.mjs`
packages `resources/update-config.json` and asks electron-builder to emit the
generic-provider metadata. Production endpoints must use HTTPS. Local update
smokes may use `http://127.0.0.1:<port>/...` or `http://localhost:<port>/...`.
StackOS uses the public `electron-updater` event lifecycle: check emits
availability, download emits progress, `update-downloaded` is the only
install-ready signal, and install calls `quitAndInstall()` after that event.

The endpoint should serve the generated electron-updater metadata and artifacts,
for example:

```text
https://example.com/stackos/macos/stable/latest-mac.yml
https://example.com/stackos/macos/stable/stackos-1.0.1-mac-arm64.zip
https://example.com/stackos/macos/stable/stackos-1.0.1-mac-arm64.dmg
https://example.com/stackos/macos/stable/stackos-1.0.1-mac-arm64.zip.blockmap
```

After an app update, the next launch sees a new app version or new packaged
payload build id and reruns `stackos install --launchd --force`, then restarts
the daemon so the running process uses the newly packaged code. Local DB,
generated assets, `seed.bin`, `auth.token`, and provider credentials remain in
the existing user-local StackOS state paths.

## Release Inputs

Release-grade macOS builds still need operator-supplied values:

- Apple Developer Team ID
- signing identity or certificate setup
- notarization credentials
- production update endpoint URL
- hosted `latest-mac.yml`, DMG, ZIP, and generated blockmap artifacts
- release notes and rollback instructions

macOS auto-update install/relaunch cannot be signed off from an unsigned build.
Unsigned local builds are useful for feed, discovery, download, and UI smoke
coverage only; the final install/relaunch gate must run from a signed installed
app copied out of the DMG.

Keep the Python package version, `stackos/__init__.py`, and
`desktop/package.json` synchronized for release updates. `electron-updater`
advertises new app versions from the desktop package metadata; the packaged
payload build info then decides whether the post-update install/repair path
needs to run even when a local test reuses the same public app version.

Do not commit these values. Pass them through the build environment or the
release system.

## Verification

Scaffold checks that do not require Electron dependencies:

```bash
make desktop-doctor
pnpm --dir desktop check
```

Release checks, once dependencies and signing are configured:

```bash
make desktop-payload
STACKOS_UPDATE_URL="https://updates.example.com/stackos/macos" make desktop-dist
```

Build-config smoke without invoking electron-builder:

```bash
STACKOS_DESKTOP_BUILD_DRY_RUN=1 \
STACKOS_UPDATE_URL="http://127.0.0.1:8765/stackos/macos" \
node desktop/scripts/build-mac.mjs
```

Strict release-config smoke:

```bash
STACKOS_DESKTOP_BUILD_DRY_RUN=1 \
STACKOS_REQUIRE_SIGNING=1 \
CSC_NAME="Example Org (ABCDE12345)" \
APPLE_API_KEY="/absolute/path/to/AuthKey_XXXXXXXXXX.p8" \
APPLE_API_KEY_ID="XXXXXXXXXX" \
APPLE_API_ISSUER="00000000-0000-0000-0000-000000000000" \
node desktop/scripts/build-mac.mjs
```

After building signed artifacts, verify signatures and stapling before sharing
outside the build machine:

```bash
codesign --verify --deep --strict --verbose=2 desktop/dist/mac/StackOS.app
spctl --assess --type open --context context:primary-signature --verbose desktop/dist/stackos-<version>-mac-arm64.dmg
xcrun stapler validate desktop/dist/stackos-<version>-mac-arm64.dmg
```

Manual release smoke should install the DMG, launch the installed app, confirm
the UI loads from `127.0.0.1:5180`, run Doctor from the app menu, and verify
update metadata is reachable from the custom endpoint. Do not run the update
smoke from the mounted DMG volume: Squirrel.Mac cannot update an app from a
read-only volume, so the app must be copied to `/Applications` or another
writable app location before testing download/install.

Local update-channel smoke before website hosting exists:

1. Build or keep an older installed `StackOS.app` in `/Applications` or another
   writable app location. Do not launch it from the mounted DMG.
2. Build a newer desktop artifact with
   `STACKOS_UPDATE_URL="http://127.0.0.1:8765/stackos/macos" make desktop-dist`.
3. Serve the generated metadata and artifacts from a local static server rooted
   at the directory that contains `stackos/macos/latest-mac.yml`.
4. Launch the installed older app with the same `STACKOS_UPDATE_URL` override
   or with a packaged update config pointed at the local URL.
5. Check for updates and download. Install/relaunch is a valid pass only when
   the app is signed; unsigned local apps may stop after download/UI evidence.
6. Confirm the UI loads from `127.0.0.1:5180`, Doctor passes, and
   `~/.local/share/stackos/stackos.db`, `~/.local/state/stackos/seed.bin`, and
   `~/.local/state/stackos/auth.token` still exist and were not rotated.

For a failure smoke, point `STACKOS_UPDATE_URL` at a missing or malformed local
feed. The app should show a readable update error and leave local StackOS state
unchanged.

For a cold-start smoke of the packaged daemon path, run `stackos stop` before
launching the desktop app. The app should run its packaged `stackos install
--launchd --force`, start the daemon through `stackos restart`, and then load
the UI.
