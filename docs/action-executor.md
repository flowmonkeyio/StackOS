# StackOS Action Executor

The action executor is the daemon-side foundation for provider and utility
calls. It is intentionally a substrate, not an agent decision layer.

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
`stackos/actions/connectors.py`:

- `validate(request)`: payload checks without provider side effects.
- `estimate_cost_cents(request)`: mechanical cost estimate.
- `execute(request)`: provider/tool call with an already-resolved credential.

Connectors receive plaintext secrets only inside the daemon process through
`ResolvedCredential`. That object is not a Pydantic response model and must not
be serialized into MCP, REST, run plans, resources, artifacts, or audit rows.

When a provider returns an HTTP failure body, connectors must raise
`ActionConnectorError` with `provider_status_code` and `provider_error` instead
of collapsing the response into a generic validation string. The executor
records that structured output on the failed `action_calls.response_json` row
and surfaces the same redacted fields in the operation error envelope so CLI,
MCP, REST, and UI clients can diagnose rate limits, auth failures, and provider
validation errors without reading daemon logs.

## Audit

Every internal execution writes an `action_calls` sidecar row with:

- project/run/run-plan linkage when available
- plugin/action/provider/connector identity
- opaque `credential_ref` and internal credential id
- redacted request/response/metadata
- status, dry-run flag, duration, cost, error, and idempotency key

`action.run` and `action.execute` return compact action-call metadata by
default for MCP/REST when the operation policy allows it. External provider
actions write the sanitized request/response envelope to a response file and
return the file path, schema version, `schema_ref`, `schema_operation`, byte
size, content type, checksum, and semantic name. CLI calls default to raw inline
output unless an explicit output policy says otherwise. Agents fetch the schema
through `schema.get` only when they need the envelope contract; compact action
responses do not expose StackOS source paths or inline schema bodies.
Internal database identifiers such as `credential_id`, `action_id`, and replay-only
`idempotency_key` stay in storage and are not returned to agents. For write
actions, callers may pass `idempotency_key` or the more agent-friendly
`intent_id`; when neither is supplied, direct calls derive a request-scoped key
and workflow calls derive a stable run/step/action key before reaching the
connector.

The read API exposes the same public audit shape at
`GET /api/v1/projects/{project_id}/action-calls`. It can be filtered by run,
run plan, run-plan step, plugin, action key, and status. Results are returned
newest first; cursor pagination continues toward older calls. The StackOS UI
exposes this as the project-level **Action Calls** ledger, while run detail
continues to show the calls scoped to a specific run/step.

The table is part of the clean StackOS core. Domain plugins store their durable
objects in resources/artifacts; removed workflow-specific storage is not part
of the current execution model.

## Availability

Visible catalog action rows and `action.describe` now include a generic
`availability` object. It is a setup signal, not a workflow decision:

- `status`: `ready`, `unknown`, `deferred`, `project_local_required`,
  `not_executable`, `missing_connector`, `missing_credential`,
  `credential_failed`, `missing_budget`, `budget_blocked`, `plugin_disabled`,
  or `provider_disabled`
- `executable`: whether the current project setup can run the action
- connector, operation, credential, and budget state
- safe opaque credential refs when connected
- machine-readable reasons such as `credential_required` or `budget_required`

Agents may use this to know what setup is missing, but they still pass exact
payloads and action refs. StackOS does not infer which action should run.
`plugin_disabled` and `provider_disabled` are also enforced by `action.run` and
`action.execute` because they are static setup policy, not agent strategy.

## Operation Surface

Action calls are now registered once as StackOS operations, then exposed through
MCP, the generic REST operation endpoint, and the CLI when the operation surface
allows it. Agents and scripts can inspect the operation contract with:

```bash
stackos ops describe action.execute --json
stackos ops describe action.list --json
stackos ops describe action.run --json
```

or through `GET /api/v1/operations/action.execute`,
`GET /api/v1/operations/action.list`, and `GET /api/v1/operations/action.run`.

Direct/read discovery operations:

- `action.list`
- `integration.list` when the agent needs compact integration/provider
  inventory and hidden-action counts without broad `auth.status` noise
- `action.describe`
- `action.validate`
- `readiness.check` when the agent already knows the selected workflow/action
  and wants only scoped setup blockers
