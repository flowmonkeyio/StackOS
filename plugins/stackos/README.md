# StackOS Plugin Distribution

This is the plugin-first distribution surface for StackOS. The plugin is
installed once into Codex and/or Claude Code, then used from any website or
business repository.

The source plugin asset uses the package console script (`stackos mcp-bridge`).
Installers rewrite the installed `.mcp.json` to the current Python environment
(`python -m stackos mcp-bridge`) so clone-mode development does not depend on a
global `stackos` executable on `PATH`. Either form starts the same thin bridge
to the singleton local StackOS daemon.
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

Installers also mirror the canonical `stackos:stackos` mechanics skill into
`~/.codex/skills/stackos/SKILL.md` and
`~/.claude/skills/stackos/SKILL.md` so Codex and Claude Code both receive
compatible agent-facing setup guidance. The skill source lives under this
plugin package; runtime mirrors are managed install artifacts, not
customer-editable project guidance. Workflow-specific or domain-specific
main-agent operating guidance belongs in StackOS skill presets that projects
resolve and adapt, not in additional globally exposed plugin skills.

If the daemon is not listening yet, the bridge auto-starts it on the configured
loopback host and writes startup output to
`~/.local/state/stackos/mcp-bridge-autostart.log`.

## Setup

Canonical setup lives in [`../../docs/setup.md`](../../docs/setup.md).

Repository development:

```bash
make install
make serve
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
automation, but the plugin bridge exposes a small workspace-scoped agent
console:

- Direct tools: `workspace.startSession`, `workspace.resolve`,
  `toolbox.describe`, and `toolbox.call`.
- Hidden setup helpers: operation discovery, project setup, auth status/tests,
  workflow templates, run plans, tracker, resources, context, budgets,
  schedules, action inspection/execution, cost views, and run audit helpers are
  available through `toolbox.describe` and `toolbox.call`.
- Step tools: when `runPlan.start` and `runPlan.claimStep` establish a running
  step, the bridge reads that step's grants; those tools become callable
  through `toolbox.call` for that run.

The bridge derives the current project from the repository that launched it. It
injects `project_id`, refuses cross-project calls, and blocks project-scoped
tools until the workspace is connected. Agents should start with
`workspace.startSession`, use `toolbox.describe` with exact tool names, and call
one hidden tool at a time through `toolbox.call`. This keeps the model context
small without removing the daemon's richer capabilities.

The installed plugin provides the StackOS entrypoint skill. Domain behavior
lives in plugin manifests and workflow templates rather than hard-coded
workflow skills.
