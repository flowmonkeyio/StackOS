# Integration Contract Agent Notes

This directory is the review gate for StackOS integration contracts.

Use [`../README.md`](../README.md) for the broader documentation map. This file
is the local gate for provider-specific contract work.

## Expectations

- Use official provider documentation as the primary source.
- Link the exact documentation page for auth, object operations, rate limits,
  pagination, errors, and provider-specific constraints.
- Do not invent executable actions. If a connector is not implemented in
  `stackos/actions`, mark the action with an explicit deferred
  `execution_mode` and `deferred_reason`.
- StackOS stores static contracts, validates explicit inputs, resolves
  daemon-held credentials, calls connectors, and records audit. Agents and
  operators make business decisions.
- Never include secrets, API keys, OAuth tokens, bearer tokens, passwords, or
  credential refs that imply real access. Use provider keys and safe refs only.
- Prefer provider-specific action refs when provider schemas differ. Avoid
  generic one-level actions such as `campaign.create` or `contact.upsert` unless
  the user has configured a local abstraction through a project-local plugin.
- Communications providers must fit the generic communication graph: profile,
  surface/channel, membership, contact, target/route, thread, message, event,
  interaction, and agent request. Generic target/context operations may resolve
  or read state, but provider sends and live provider history fetches remain
  explicit provider actions with provider-specific contracts.
- Use safe reference fields such as `account_ref`, `company_ref`, `contact_ref`,
  `campaign_ref`, `sequence_ref`, and `record_refs`; do not require provider
  object ids in reusable templates.
- Templates describe setup, context requirements, action/resource contracts,
  approval gates, outputs, and failure behavior. Concrete action inputs belong
  in run plans.
- Resources store durable records and provenance; connectors normalize external
  responses into safe JSON and action-call audit rows.
- External provider action outputs are file-backed by default when invoked
  through `action.run` or `action.execute`. Connectors should return sanitized
  JSON and summaries. The compact response carries `schema_ref` plus
  `schema_operation=schema.get` for the shared response-file envelope; do not
  invent connector-specific schema paths or raw-file write paths unless the
  provider contract explicitly requires a separate generated asset.

## Review Shape

Each contract review should include:

- provider docs ledger with official links
- auth and setup implications
- proposed provider keys and safe auth method fields
- action refs, risk levels, and executable status
- input/output contract principles
- resource mapping
- approval and budget considerations
- pagination/rate-limit/error handling notes
- gaps before enabling execution
- recommended manifest/template corrections

## Signoff Checklist

Before an integration delivery is signed off, verify:

- builtin plugin manifests load successfully
- the connector has a current row in
  [`connector-quality.md`](./connector-quality.md) covering validation, safe
  errors, pagination/status, rate limits/budget, provider docs, and audit depth
- every workflow action contract points to an action in the matching plugin
- stale action refs from replaced contracts are absent from current manifests,
  workflow templates, tests, and operator-facing docs
- REST, MCP, repository, and schema tests align with the manifest action names
- deferred actions have no `config.connector`, include `execution_mode` and
  `deferred_reason`, and report the deferred/project-local availability state
- executable actions have daemon connector docs links, sanitized error handling,
  no-secret auth resolution, audit coverage, and grant tests
- provider HTTP failures raise `ActionConnectorError` and preserve redacted
  `provider_status_code` and `provider_error` fields in both operation error
  envelopes and failed action-call `response_json`
- executable external-provider actions return compact response-file paths,
  `schema_ref`, and `schema_operation` by default and avoid dumping bulky raw
  provider payloads into MCP responses;
  connector-created durable artifacts still return artifact ids when intentional
- setup metadata tells operators which safe refs/scopes/accounts are needed
  without exposing tokens, API keys, passwords, or raw provider ids
- provider manifest `config.setup` includes the credential label, setup note,
  official registration/console/API-key/billing/docs URLs where verified,
  `url_confidence`, `verified_at`, and a fallback URL/reason when exact vendor
  setup pages are account-gated or not public
- `integration.list`, `readiness.check`, `action.describe`, and compact
  `auth.status` expose enough setup metadata for an agent to answer where to
  connect in StackOS, where to register with the vendor, and where to get the
  API key

For media-generation image or video tools, also follow
[`media-generation-runbook.md`](./media-generation-runbook.md). It is the
required checklist for capability metadata, generated-asset persistence,
async video jobs, unsupported provider features, and per-provider signoff.

## Comments And Links

When implementation files need provider-specific details, add concise comments
with links to the exact official documentation page. Do not add link dumps to
runtime code; place larger doc ledgers in this directory and reference them from
manifests or implementation comments only when useful.
