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
  - key: campaign.create
    name: Create Campaign
    provider: meta-ads
    capability: media-buying
    risk_level: write
    input_schema:
      type: object
      required: [account_id, campaign]
      properties:
        account_id: { type: string }
        campaign: { type: object }
    output_schema:
      type: object
```

The action validates shape and executes the requested operation. The agent
decides campaign intent, structure, budget strategy, and variants before calling
the action.

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
key: media-buying.launch-campaign
name: Launch Campaign
version: 0.1.0
domain: media-buying
inputs:
  - key: objective
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
expected_outputs:
  - launch_summary
  - action_call_ids
```

Templates should define the reusable setup and constraints. Concrete action
items belong in the run plan created by the agent.

## Add MCP Tools

Add direct MCP tools only for generic StackOS primitives. Domain operations
should be actions or scoped hidden tools. For every tool change, update:

- input/output model
- repository invariant
- bridge visibility
- permission grant
- tests for grant and visibility
- generated UI API types when REST changes
- docs

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
