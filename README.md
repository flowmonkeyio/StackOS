# StackOS

StackOS is a local operating layer for agent-run business work. It lets a
project install the tools it needs, connect provider accounts safely, reuse
workflow templates, and give agents a consistent way to execute work without
handing them secrets.

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
| Plugin | A domain package such as SEO, media buying, GTM, publishing, or utilities. |
| Connection | A safe provider credential profile, such as an API key, OAuth account, SMTP setup, CMS account, or internal webhook credential. |
| Workflow template | Reusable setup for repeated work, including inputs, context needs, gates, expected outputs, and default steps. |
| Run plan | One concrete execution instance with scoped permissions, steps, outputs, and audit history. |
| Action call | A validated provider/tool call executed by the daemon and recorded for review. |

## Current Domains

First-party plugin coverage currently includes:

- **SEO**: keyword research, SERP analysis, PAA extraction, competitor/backlink
  research, SEO resources, and reusable SEO templates.
- **Media buying**: paid media providers, campaign and creative resources,
  launch/review templates, and first provider connectors.
- **GTM and RevOps**: CRM, enrichment, outbound, workspace, and pipeline
  provider contracts with reusable GTM templates.
- **Publishing**: WordPress and Ghost publishing actions.
- **Utilities**: image generation, web retrieval, sitemap fetching, Reddit
  research, and generic configured HTTP tools.

The plugin model is intentionally open-ended. A project can add internal tools
or new provider domains without changing the core runtime.

## Secret-Safe Execution

Agents never receive API keys, OAuth tokens, SMTP passwords, app passwords, or
raw encrypted payloads. Operators connect providers through typed auth methods,
and agents receive only safe references, status, scopes, and diagnostics.

When a run plan grants a tool call, the daemon resolves credentials inside the
provider process, executes the explicit request, redacts sensitive output, and
records the action call.

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

## Agent And Automation Access

Callable behavior is registered once as a StackOS operation or plugin action
contract. The same contract can be exposed through MCP, REST, CLI, and the UI
operation catalog when allowed.

Agents should inspect operation/action descriptions before calling tools.
Scripts can use the CLI or REST operation endpoint for the same execution path.
Provider/vendor calls go through plugin actions and `action.execute`; direct
MCP tools are reserved for generic StackOS primitives.

## Running Locally

For repository development:

```bash
TPF_LLM_TOOL=codex tpf make install
TPF_LLM_TOOL=codex tpf make serve
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

## Clean-Cut Rule

The current architecture is StackOS-first. Removed flows should not remain as
routes, MCP tools, UI pages, tests, install assets, or active docs. When a flow
is replaced, update the model, operation/action surface, plugin/template
metadata, UI renderer, tests, and documentation in the same delivery.
