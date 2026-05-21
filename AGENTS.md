# StackOS agent notes

StackOS is the working direction for this repository. The historical
`content-stack` / SEO implementation remains important compatibility surface,
but new work should treat SEO as a first-party plugin domain, not as the core
product identity.

## Product Boundary

StackOS is a local tool runtime for agents and humans:

- Agents and humans decide strategy, interpret results, choose next actions,
  and write decisions/learnings.
- StackOS stores, retrieves, schema-checks, enforces auth/grants/budgets,
  executes external actions, redacts, persists, and audits.
- StackOS must not contain domain strategy logic such as "pick the winner",
  "optimize the campaign", "choose the next SEO topic", or "decide what to
  publish".
- StackOS tools should be static operations: set data, retrieve data, validate
  input, trigger external tools, and record auditable outputs.

No secrets to agents. Credentials, tokens, refresh tokens, API keys, OAuth
grants, and provider secrets stay inside the daemon/auth provider layer.
Agent-facing results must use opaque credential refs, setup URLs, sanitized
status, redacted logs, and artifact/resource references.

## Target Architecture

The target model is:

```text
StackOS core
  local daemon, database, MCP/REST surfaces, auth boundary, grant enforcement,
  generic resources/artifacts/context/learnings/experiments, workflow templates,
  run plans, runs, audit, generic UI renderers

Plugins
  SEO, media buying, GTM, utils, and private/company domains. Plugins contribute
  manifests, capabilities, provider/action schemas, resource schemas, workflow
  templates, UI navigation/resource-view configs, and local instructions.

Agents / humans
  customize templates, create concrete run plans, select context, decide what
  actions to take, call granted tools, and record outcomes/learnings.
```

Current SEO procedures, skills, article tables, vendor wrappers, and UI pages
are legacy/current-state implementation. Move or wrap them incrementally behind
the StackOS plugin/generic model. Preserve existing SEO behavior until a task
explicitly scopes its migration or removal.

Clean-cut migration rule: add new StackOS primitives as sidecars and route new
code through them. Do not add Alembic migrations that drop old SEO/procedure/
content-stack tables during this pivot. Physical destructive cleanup requires a
separate explicitly signed-off task with backup/restore and verification plans.
For current pivot deliverables, prefer links from new StackOS sidecar tables to
existing rows over modifying legacy/current tables. A migration may only remove
tables or indexes that the same deliverable created, never previous SEO,
procedure, article, run, or integration history.

## Workflows

Procedures are legacy compatibility. New workflow work should use:

- **Workflow Template**: reusable, editable setup for a class of work. It can
  contain purpose, inputs, context requirements, instructions, action/resource
  contracts, policies, approval gates, output contracts, and extension points.
- **Run Plan**: a concrete agent-authored execution plan derived from a
  template for one run. It freezes selected steps, grants, inputs, context
  filters, budgets, approval gates, and expected outputs.
- **Run**: auditable execution state and history for a run plan.

Templates should not contain one-size-fits-all hard-coded business logic. They
provide a base contract that agents can adapt in a project/repo context.
Template precedence is repo/company `.stackos/workflows` over project/user DB
templates over plugin defaults in `plugins/<plugin>/workflows`.

Project-level context, learnings, experiments, decisions, artifacts, and metric
snapshots are data stores. StackOS can filter and retrieve them; agents decide
what they mean.

## Tooling And MCP Boundary

The daemon owns the full internal MCP catalog for the UI, tests, jobs, and
automation. The installable agent plugin/bridge must expose a deliberately
small surface.

Target exposure levels:

- **Direct/discovery tools**: workspace/project navigation, plugin/catalog/
  capability/provider discovery, sanitized auth status, workflow-template
  list/describe/validate, run-plan create/validate/start/get/list, run status.
- **Setup/admin-gated tools**: project plugin enable/disable and auth
  start/revoke. These are human/local-admin/project-owner setup operations, not
  normal agent-facing mutations.
- **Step-scoped/gated tools**: action execution, mutating resource/artifact
  calls, context fields beyond safe metadata, and learning/experiment/decision
  writes. These require explicit run-plan grants.
- **Compatibility tools**: `procedure.*`, existing SEO/vendor tools, and old
  setup helpers remain only as compatibility surfaces until migrated.

