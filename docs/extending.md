# Extending StackOS

Add new behavior through plugins, workflow templates, providers, actions, and
generic UI renderers. Core changes should be reserved for primitives that every
domain can use.

## Add A Plugin

Create a plugin manifest under `plugins/<slug>/plugin.yaml` with:

- `slug`, `name`, `version`, `description`
- capabilities
- providers
- resources
- actions
- optional UI navigation
- optional workflow templates under `plugins/<slug>/workflows/`

Plugin slugs and resource keys should be stable. A plugin can represent a
domain such as SEO, media buying, GTM, analytics, or utilities.

## Add A Provider

A provider describes an external or local system:

```yaml
providers:
  - key: meta-ads
    name: Meta Ads
    auth_type: oauth
    description: Media-buying account and campaign operations.
```

Provider implementation belongs in daemon-side integration code. It should own:

- auth resolution
- retries and rate limits
- budget checks
- request/response normalization
- credential usage recording
- safe error messages

Agents should pass provider/account references, not secrets.

## Add An Action

Actions are static tool definitions:

```yaml
actions:
  - key: meta.campaign.create
    name: Create Meta Campaign
    provider: meta-ads
    capability: campaign-management
    risk_level: write
    input_schema:
      type: object
      required: [account_ref, campaign]
      properties:
        account_ref: { type: string }
        campaign: { type: object }
    output_schema:
      type: object
```

The action validates shape and executes the requested operation. The agent
decides campaign intent, structure, budget strategy, and variants before calling
the action.

For user-owned HTTP/Webhook tools, use the generic daemon connector instead of
adding core code:

```yaml
config:
  schema_version: stackos.action.v1
  connector: http
  operation: request
  requires_credential: true
  http:
    method: POST
    url: https://internal.example/tools/campaigns
    auth: { type: bearer }
    request_mode: json
    response_mode: json
```

The endpoint remains static configuration. The action's `input_schema` defines
the explicit input body the agent may pass, and the daemon injects the provider
credential.

## Add A Resource

Resources define durable plugin-owned records:

```yaml
resources:
  - key: campaign
    name: Campaign
    description: External or local campaign metadata.
    schema:
      type: object
      additionalProperties: true
```

Prefer generic resource rendering unless the domain truly needs a specialized
editor.

## Add A Workflow Template

Create `plugins/<slug>/workflows/<template-key>.yaml`:

```yaml
schema_version: stackos.workflow-template.v1
key: media-buying.campaign-launch
name: Media Buying Campaign Launch
version: 0.1.0
domain: media-buying
inputs:
  - key: goal
    type: string
    required: true
context_requirements:
  - id: recent_campaign_runs
    source: runs
    fields: [kind, status, summary, output_json, ended_at]
    max_items: 10
steps:
  - id: inspect-context
    title: Inspect Context
    instructions: Review prior runs, learnings, active experiments, and provider status.
  - id: propose-plan
    title: Propose Launch Plan
    instructions: Decide channels, structure, budget, assets, and approval needs.
  - id: execute-approved-actions
    title: Execute Approved Actions
    instructions: Call only validated actions after required approvals.
outputs:
  - key: launch_summary
    type: object
```

Templates should define the reusable setup and constraints. Concrete action
items belong in the run plan created by the agent.

## Add A Callable Operation

Register callable behavior once as a StackOS operation, then expose it through
allowed MCP, REST, CLI, and UI operation-catalog surfaces from that spec.

An operation change should define or update:

- input/output models
- handler and repository invariant
- mutating/read-only classification
- surface policy for MCP, REST, CLI, and UI docs
- grant policy and no-secret behavior
- examples and agent-facing guidance
- tests for visibility, grants, validation, and audit
- generated UI API types when REST/resource routes change
- documentation

Direct MCP tools are only for generic StackOS primitives. Provider/vendor
operations should be plugin actions executed through `action.run` for one
explicit direct action or `action.execute` inside a granted run-plan step. If a
provider needs a new callable operation, add the provider manifest entry, action
manifest, connector, grant tests, integration-contract docs, and operation
visibility together.

## Add UI

Start with generic renderers:

- plugin catalog row
- resource table/detail
- artifact preview
- workflow template detail
- run-plan step renderer
- action call history

Specialized UI is acceptable only when the generic renderer cannot support the
workflow without harming operator accuracy or speed.

## Add Tests

Minimum useful coverage:

- manifest validation
- provider/auth no-secret behavior
- action validation and execution wrapper
- workflow template parsing
- run-plan creation and step recording
- MCP direct/hidden surface expectations
- UI rendering for plugin/resource/template/run-plan objects
