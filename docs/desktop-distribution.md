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

The macOS bundle icon is generated from the same source as the UI favicon:
`ui/public/favicon.svg`. `desktop/scripts/build-icons.mjs` syncs that SVG into
`desktop/assets/stackos-icon.svg` and generates `desktop/assets/stackos-icon.icns`
for electron-builder.

## Update Flow

The app uses a custom generic update endpoint. At runtime, the endpoint comes
from:

1. `STACKOS_UPDATE_URL`
2. packaged `resources/update-config.json`
3. development `desktop/update-config.json`

The endpoint should serve electron-updater generic metadata and artifacts, for
example:

```text
https://updates.example.com/stackos/macos/latest-mac.yml
https://updates.example.com/stackos/macos/stackos-1.0.1-mac-arm64.zip
https://updates.example.com/stackos/macos/stackos-1.0.1-mac-arm64.dmg
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
- hosted DMG/ZIP/update metadata

Do not commit these values. Pass them through the build environment or the
release system.

## Verification

Scaffold checks that do not require Electron dependencies:

```bash
make desktop-doctor
cd desktop && node --check src/main.js
cd desktop && node --check src/preload.js
cd desktop && node --check src/service.js
cd desktop && node --check src/updates.js
```

Release checks, once dependencies and signing are configured:

```bash
make desktop-payload
STACKOS_UPDATE_URL="https://updates.example.com/stackos/macos" make desktop-dist
```

Manual release smoke should install the DMG, launch `stackos`, confirm the UI
loads from `127.0.0.1:5180`, run Doctor from the app menu, and verify update
metadata is reachable from the custom endpoint.

For a cold-start smoke of the packaged daemon path, run `stackos stop` before
launching the desktop app. The app should run its packaged `stackos install
--launchd --force`, start the daemon through `stackos restart`, and then load
the UI.
