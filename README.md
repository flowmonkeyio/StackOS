# StackOS

StackOS is a local tool and plugin runtime for agent-operated work. It lets a
project install the domains it needs, such as SEO, media buying, GTM, or shared
utility tooling, then gives agents a consistent way to read context, create run
plans, call tools, and write durable results.

The core rule is simple: StackOS stores state and executes explicit calls. The
agent decides what to do.

## What StackOS Owns

- Projects, plugins, capabilities, providers, and action definitions
- Auth provider references and encrypted credential storage
- Generic resources and artifacts
- Workflow templates and concrete run plans
- Context snapshots, learnings, experiments, and decisions
- Action call records, run audit trails, and cost/status metadata
- A generic UI for templates, run plans, resources, artifacts, auth, and plugins

StackOS does not contain strategy engines. A tool can validate configuration,
resolve credentials, call an external service, and persist structured output.
It should not decide campaign structure, SEO strategy, media-buying direction,
GTM sequencing, or creative variants. Those decisions belong to the agent.

## Runtime Layers

| Layer | Purpose |
| --- | --- |
| Project | Durable workspace with plugin enablement, auth refs, history, context, resources, and artifacts. |
| Workflow template | Reusable setup for a class of work: inputs, instructions, context filters, gates, actions, expected outputs, and default steps. |
| Run plan | A specific execution instance with ordered steps, scoped grants, status, outputs, approvals, and audit history. |
| Action call | A concrete tool invocation with validated input, server-side auth resolution, response, error, and cost metadata. |

Most work should start from a reusable workflow template. The agent can fork or
extend templates for a project, create a run plan for the current goal, retrieve
bounded context, execute actions, and record learnings.

## Plugins

Plugins package domain-specific capabilities without changing the core product:

- resources such as `landing-page`, `campaign`, `ad-account`, `creative`, or `lead`
- providers such as Google Search Console, Meta Ads, Outbrain, Taboola, OpenAI
  Images, Firecrawl, or internal services
- actions such as `campaign.create`, `creative.generate`, `keyword.discover`,
  or `web.scrape`
- workflow templates that describe reusable work patterns
- UI navigation contributions rendered by generic components

The first-party plugins currently include:

- `core`: project memory, learnings, experiments, decisions, and shared context
- `seo`: SEO content and search operations as a domain plugin
- `utils`: reusable utility actions such as image generation and web retrieval

## Auth

Agents never receive secrets.

Provider credentials are stored server-side and exposed to agents only as safe
references: provider key, account id, status, scopes, last test result, and
diagnostics. When an agent calls an action, the daemon resolves the credential in
the tool process and records usage.

This lets agents operate across vendors without copying API keys into prompts,
run plans, or tool arguments.

## MCP Surface

The agent-facing MCP surface is intentionally generic:

- bootstrap/setup tools for `workspace.*`, `project.*`, budgets, schedules, and
  run-plan creation/start
- `plugin.*`, `catalog.*`, `capability.*`, `provider.*`
- `auth.*`
- `workflowTemplate.*`
- `runPlan.*`
- `resource.*`, `artifact.*`
- `context.*`, `learning.*`, `experiment.*`, `decision.*`
- `action.describe`, `action.validate`, `action.execute`
- `run.*` audit tools

Operational vendor calls should be reached through action execution or scoped
tool grants, not by expanding the direct agent surface.

Bootstrap/setup writes are the only direct pre-run writes. Workflow writes such
as resources, artifacts, learnings, experiments, decisions, and
`action.execute` require an active run-plan step grant.

## UI

The UI is a generic StackOS console:

- projects and plugin status
- workflow templates
- run plans and run detail
- resources and artifacts
- auth providers and credential status
- context, learnings, experiments, and decisions
- action call history

Domain plugins can contribute navigation and resource definitions, but the UI
should render configuration and run state generically.

## Development

Install dependencies:

```bash
TPF_LLM_TOOL=codex tpf make install
```

Run the daemon:

```bash
TPF_LLM_TOOL=codex tpf make serve
```

Run tests:

```bash
TPF_LLM_TOOL=codex tpf make test
```

Generate UI API types after API changes:

```bash
TPF_LLM_TOOL=codex tpf make gen-types
```

Build the UI bundle:

```bash
TPF_LLM_TOOL=codex tpf make build-ui
```

## Repository Map

| Path | Purpose |
| --- | --- |
| `content_stack/api/` | REST adapters for core StackOS resources. |
| `content_stack/mcp/` | MCP registry, bridge, permissions, and tool schemas. |
| `content_stack/repositories/` | Transport-agnostic business invariants. |
| `content_stack/db/` | SQLModel models and migrations. |
| `plugins/` | Built-in plugin manifests and workflow templates. |
| `skills/` | Optional agent skill prompts used as domain guidance. |
| `ui/` | Vue source for the StackOS console. |
| `docs/` | Current architecture, security, plugin, workflow, run-plan, and delivery docs. |

## Clean-Cut Rule

The current architecture is StackOS-first. Removed flows should not remain as
routes, MCP tools, UI pages, tests, install assets, or docs. When replacing a
flow, update the data model, MCP surface, plugin/template metadata, UI renderer,
tests, and docs in the same delivery.
