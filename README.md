# StackOS

StackOS is a local operating layer for agent-run business work. In StackOS
docs, an agent means the MCP/tool consumer that calls StackOS operations and
plugin actions. That caller may also have host-provided repo tools, but
StackOS itself only scopes project state and executes explicit operations. It
lets a project install the tools it needs, connect provider accounts safely,
reuse workflow templates, and give agents a consistent way to execute work
without handing them secrets.

The core idea is simple: StackOS stores the setup and executes explicit calls.
The agent or operator decides what to do.

## Why It Exists

Most businesses do not run one isolated workflow. They run SEO, media buying,
GTM, publishing, research, reporting, creative production, and internal tooling
at the same time. Each area needs its own providers, credentials, records,
templates, and history.

StackOS turns that into a project-scoped tool library:

- install only the plugins a project needs
- connect each provider with the right auth flow
- reuse templates for repeated work
- create concrete run plans for actual execution
- keep resources, artifacts, decisions, experiments, and learnings in one place
- audit every tool call without exposing secrets to the agent

## What It Is

StackOS is not an SEO app, a media-buying app, or a GTM app. Those are plugin
domains. The core product is the runtime underneath them.

| Layer | Business Role |
| --- | --- |
| Project | The workspace for a company, site, product, or client. |
| Workspace binding | The daemon-owned mapping from the current repository root to one StackOS project for MCP sessions. |
| Plugin | A domain package such as SEO, media buying, GTM, publishing, or utilities. |
| Connection | A safe provider credential profile, such as an API key, OAuth account, SMTP setup, CMS account, or internal webhook credential. |
| Workflow template | Reusable setup for repeated work, including inputs, context needs, gates, expected outputs, and default steps. |
| Agent preset | A generic MCP/tool-consumer role contract that must be adapted to the project before use. |
| Run plan | One concrete execution instance with scoped permissions, steps, outputs, and audit history. |
| Tracker task/ticket | Durable project work state with dependencies, lifecycle, provenance, and verification context. |
| Action call | A validated provider/tool call executed by the daemon and recorded for review. |

## Current Domains

First-party plugin coverage currently includes:

- **Engineering**: tracked delivery workflows, SDLC agent role presets,
  engineering decisions/evidence, and release closeout flow.
- **Marketing**: end-to-end campaign production from brand/product intake
  through planned media generation, landing page variants, visual signoff
  evidence, and a local campaign gallery.
- **SEO**: keyword research, SERP analysis, PAA extraction, competitor/backlink
  research, SEO resources, and reusable SEO templates.
- **Media buying**: paid media providers, campaign and creative resources,
  launch/review templates, and first provider connectors.
- **GTM and RevOps**: CRM, enrichment, outbound, workspace, and pipeline
  provider contracts with reusable GTM templates.
- **Communications**: provider-neutral send/reply flows, stored communication
  history, Telegram and Slack bot actions, SMTP send, and IMAP mailbox/message
  lifecycle.
- **Publishing**: WordPress and Ghost publishing actions.
- **Utilities**: image generation and reference-image edits, web retrieval,
  sitemap fetching, Reddit research, generic configured HTTP tools, and a
  deferred provider-neutral video generation contract.

The plugin model is intentionally open-ended. A project can add internal tools
or new provider domains without changing the core runtime.

## Secret-Safe Execution

Agents never receive API keys, OAuth tokens, SMTP passwords, app passwords, or
raw encrypted payloads. Operators connect providers through typed auth methods,
and agents receive only safe references, status, scopes, and diagnostics.

When a run plan grants a tool call, the daemon resolves credentials inside the
provider process, executes the explicit request, redacts sensitive output, and
records the action call.

Before asking for credentials, agents should check only the selected workflow
or action with `readiness.check`. This keeps setup guidance scoped to the
providers and budgets that matter for the next piece of work instead of making
an empty project look broken because every possible provider is disconnected.

## Operator Console

The StackOS console is generic by design. It focuses on:

- projects and enabled plugins
- connected tools and credential status
- workflow templates and concrete run plans
- resources and artifacts
- action call history
- context, learnings, experiments, and decisions

Domain plugins can contribute navigation and resource definitions, but the UI
should render configuration and run state generically.

