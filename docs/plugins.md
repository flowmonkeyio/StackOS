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
  - key: campaign.create
    name: Create Campaign
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

Actions should not decide strategy. For example, `campaign.create` creates the
campaign structure the agent passes in; it does not decide which campaign should
exist.

Providers may include safe `config.setup_fields` for local-admin setup values
such as site URLs or API versions. These fields are stored in credential
`config_json`; secrets still belong only in the encrypted credential payload.

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
