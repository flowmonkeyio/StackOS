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
3. repository `.venv/bin/python -m stackos` in development only
4. `stackos` on `PATH` in development only

Packaged desktop builds fail closed if `resources/stackos/bin/stackos` is
missing or not executable. They must not fall back to a developer checkout or a
global `stackos` on `PATH`, because that hides broken release artifacts on the
build machine.

Current desktop distribution is **Apple Silicon (`arm64`) only**. Do not publish
an x64 DMG/ZIP until the build creates a separate x64 standalone Python payload
or a verified universal2 payload.

## Packaging Dependencies

Runtime users should not need Python, uv, Node, pnpm, Homebrew, Xcode, or a
source checkout. The DMG must contain the standalone StackOS Python payload.

Build machines need:

| Dependency | Purpose | Required for |
| --- | --- | --- |
| Node.js + pnpm | Electron build scripts and dependencies | all desktop builds |
| `uv` | Build the StackOS wheel and Python payload | all desktop builds |
| `bash` + `rsync` | Build/copy the standalone Python runtime | all desktop builds |
| `file`, `lipo`, `otool`, `codesign`, `security` | Inspect/thin/sign bundled Mach-O runtime files | all desktop builds |
| `xcrun notarytool` + `xcrun stapler` | Submit, staple, and validate notarization | public release |
| Developer ID Application certificate | Sign the app and DMG | signed/release builds |
| Apple notarization credentials | Apple malware/trust ticket for non-App-Store distribution | public release |

Run the preflight before public release packaging:

```bash
CSC_NAME="Example Org (ABCDE12345)" \
APPLE_KEYCHAIN_PROFILE="stackos-notary" \
STACKOS_UPDATE_URL="https://flowmonkey.io/StackOS/" \
pnpm --dir desktop run release:preflight
```

The preflight prints only whether required inputs are visible; it does not print
secret values. To also validate notarization credentials with Apple:

```bash
STACKOS_PREFLIGHT_VALIDATE_NOTARY=1 \
CSC_NAME="Example Org (ABCDE12345)" \
APPLE_KEYCHAIN_PROFILE="stackos-notary" \
STACKOS_UPDATE_URL="https://flowmonkey.io/StackOS/" \
pnpm --dir desktop run release:preflight
```

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

Build unsigned local development artifacts:

```bash
pnpm --dir desktop run dist:mac:dev
```

This command sets `STACKOS_UNSIGNED_DEV=1` and
`CSC_IDENTITY_AUTO_DISCOVERY=false`, so electron-builder writes `mac.identity`
to `null`, leaves the DMG unsigned, and disables notarization even on a machine
that has a Developer ID certificate in the keychain.

Build signed local artifacts without notarization:

```bash
pnpm --dir desktop run dist:mac:signed
```

This command sets `STACKOS_ALLOW_SIGNING_AUTO_DISCOVERY=1`, so it works on a
developer Mac when exactly one Developer ID Application identity is visible. Set
`CSC_NAME="Example Org (ABCDE12345)"` as well when multiple identities are
present or when CI should pin the signing identity.

### Current direct-distribution signing command

The current Developer ID identity installed for StackOS releases is:

```text
SERGEY RURA (TSHN26FR48)
```

Pass the identity without the `Developer ID Application:` prefix. For the
website-hosted static distribution that is signed but intentionally not
notarized, use:

```bash
CSC_NAME="SERGEY RURA (TSHN26FR48)" \
STACKOS_UPDATE_URL="https://flowmonkey.io/StackOS/" \
pnpm --dir desktop run dist:mac:signed
```

This produces the DMG, ZIP, blockmaps, and `latest-mac.yml` with Developer ID
signatures and the production update endpoint, while
`STACKOS_SKIP_NOTARIZATION=1` is supplied by the script. This is distinct from
an App Store submission. It is also distinct from a notarized direct download:
macOS Gatekeeper can still warn or reject a signed-but-not-notarized app on a
different Mac. When a notarytool credential profile is added, use
`dist:mac:release` for the smooth public-download path.

