# Upgrading StackOS

The package, CLI, plugin slug, and MCP server are named `stackos`. Re-running
the install pipeline is idempotent: the end state after one run matches the
end state after ten.

## pipx mode

For a StackOS installation sourced from PyPI:

```bash
# Stage the intended wheel in a separate environment first. Use that staged
# CLI, with the source installation's STACKOS_HOST/PORT/DATA_DIR/STATE_DIR.
/path/to/staged/stackos upgrade-identity
/path/to/staged/stackos prepare-upgrade
pipx upgrade stackos
stackos install
stackos restart
```

Preparation must precede the wheel swap. Install the same staged version/artifact;
if resolution selects a different target, postflight holds rather than adopting it.
`pipx upgrade` swaps the wheel; `stackos install` then re-mirrors the
Codex and Claude Code skill mirrors from bundled `_assets/skills`, hydrates the
StackOS plugin from bundled `_assets/plugins`, refreshes any existing Codex
runtime cache copy, repairs MCP registrations, and runs `doctor`. Use
`stackos start` for first start and `stackos restart` after an upgrade when the
daemon is already running.

Standalone host wheel metadata pins `stackos-connectors==0.2.9`. Until that
connector version is published separately, a package-index-only installation
cannot resolve it. The 2.1.45 desktop payload instead bundles the accepted
immutable connector source through the frozen uv export; see
[packaging dependencies](desktop-distribution.md#packaging-dependencies).
For a local StackOS wheel installation, see
[package setup](setup.md#package-or-operator-setup).

## Clone mode

Update the StackOS checkout and its lockfile together. For 2.1.45, uv resolves
the accepted connector 0.2.9 Git revision pinned in `tool.uv.sources` and
`uv.lock`; no sibling checkout needs updating. Keep the source override and
lockfile together until a separately reviewed publication transition.
For a wheel installation, follow [package setup](setup.md#package-or-operator-setup).

```bash
# From a separately staged copy of the intended revision, before git pull:
/path/to/staged/.venv/bin/python -m stackos prepare-upgrade
git pull
make install
stackos restart
```

`make install` re-syncs Python deps, runs migrations to head, verifies the
committed UI bundle (building only if it is missing), mirrors the Codex and
Claude Code skills, hydrates the plugin, refreshes any existing Codex runtime
cache copy, repairs MCP registrations, and runs `doctor`.
An existing clone environment prepares before dependency sync; full CLI install
prepares before migrations/assets/launchd changes. The install command owns
migration ordering. A first clone may prepare only after its separate target
dependencies exist. Direct environment replacement outside this sequence is not
a managed continuation boundary.

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
STACKOS_UPDATE_URL="http://127.0.0.1:8765/stackos/macos" \
pnpm --dir desktop run dist:mac:dev
stackos stop
open desktop/dist/stackos-<version>-mac-arm64.dmg
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

Desktop updates disable installation on ordinary app quit. After download, the
controller passes the actual staged ZIP to `stackos prepare-upgrade`; its unique
packaged `build-info.json` supplies the intended identity. Only successful
preparation allows Electron installation. Next launch runs normal install/repair
and restart. Full CLI install and `autostart install` also prepare before their
disruptive changes; `stackos restart` consumes the same transaction, waits for
target health, and runs the target doctor before finish. Registration-only and
skill-only repairs do not interrupt the daemon.

`stackos prepare-upgrade` defaults to the staged CLI's own runtime identity;
`--target-identity` selects an explicitly read staged identity and
`--target-archive` reads the downloaded desktop ZIP without extracting it.
For non-default installations, retain the same `STACKOS_HOST`, `STACKOS_PORT`,
`STACKOS_DATA_DIR`, and `STACKOS_STATE_DIR` throughout staging and postflight.
Desktop daemon host/port overrides are passed to its CLI subprocesses as well.

The pre-handshake 2.1.44 daemon cannot checkpoint active work. A staged new CLI
probes its capability before interruption and reads existing ownership scalars
without rewriting history. Admitted tasks, managed setup, unknown native owners
or unreadable state refuse bootstrap with exact owner/repair context. Let those
owners finish or use their separately authorized recovery, keep the source intact,
then coordinate a quiescent manual installation of the feature. There is no
abort/reopen workaround or retroactive automatic continuity for 2.1.44. Quiescent
bootstrap has no source admission fence; keep new work out until installation
finishes. Authentication or policy denial never falls back to bootstrap.

Supported managed delivery continuation uses the installation-admin
`maintenance.prepareUpgrade`, `maintenance.upgradeStatus` and
`maintenance.finishUpgrade` operations. Preparation checkpoints only authorized
queued/running delivery work and retires its exact native/callback owners.
After replacement, startup leaves those checkpoints fenced. The lifecycle caller
must compare the intended staged target with the target daemon's runtime
identity, observe health, run the target doctor, and explicitly finish with its
current generation, revision and doctor command evidence. Failed postflight does
not resume anything. See the [continuation contract](task-tracker.md#managed-upgrade-continuation)
for the input fields and identity format.

Safe checkpoints rejoin the existing queue under project capacity and directory
exclusion. Delivery, repair, independent review and workflow mirrors retain their
original phase/session/settings; resuming review never replays accepted delivery.
Unsafe checkpoints return held repair context while unrelated stops, failures,
aborts, deferred work and manual work remain untouched. Repeating finish is safe.
An ordinary restart, source edit or wheel swap alone supplies no continuation
authority. These operations do not attest a signed desktop update or make an older
source daemon support the handshake; caller capability probing and actual package
installation remain separate lifecycle obligations.

Failed install/restart/doctor never completes unresolved checkpoints. While
preparation is active, its installation-wide fence remains.
Repair the same intended target and retry `stackos restart`, or run
`stackos finish-upgrade --upgrade-id <original-upgrade-id>` after it is healthy.
Finish records the actual doctor
command, exit status and output in a private uniquely named state-directory
receipt and sends that ref with the current daemon generation/revision. Even
`install --skip-doctor` cannot skip this required continuation doctor. A held
checkpoint remains a required repair. A `partial` result releases the global
fence for safe and unrelated work while keeping individual checkpoints held;
CLI restart and desktop install/repair report failure and preserve the last good
install state. The failed CLI command retains the structured result on stdout
and names the original upgrade, held tasks and retry command on stderr.

After repairing the reported cause, retry that exact upgrade with
`stackos finish-upgrade --upgrade-id <original-upgrade-id>`. It reads the current
revision and reaches the existing continuation owner for the remaining checkpoints;
already dispatched or settled work is never replayed. A committed continuation
that lost its target daemon before dispatch can rejoin the existing queue after
current target health/doctor and exact checkpoint validation. Named finish checks
`recovery_required` even when the retained status is `continued`; it reports
unresolved drift, unconfirmed dispatch or a prior-generation dispatch hold as a
failure naming the original upgrade and recovery action. Only proven pending
work can be rebound automatically. Repeating a completed named finish acknowledges
its retained completion. Without `--upgrade-id`, finish selects only
the currently active global fence, so an idle result does not establish that
earlier partial upgrades completed. To inspect a retained transaction, call
`stackos ops call maintenance.upgradeStatus --input status.json`, where the file
contains `{"upgrade_id":"<original-upgrade-id>"}`. Pre-existing stopped, failed,
aborted, deferred and manual work is never resumed by this flow.

| Step | Behaviour |
|---|---|
| Schema | `alembic upgrade head` runs at every daemon start. Down-migrations exist but are discouraged. |
| Skill mirrors | The canonical StackOS mechanics skill source is `plugins/stackos/skills/stackos/SKILL.md` in clone mode and `stackos/_assets/skills/stackos/SKILL.md` in package mode. Install mirrors it into `~/.codex/skills/stackos/SKILL.md` and `~/.claude/skills/stackos/SKILL.md`; stale files in those managed mirrors are removed. Workflow operating guidance belongs in skill presets, not additional global skill mirrors. |
| Plugin | `rsync -a --delete` mirrors and hydrates `~/.codex/plugins/stackos`; package installs do the equivalent from bundled assets. Existing Codex runtime cache copies under `~/.codex/plugins/cache/local-stackos/stackos/*` are refreshed from the same source so the plugin-provided `stackos:stackos` skill guidance stays current. Retired plugin assets disappear from the plugin catalog and cache on the next install. |
| MCP registration | ChatGPT / Codex, Claude Code, Claude Desktop, Gemini CLI, Grok, and explicit Hermes profiles use the shared host MCP lifecycle in [`setup.md`](./setup.md). Current local stdio registrations are a no-op. Only selected StackOS-owned stale entries are repaired; unmanaged same-name entries are preserved for review. Package installs register the bundled StackOS launcher and pass a host-specific `--runtime`. Desktop launch completes reconciliation before loading the UI so the first status report observes current state. ChatGPT-bundled Codex is preferred, standalone Codex and `Codex.app` remain compatible, and plugin/direct Codex paths use `--runtime codex`. Grok uses its native user-scoped config and `--runtime grok`; imported Claude and project entries are not modified. Hermes is never installed or auto-selected; an explicitly chosen existing profile receives MCP plus only the canonical StackOS skill. Claude Desktop config changes require restarting Claude Desktop. Legacy `~/.claude/mcp.json` StackOS entries are cleanup-only and sibling servers are preserved. No host registration stores a bearer token in client config. |
| Auth token | **Does not rotate on upgrade.** Run `stackos rotate-token --yes` or `make rotate-token` explicitly to rotate; registration refreshes saved configs. Restart any running daemon so middleware loads the new token. |
| launchd plist | `stackos autostart install` owns plist generation for clone and package installs. If the existing plist matches the generated one, it is a no-op. If different, `--force` overwrites with a `.bak` retained. |
| Browser command | Install/repair refreshes the StackOS-owned `~/.local/bin/stackos.browser` launcher with the current package path and daemon host/port/data/state context. Prepared desktop startup repairs missing/stale owned launchers. Foreign same-name entries remain untouched. Uninstall removes only managed launchers; profiles and session history are preserved. |
| Desktop app | The Electron shell checks the custom update endpoint, installs the app update, then runs the normal install/repair path on next launch when the app version or packaged payload build id changed. The app restarts the daemon after install/repair so the running process uses the packaged code. Local DB, generated assets, seed, token, provider credentials, and encrypted payload transit values remain in user-local StackOS paths. |

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

Before rollback, serious upgrades, or cross-machine moves, configure the
public recovery key using
[backup setup](./backups.md), then create an encrypted archive:

```bash
stackos backup --output /private-backups/stackos/manual.sbak
```

The archive contains:

- `~/.local/share/stackos/stackos.db`
- `~/.local/state/stackos/seed.bin`
- `~/.local/state/stackos/auth.token`
- `manifest.json`
- All managed project files under `data/projects/<project-id>/`, including Finance
  masters, safe incarnation metadata, invoices, receipts, schemas and reviews
- Every explicitly enrolled additional external folder

The entire archive is encrypted with an independent recovery key and written
with mode `0600`. Keep the private recovery key separately; it is never stored
inside the backup. Automatic backups remain disabled until opted in, including
after upgrading. Existing ZIP backups remain plaintext.

`stackos restore ARCHIVE --private-key-file FILE --output-dir NEW_DIRECTORY`
authenticates and validates recovery into an inert directory. It does not
overwrite live state or activate the included policy. The archive does not
contain TDLib's native Account database, so a Telegram user Account requires a
new sign-in after archive-only recovery on another machine. In-place upgrades
preserve it.

## Schema migrations

Revision `0041_ui804_consolidation` joins the project catalog/cleanup and
project-folder/Board-arrival migration histories. Upgrades from either prior
head and fresh installs converge on the same schema without restamping or
renumbering existing revisions. Saved catalog order, cleanup state, managed-file
custody and explicit folder selections are preserved. Existing projects without
a selected folder still require explicit setup; Board normalization retains its
one-time audit marker and preserves saved manual order.

Programmatic startup and CLI upgrades carry the selected `Settings` through
Alembic, including the state directory used by credential migrations. An
ambient `STACKOS_STATE_DIR` must not redirect the selected database to another
installation's encryption seed. Standalone Alembic commands continue to use
their environment configuration. This changes no seed, credential identity,
or schema revision; existing encrypted records retain their matching seed.

Migrations run automatically every time the daemon boots
(`alembic upgrade head` is invoked from the lifespan). Operators do
not normally need to run `make migrate` by hand. Down-migrations
exist (`alembic downgrade <rev>`) but are discouraged: they run
forward-only in release validation, and a down-migration that drops
columns may shed data depending on the change.

Breaking schema changes bump the major version (1.x → 2.0). Release
notes call out manual operator steps.

## Cross-machine moves

For a machine move, verify the encrypted backup into a fresh recovery folder
with the separate private key. Stop the target daemon before adopting the
matching recovered files together:

- `~/.local/share/stackos/stackos.db` (the canonical DB)
- `~/.local/state/stackos/seed.bin` (encryption seed)
- `~/.local/state/stackos/auth.token` (bearer token)

Adopt the recovered `data/projects/` tree with its matching database. Keep the
permanent project-ID directories intact; never merge or reassign another project's
files. Opening Finance verifies identity and custody before refreshing its storage
generation. Recover additional enrolled folders from `external/<target-id>/` and
explicitly reconfigure only that external coverage on the new machine.
Recovery does not enable a saved schedule. These files restore StackOS
credential metadata and encrypted secrets, but not
Telegram's native TDLib authorization database. User Accounts need a new
Telegram sign-in after a move that copies only these files; bots can connect
with their saved token.

Without `seed.bin`, the daemon refuses to start and `doctor` reports a
credential decrypt/seed problem. Restore the matching seed from backup, or
recreate the affected provider credentials through the StackOS Connections UI.
The DB itself stays intact, but encrypted provider credentials and payload
transit values are unrecoverable without the original seed. Recreate lost
payload values with `secret.set` and replace their opaque refs in any pending
action input.
