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

Compact reads keep bounded resource/record summaries, record ids, `data_json`,
timestamps, counts, and cursors so agents can choose the next call without a
raw retry.

MCP write tool:

- `resource.upsert`

Writes are granted through run plans/tool grants. They are not global
agent powers.

## Artifacts

An artifact is a stored reference to generated or fetched material: image,
video, export, screenshot, page snapshot, document, or any other blob-like
output. Artifacts are intentional durable records, not the default scratchpad
for normal agent iteration. Use them for approved outputs, final publication
packets, durable evidence, operator-approved draft records, or workflow outputs
that the workflow deliberately preserves. Local working drafts, research notes,
angle exploration, and review scratch should follow the current project's local
agent instructions instead of being forced into StackOS artifacts.

Artifact rows live in `artifacts`:

- `project_id`: optional project owner.
- `plugin_id`: optional plugin/provider owner.
- `resource_record_id`: optional link to a generic resource record.
- `kind`: generic type such as `image`, `web-document`, `video`, or `export`.
- `uri`: local or external artifact reference.
- `status`: lifecycle state, normally `draft`, `approved`, `superseded`, or
  `archived`.
- `superseded_by_artifact_id`: optional replacement artifact pointer.
- `name`, `mime_type`, `size_bytes`: optional display/storage metadata.
- `metadata_json`, `provenance_json`: sanitized metadata.
- `created_at`, `updated_at`: lifecycle timestamps.

MCP read tools:

- `artifact.get`
- `artifact.query`

MCP write tool:

- `artifact.create`
- `artifact.update`
- `artifact.archive`
- `artifact.supersede`

`artifact.create` creates a durable artifact row. New artifacts default to
`draft` unless the caller supplies a different lifecycle status. Use
`artifact.update` to refine an intentionally durable artifact, attach review
metadata, or mark it `approved`. Use `artifact.supersede` when a durable draft
or output is replaced by another artifact. Use `artifact.archive` for accidental
artifact clutter or records that should no longer appear in normal active
queries. Hard delete is intentionally not the default lifecycle operation
because artifacts are part of workflow evidence and audit history.

`artifact.query` returns active artifacts (`draft` and `approved`) by default.
Pass `status` to query a specific state or `include_inactive=true` to include
`superseded` and `archived` rows.

Metadata and provenance are deep-redacted for secret-looking keys such as
tokens, API keys, passwords, authorization headers, and credentials.

## File-Backed Action Outputs

External provider actions executed through `action.run` or `action.execute`
write sanitized request+response envelopes to generated-assets files by default.
Execution contexts or one-off action calls can still set `output_policy_json`:

- `{"mode": "inline"}` keeps the sanitized action output inline.
- `{"mode": "file_if_large", "max_inline_bytes": 16000}` writes oversized
  sanitized JSON outputs to the generated-assets directory.
- `{"mode": "always_file"}` always writes the sanitized JSON output to a file.
- Add `path: "/absolute/project/or/workspace/path"` when the agent wants the
  response file written under a specific directory. The path must be absolute,
  and StackOS generates the filename.

The file uses `schema_version: stackos.action-output.v1` and includes sanitized
project/run/action metadata, request `input_json`, provider context, connector
`output_json`, connector metadata, cost, and duration. `action.run` and
`action.execute` return a compact pointer with the filesystem path, byte size,
SHA-256 checksum, content type, semantic name, `schema_ref`, and
`schema_operation`. Read the file only when the agent needs the full
request/response envelope. If the agent needs the envelope schema, call
`schema.get` with the returned `schema_ref`; do not depend on StackOS source
file paths.

These default response files are not artifacts or resources. Agents should read
them through the returned filesystem path when needed. Use `artifact.read` only
for intentional artifact rows created by `artifact.create` or connector actions
that deliberately return artifact ids.

`artifact.read` follows the same response-mode contract as other operations:
compact mode returns metadata and omits the content body; pass
`response_mode=raw` with a narrow `json_path`/`max_bytes` when the artifact
content should enter the agent context.

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
