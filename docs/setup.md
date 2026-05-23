# StackOS Setup

StackOS is the product/runtime. The Python package, CLI, and plugin slug remain
`content-stack` for install compatibility.

Setup has one goal: install StackOS once, connect the project and provider
accounts once, then let agents or operators use the same contracts through the
UI, MCP bridge, REST API, or CLI.

## Setup Contract

Every supported setup path should land at the same state:

1. Create local data and state directories.
2. Create `seed.bin` and `auth.token` with mode `0600`.
3. Run database migrations.
4. Hydrate the `content-stack` Codex plugin from bundled assets.
5. Register the stdio MCP bridge for supported agent runtimes.
6. Start the daemon now, or install daemon autostart.
7. Open the StackOS UI at `http://127.0.0.1:5180/`.
8. Create or select a project.
9. Enable needed plugins.
10. Add provider connections.
11. Connect any working repository through workspace binding.
12. Run workflow templates, run plans, and action calls.

Agents never receive the auth token, seed, API keys, OAuth tokens, SMTP
passwords, app passwords, or encrypted credential payloads. They receive safe
provider/account ids, auth method refs, scopes, status, and diagnostics.

## Dev Clone Setup

Use this path when working from this repository:

```bash
TPF_LLM_TOOL=codex tpf make install
TPF_LLM_TOOL=codex tpf make serve
```

`make install` syncs Python dependencies, initializes state, runs migrations,
checks the committed UI bundle, installs plugin assets, registers MCP bridge
entries, and runs `doctor`. It is normal for the final doctor check to report
`daemon_up: False` before `make serve` starts the daemon.

`make serve` runs the daemon in the foreground on:

```text
http://127.0.0.1:5180/
```

For UI development, keep the daemon running and start Vite:

```bash
TPF_LLM_TOOL=codex tpf make dev-ui
```

Open:

```text
http://127.0.0.1:5173/
```

The Vite app proxies `/api` and `/mcp` to the daemon on port `5180`.

## Package Or Operator Setup

Use this path when StackOS is installed as a Python package or pipx app:

```bash
content-stack install
content-stack start
```

`content-stack install` initializes local state, hydrates plugin assets from the
package, registers MCP bridge entries, and runs `doctor`. A daemon-down doctor
result is treated as a first-run warning during install; run `content-stack
start` next to start the singleton daemon in the background.

Use `content-stack restart` when a daemon is already running and should reload
settings, token rotation, or package code.

## Autostart

On macOS, StackOS can install a launchd job for the current user:

```bash
content-stack autostart install
content-stack autostart status
content-stack autostart uninstall
```

The plist runs:

```text
python -m content_stack serve
```

using the same Python environment that installed StackOS. It stores no bearer
token and no provider secrets. Logs go to:

```text
~/.local/state/content-stack/daemon.log
```

If an existing plist differs, the installer refuses to overwrite it unless
called with:

```bash
content-stack autostart install --force
```

The clone-mode convenience target delegates to the same CLI-owned behavior:

```bash
TPF_LLM_TOOL=codex tpf make install-launchd
```

## First Run In The UI

After the daemon is running, use the UI in this order:

1. Open `http://127.0.0.1:5180/`.
2. Create or select the project that represents the business, site, product, or
   client.
3. Enable the plugins that match current work, such as SEO, media buying, GTM,
   publishing, or utilities.
4. Add connections for each provider account. A provider may allow multiple
   connections, for example multiple Meta ad accounts or several SMTP profiles.
5. Review workflow templates available from enabled plugins.
6. Create a run plan from a template or from an agent-authored plan.
7. Grant only the tool actions needed by the run plan.
8. Review action calls, resources, artifacts, learnings, experiments, and
   decisions after the run.

StackOS should render generic objects instead of bespoke workflow pages. A
plugin can define resources, templates, and actions; the agent still decides
strategy and passes explicit inputs.

## Agent Setup From Any Repo

After install, agents can use the `content-stack` plugin from a business or
website repository without copying setup files into that repo.

The bridge command is:

```text
python -m content_stack mcp-bridge
```

The bridge reads the local daemon token from the state directory and forwards
MCP traffic to the singleton daemon. If the daemon is not already listening,
the bridge attempts a loopback-only auto-start and logs to:

```text
~/.local/state/content-stack/mcp-bridge-autostart.log
```

Agents should first bind the working repository to a StackOS project, inspect
templates/actions, create or start a run plan, then call granted actions through
the generic action execution path. Provider-specific actions should be added as
plugin action contracts, not as one-off MCP tools.

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
agents can use `content-stack ops ...`, `content-stack actions ...`, or REST for
the same operation catalog.

## Health Checks

Run:

```bash
content-stack doctor
```

Important exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Install is healthy. |
| 1 | Daemon is down. Start it with `content-stack start` or `make serve`. |
| 4 | Database migration head mismatch. Run migrations or restart the daemon. |
| 7 | Auth token is missing or has the wrong file mode. |
| 8 | Seed is missing, has the wrong mode, or credentials cannot decrypt. |

Use JSON output for automation:

```bash
content-stack doctor --json
```

## Local Paths

Default paths:

| Item | Path |
| --- | --- |
| SQLite DB | `~/.local/share/content-stack/content-stack.db` |
| Generated assets | `~/.local/share/content-stack/generated-assets/` |
| Auth token | `~/.local/state/content-stack/auth.token` |
| Credential seed | `~/.local/state/content-stack/seed.bin` |
| Daemon log | `~/.local/state/content-stack/daemon.log` |
| PID file | `~/.local/state/content-stack/daemon.pid` |
| Codex plugin | `~/.codex/plugins/content-stack` |
| Plugin marketplace | `~/.agents/plugins/marketplace.json` |

Moving an install to another machine requires copying the DB, `seed.bin`, and
`auth.token` together. Without the original seed, encrypted provider
credentials cannot be recovered.

## Repair And Upgrade

Re-run install to repair plugin assets and MCP registration:

```bash
content-stack install
```

In clone mode:

```bash
TPF_LLM_TOOL=codex tpf make install
```

After token rotation or package upgrades, restart the daemon:

```bash
content-stack restart
```

See [`upgrade.md`](./upgrade.md) for upgrade and cross-machine move details.
