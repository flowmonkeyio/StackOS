# StackOS Setup

StackOS is the product/runtime, package, CLI, plugin slug, and MCP identity.

Setup has one goal: install StackOS once, connect the project and provider
accounts once, then let agents use the same contracts through the MCP bridge,
REST API, or CLI. The UI remains a local-admin and observability surface for
projects, readiness, connections, runs, action calls, resources, and artifacts.

## Setup Contract

Every supported setup path should land at the same state:

1. Create local data and state directories.
2. Create `seed.bin` and `auth.token` with mode `0600`.
3. Run database migrations.
4. Install the packaged Playwright Chromium runtime for daemon-owned browser
   automation.
5. Hydrate the `stackos` skill mirrors for Codex and Claude from the canonical
   package-managed skill.
6. Hydrate the `stackos` Codex plugin from bundled assets and refresh any
   existing Codex runtime cache copy.
7. Register the stdio MCP bridge for supported agent runtimes.
8. Start the daemon now, or install daemon autostart.
9. Open the StackOS UI at `http://127.0.0.1:5180/`.
10. Create or select a project.
11. Enable needed plugins.
12. Add provider connections.
13. Connect any working repository through workspace binding.
14. Run workflow templates, run plans, browser sessions, and action calls.

Agents never receive the auth token, seed, API keys, OAuth tokens, SMTP
passwords, app passwords, or encrypted credential payloads. They receive safe
provider/account ids, auth method refs, scopes, status, and diagnostics.
In setup docs, agent means the MCP/tool consumer. StackOS project binding does
not grant repository filesystem access; any repo read/write capability is
provided separately by the host agent runtime.

## Dev Clone Setup

Use this path when working from this repository:

```bash
make install
make serve
```

`make install` syncs Python dependencies, initializes state, runs migrations,
installs the Playwright Chromium browser runtime, checks the committed UI bundle, installs
Codex and Claude skill mirrors, installs plugin assets, refreshes any existing
Codex plugin cache copy, registers MCP bridge entries, and runs `doctor`. It is
normal for the final doctor check to report `daemon_up: False` before
`make serve` starts the daemon.

`make serve` runs the daemon in the foreground on:

```text
http://127.0.0.1:5180/
```

For UI development, keep the daemon running and start Vite:

```bash
make dev-ui
```

Open:

```text
http://127.0.0.1:5173/
```

The Vite app proxies `/api` and `/mcp` to the daemon on port `5180`.

## Package Or Operator Setup

Use this path when StackOS is installed as a Python package or pipx app:

```bash
stackos install
stackos start
```

`stackos install` initializes local state, runs database migrations, hydrates
Codex and Claude skill mirrors from the package-managed `stackos:stackos`
skill, installs the Playwright Chromium browser runtime, hydrates plugin assets, refreshes
any existing Codex plugin cache copy, registers MCP bridge entries, and runs
`doctor`. Operators and customers should
not edit the managed StackOS skill by hand. Project-specific agent guidance
belongs in project docs, workflow templates, tracker tasks, and adapted agent
presets. A daemon-down doctor result is treated as a first-run warning during
install; run `stackos start` next to start the singleton daemon in the
background.

Use `stackos restart` when a daemon is already running and should reload
settings, token rotation, or package code.

Use `stackos stop` to shut down the singleton daemon without immediately
starting it again. This is useful when testing a packaged desktop app launch and
you want the app to prove it can start its bundled daemon path from a cold
state.

## macOS Desktop App

The macOS desktop app is named `StackOS`. It is an Electron shell around the
same local daemon and UI. On first launch for an app version or packaged payload
build id, it runs:

```bash
stackos install --launchd --force
stackos restart
```

That means desktop installs converge on the same local state as package and
clone installs: DB, `seed.bin`, `auth.token`, migrations, plugin assets,
managed skill mirrors, MCP registrations, and daemon autostart. The desktop app
does not store provider secrets and does not rotate `seed.bin` or `auth.token`
on install or update. After install or repair, the desktop service consumes the
same `stackos doctor --json` readiness envelope used by the CLI.

Build and release details live in
[`desktop-distribution.md`](./desktop-distribution.md).

## Autostart

On macOS, StackOS can install a launchd job for the current user:

```bash
stackos autostart install
stackos autostart status
stackos autostart uninstall
```

The plist runs:

```text
python -m stackos serve
```

using the same Python environment that installed StackOS. It stores no bearer
token and no provider secrets. Logs go to:

```text
~/.local/state/stackos/daemon.log
```

If an existing plist differs, the installer refuses to overwrite it unless
called with:

```bash
stackos autostart install --force
```

The clone-mode convenience target delegates to the same CLI-owned behavior:

```bash
make install-launchd
```

## Uninstall

The default uninstall path removes local integration artifacts while preserving
user-owned state:

```bash
stackos uninstall
```

From a clone checkout, the Makefile convenience target performs the same
default-preserve cleanup:

```bash
make uninstall
```

It removes the launchd job, StackOS skill mirrors, StackOS Codex plugin source
and cache entries, and Codex/Claude MCP registrations. It preserves the local
database under `~/.local/share/stackos/` and daemon state under
`~/.local/state/stackos/`, including `seed.bin` and `auth.token`.

## Backup

Before serious upgrades or machine moves, export the minimal local state needed
for operator-guided recovery:

```bash
stackos backup --output ~/Desktop/stackos-backup.zip
```

The backup contains `stackos.db`, `seed.bin`, `auth.token`, and
`manifest.json`, and is written with mode `0600`. Automated restore is not
implemented yet.

## First Run In The UI

After the daemon is running, use the UI in this order:

1. Open `http://127.0.0.1:5180/`.
2. Create or select the project that represents the business, site, product, or
   client.
3. Review the installed plugin catalog and confirm the project has the domains
   it needs, such as engineering, SEO, media buying, GTM, publishing, or utilities. The
   browser should not become a broad plugin-mutation console. Catalog mutation
   belongs in a deliberate admin path such as CLI, REST-admin, or an
   agent-owned setup operation that still writes the same project state.
4. Open the project Setup page to review runtime, plugin, connection, template,
   action, and run-plan readiness.
5. Add connections for each provider account. A provider may allow multiple
   connections, for example multiple Meta ad accounts or several SMTP profiles.
6. Review workflow templates available from enabled plugins.
7. Create a run plan from a template or from an agent-authored plan.
8. Grant only the tool actions needed by the run plan.
9. Review action calls, resources, artifacts, learnings, experiments, and
   decisions after the run.

StackOS should render generic objects instead of bespoke workflow pages. A
plugin can define resources, templates, and actions; the agent still decides
strategy and passes explicit inputs.

## Agent Setup From Any Repo

After install, agents can use the `stackos` plugin from a business or
website repository without copying setup files into that repo.

The bridge command is:

```text
python -m stackos mcp-bridge
```

The bridge reads the local daemon token from the state directory and forwards
MCP traffic to the singleton daemon. If the daemon is not already listening,
the bridge attempts a loopback-only auto-start and logs to:

```text
~/.local/state/stackos/mcp-bridge-autostart.log
```

Agents should first bind the working repository to a StackOS project. After the
binding exists, the bridge resolves and injects `project_id` from the current
repo and refuses cross-project StackOS calls. This binding is not a filesystem
permission grant and does not let StackOS inspect the repo. Use `action.run`
for one explicit direct action; use workflow templates, run plans, and
step-granted `action.execute` for multi-step work. Provider-specific actions
should be added as plugin action contracts, not as one-off MCP tools.

## CLI, MCP, REST, And UI Flow

Callable behavior is registered once as a StackOS operation or plugin action.
The entrypoints differ, but the execution path converges:

```text
MCP / CLI / REST / UI
-> operation or action contract
-> input validation
-> run-plan grant checks when required
-> server-side credential resolution
-> connector or internal repository call
-> redaction
-> structured response and audit record
```

The agent-facing MCP surface stays small and generic. Scripts that are not AI
agents can use `stackos ops ...`, `stackos actions ...`, or REST for
the same operation catalog. Run-plan claim/record/action mechanics are
intentionally explicit because the primary execution user is an agent, not a
human clicking through a bespoke workflow UI.

## Local Communication Ingress

Telegram, Slack, and future webhook-based communication providers use one
project-level public ingress endpoint. The daemon still binds to
`127.0.0.1:5180`; only provider-verified `/api/v1/ingress/*` paths are allowed
to receive tunnel or deployed Host headers.