## Agent Operating Loop

The normal MCP path is intentionally compact:

1. Start with `workspace.startSession`. It creates or reuses the local
   workspace binding and returns the workspace-bound project, setup state, and
   UI links.
2. Use `toolbox.describe` and `toolbox.call` for project operations such as
   `workflowTemplate.list`, `agentPreset.list`, `readiness.check`,
   `tracker.status`, `runPlan.create`, and `action.run`.
3. Choose a workflow template when the work should follow a reusable contract,
   then create and start a run plan. Step-scoped grants control write access to
   actions, resources, artifacts, decisions, and evidence.
4. Use tracker tasks and tickets for durable planning and delivery state. Agents
   create dependencies, update status atomically, and record completion evidence
   instead of leaving work only in chat.
5. Resolve agent presets for the selected workflow, then adapt those generic
   role contracts to the project stack, local rules, docs, skills, and signoff
   expectations before using them as host-side agents.

First-run and ongoing sessions use the same entrypoint. On first run,
`workspace.startSession` bootstraps the binding. In ongoing work, it simply
resolves the existing project and agents call only the scoped tools needed for
the current task.

## Agent And Automation Access

Callable behavior is registered once as a StackOS operation or plugin action
contract. The same contract can be exposed through MCP, REST, CLI, and the UI
operation catalog when allowed.

Workspace binding resolves which StackOS project the current MCP bridge session
may access. It does not grant filesystem access to the repository. Reading or
editing repo files is a separate host-agent capability outside StackOS.

Agents should inspect operation/action descriptions before calling tools.
Scripts can use the CLI or REST operation endpoint for the same execution path.
Provider/vendor calls go through plugin actions: `action.run` for one explicit
direct call, or `action.execute` from a granted run-plan step. Provider-neutral
messages use `communication.send` and `communication.reply`. Direct MCP tools
are reserved for generic StackOS primitives, while most project work flows
through the scoped toolbox to avoid cluttering agent context with every
provider, plugin, and workflow operation at once.

Communication reads are stored-state reads unless a provider-specific history
action exists. `communicationContext.query` returns messages and interactions
that StackOS sent, ingested, or otherwise recorded, and `resource_get` inspects
those stored records. It does not fetch arbitrary Slack channel history or
backfill messages that were never delivered to StackOS. Telegram bots are more
limited by the platform: they generally receive updates going forward through
webhook/polling rather than fetching prior chat history.

## Running Locally

For repository development:

```bash
make install
make serve
```

Open the committed StackOS UI bundle:

```text
http://127.0.0.1:5180/
```

For package/operator installs:

```bash
stackos install
stackos start
```

For Vue UI development, run `make dev-ui` and open:

```text
http://127.0.0.1:5173/
```

The complete setup contract, first-run UI sequence, autostart commands, and
agent-from-any-repo flow are in [`docs/setup.md`](./docs/setup.md).

## Documentation

Start with [`docs/README.md`](./docs/README.md). It routes architecture,
operations, auth, plugin, workflow, run-plan, UI, and integration-contract
questions to the right canonical document.

Installed StackOS and deciding what to do next? Use the public
[getting-started guide](https://stackos.flowmonkey.io/getting-started). It is
the same guide linked by the desktop app and fetched by StackOS-aware agents.

Useful technical entrypoints:

- [`docs/setup.md`](./docs/setup.md)
- [`docs/architecture.md`](./docs/architecture.md)
- [`docs/operations.md`](./docs/operations.md)
- [`docs/action-executor.md`](./docs/action-executor.md)
- [`docs/auth-providers.md`](./docs/auth-providers.md)
- [`docs/plugins.md`](./docs/plugins.md)
- [`docs/workflow-templates.md`](./docs/workflow-templates.md)
- [`docs/run-plans.md`](./docs/run-plans.md)
- [`docs/extending.md`](./docs/extending.md)
- [`docs/repository-rename.md`](./docs/repository-rename.md)

## Clean-Cut Rule

The current architecture is StackOS-first. Removed flows should not remain as
routes, MCP tools, UI pages, tests, install assets, or active docs. When a flow
is replaced, update the model, operation/action surface, plugin/template
metadata, UI renderer, tests, and documentation in the same delivery.
