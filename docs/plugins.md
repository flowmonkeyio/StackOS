# StackOS Plugins

Plugins are the boundary between StackOS core and domain/tooling behavior.

StackOS core owns storage, retrieval, schema validation, auth/grant/budget
enforcement, redaction, execution plumbing, audit, and generic UI renderers.
Plugins describe what exists for a domain: capabilities, providers, static
action schemas, resource schemas, workflow templates, and UI contributions.

Agents and humans decide how to use those tools. Plugin manifests must not
encode business strategy such as choosing winners, optimizing campaigns, or
deciding the next topic.

Executable action details are documented in
[`action-executor.md`](action-executor.md). In D08, StackOS can describe,
validate, and internally execute configured actions through daemon connectors,
but normal agents still see only `action.describe` and `action.validate`.

## Manifest Shape

The catalog manifest is intentionally metadata-only:

```yaml
slug: seo
name: SEO
version: 0.1.0
description: Compatibility plugin facade for current SEO content operations.
source: builtin
capabilities:
  - key: seo-content
    name: SEO Content
    description: Topics, articles, sources, links, schema, and publishing.
    kind: domain
providers:
  - key: gsc
    name: Google Search Console
    description: Search Console OAuth-backed metrics provider.
    auth_type: oauth
actions:
  - key: topic.bulk-create
    name: Create Topic Candidates
    description: Create topic records from agent-selected SEO candidates.
    capability: seo-content
    risk_level: write
    input_schema:
      type: object
      additionalProperties: true
    output_schema:
      type: object
      additionalProperties: true
resources:
  - key: article
    name: Article
    description: Compatibility schema for SEO article records.
    schema:
      type: object
      additionalProperties: true
```

Required identifiers use lowercase kebab or dotted form, for example
`seo-content`, `openai-images`, or `catalog.describe`.

Workflow templates live beside plugin metadata as files:

```text
plugins/<plugin>/workflows/*.yaml
```

The loader treats those files as plugin defaults. They can be overridden by
project/user templates in the DB or by repository/company templates under
`.stackos/workflows`.

## Built-In Plugins

The initial StackOS daemon registers three built-ins:

- `core`: domain-neutral StackOS catalog/workflow/project-data primitives.
- `seo`: compatibility facade for the current SEO content operations.
- `utils`: reusable utility capabilities and providers such as image
  generation and web retrieval.

Built-in resource schemas include:

- `core`: `project-note`, `learning`, `experiment`.
- `seo`: `topic`, `article`, `research-source`, `article-asset`.
- `utils`: `generated-image`, `web-document`.

## Agent Exposure

Before D09, normal agents may discover catalog metadata and bounded generic
reads:

- `action.describe`
- `action.validate`
- `plugin.list`
- `catalog.list`
- `catalog.describe`
- `capability.list`
- `capability.describe`
- `provider.list`
- `provider.describe`
- `auth.status`
- `auth.test`
- `resource.get`
- `resource.query`
- `artifact.get`
- `artifact.query`
- `workflowTemplate.list`
- `workflowTemplate.describe`
- `workflowTemplate.validate`

Setup/admin-gated mutations are registered in the daemon catalog but are not
advertised through the normal agent bridge and are not granted to the
system/bootstrap agent surface before D09:

- `plugin.enable`
- `plugin.disable`
- `auth.start`
- `auth.revoke`
- `resource.upsert`
- `artifact.create`
- `workflowTemplate.save`
- `workflowTemplate.fork`

Generic execution is also withheld from normal agents before the grant model:

- `action.execute`

Credential setup is metadata-driven by plugin providers, but secrets stay
outside agent context. Provider manifests declare `auth_type`; the daemon maps
that declaration into auth-provider rows and opaque credential refs. Agents use
`auth.status` and `auth.test`, while local UI/REST setup handles plaintext or
OAuth. Compatibility tools such as `integration.set` remain daemon-local and are
not part of the normal plugin agent surface.
