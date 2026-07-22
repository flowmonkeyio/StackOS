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
    config:
      schema_version: stackos.action.v1
      connector: meta-ads
      operation: campaign.create
      requires_credential: true
      required_scopes:
        - ads_management
```

## Built-In Plugins

- `core`: project memory, learnings, experiments, decisions, and shared context.
- `communications`: Telegram bot, Slack bot, SMTP, IMAP, communication
  resources, and generic agent request triggers.
- `engineering`: tracked delivery workflow, SDLC agent presets, and engineering
  decision/evidence records.
- `branding`: governed, evidence-grounded authority content production,
  canonical-first distribution, and outcome capture.
- `gtm`: go-to-market and RevOps provider contracts, resources, and templates.
- `marketing`: campaign production workflow, brand profile and campaign brief
  records, signoff evidence, and creative production agent presets.
- `media-buying`: paid media provider contracts, resources, and templates.
- `publishing`: CMS publishing providers, post actions, and publication records.
- `seo`: SEO content/search resources, providers, actions, and templates.
- `support`: customer issue investigation and delivery handoff workflows.
- `trackbooth`: Trackbooth Agent API action bridge for permission-filtered
  catalog discovery and explicit operation execution.
- `utils`: reusable utility actions such as image generation and edits, web
  retrieval, and the deferred video generation contract.

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

The provider and method `auth_type` values describe the actual protocol, not
the fact that setup happens to contain a secret string. OAuth application
credentials are OAuth, WordPress username/application-password pairs are
`application-password`, and client-credentials providers are
`oauth-client-credentials`; do not classify these as `api-key`. A provider may
offer several honest methods, such as interactive OAuth plus a manual token or
an API-token alternative.

Set `interactive: true` only on an auth method backed by a daemon OAuth
contract. The shared auth core then owns state, the fixed callback, code
exchange, renewal, scope records, concurrency, and sanitized failures. The
contract supplies trusted endpoints, client-auth style, scopes, PKCE mode, and
the few provider-specific hooks actually required. Provider connectors receive
an already-resolved credential and must not acquire or refresh tokens.

Actions that depend on OAuth grants declare exact `config.required_scopes`.
Those scopes are enforced by the shared credential resolver before the
connector runs. Provider-level consent scopes describe what StackOS asks the
operator to authorize; action-level required scopes keep each callable action
honest about the subset it needs.

Providers also declare `config.setup` as the single self-service source for
agents and setup UIs. Include the credential label, setup note, official
registration/console/credential/billing/docs URLs when known, fallback URL/reason
when exact pages are account-gated, `url_confidence` (`verified` or
`directional`), and `verified_at`. Do not repeat this provider setup guidance
inside every action.

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
The action remains available through catalog/setup inspection and
`include_unavailable_integrations=true` for planning, templating, auth setup,
and resource design, while normal action discovery hides it until execution is
actually available.

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
