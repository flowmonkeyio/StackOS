# StackOS Action Executor

D08 added the internal action execution foundation. D10 exposes it through
run-plan-scoped grants for the first utility action. It is intentionally a
daemon substrate, not an agent decision layer.

Agents and humans still decide what to do. StackOS only describes action
contracts, validates explicit payloads, resolves daemon-held credentials, calls
registered connector adapters, redacts output, records audit, and enforces
mechanical limits such as idempotency and optional budget pre-emption.

## Action Manifest

Action catalog rows come from plugin manifests. The executable fields live in
`actions.config_json`:

```json
{
  "schema_version": "stackos.action.v1",
  "connector": "openai-images",
  "operation": "image.generate",
  "requires_credential": true,
  "allows_credential": true,
  "budget_kind": "openai-images",
  "enforce_budget": true
}
```

The manifest is static configuration. It must not contain API keys, bearer
tokens, OAuth tokens, passwords, refresh tokens, or provider-specific strategy.
Raw secrets are rejected during manifest parsing.

Credential refs are rejected unless the action manifest explicitly allows
credential use. For most authenticated providers, `requires_credential` implies
`allows_credential`; no-auth/local actions do not receive credentials by
accident.

## Connector Boundary

Connectors implement the tiny adapter contract in
`content_stack/actions/connectors.py`:

- `validate(request)`: payload checks without provider side effects.
- `estimate_cost_cents(request)`: mechanical cost estimate.
- `execute(request)`: provider/tool call with an already-resolved credential.

Connectors receive plaintext secrets only inside the daemon process through
`ResolvedCredential`. That object is not a Pydantic response model and must not
be serialized into MCP, REST, run plans, resources, artifacts, or audit rows.

## Audit

Every internal execution writes an `action_calls` sidecar row with:

- project/run/run-plan linkage when available
- plugin/action/provider/connector identity
- opaque `credential_ref` and internal credential id
- redacted request/response/metadata
- status, dry-run flag, duration, cost, error, and idempotency key

`action.execute` returns the public action-call audit shape. Internal database
identifiers such as `credential_id`, `action_id`, and replay-only
`idempotency_key` stay in storage and are not returned to agents.

The table is part of the clean StackOS core. Domain plugins store their durable
objects in resources/artifacts; the core action executor does not preserve
legacy workflow tables for compatibility.

## Availability

Visible catalog action rows and `action.describe` now include a generic
`availability` object. It is a setup signal, not a workflow decision:

- `status`: `ready`, `unknown`, `not_executable`, `missing_connector`,
  `missing_credential`, `credential_failed`, `missing_budget`,
  `budget_blocked`, `plugin_disabled`, or `provider_disabled`
- `executable`: whether the current project setup can run the action
- connector, operation, credential, and budget state
- safe opaque credential refs when connected
- machine-readable reasons such as `credential_required` or `budget_required`

Agents may use this to know what setup is missing, but they still pass exact
payloads and action refs. StackOS does not infer which action should run.
`plugin_disabled` and `provider_disabled` are also enforced by `action.execute`
because they are static setup policy, not agent strategy.

## MCP Surface

Direct/read discovery tools:

- `action.describe`
- `action.validate`

Hidden, run-plan-scoped execution tool:

- `action.execute`

`action.execute` is not direct agent surface. It is callable only through a
started run plan, exactly one active claimed step, an explicit
`mcp_tool_grants` entry with `tool: "action.execute"`, and matching
`action_refs`. The active step must also declare the same action ref in
`action_refs`.

Registered first-party connectors now cover the migrated clean path for:

- `openai-images`: `utils.image.generate`
- `firecrawl`: `utils.web.scrape`, `utils.web.crawl`, `utils.web.map`,
  `utils.web.extract`
- `jina`: `utils.web.read` with optional credentials
- `sitemap`: `utils.sitemap.fetch`
- `reddit`: `utils.reddit.search-subreddit`, `utils.reddit.top-questions`
- `dataforseo`: `seo.keyword.research`, `seo.serp.analyze`, `seo.paa.extract`
- `ahrefs`: `seo.competitor.keywords`, `seo.backlink.research`
- `wordpress`: `publishing.wordpress.post.create`
- `ghost`: `publishing.ghost.post.create`

The OpenAI Images connector persists base64 image bytes under generated assets
and returns local artifact URLs with no `b64_json` payload. Other connectors
normalize wrapper results into action output JSON and record the provider,
operation, cost, status, and redacted payloads in `action_calls`.

## Boundary

Actions are dumb execution units. They do not pick campaigns, choose variants,
optimize budgets, interpret SEO opportunities, or decide next steps. Those
decisions belong to the agent/person and are passed into StackOS as explicit
payloads, run plans, resources, learnings, decisions, or approvals.