Every completed macOS build also refreshes
`stackos-latest-mac-arm64.dmg` from the finished versioned DMG. Release builds
create this alias only after notarization and stapling, so the stable website
download is byte-identical to the verified release artifact.

### One-time notarization profile setup

Notarization is an automated Apple security scan for Developer ID software
distributed outside the App Store; it is not an App Store submission. Create
an app-specific password for the release Apple ID, then run this command in a
local terminal. Do not put the password in the command or repository. When
prompted, enter the app-specific password securely:

```bash
xcrun notarytool store-credentials stackos-notary \
  --apple-id "YOUR_APPLE_ID" \
  --team-id "TSHN26FR48"
```

Validate the stored profile and all release inputs:

```bash
STACKOS_PREFLIGHT_VALIDATE_NOTARY=1 \
CSC_NAME="SERGEY RURA (TSHN26FR48)" \
APPLE_KEYCHAIN_PROFILE="stackos-notary" \
STACKOS_UPDATE_URL="https://flowmonkey.io/StackOS/" \
pnpm --dir desktop run release:preflight
```

Then build, submit, wait for acceptance, staple, and validate the public
release artifacts:

```bash
CSC_NAME="SERGEY RURA (TSHN26FR48)" \
APPLE_KEYCHAIN_PROFILE="stackos-notary" \
STACKOS_UPDATE_URL="https://flowmonkey.io/StackOS/" \
pnpm --dir desktop run dist:mac:release
```

Build signed and notarized release artifacts:

```bash
CSC_NAME="Example Org (ABCDE12345)" \
APPLE_KEYCHAIN_PROFILE="stackos-notary" \
STACKOS_UPDATE_URL="https://flowmonkey.io/StackOS/" \
pnpm --dir desktop run dist:mac:release
```

Public release builds require `CSC_NAME` and a non-localhost HTTPS
`STACKOS_UPDATE_URL`. `CSC_LINK` may still be supplied when CI imports a
certificate, but `CSC_NAME` pins the Developer ID identity used by the packaged
runtime signing hook. The update URL makes electron-builder emit
generic-provider update metadata for the custom endpoint.

Unsigned local development builds intentionally do not require Apple signing or
notarization parameters and deliberately disable signing auto-discovery. Signed
local builds require signing inputs and explicitly set
`STACKOS_SKIP_NOTARIZATION=1`; they are not public release evidence. Release
builds use `STACKOS_REQUIRE_SIGNING=1` and `STACKOS_REQUIRE_UPDATE_URL=1`, and
require `CSC_NAME`, notarization inputs, and update metadata. A
release-intent build is also detected when `STACKOS_UPDATE_URL` points at a
non-localhost HTTPS endpoint. Release-intent builds require signing and
notarization inputs unless an operator explicitly sets
`STACKOS_ALLOW_UNSIGNED_RELEASE=1` for a non-release smoke.

The release wrapper rebuilds the standalone Python payload, signs the generated
app bundle, signs the generated DMG, submits to Apple notarization, staples the
DMG ticket, validates the staple, and then regenerates the DMG blockmap and its
`latest-mac.yml` hash and size. That final metadata refresh is required because
stapling changes the DMG bytes after electron-builder first emits updater
metadata.

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

`CSC_NAME` is required for public release because StackOS signs the bundled
Python runtime in an `afterPack` hook before electron-builder performs the
final app signing and notarization. `CSC_LINK` is supported as a certificate
import mechanism when `CSC_NAME` also names the imported identity.
Auto-discovery is reserved for local signed smokes and only works when exactly
one Developer ID Application identity is visible to `security find-identity`
during the full Electron build.

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
xcrun notarytool store-credentials stackos-notary \
  --apple-id "developer@example.com" \
  --team-id "ABCDE12345" \
  --password "app-specific-password"