Do not add operational/domain/vendor tools to the direct agent list. Vendor
actions belong behind the generic action/toolbox path with grants, credential
refs, budgets, redaction, and audit.

## Existing Content-Stack Compatibility

While the migration is underway, the current implementation still uses:

- `content_stack/mcp/tools/*` for the daemon's internal tool catalog.
- `content_stack/mcp/bridge.py` for the filtered agent bridge.
- `content_stack/mcp/permissions.py` for grant checks.
- `toolbox.describe` and `toolbox.call` for hidden daemon tools.
- `procedures/*` and `skills/**/SKILL.md` for existing SEO procedure flows.
- `content_stack/integrations/*` for vendor wrappers.

When changing any current SEO/procedure flow before it is migrated, keep these
in sync:

1. Daemon tool or integration wrapper.
2. Bridge visibility: direct only for navigation/setup; hidden/gated for
   operations.
3. Permission grant in `content_stack/mcp/permissions.py`.
4. Skill frontmatter `allowed_tools`.
5. Skill/procedure prompt text naming real tools.
6. Focused tests proving hidden tools are not advertised directly and are
   available only when granted.

External integrations such as DataForSEO, Firecrawl, GSC, OpenAI Images,
Reddit, Jina, Ahrefs, WordPress, Ghost, Meta, Taboola, or Outbrain must keep
auth, retries, rate limits, budget checks, cost logging, request shaping,
normalization, and redaction inside daemon-side wrappers/connectors. Agents
must not call vendor SDKs directly or receive raw secrets.

## UI Direction

Generic UI should become simpler and renderer-driven:

- Core views: Plugins, Capabilities, Auth/Connections, Workflow Templates,
  Runs, Project Data, Resource Explorer.
- Generic renderers: template, run plan, action schema, resource view, context
  query, plugin navigation.
- SEO/media-buying/GTM views should be plugin contributions or generic
  resource views, not permanent bespoke workflow pages.
- Group context snapshots, learnings, experiments, decisions, artifacts,
  metrics, and timeline under Project Data.
- Raw JSON panels may show sanitized server payloads only.

Do not add new per-workflow UI unless a signed-off task explicitly scopes it.

## Delivery Discipline

Follow `docs/stackos-deliverable-task-plan.md`.

- One deliverable equals one coherent commit after verification and signoff.
- Preserve existing SEO flows unless the task explicitly migrates/removes them.
- Keep migrations additive for the pivot; no dropping legacy tables/history
  without a separate destructive-cleanup ticket and signoff.
- Add tests proportional to the blast radius.
- Any task touching auth, actions, context, logs, or audit needs redaction
  tests.
- Any task adding agent-visible tools must prove the direct/gated/compatibility
  boundary with tests.
- Keep docs updated when behavior or architecture changes.

## TPF Token Proxy Filter

Prefix shell commands with `TPF_LLM_TOOL=codex tpf` unless the command is one of:
`cd`, `echo`, `cat`, `head`, `tail`, `mkdir`, `rm`, `mv`, `cp`, `chmod`,
`pwd`, `export`, `source`, `set`, `unset`, `alias`, `read`, `printf`,
`test`, `true`, `false`, `which`, `touch`.

For piped commands, put the pipe in `TPF_PIPE`:

```bash
TPF_PIPE='head -20' TPF_LLM_TOOL=codex tpf git log --oneline
```

Do not wrap redirections, logical OR, background jobs, or subshells.

## Serena MCP

Use this project's dedicated Serena MCP server, not the shared/global `serena`
server:

- Codex MCP name: `serena-content-stack`
- URL: `http://localhost:9123/mcp`
- launchd label: `com.oraios.serena-mcp.content-stack`
- launchd plist: `~/Library/LaunchAgents/com.oraios.serena-mcp-content-stack.plist`
- project: `/Users/sergeyrura/Bin/content-stack`
- log: `~/Library/Logs/serena-mcp-content-stack.log`

Do not call `activate_project` on the shared `serena` MCP to switch it to
content-stack. That server is used by other projects and can expose stale
project memory. Do not write, rename, edit, or delete Serena memories unless
the user explicitly asks.