- `toolProfile.resolve` when an agent needs a safe provider/profile/credential
  tuple before choosing an explicit action payload

Use `action.list` before `action.describe` when the agent knows the plugin,
provider, capability, executable state, or search term but does not yet know
the exact `action_ref`. By default it returns actions visible in the current
project state: executable local/no-credential actions plus external provider
actions whose required integration is connected and runnable. Disconnected,
deferred, project-local, missing-connector, and otherwise non-executable
external-provider actions are hidden from normal discovery. Setup and admin
flows can pass `include_unavailable_integrations=true` to inspect the full
action inventory deliberately.

Generated provider inventories expose stable public refs only. Internal
inventory storage keys and retired generated rows are not valid agent-facing
action refs and must stay hidden from normal discovery. Search is
intent-oriented: it matches titles, descriptions, tags, paths, operation ids,
request/response fields, and common token variants before the agent calls
`action.describe`.

Use `integration.list` when the agent needs to answer "which integrations are
available or missing for this project?" without treating every disconnected
provider as a blocker. It returns provider rows, connected counts, visible
action counts, hidden action counts, and safe next setup hints. Each provider
row includes normalized `setup` guidance derived from the provider manifest:
the local StackOS connection URL plus safe vendor registration, API-key,
billing, docs, fallback, and URL-confidence metadata when known.

Use `readiness.check` before `auth.status` for selected work. It reuses action
availability and workflow template action contracts to report only the
credentials, budgets, or connectors needed by that workflow/action. This keeps
an agent from treating every disconnected provider in the project as a blocker
for one concrete run. Missing credential items include the same provider
`setup` guidance, so an agent can answer "where do I connect it in StackOS?"
and "where do I get the vendor key?" from the scoped blocker.

Use `action.describe` when the agent already knows the action and needs its
payload schema, capability metadata, and provider setup context in one response.
Use `auth.status` for sanitized credential diagnostics; compact responses keep
only a small setup summary and omit full auth-method details.

Run-plan-scoped execution operation:

- `action.execute`
- `communication.send` when a workflow step grants explicit targets
- `communication.reply` when a workflow step grants explicit origin sources

Direct one-action execution operation:

- `action.run`
- `communication.send`
- `communication.reply`

`action.run` is direct execution for one explicit action. Non-read actions
require `confirm_direct=true` and `intent_summary`; callers may pass
`intent_id` or `idempotency_key` for stable retries. External provider action
output is file-backed by default for MCP and REST calls: the response returns
compact action-call, file path, checksum, byte size, content type, semantic
name, `schema_ref`, and `schema_operation`, while the sanitized
request+response envelope is stored as a plain response file. CLI calls default
to raw inline output unless an explicit output policy says otherwise. Call
`schema.get` with `schema_ref` if the agent needs the file envelope schema. It
is not a substitute for workflow memory, approval gates, artifacts, learnings,
experiments, or decisions.

Action inputs are split into endpoint payload and provider execution context.
`input_json` is the action-specific body/query/path contract. Optional
`provider_context_json` is validated against the selected action's
`provider_context_schema_json` and mapped by the connector to provider scope or
auth context. It is never copied into endpoint payload fields.

Reusable `context_ref` values can supply the credential, typed provider
context, output policy, request budget, and artifact namespace for
`action.validate`, `action.run`, and workflow-scoped `action.execute`. Risk
classification is part of the action contract: read-like POST reporting actions
may be classified as read when catalog metadata and semantics say they are
safe, but idempotency alone never makes a mutating action read-safe.

`output_policy_json` may be passed on `action.run` or `action.execute` for a
deliberate one-call override. Supported modes are `inline`, `file_if_large`,
and `always_file`; callers may supply `semantic_name` and an absolute
directory `path`. StackOS generates the filename inside that directory. When no
path is supplied, the daemon writes under the configured generated-assets
directory.

`action.describe` includes an `execution_context` block for agent consumers.
It names when to use a context, the payload boundary between `input_json` and
provider scope, and the next `executionContext.discover` /
`executionContext.create` calls for the selected action.

`action.execute` is not direct execution surface. It is callable only through a
started run plan, exactly one active claimed step, an explicit
`mcp_tool_grants` entry with `tool: "action.execute"`, and matching
`action_refs`. The active step must also declare the same action ref in
`action_refs`, so both the workflow step contract and the frozen grant snapshot
must agree before the connector is invoked.