export APPLE_KEYCHAIN_PROFILE="stackos-notary"
export APPLE_KEYCHAIN="login.keychain"
```

For a strict release dry-run that validates the env contract without invoking
electron-builder:

```bash
STACKOS_DESKTOP_BUILD_DRY_RUN=1 \
STACKOS_REQUIRE_SIGNING=1 \
STACKOS_REQUIRE_UPDATE_URL=1 \
CSC_NAME="Example Org (ABCDE12345)" \
APPLE_KEYCHAIN_PROFILE="stackos-notary" \
STACKOS_UPDATE_URL="https://flowmonkey.io/StackOS/" \
node desktop/scripts/build-mac.mjs
```

For an unsigned local smoke where release metadata should still be generated,
make the bypass explicit:

```bash
STACKOS_ALLOW_UNSIGNED_RELEASE=1 \
STACKOS_UNSIGNED_DEV=1 \
STACKOS_SKIP_NOTARIZATION=1 \
CSC_IDENTITY_AUTO_DISCOVERY=false \
STACKOS_UPDATE_URL="https://flowmonkey.io/StackOS/" \
pnpm --dir desktop run dist:mac
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
Electron app can run. The payload vendors a standalone CPython runtime under
`.venv`, including the standard library, `lib-dynload`, `site-packages`,
`bin/python`, and `libpython*.dylib`. Desktop payload builds also install
Playwright Chromium into `ms-playwright` by default. The wrapper sets the
packaged `PYTHONHOME` and `PLAYWRIGHT_BROWSERS_PATH`, disables bytecode writes,
ignores user site packages, and clears ambient Python environment variables so
another Mac does not need uv, Python, Homebrew, the source checkout, or a
first-run browser download. The app records a composite install key from its
app version plus packaged payload build info, so replacing a locally built app
with the same public version still reruns install/repair once.

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
https://flowmonkey.io/StackOS/
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
https://flowmonkey.io/StackOS/latest-mac.yml
https://flowmonkey.io/StackOS/stackos-1.1.1-mac-arm64.zip
https://flowmonkey.io/StackOS/stackos-1.1.1-mac-arm64.dmg
https://flowmonkey.io/StackOS/stackos-1.1.1-mac-arm64.zip.blockmap
https://flowmonkey.io/StackOS/stackos-latest-mac-arm64.dmg
```

Set `STACKOS_UPDATE_URL` to the directory URL (`https://flowmonkey.io/StackOS/`),
not to `latest-mac.yml`, a DMG, a ZIP, or a blockmap file. Electron's macOS
generic updater checks `latest-mac.yml` at that base URL; the YAML then points
to the downloadable ZIP artifact. The DMG can live in the same directory for
manual download and drag-and-drop installation. The website should link to the
stable `stackos-latest-mac-arm64.dmg` alias; updater metadata must continue to
reference the versioned ZIP and checksums from the same release build.

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
CSC_NAME="Example Org (ABCDE12345)" \
APPLE_KEYCHAIN_PROFILE="stackos-notary" \
STACKOS_UPDATE_URL="https://flowmonkey.io/StackOS/" \
pnpm --dir desktop run release:preflight
CSC_NAME="Example Org (ABCDE12345)" \
APPLE_KEYCHAIN_PROFILE="stackos-notary" \
STACKOS_UPDATE_URL="https://flowmonkey.io/StackOS/" \
pnpm --dir desktop run dist:mac:release
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
codesign --verify --deep --strict --verbose=2 desktop/dist/mac-arm64/StackOS.app
spctl --assess --type open --context context:primary-signature --verbose desktop/dist/stackos-<version>-mac-arm64.dmg
xcrun stapler validate desktop/dist/stackos-<version>-mac-arm64.dmg
PYTHONHOME=/tmp/bad PYTHONPATH=/tmp/bad desktop/dist/mac-arm64/StackOS.app/Contents/Resources/stackos/bin/stackos --version
```

Standalone payload checks should also confirm no build-machine paths leak into
the app resources:

```bash
rg "/Users/|\\.local/share/uv|payload-build" desktop/dist/mac-arm64/StackOS.app/Contents/Resources/stackos
find desktop/dist/mac-arm64/StackOS.app/Contents/Resources/stackos/.venv -name '*.pyc' -o -name '__pycache__'
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
   `STACKOS_UPDATE_URL="http://127.0.0.1:8765/stackos/macos" pnpm --dir desktop run dist:mac:dev`.
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
