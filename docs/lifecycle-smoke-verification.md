# Lifecycle Smoke Verification

Use this checklist before release closeout when changes touch install, repair,
upgrade, uninstall, backup, desktop startup, or local state custody.

The goal is narrow confidence, not a broad release suite. Prove the lifecycle
paths that changed, keep destructive operations inside a temporary home, and
record any skipped check with a reason.

## Automated Checks

Run the checks that match the changed surface:

```bash
.venv/bin/python -m pytest tests/unit/test_cli_install.py -q
.venv/bin/python -m pytest tests/integration/test_install_scripts -q
pnpm --dir desktop check
git diff --check
```

Notes:

- `tests/integration/test_install_scripts` builds wheel-layout fixtures and may
  need network access for Python build requirements.
- `pnpm --dir desktop check` covers the desktop scaffold checks plus service
  readiness and upgrade-state tests.
- Add focused tests for any new lifecycle branch before relying on the broad
  suite.

## Manual Smokes

Use a temporary home for uninstall and backup checks. Do not run destructive
cleanup against the operator's real home during smoke verification.

### Install And Repair

1. Stop the daemon from the supported lifecycle path.
2. Run desktop or CLI repair/install through the public entrypoint under test.
3. Confirm the daemon health endpoint returns OK.
4. Confirm `stackos doctor --json` returns a parseable readiness envelope.
5. Confirm existing local DB, `seed.bin`, and `auth.token` are still present.

Expected evidence:

- command used
- health result
- doctor/readiness status
- preserved-state assertion

### Upgrade

1. Run `node desktop/scripts/test-service-upgrade.cjs`.
2. In a temp user-data directory, simulate payload A prepared successfully.
3. Re-run with payload A and confirm it skips.
4. Re-run with payload B and confirm it repairs.
5. Simulate failed payload B repair and confirm payload A install-state remains
   the last good state and the next launch retries.

Expected evidence:

- skipped/current result
- payload-change repair result
- failed-upgrade preservation result

### Uninstall

Run both supported entrypoints in a temp home when either path changed:

```bash
STACKOS_HOME="$tmp_home" HOME="$tmp_home" .venv/bin/python -m stackos uninstall
STACKOS_HOME="$tmp_home" HOME="$tmp_home" make uninstall
```

Confirm removed:

- launchd plist
- StackOS Codex and Claude skill mirrors
- StackOS Codex plugin source
- StackOS Codex plugin cache
- StackOS MCP registrations

Confirm preserved:

- `~/.local/share/stackos/stackos.db`
- `~/.local/state/stackos/seed.bin`
- `~/.local/state/stackos/auth.token`

### Backup

In a temp home with a real SQLite DB, seed, and token:

```bash
STACKOS_HOME="$tmp_home" HOME="$tmp_home" .venv/bin/python -m stackos backup --output "$tmp/backup.zip"
```

Confirm:

- archive mode is `0600`
- archive includes `manifest.json`, `data/stackos.db`, `state/seed.bin`, and
  `state/auth.token`
- archive includes `state/seed.bin.bak` when a staged seed backup exists
- manifest schema is `stackos.local-backup.v1`
- manifest says automated restore is not implemented
- copied SQLite DB opens and contains expected data

## Closeout

Before marking lifecycle verification complete:

1. Every delivered lifecycle ticket has automation and manual evidence.
2. Deferred lifecycle scope is named explicitly.
3. `docs/product-direction.md` status matches the tracker.
4. The worktree is clean after commits.
5. The run-plan verification step records run commands, manual smokes, skipped
   checks, and residual risks.