Registered first-party connectors are one provider per connector file and now
cover the migrated clean path for:

- `openai-images`: `utils.image.generate`, `utils.image.edit`
- `xai-imagine`: `utils.xai.image.generate`, `utils.xai.image.edit`, and
  `utils.xai.video.generate`
- `reve`: `utils.reve.image.generate`, `utils.reve.image.edit`, and
  `utils.reve.image.remix`
- `google-gemini-image`: `utils.google.image.generate` and
  `utils.google.image.edit`
- `google-veo`: `utils.google.video.generate`
- `ideogram`: `utils.ideogram.image.generate` and
  `utils.ideogram.image.remix`
- `byteplus-ark`: `utils.byteplus.image.generate` and
  `utils.byteplus.image.edit`, plus `utils.byteplus.video.generate`
- `alibaba-wan`: `utils.alibaba.video.generate`
- `kling-video`: `utils.kling.video.generate`
- `firecrawl`: `utils.web.scrape`, `utils.web.crawl`, `utils.web.map`
- `jina`: `utils.web.read` with optional credentials
- `sitemap`: `utils.sitemap.fetch`
- `ftp`: stateless directory listing, recursive multi-path upload/download,
  exact file/directory deletion, directory creation, and remote rename/move
- `cloudflare`: zone listing plus individual DNS record list/get/create/edit/replace/delete
- `reddit`: `utils.reddit.search-subreddit`, `utils.reddit.top-questions`
- `dataforseo`: `seo.keyword.research`, `seo.serp.analyze`, `seo.paa.extract`
- `serper`: `seo.serper.search`
- `ahrefs`: `seo.competitor.keywords`, `seo.backlink.research`
- `google-search-console`: `seo.search-console.sites.list`,
  `seo.search-console.search-analytics.query`,
  `seo.search-console.sitemaps.list`, `seo.search-console.url.inspect`
- `google-analytics`: `seo.ga4.account_summaries.list`,
  `seo.ga4.properties.metadata.get`, `seo.ga4.properties.run_report`, and
  `seo.ga4.properties.run_realtime_report`
- `google-tag-manager`: `seo.google-tag-manager.accounts.list`,
  `seo.google-tag-manager.containers.list`,
  `seo.google-tag-manager.container.snippet.get`,
  `seo.google-tag-manager.workspaces.list`,
  `seo.google-tag-manager.workspace.tags.list`, and
  `seo.google-tag-manager.workspace.triggers.list`
- `wordpress`: `publishing.wordpress.post.create`
- `ghost`: `publishing.ghost.post.create`
- `branding`: `branding.evidence.capture`,
  `branding.evidence.sanitize-mark`
- `http`: static custom HTTP/Webhook actions declared by installed plugins
- `telegram-bot`: project-scoped Telegram bot identity, message/photo sends,
  callback answers, diagnostic update inspection, and webhook set/delete/info
- `slack-bot`: project-scoped Slack bot identity, message sends, conversation
  open/info/list/member discovery, and signed HTTP ingress resource flow
- `smtp`: `communications.smtp.email.send` with daemon-side password auth and
  accepted/rejected recipient metadata only
- `imap`: mailbox list, bounded UID search, selected message fetch, and mark
  seen/unseen lifecycle actions
- `hubspot`, `salesforce`, `apollo`, `pipedrive`, `clay`, `outreach`,
  `salesloft`, `google-workspace`, and `microsoft-365`: first GTM/RevOps
  provider actions
- `meta-ads`, `google-ads`, and `taboola`: first paid media provider actions

OpenRouter is registered as a Utilities connection and auth-test integration
only. It intentionally has no generic text-generation action until a workflow
owns the model-call policy, grants, audit shape, output persistence, and
operator approval boundary.

Actions that are intentionally not executable use explicit `execution_mode`
metadata, such as `deferred-partner-api`, `deferred-inbound`,
`deferred-firecrawl-async-extract`, `deferred-video-backend-selection`, or
`project-local-http`. `utils.web.extract`
is intentionally deferred until StackOS has an explicit Firecrawl status-poll
action and output artifact contract. `utils.video.generate` is intentionally
deferred as a provider-neutral planning placeholder. Concrete video execution
is exposed through provider-specific actions such as
`utils.xai.video.generate`, `utils.google.video.generate`,
`utils.byteplus.video.generate`, `utils.alibaba.video.generate`, and
`utils.kling.video.generate`. Catalog availability reports those modes
directly instead of treating them as missing connectors. Outbrain and user-owned
webhook actions remain deferred until endpoint-level contracts or project-local
static HTTP config are supplied.

