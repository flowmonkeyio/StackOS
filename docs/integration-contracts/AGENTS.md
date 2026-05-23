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
- Use safe reference fields such as `account_ref`, `company_ref`, `contact_ref`,
  `campaign_ref`, `sequence_ref`, and `record_refs`; do not require provider
  object ids in reusable templates.
- Templates describe setup, context requirements, action/resource contracts,
  approval gates, outputs, and failure behavior. Concrete action inputs belong
  in run plans.
- Resources store durable records and provenance; connectors normalize external
  responses into safe JSON and action-call audit rows.

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
- setup metadata tells operators which safe refs/scopes/accounts are needed
  without exposing tokens, API keys, passwords, or raw provider ids

## Comments And Links

When implementation files need provider-specific details, add concise comments
with links to the exact official documentation page. Do not add link dumps to
runtime code; place larger doc ledgers in this directory and reference them from
manifests or implementation comments only when useful.
