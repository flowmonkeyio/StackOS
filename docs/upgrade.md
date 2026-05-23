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

`pipx upgrade` swaps the wheel; `stackos install` then re-mirrors
the hydrated stackos plugin from the wheel's bundled `_assets/` tree,
refreshes MCP registrations, and runs `doctor`. Use `stackos start` for
first start and `stackos restart` after an upgrade when the daemon is
already running.

## Clone mode

```bash
git pull
make install
```

`make install` re-syncs Python deps, runs migrations to head, rebuilds
the UI bundle (no-op once committed), and re-runs the plugin, MCP, and
doctor install steps.

## What happens during upgrade

| Step | Behaviour |
|---|---|
| Schema | `alembic upgrade head` runs at every daemon start. Down-migrations exist but are discouraged. |
| Plugin | `rsync -a --delete` mirrors and hydrates `~/.codex/plugins/stackos`. Retired plugin assets disappear from the plugin catalog on the next install. |
| MCP registration | Codex CLI: `codex mcp add` registers the local `mcp-bridge` stdio command and is a no-op when already registered (the script greps `mcp list` first). Claude Code: atomic JSON merge with `.bak` backup; sibling servers preserved. Neither registration stores a bearer token in client config. |
| Auth token | **Does not rotate on upgrade.** Run `stackos rotate-token --yes` or `make rotate-token` explicitly to rotate; registration refreshes saved configs. Restart any running daemon so middleware loads the new token. |
| launchd plist | `stackos autostart install` owns plist generation for clone and package installs. If the existing plist matches the generated one, it is a no-op. If different, `--force` overwrites with a `.bak` retained. |

## Breaking changes

Bump major version. Release notes call out manual migrations:

- DB schema changes: covered by Alembic — no action required.
- Skill or plugin asset removals: documented in the changelog; the install
  step deletes them automatically via `rsync --delete`.
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

Automated backup/restore commands are reserved and should not be treated as
available operator recovery yet. Before rollback or cross-machine moves, stop
the daemon and take a manual copy of:

- `~/.local/share/stackos/stackos.db`
- `~/.local/state/stackos/seed.bin`
- `~/.local/state/stackos/auth.token`

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
