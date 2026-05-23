# Integration Wrapper Agent Notes

Integration wrappers are daemon-side vendor clients used by action connectors
and auth tests.

## Expectations

- Read [`../../docs/auth-providers.md`](../../docs/auth-providers.md),
  [`../../docs/action-executor.md`](../../docs/action-executor.md), and the
  relevant contract review in
  [`../../docs/integration-contracts/`](../../docs/integration-contracts/)
  before changing vendor clients.
- Keep wrappers thin: auth headers, endpoint calls, rate limits, retries, cost
  extraction, and response persistence only.
- Link official provider docs beside each endpoint method or pricing table.
- Do not add agent-visible strategy, ranking, workflow, or business decisions.
- Use provider-reported request IDs, cost fields, and pagination/status fields
  when the docs expose them.
- Keep default QPS at or below the strictest documented limit for the exposed
  live endpoints. Project budgets may lower the limit further.
- Persist large binary/base64 outputs to generated assets and return artifact
  refs or local URLs instead of raw payloads.
- Do not keep dormant wrapper operations agent-executable without a plugin
  action manifest, schema, grants, docs, and tests.
- A wrapper method is infrastructure. It is not a supported tool until a plugin
  action and operation/action tests expose it through the clean StackOS path.

## Cost And Auth

- Budget pre-check estimates are guardrails, not invoice truth.
- Prefer provider response costs or usage headers for reconciliation.
- Secrets live in encrypted credential payloads and are decoded only inside the
  daemon process.