The OpenAI Images connector persists base64 image bytes under generated assets,
registers the persisted files as generic `image` artifacts when action
execution has repository context, and returns local artifact URLs/ids with no
`b64_json` payload. For
`utils.image.edit` it loads generated-assets input refs daemon-side and submits
them as multipart `image` file uploads. JSON edit requests use `images` only
for public URL or file-id references, not local generated-assets files.
`input_fidelity` is forwarded only for the models that accept it. Other
connectors normalize wrapper results into action output JSON and record the
provider, operation, cost, status, and redacted payloads in `action_calls`.

The xAI Imagine connector follows the same generated-assets contract for Grok
images and videos. Image actions use JSON/base64 payloads where the provider
requires them, while video generation submits a job, polls by `request_id`,
downloads the temporary video URL, registers a generic `video` artifact, and
returns local artifact refs plus safe provider job metadata. Successful xAI
responses reconcile action-call cost from `usage.cost_in_usd_ticks` when
present; pre-call budget checks still reserve the estimate and top up only when
the actual connector cost is higher.

The Google Gemini Image connector uses Gemini Developer API
`models/{model}:generateContent` for `gemini-3.1-flash-image`,
`gemini-3-pro-image`, and `gemini-2.5-flash-image`. It persists inline
MIME/base64 image parts under generated assets, registers generic `image`
artifacts, and validates generated-assets image refs plus model-specific
shape/input-count limits before provider calls. StackOS v1 does not expose
Gemini Files API input, Google Search grounding tools, video input, chat state,
or Vertex AI parity through these actions.

The Ideogram connector uses multipart Ideogram 4.0 generate/remix endpoints,
downloads temporary provider image URLs immediately, strips signed provider
URLs from action outputs and audit records, persists images under generated
assets, and registers generic `image` artifacts. StackOS v1 exposes text prompt
generation and one-image remix only; structured `json_prompt`, magic-prompt,
describe, v3 background utilities, remove-background, upscale, legacy edit, and
`rendering_speed=FLASH` remain deferred until each endpoint contract is modeled.

The BytePlus Seedream connector uses the reusable `byteplus-ark` ModelArk
wrapper for `POST /api/v3/images/generations`, supports text generation and
generated-assets reference-image edits, downloads 24-hour provider URLs or
persists returned base64 bytes immediately, strips provider media payloads from
outputs/audit, and registers generic `image` artifacts. StackOS v1 exposes
priced Seedream 5 Lite, 4.5, and 4.0 model ids only with model-specific size
shortcut validation; streaming partial images, external URL inputs, non-lite
Seedream 5.0, Seededit controls, and extra BMP/TIFF/GIF/HEIC/HEIF input
formats remain deferred until separately modeled.

Communication setup is not an action connector. Telegram communication profile
setup uses the shared `communicationProfile.upsert/get/list` operations across
REST, CLI, and MCP after the project-scoped `telegram-bot` credential exists.
Slack uses project-scoped `communication-profile` records with a
`provider_facets.slack-bot.auth_profile_key` binding after the project-scoped
`slack-bot` credential exists. Normal agent messaging goes through
`communication.send` or `communication.reply`; `action.run` and
`action.execute` remain lower-level escape hatches for explicit provider
diagnostics, webhook setup, or provider-specific work. SMTP and IMAP credentials
are also project-scoped auth profiles; agents receive only opaque credential
refs and safe status, while the connector resolves host/user/password/TLS config
inside the daemon process.

The generic HTTP connector is a plugin-authoring escape hatch, not a direct
agent browsing tool. The endpoint, method, auth mode, request mode, static
headers, timeout, and response mode live in action `config_json.http`; the
agent only supplies the action input payload allowed by that plugin action's
schema.

## Boundary

Actions are dumb execution units. They do not pick campaigns, choose variants,
optimize budgets, interpret SEO opportunities, or decide next steps. Those
decisions belong to the agent/person and are passed into StackOS as explicit
payloads, run plans, resources, learnings, decisions, or approvals.