Production uses a deployed HTTPS base URL:

```bash
printf '%s' '{"driver":"public-url","public_base_url":"https://stackos.example.com"}' \
  | stackos ops call ingressEndpoint.configure --project 1 --input -
stackos ops call ingressEndpoint.sync --project 1
```

Local development can use a tunnel driver. For ngrok, start the tunnel to the
daemon port, then let StackOS discover the public HTTPS URL from the ngrok agent
API:

```bash
ngrok http 5180
printf '%s' '{"driver":"local-tunnel","driver_config":{"provider":"ngrok"}}' \
  | stackos ops call ingressEndpoint.configure --project 1 --input -
printf '%s' '{"sync_profiles":true}' \
  | stackos ops call ingressEndpoint.refresh --project 1 --input -
```

Agents should prefer the same operations through MCP when available:
`ingressEndpoint.configure`, `ingressEndpoint.refresh`,
`ingressEndpoint.routes`, `ingressEndpoint.sync`, and
`ingressEndpoint.status`. Local tunnel provider settings, including ngrok, belong
only in `driver_config`; provider routes stay Telegram/Slack-agnostic and are
regenerated from the stored project endpoint.

Slack needs the generated Slack ingress URL in two Slack app screens:

- **Event Subscriptions -> Request URL** for message and mention events.
- **Interactivity & Shortcuts -> Request URL** for Block Kit button clicks.

Use the same profile-specific URL from `ingressEndpoint.routes`, for example
`https://public.example/api/v1/ingress/slack/{project_id}/{profile_key}`.
If Events are verified but buttons show a warning marker in Slack, Interactivity
is missing or failing.

## Health Checks

Run:

```bash
stackos doctor
```

Important exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Install is healthy. |
| 1 | Daemon is down. Start it with `stackos start` or `make serve`. |
| 4 | Database migration head mismatch. Run migrations or restart the daemon. |
| 7 | Auth token is missing or has the wrong file mode. |
| 8 | Seed is missing, has the wrong mode, or credentials cannot decrypt. |
| 9 | Installed StackOS plugin or managed skill assets are missing or stale. |

Use JSON output for automation:

```bash
stackos doctor --json
```

The JSON envelope includes `checks.provider_readiness_available` and
`info.provider_readiness`. Provider readiness is advisory: missing provider
credentials do not make the local install unhealthy, but the details point
operators and agents to the project Connections page plus `readiness.check`,
`auth.status`, and `auth.test`. The envelope never includes raw provider
secrets.

## Local Paths

Default paths:

| Item | Path |
| --- | --- |
| SQLite DB | `~/.local/share/stackos/stackos.db` |
| Generated assets | `~/.local/share/stackos/generated-assets/` |
| Auth token | `~/.local/state/stackos/auth.token` |
| Credential seed | `~/.local/state/stackos/seed.bin` |
| Daemon log | `~/.local/state/stackos/daemon.log` |
| PID file | `~/.local/state/stackos/daemon.pid` |
| Codex skill mirror | `~/.codex/skills/stackos/SKILL.md` |
| Claude Code skill mirror | `~/.claude/skills/stackos/SKILL.md` |
| Codex plugin | `~/.codex/plugins/stackos` |
| Codex plugin runtime cache | `~/.codex/plugins/cache/local-stackos/stackos/*` |
| Plugin marketplace | `~/.agents/plugins/marketplace.json` |

Moving an install to another machine requires copying the DB, `seed.bin`, and
`auth.token` together. Without the original seed, encrypted provider
credentials cannot be recovered.

## Repair And Upgrade

Re-run install to repair plugin assets, stale Codex plugin cache copies, and MCP
registration:

```bash
stackos install
```

In clone mode:

```bash
make install
```

If only the host-agent skill mirrors are stale, use:

```bash
stackos install --skills-only
```

or, in clone mode:

```bash
make install-skills-codex
make install-skills-claude
```

If only MCP registration is stale, use:

```bash
stackos install --mcp-only
```

or, in clone mode:

```bash
bash scripts/register-mcp-codex.sh --force
```

After token rotation or package upgrades, restart the daemon:

```bash
stackos restart
```

See [`upgrade.md`](./upgrade.md) for upgrade and cross-machine move details.
