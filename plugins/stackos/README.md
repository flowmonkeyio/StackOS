# StackOS Plugin Distribution

This is the plugin-first distribution surface for StackOS. The plugin is
installed once into Codex and/or Claude Code, then used from any website or
business repository.

The plugin starts a thin MCP bridge (`python -m stackos mcp-bridge` in
the hydrated install) that connects to the singleton local StackOS daemon.
The bridge is disposable; the daemon owns the SQLite database, project
bindings, credentials, workflow templates, run plans, resources, actions,
context, learnings, experiments, decisions, and audit trails.

Website repositories should not need StackOS setup files. Repo-specific
knowledge is discovered from the current working directory and persisted in the
daemon through `workspace.*` tools.

Installers hydrate the personal Codex plugin location
`~/.codex/plugins/stackos` and register it through
`~/.agents/plugins/marketplace.json` with source path
`./.codex/plugins/stackos`, which resolves against the user's home
directory for the personal marketplace. Restart Codex after install or upgrade,
then use `/plugins` to inspect or toggle the plugin.

The installed `.mcp.json` is rewritten during install to use the current Python
environment (`python -m stackos mcp-bridge`), so clone-mode development
does not require a global `stackos` executable on `PATH`.
If the daemon is not listening yet, the bridge auto-starts it on the configured
loopback host and writes startup output to
`~/.local/state/stackos/mcp-bridge-autostart.log`.

## Setup

Canonical setup lives in [`../../docs/setup.md`](../../docs/setup.md).

Repository development:

```bash
TPF_LLM_TOOL=codex tpf make install
TPF_LLM_TOOL=codex tpf make serve
```

Package/operator install:

```bash
stackos install
stackos start
```

Optional macOS autostart:

```bash
stackos autostart install
```

After setup, create or select a project, enable needed plugins, add provider
connections, and create run plans from workflow templates. Website repositories
should bind to the project through workspace tools rather than carrying local
StackOS setup files.

## Agent-Facing MCP Surface

The daemon keeps the full internal MCP catalog for the UI, tests, and advanced
automation, but the plugin bridge exposes a small agent console:

- Direct tools: workspace binding/session tools, project list/create/get/update
  and active-project selection, `meta.enums`, workflow-template tools,
  run-plan tools, and a few `run.*` status/control calls.
- Hidden setup helpers: credential metadata, budgets, schedules, sitemap
  fetches, and setup-only project controls are available through
  `toolbox.describe` and `toolbox.call`.
- Step tools: when `runPlan.start` and `runPlan.claimStep` establish a running
  step, the bridge reads that step's grants; those tools become callable
  through `toolbox.call` for that run.

Agents should use direct tools for setup and run-plan control, then use
`toolbox.describe` before calling any hidden tool. This keeps the model context
small without removing the daemon's richer capabilities.

The installed plugin provides the StackOS entrypoint skill. Domain behavior
lives in plugin manifests and workflow templates rather than hard-coded
workflow skills.
