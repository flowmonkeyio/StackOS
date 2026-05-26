# Workflow Templates

Workflow templates are reusable setup for agent work. They are not hidden
automation. A template gives the agent a strong starting structure, then the
agent creates a concrete run plan for the current project and goal.

## Template Schema

A template should be generic across domains and include:

- `schema_version`
- `key`, `name`, `version`, `description`
- `domain` and optional plugin slug
- `owner`
- `when_to_use` and `when_not_to_use`
- `inputs`
- `instructions`
- `context_requirements`
- `capability_requirements`
- `provider_requirements`
- `action_requirements`
- `policies`
- `approval_gates`
- `steps`
- `expected_outputs`
- `quality_checks`
- `history_policy`

The template should not contain project secrets or one-off task state.

## Context Requirements

Context requirements define how the agent can retrieve history without loading
everything:

```yaml
context_requirements:
  - id: recent_related_runs
    source: runs
    filters:
      domain: media-buying
      statuses: [success, failed]
    fields: [kind, status, summary, output_json, ended_at]
    max_items: 10
    return_mode: compact
```

Supported sources should include runs, resources, artifacts, learnings,
experiments, decisions, action calls, and provider status.

## Steps

Steps are defaults, not a prison. A good step defines:

- `id`
- `title`
- `purpose`
- `instructions`
- `allowed_actions`
- `inputs`
- `expected_outputs`
- `approval_gate` if needed
- `completion_criteria`

Agents can adapt a run plan when the project requires it. If a project repeats
the adapted pattern, the agent should save a project-scoped template version.

Template step refs are planning contracts, not executable grants. For example,
`action_refs: [send_email]` points at an `action_contracts` entry. When an
agent derives a run plan, it must resolve that contract to concrete action refs
and MCP grants such as `action.execute` with `action_refs:
[communications.smtp.email.send]`. `runPlan.validate` returns warnings when a
template-derived plan is structurally valid but lacks the grants needed to run.

## Built-In And Project Templates

StackOS can ship built-in templates through plugins. A project can also save
its own templates. Project templates should record their source and version so
agents can understand whether they are using a built-in pattern or a local
operating method.

## Examples

SEO templates can describe keyword discovery, page refresh, or search
opportunity analysis. Media-buying templates can describe campaign launch, creative
testing, budget pacing, or account QA. GTM templates can describe list building,
sequence setup, pipeline hygiene, or launch retrospectives.

All of them should use the same StackOS primitives: context, resources,
artifacts, actions, approvals, learnings, experiments, and run plans.

## Validation

Template validation should check:

- stable keys and versions
- valid input schema
- known plugin/capability/provider/action references
- bounded context filters
- approval gates referenced by steps
- no embedded secrets
- no domain-only assumptions in core fields
