# Upgrading StackOS

The package, CLI, plugin slug, and MCP server are named `stackos`. Re-running
the install pipeline is idempotent: the end state after one run matches the
end state after ten.

## pipx mode

Once published to PyPI:

```bash
pipx upgrade stackos
stackos install
```

`pipx upgrade` swaps the wheel; `stackos install` then re-mirrors the
Codex and Claude Code skill mirrors from bundled `_assets/skills`, hydrates the
StackOS plugin from bundled `_assets/plugins`, refreshes any existing Codex
runtime cache copy, repairs MCP registrations, and runs `doctor`. Use
`stackos start` for first start and `stackos restart` after an upgrade when the
daemon is already running.

## Clone mode

```bash
git pull
make install
```

`make install` re-syncs Python deps, runs migrations to head, verifies the
committed UI bundle (building only if it is missing), mirrors the Codex and
Claude Code skills, hydrates the plugin, refreshes any existing Codex runtime
cache copy, repairs MCP registrations, and runs `doctor`.

## macOS desktop mode

The `stackos` desktop app updates through a custom generic update endpoint.
The endpoint is a static HTTPS website path consumed by `electron-updater`.
Upload/deploy mechanisms such as FTP are outside the app and must not be
packaged into the runtime.
After the app bundle is updated, the next launch reruns the idempotent install
repair step for the new app version or new packaged payload build id:

```bash
stackos install --launchd --force
stackos restart
```

This repairs assets, migrations, MCP registration, and launchd without rotating
`seed.bin` or `auth.token`. See
[`desktop-distribution.md`](./desktop-distribution.md) for endpoint metadata,
payload, signing, and release-artifact requirements.

For local desktop upgrade testing after development changes:

```bash
STACKOS_UPDATE_URL="http://127.0.0.1:8765/stackos/macos" make desktop-dist
stackos stop
open desktop/dist/stackos-1.0.1-mac-arm64.dmg
```

Serve the generated update metadata and artifacts from the matching localhost
static path, replace the installed app from the DMG if needed, then launch the
installed copy. Do not test managed updates while running from the mounted DMG:
the mounted volume is read-only and Squirrel.Mac refuses to update from it.
Unsigned local builds can validate update discovery, download, and the prompt
only. macOS install/relaunch evidence requires a signed installed app.
The payload build writes `resources/stackos/build-info.json`; if that build id
changed, the app runs the install/repair path once and restarts the daemon even
when `desktop/package.json` still has the same public version. For release
updates, still bump `pyproject.toml`, `stackos/__init__.py`, and
`desktop/package.json` together so electron-updater can advertise a new version.

## What happens during upgrade

| Step | Behaviour |
|---|---|
| Schema | `alembic upgrade head` runs at every daemon start. Down-migrations exist but are discouraged. |
| Skill mirrors | The canonical StackOS mechanics skill source is `plugins/stackos/skills/stackos/SKILL.md` in clone mode and `stackos/_assets/skills/stackos/SKILL.md` in package mode. Install mirrors it into `~/.codex/skills/stackos/SKILL.md` and `~/.claude/skills/stackos/SKILL.md`; stale files in those managed mirrors are removed. Workflow operating guidance belongs in skill presets, not additional global skill mirrors. |
| Plugin | `rsync -a --delete` mirrors and hydrates `~/.codex/plugins/stackos`; package installs do the equivalent from bundled assets. Existing Codex runtime cache copies under `~/.codex/plugins/cache/local-stackos/stackos/*` are refreshed from the same source so the plugin-provided `stackos:stackos` skill guidance stays current. Retired plugin assets disappear from the plugin catalog and cache on the next install. |
| MCP registration | Codex CLI, Claude Code, Claude Desktop, and Gemini CLI use the shared host MCP lifecycle service. Current local stdio registrations are a no-op; stale `stackos` entries that point at an old app/package path are removed and replaced by install, desktop Repair, or the focused registration scripts where available. Package installs register the bundled StackOS launcher instead of system Python and pass a host-specific `--runtime` to the bridge. Desktop launch also runs MCP-only reconciliation when the packaged payload is already prepared, because external client state can drift independently from app install state. Hosts installed after StackOS remain advisory until desktop Repair, app relaunch, or `stackos install --mcp-only` registers them. Claude Desktop config changes require restarting Claude Desktop. Legacy `~/.claude/mcp.json` StackOS entries are cleanup-only and sibling servers are preserved. No host registration stores a bearer token in client config. |
| Auth token | **Does not rotate on upgrade.** Run `stackos rotate-token --yes` or `make rotate-token` explicitly to rotate; registration refreshes saved configs. Restart any running daemon so middleware loads the new token. |
| launchd plist | `stackos autostart install` owns plist generation for clone and package installs. If the existing plist matches the generated one, it is a no-op. If different, `--force` overwrites with a `.bak` retained. |
| Desktop app | The Electron shell checks the custom update endpoint, installs the app update, then runs the normal install/repair path on next launch when the app version or packaged payload build id changed. The app restarts the daemon after install/repair so the running process uses the packaged code. Local DB, generated assets, seed, token, and provider credentials remain in user-local StackOS paths. |

## Breaking changes

Bump major version. Release notes call out manual migrations:

- DB schema changes: covered by Alembic — no action required.
- Managed StackOS skill or plugin asset removals: documented in the changelog;
  the install step deletes them automatically from managed mirrors via
  `rsync --delete` or equivalent package-resource mirroring.
- Auth token format change: would require an explicit `rotate-token`
  call; surfaced in release notes if it ever happens.
- `stackos` CLI subcommand removal: documented as a breaking change and
  removed cleanly from commands, docs, tests, and install assets. Do not keep
  compatibility shims for replaced execution paths.

## Rollback

```bash
# Roll back to a previous commit (clone mode)
git checkout <previous-tag>
make install

# Roll back to a previous wheel (pipx mode)
pipx install --force "stackos==<previous-version>"
stackos install
```

Before rollback, serious upgrades, or cross-machine moves, create a local
backup archive:

```bash
stackos backup --output ~/Desktop/stackos-backup.zip
```

The archive contains:

- `~/.local/share/stackos/stackos.db`
- `~/.local/state/stackos/seed.bin`
- `~/.local/state/stackos/auth.token`
- `manifest.json`

The archive is written with mode `0600` because it contains the daemon auth
token and the seed required to decrypt local credentials. Automated restore is
not implemented yet; keep the archive private and treat restore as an
operator-guided recovery step.

## Schema migrations

Migrations run automatically every time the daemon boots
(`alembic upgrade head` is invoked from the lifespan). Operators do
not normally need to run `make migrate` by hand. Down-migrations
exist (`alembic downgrade <rev>`) but are discouraged: they run
forward-only in release validation, and a down-migration that drops
columns may shed data depending on the change.

Breaking schema changes bump the major version (1.x → 2.0). Release
notes call out manual operator steps.

## Cross-machine moves

Migration of an install across machines requires copying:

- `~/.local/share/stackos/stackos.db` (the canonical DB)
- `~/.local/state/stackos/seed.bin` (encryption seed)
- `~/.local/state/stackos/auth.token` (bearer token)

Without `seed.bin`, the daemon refuses to start and `doctor` reports a
credential decrypt/seed problem. Restore the matching seed from backup, or
recreate the affected provider credentials through the StackOS Connections UI.
The DB itself stays intact, but encrypted credential payloads are unrecoverable
without the original seed.
