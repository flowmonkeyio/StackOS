# StackOS Resources And Artifacts

D03 adds generic storage primitives beside the current SEO tables. The rule is
the same StackOS rule everywhere: the agent decides what matters; StackOS stores,
retrieves, filters, redacts, and audits static data.

## Resources

A resource is a plugin-declared schema, such as `core.learning`,
`seo.article`, or `utils.generated-image`.

Resource schemas live in plugin manifests and are synced into the catalog. They
are not workflow logic. They describe record shape so generic UI, agents, and
future validators can render and retrieve the data without every plugin needing
custom screens.

Project records live in `resource_records`:

- `project_id`: the project context the record belongs to.
- `resource_id`: the plugin-declared schema.
- `external_id`: optional stable id for idempotent upsert.
- `title`: optional human-readable label.
- `data_json`: the record payload.
- `provenance_json`: optional source/run/tool metadata.

MCP read tools:

- `resource.get`
- `resource.query`

MCP write tool:

- `resource.upsert`

`resource.upsert` is intentionally admin-gated before D09. Normal agents can
read bounded resource context, but generic writes need the later grant model.

## Artifacts

An artifact is a stored reference to generated or fetched material: image,
video, export, screenshot, page snapshot, or any other blob-like output.

Artifact rows live in `artifacts`:

- `project_id`: optional project owner.
- `plugin_id`: optional plugin/provider owner.
- `resource_record_id`: optional link to a generic resource record.
- `kind`: generic type such as `image`, `web-document`, or `article-asset`.
- `uri`: local or external artifact reference.
- `name`, `mime_type`, `size_bytes`: optional display/storage metadata.
- `metadata_json`, `provenance_json`: sanitized metadata.

MCP read tools:

- `artifact.get`
- `artifact.query`

MCP write tool:

- `artifact.create`

`artifact.create` is also admin-gated before D09. Metadata and provenance are
deep-redacted for secret-looking keys such as tokens, API keys, passwords,
authorization headers, and credentials.

## SEO Compatibility

The existing SEO tables remain authoritative for current SEO workflows:

- `topics`
- `articles`
- `research_sources`
- `article_assets`
- related publishing/schema/link tables

D03 does not replace those tables. It adds resource schemas for them in the
`seo` plugin catalog so generic StackOS surfaces can understand they exist.

New article assets are mirrored into `artifacts` as `article-asset` references
with compatibility provenance pointing back to `article_assets`. That gives the
new artifact explorer a generic path while preserving the typed SEO API.

## Agent Boundary

The bridge exposes only bounded reads before D09:

- resource/artifact reads are visible.
- generic writes are registered but admin-gated.
- secrets are never returned to agents.
- tools remain static execution/storage surfaces, not decision engines.

This keeps StackOS useful for context retrieval today while leaving mutation
rights to the upcoming grant/run-plan model.
