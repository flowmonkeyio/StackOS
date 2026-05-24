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
4. Hydrate the `stackos` Codex plugin from bundled assets.
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
stackos install
stackos start
```

`stackos install` initializes local state, runs database migrations,
hydrates plugin assets from the package, registers MCP bridge entries, and runs
`doctor`. A daemon-down doctor result is treated as a first-run warning during
install; run `stackos start` next to start the singleton daemon in the
background.

Use `stackos restart` when a daemon is already running and should reload
settings, token rotation, or package code.

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
TPF_LLM_TOOL=codex tpf make install-launchd
```

## First Run In The UI

After the daemon is running, use the UI in this order:

1. Open `http://127.0.0.1:5180/`.
2. Create or select the project that represents the business, site, product, or
   client.
3. Review the installed plugin catalog and confirm the project has the domains
   it needs, such as SEO, media buying, GTM, publishing, or utilities. The
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
repo and refuses cross-project calls. Use `action.run` for one explicit direct
action; use workflow templates, run plans, and step-granted `action.execute`
for multi-step work. Provider-specific actions should be added as plugin action
contracts, not as one-off MCP tools.

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

Use JSON output for automation:

```bash
stackos doctor --json
```

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
| Codex plugin | `~/.codex/plugins/stackos` |
| Plugin marketplace | `~/.agents/plugins/marketplace.json` |

Moving an install to another machine requires copying the DB, `seed.bin`, and
`auth.token` together. Without the original seed, encrypted provider
credentials cannot be recovered.

## Repair And Upgrade

Re-run install to repair plugin assets and MCP registration:

```bash
stackos install
```

In clone mode:

```bash
TPF_LLM_TOOL=codex tpf make install
```

After token rotation or package upgrades, restart the daemon:

```bash
stackos restart
```

See [`upgrade.md`](./upgrade.md) for upgrade and cross-machine move details.
