# Plugins

Plugins are StackOS domain packages. They let the core stay generic while each
domain brings its own capabilities, providers, resources, actions, workflow
templates, and optional navigation.

## Manifest

Plugin manifests live at `plugins/<slug>/plugin.yaml`:

```yaml
slug: media-buying
name: Media Buying
version: 0.1.0
description: Campaign, creative, budget, and reporting operations.
capabilities:
  - key: campaign-management
    name: Campaign Management
    kind: domain
providers:
  - key: meta-ads
    name: Meta Ads
    auth_type: oauth
resources:
  - key: campaign
    name: Campaign
    schema:
      type: object
      additionalProperties: true
actions:
  - key: meta.campaign.create
    name: Create Meta Campaign
    provider: meta-ads
    capability: campaign-management
    risk_level: write
    input_schema:
      type: object
      additionalProperties: true
    output_schema:
      type: object
      additionalProperties: true
```

## Built-In Plugins

- `core`: project memory, learnings, experiments, decisions, and shared context.
- `communications`: Telegram bot, SMTP, IMAP, communication resources, and
  planned agent request triggers.
- `gtm`: go-to-market and RevOps provider contracts, resources, and templates.
- `media-buying`: paid media provider contracts, resources, and templates.
- `publishing`: CMS publishing providers, post actions, and publication records.
- `seo`: SEO content/search resources, providers, actions, and templates.
- `utils`: reusable utility actions such as image generation and web retrieval.

## Actions

Actions describe what can be called. Execution stays in daemon-side code so
auth, rate limits, retries, budget checks, and output normalization are enforced
outside the agent prompt.

An action should include:

- stable key
- provider
- capability
- risk level
- input schema
- output schema
- static config such as a local tool reference or vendor operation key

Actions should not decide strategy. For example, `meta.campaign.create` creates
the campaign structure the agent passes in for Meta; it does not decide which
campaign should exist.

Providers declare `auth_methods` for local-admin setup. Each method lists the
fields the UI should render, marks secret fields, and declares how the daemon
serializes the encrypted payload (`raw`, `json`, or `none`). Safe fields such
as site URLs, account refs, API versions, or SMTP host/port values are stored
as credential config; secret fields such as API keys, passwords, client
secrets, refresh tokens, and bearer tokens stay only in the encrypted backing
payload.

Custom internal tools can use the generic HTTP/Webhook connector by declaring a
static action config:

```yaml
config:
  schema_version: stackos.action.v1
  connector: http
  operation: request
  requires_credential: true
  http:
    method: POST
    url: https://internal.example/actions/create-campaign
    auth:
      type: bearer
    request_mode: json
    response_mode: json
```

The URL and auth mode are static plugin configuration. The agent supplies only
the action input JSON allowed by the action schema, and the daemon injects the
credential inside the connector process.

If a first-party provider connector is not implemented yet, mark the action with
an explicit `execution_mode` and `deferred_reason` instead of omitting intent.
The action remains visible for planning, templating, auth setup, and resource
design, while availability reports the deferred or project-local state.

## Resources

Resources are plugin-owned durable records. Use them for objects that agents
need to query, update, or link across runs: campaigns, content pieces,
creatives, leads, experiments, generated assets, and so on.

The core UI should render resources generically by plugin and key.

## Workflow Templates

Plugins can ship workflow templates under `plugins/<slug>/workflows/`. These
templates should define reusable setup and context requirements, not one-off run
state. Agents create concrete run plans from them.

## Enablement

Plugins can be enabled per project. A disabled plugin should not appear in the
project catalog, but its historical records can still be displayed when they are
part of prior run history.

## Tests

Plugin changes should verify:

- manifest validation
- built-in catalog sync
- provider/action/resource indexing
- project enable/disable behavior
- workflow template loading
- generic UI rendering for plugin nav and resources
