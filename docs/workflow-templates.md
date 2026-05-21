# Workflow Templates

Workflow templates are reusable StackOS setup contracts. They give an agent a
strong base for a class of work, but they are not run plans and they do not
execute actions.

Templates may describe:

- purpose, when to use, and when not to use
- required inputs and expected outputs
- bounded context requirements
- capability and auth requirements
- action and resource contracts
- policies, approval gates, and failure handling
- reusable learning hooks
- UI/rendering metadata

Templates must not contain:

- API keys, OAuth tokens, passwords, bearer strings, or credential refs
- concrete provider object ids, such as a real campaign id or ad account id
- final action payloads for a specific run
- hard-coded business decisions, such as which experiment won
- procedure-runner control flow

Agents turn templates into concrete run plans. The run plan is where selected
providers, credential refs, payload drafts, context snapshots, approvals, and
action order become explicit. See `docs/run-plans.md` for the concrete
execution record and approval-gate lifecycle.

## Sources And Precedence

Templates can come from three places:

```text
Plugin defaults:
  plugins/<plugin>/workflows/*.yaml

Project/user templates:
  workflow_templates + workflow_template_versions tables

Repository/company overrides:
  .stackos/workflows/*.yaml
```

Effective precedence is:

```text
repo/company > project/user > plugin
```

`workflowTemplate.list` returns the effective set by default. Passing
`include_shadowed=true` also returns lower-precedence templates with
`shadowed_by` set.

## Schema

Templates use `schema_version: stackos.workflow-template.v1`.

Minimal example:

```yaml
schema_version: stackos.workflow-template.v1
key: core.project-memory-review
name: Project Memory Review
version: 0.1.0
inputs:
  - key: goal
    type: string
    required: true
context_requirements:
  - id: accepted_learnings
    source: learnings
    filters:
      statuses: [active]
    fields: [statement, confidence, tags]
    max_items: 20
steps:
  - id: retrieve-context
    title: Retrieve bounded context
    context_refs: [accepted_learnings]
    output_refs: [context_summary]
outputs:
  - key: context_summary
    type: object
```

Context sources are intentionally generic: `runs`, `events`, `index`,
`snapshots`, `learnings`, `experiments`, `decisions`, `metrics`, `resources`,
and `artifacts`.

## MCP Boundary

Agent-visible read tools:

- `workflowTemplate.list`
- `workflowTemplate.describe`
- `workflowTemplate.validate`

Admin-gated mutation tools:

- `workflowTemplate.save`
- `workflowTemplate.fork`

`validate` is read-only for workflow templates. It only checks schema,
references, limits, and secret-like values. `save` and `fork` are deliberately
not system-granted before the broader D09 action/grant cleanup.

## Clean Cut

D06 adds workflow-template sidecars only. It does not drop old SEO,
procedure, or content-stack tables, and it does not migrate existing procedures
into templates. Legacy procedures remain compatibility execution until the
later migration tasks.
