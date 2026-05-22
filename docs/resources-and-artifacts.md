# StackOS Resources And Artifacts

Resources and artifacts are the generic storage layer for StackOS. The rule is
the same everywhere: the agent decides what matters; StackOS stores, retrieves,
filters, redacts, and audits static data.

## Resources

A resource is a plugin-declared schema, such as `core.learning`,
`seo.keyword-opportunity`, `media-buying.campaign-brief`, or
`utils.generated-image`.

Resource schemas live in plugin manifests and are synced into the catalog. They
are not workflow logic. They describe record shape so generic UI, agents, and
validators can render and retrieve data without every plugin needing custom
screens.

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

Writes are granted through run plans/tool grants. They are not global
agent powers.

## Artifacts

An artifact is a stored reference to generated or fetched material: image,
video, export, screenshot, page snapshot, document, or any other blob-like
output.

Artifact rows live in `artifacts`:

- `project_id`: optional project owner.
- `plugin_id`: optional plugin/provider owner.
- `resource_record_id`: optional link to a generic resource record.
- `kind`: generic type such as `image`, `web-document`, `video`, or `export`.
- `uri`: local or external artifact reference.
- `name`, `mime_type`, `size_bytes`: optional display/storage metadata.
- `metadata_json`, `provenance_json`: sanitized metadata.

MCP read tools:

- `artifact.get`
- `artifact.query`

MCP write tool:

- `artifact.create`

Metadata and provenance are deep-redacted for secret-looking keys such as
tokens, API keys, passwords, authorization headers, and credentials.

## Plugin Ownership

Domain plugins own domain records as resources. SEO can own keyword
opportunities and content pieces. Publishing can own published-post refs and
publish targets. Media-buying can own campaign briefs, creative variants,
placements, or spend snapshots. GTM can own lead segments or outreach tasks.
The core does not create typed tables or workflow screens for those domains.

## Agent Boundary

- resource/artifact reads are bounded and filterable.
- writes require explicit grants.
- secrets are never returned to agents.
- tools remain static execution/storage surfaces, not decision engines.

This keeps StackOS useful for context retrieval and durable memory while
preserving the clean separation: agents decide, StackOS stores and executes
explicit requests.
