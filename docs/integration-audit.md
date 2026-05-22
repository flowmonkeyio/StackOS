# StackOS Integration Audit

This audit maps the current integration surface after the StackOS pivot and
identifies the integrations and architecture changes needed to support SEO,
media buying, GTM, utilities, and user-installed plugins without putting
business logic or secrets into the agent context.

## Design Position

StackOS should remain a tool, storage, auth, and audit layer.

Agents and operators decide what to do. StackOS describes available tools,
validates explicit payloads, resolves daemon-held credentials, executes
configured provider operations, stores results, and records audit.

External tools should be exposed through static plugin metadata:

- providers: what external or local systems exist
- auth providers: how credentials are stored and tested
- actions: exact callable operations with input and output schemas
- resources: durable domain records the agent can write or query
- workflow templates: reusable setup, context needs, constraints, and base steps
- run plans: concrete action refs, inputs, scoped grants, and outputs for one run

No action config, run plan, resource, MCP response, UI JSON, or template should
contain plaintext secrets. Agents should only see provider keys, account refs,
and opaque `credential_ref` values.

## Current Integration Surfaces

### Plugin Catalog

Files:

- `content_stack/plugins/manifest.py`
- `plugins/gtm/plugin.yaml`
- `plugins/media-buying/plugin.yaml`
- `plugins/publishing/plugin.yaml`
- `plugins/seo/plugin.yaml`
- `plugins/gtm/workflows/account-research.yaml`
- `plugins/gtm/workflows/lead-enrichment-scoring.yaml`
- `plugins/gtm/workflows/outbound-sequence-preparation.yaml`
- `plugins/gtm/workflows/crm-hygiene-pass.yaml`
- `plugins/gtm/workflows/pipeline-risk-review.yaml`
- `plugins/media-buying/workflows/campaign-launch.yaml`
- `plugins/media-buying/workflows/creative-variant-generation.yaml`
- `plugins/media-buying/workflows/performance-diagnosis.yaml`
- `plugins/media-buying/workflows/budget-reallocation-review.yaml`
- `plugins/media-buying/workflows/landing-page-creative-experiment.yaml`
- `plugins/core/workflows/project-memory-review.yaml`
- `plugins/seo/workflows/keyword-research.yaml`
- `plugins/seo/workflows/content-refresh.yaml`

Current built-in plugins:

| Plugin | What It Provides | Current State |
| --- | --- | --- |
| `core` | Local daemon provider, catalog primitives, project data resources, project memory review template | Good generic foundation. |
| `utils` | OpenAI Images, Firecrawl, Jina, Reddit providers; image generation, web retrieval, and community research actions | Core utility connectors are now executable through the generic action path. |
| `gtm` | HubSpot, Salesforce, Pipedrive, Apollo, Clay, Clearbit, Outreach, Salesloft, Google Workspace, Microsoft 365, and internal GTM provider contracts; GTM resources; five reusable templates | First provider-specific connectors are executable; Clearbit, inbound Clay result ingestion, and user-owned webhooks remain explicit deferred modes. |
| `media-buying` | Meta Ads, Google Ads, Outbrain, Taboola, and internal campaign-tool provider contracts; paid media resources; five reusable templates | Meta Ads, Google Ads, and Taboola first connectors are executable; Outbrain and user-owned webhooks remain explicit deferred modes. |
| `publishing` | WordPress and Ghost providers, CMS publishing actions, and generic publishing resources | First post-create connector path is wired. Media/upload/update actions and templates are still needed. |
| `seo` | DataForSEO and Ahrefs providers, SEO actions, SEO resource schemas, two SEO templates | First SEO connector path is wired, including PAA extraction through DataForSEO. More templates and richer provider actions are still needed. |

Current templates are intentionally small and reusable, but the library is thin:

- `core.project-memory-review`
- `gtm.account-research`
- `gtm.crm-hygiene-pass`
- `gtm.lead-enrichment-scoring`
- `gtm.outbound-sequence-preparation`
- `gtm.pipeline-risk-review`
- `media-buying.budget-reallocation-review`
- `media-buying.campaign-launch`
- `media-buying.creative-variant-generation`
- `media-buying.landing-page-creative-experiment`
- `media-buying.performance-diagnosis`
- `seo.keyword-research`
- `seo.content-refresh`

This explains why the UI shows fewer templates than the older procedure system.
The clean-cut architecture removed hard-coded procedure catalogs. We now need to
rebuild the first-party template library as plugin templates.

### Auth And Credential Boundary

Files:

- `content_stack/auth_providers/repository.py`
- `content_stack/api/auth_providers.py`
- `content_stack/mcp/tools/auth.py`
- `docs/auth-providers.md`

REST routes exist for:

- list providers
- inspect sanitized project auth status
- start local setup
- store a secret through the local-admin path
- test a credential
- revoke a credential

MCP tools exist for:

- `auth.status`
- `auth.start`
- `auth.test`
- `auth.revoke`

The bridge advertises only `auth.status` and `auth.test` to normal agents.
`auth.start` and `auth.revoke` are admin/setup operations.

Auth providers are synced from plugin provider manifests. A daemon wrapper alone
does not make a provider setup-ready. WordPress and Ghost now have first-party
publishing provider manifests; future wrappers should follow the same path
before they are treated as user-configurable providers.

The repository correctly treats plaintext credentials as daemon-only data.
`ResolvedCredential` is an internal dataclass with plaintext hidden from
serialization. `CredentialConnectionOut` returns safe refs and status only.

Current implementation note: the new `credentials` table is still backed by the
older encrypted `integration_credentials` table. That is acceptable as an
internal secret blob store during the clean-cut transition, but it should remain
an implementation detail. Long term we should either promote the new credential
model as the only public concept and keep `integration_credentials` fully
internal, or migrate the encrypted payload columns into the credential model and
remove the duplicate naming.

Current state: the UI `Connections` page exposes generic local-admin controls
to store, test, and revoke provider credentials. Secrets are typed into a
write-only field, sent only to the daemon credential endpoint, cleared after a
successful save, and replaced in UI state by an opaque `credential_ref`.

### Generic Action Executor

Files:

- `content_stack/actions/connectors.py`
- `content_stack/actions/manifest.py`
- `content_stack/actions/repository.py`
- `content_stack/actions/openai_images.py`
- `content_stack/mcp/tools/actions.py`
- `content_stack/workflows/run_plan_grants.py`
- `docs/action-executor.md`

The action executor is the clean StackOS path:

1. Plugin action manifest defines static schema and connector config.
2. Agent validates an explicit payload.
3. A run-plan step grants `action.execute` for exact `action_refs`.
4. The daemon resolves the credential internally.
5. Connector executes the operation.
6. `action_calls` records redacted request, response, status, cost, and refs.

Current executable connector registry:

| Connector | Action Ref | Provider | Status |
| --- | --- | --- | --- |
| `openai-images` | `utils.image.generate` | `openai-images` | Ready and connected to generic `action.execute`. |
| `firecrawl` | `utils.web.scrape`, `utils.web.crawl`, `utils.web.map`, `utils.web.extract` | `firecrawl` | Ready through generic `action.execute`. |
| `jina` | `utils.web.read` | `jina` | Ready with optional credentials. |
| `sitemap` | `utils.sitemap.fetch` | none | Ready as a no-auth utility action, while the setup helper remains available. |
| `reddit` | `utils.reddit.search-subreddit`, `utils.reddit.top-questions` | `reddit` | Ready through generic `action.execute`. |
| `dataforseo` | `seo.keyword.research`, `seo.serp.analyze`, `seo.paa.extract` | `dataforseo` | Ready for the first SEO research actions. |
| `ahrefs` | `seo.competitor.keywords`, `seo.backlink.research` | `ahrefs` | Ready for the first SEO research actions. |
| `wordpress` | `publishing.wordpress.post.create` | `wordpress` | Ready for explicit WordPress post payloads. |
| `ghost` | `publishing.ghost.post.create` | `ghost` | Ready for explicit Ghost post payloads. |
| `http` | project/plugin-defined custom HTTP/Webhook actions | plugin-defined | Ready as a static connector foundation for user-owned tools. |

`action.execute` now returns the public action-call audit shape. Internal
database identifiers such as `credential_id`, `action_id`, and replay-only
`idempotency_key` stay in storage and are not returned to agents.

### Removed Vendor MCP Tools

The old `content_stack/mcp/tools/vendor_ops.py` module has been removed from
the daemon catalog. Provider operations now enter the agent runtime through
plugin action manifests and generic action connectors only.

This removes the second execution path that used names such as
`dataforseo.serp`, `firecrawl.scrape`, `openaiImages.generate`, `jina.read`, and
`ahrefs.keywordsForSite`. Those names should remain unknown to MCP clients. If a
provider operation is needed again, add it as a plugin action with connector
config, schemas, auth policy, run-plan grants, and tests.

### Daemon Integration Wrappers

Files:

- `content_stack/integrations/__init__.py`
- `content_stack/integrations/dataforseo.py`
- `content_stack/integrations/ahrefs.py`
- `content_stack/integrations/firecrawl.py`
- `content_stack/integrations/openai_images.py`
- `content_stack/integrations/reddit.py`
- `content_stack/integrations/google_paa.py`
- `content_stack/integrations/jina_reader.py`
- `content_stack/integrations/wordpress.py`
- `content_stack/integrations/ghost.py`
- `content_stack/integrations/sitemap.py`

Wrapper inventory:

| Provider | Wrapper Operations | Current Exposure | Readiness |
| --- | --- | --- | --- |
| `openai-images` | `generate`, `test_credentials` | Generic action connector | Ready. |
| `dataforseo` | SERP, keyword volume, domain intersection, keywords for site, PAA, credential test | SEO plugin actions | First connector path ready for `keyword.research`, `serp.analyze`, and `paa.extract`; remaining wrapper operations need action contracts. |
| `ahrefs` | keywords for site, top backlinks, credential test | SEO plugin actions | First connector path ready for competitor keywords and backlink research. |
| `firecrawl` | scrape, crawl, map, extract, credential test | Utils actions | Ready through generic utility actions. |
| `jina` | read URL, credential test | Utils action | Ready through `utils.web.read` with optional auth. |
| `reddit` | search subreddit, top questions, credential test | Utils actions | Ready through generic community research actions. |
| `google-paa` | PAA extraction through Firecrawl | Wrapper only | Partial. No provider/action manifest; dependency on Firecrawl should be explicit. |
| `wordpress` | current user, credential test, post create | Publishing plugin actions | Ready for post creation through generic `action.execute`; media/upload/update actions remain. |
| `ghost` | users, credential test, post create | Publishing plugin actions | Ready for post creation through generic `action.execute`; image/upload/update actions remain. |
| sitemap | sitemap fetch | Project REST/MCP setup utility and `utils.sitemap.fetch` action | Ready for setup and run-plan use. |

## Key Gaps

### 1. Remaining Connector Coverage

The first utility and SEO wrappers are now on the generic connector path. The
remaining connector gaps are:

- Firecrawl-derived Google PAA needs an explicit action contract and dependency
  decision if we want that wrapper as a separate action. The first PAA action is
  `seo.paa.extract` through DataForSEO.
- WordPress and Ghost now have provider manifests and post-create connectors;
  media/upload/update actions still need contracts.
- User-owned tools now have a static HTTP/Webhook connector foundation; project
  plugin authoring and install affordances still need depth.
- Legacy vendor MCP tools are removed; keep them from reappearing as a parallel execution path.

### 2. SEO Actions Need Broader Coverage

The first SEO actions are executable:

- `seo.keyword.research`
- `seo.serp.analyze`
- `seo.paa.extract`
- `seo.competitor.keywords`
- `seo.backlink.research`

The remaining gap is breadth, not basic executability. DataForSEO domain
intersection, keywords-for-site, GSC, GA4, and crawl imports
still need explicit plugin actions, schemas, templates, and tests.

### 3. Legacy Vendor MCP Tools Are Removed

The old provider-specific MCP tools have been removed from the daemon catalog.
That path was useful for the SEO-era procedure model, but the StackOS
architecture now converges on:

- direct discovery: `action.describe`, `action.validate`
- run-plan execution: `action.execute`
- plugin metadata: provider/action/resource/template contracts
- daemon connectors: provider wrappers, auth, rate limits, budgets, retries

The agent should not see `dataforseo.serp` or `firecrawl.scrape` as a direct
tool name. It should call `seo.serp.analyze`, `utils.web.scrape`, or another
plugin action ref selected by the run plan.

### 4. Auth UI Boundary Is Wired

The backend and UI now support the baseline local-admin credential flow:

- provider cards render from generic auth-provider metadata
- secret inputs are write-only and clear after successful save
- safe labels can be stored in `config_json`
- credential status and refs refresh after store/test/revoke
- secret setup stays out of the agent-visible MCP bridge

Remaining auth UI depth is provider setup breadth, not the core boundary:

- OAuth/start-flow providers need a generic start/return UI path.
- Providers with non-secret safe config such as login, site URL, base URL, or
  scope selection should declare schema-driven setup fields instead of relying
  on ad hoc UI controls.
- Provider account metadata should be displayed when wrappers return safe
  account details.

This must remain local-admin UI. The agent can inspect status and test results,
but should never see secret values.

### 5. Provider Catalog And Wrapper Registry Are Not Fully Aligned

Some wrappers exist without first-class plugin provider/action definitions:

- `google-paa`

The original set of plugin providers without generic executable actions has
been closed for Firecrawl, Jina, Reddit, DataForSEO, Ahrefs, WordPress, and
Ghost. Future plugin providers should not be considered setup-ready until they
have the same action connector coverage, even if a daemon wrapper already
exists.

For a clean plugin system, every external provider should have matching:

- plugin provider manifest
- auth provider metadata
- daemon wrapper
- action connector
- action manifest
- tests
- docs

Jina's generic action now preserves the wrapper's optional-auth behavior by
setting `requires_credential: false` and `allows_credential: true`. Future
optional-auth providers should follow that explicit manifest shape so agents are
not blocked when a public/no-key mode is valid.

### 6. Campaign And Variant Operations Need Provider-Specific Depth

The old "one level" shape is not enough. "Create campaign" is not one universal
operation. It might mean Meta, Google Ads, Outbrain, Taboola, TikTok, LinkedIn,
Microsoft Ads, or a user's internal campaign tool.

The clean model is provider-specific action refs with shared resource schemas:

- `media-buying.meta.campaign.create`
- `media-buying.meta.ad_set.create`
- `media-buying.meta.ad_creative.create`
- `media-buying.google.campaign.create`
- `media-buying.outbrain.campaign.create`
- `media-buying.outbrain.promoted_link.create`
- `media-buying.taboola.campaign.create`
- `media-buying.taboola.item.create`
- `media-buying.webhook.media_campaign.create`

The shared `campaign`, `ad-group`, `creative`, `audience`, and `performance`
resources can store normalized durable records. The provider-specific actions
own request shape, auth, idempotency, and output normalization.

"Generate variants" has the same issue. A template can say the workflow may
generate variants, but the run plan must specify:

- variant type: copy, image, video, landing page, audience, bid, offer, title, CTA
- source assets or source records
- constraints: brand, channel, dimensions, policy rules, exclusions
- count and axes
- required output schema
- whether the result is only a resource, an artifact, or an external platform mutation
- which action refs are allowed

The tool should not decide those axes. The agent decides and passes explicit
configuration to a utility or domain action.

### 7. Action Availability Needs To Be Visible

Operators and agents need to know whether a catalog action is actually
executable before starting a run. `action.describe` and catalog action rows now
return generic setup status such as:

- executable
- missing connector
- missing credential
- plugin disabled
- provider disabled
- missing budget
- budget blocked

This is important while the repo contains a mix of migrated actions,
setup-only utilities, and wrappers that do not yet have provider manifests.

## Integration Contract For New Providers

Every new external provider should ship with the same checklist:

1. Plugin manifest provider entry with `auth_type`, scopes, and safe metadata.
2. Resource schemas for durable records the agent can store and query.
3. Action manifests with exact input/output schemas, risk level, connector,
   operation, credential policy, and budget kind.
4. Daemon integration wrapper for provider API calls, retries, rate limits,
   cost accounting, response normalization, and safe errors.
5. Action connector that adapts static action payloads to the wrapper without
   deciding strategy.
6. Auth test implementation and local-admin setup requirements.
7. Run-plan grant tests proving `action.execute` only works for declared
   `action_refs`.
8. UI rendering through generic catalog, connection, resource, action-call,
   template, and run-plan pages.
9. Documentation for setup, action schemas, output schemas, and known limits.

## Needed Integrations

### P0: Finish The Existing Integration Migration

These are needed before adding many new domains:

| Need | Why |
| --- | --- |
| Guard against vendor MCP tool reintroduction | Firecrawl, Jina, Reddit, DataForSEO, Ahrefs, WordPress, and Ghost now have generic action connectors, so provider-specific MCP tools should remain removed. |
| Firecrawl-derived Google PAA action contract | DataForSEO PAA is now wired as `seo.paa.extract`; decide later whether the Firecrawl-derived wrapper belongs as a separate utility or SEO action. |
| WordPress publishing connector breadth | `publishing.wordpress.post.create` is wired; media upload, post update, and richer publication-state actions remain. |
| Ghost publishing connector breadth | `publishing.ghost.post.create` is wired; image upload, post update, and richer publication-state actions remain. |
| Custom HTTP/Webhook connector | Static plugin actions can now use connector `http`; next add authoring docs/examples and optional UI affordances for project-local plugin installation. |

### P1: Utilities Plugin Expansion

Utilities should be available to any domain:

- image generation: already started with OpenAI Images
- video generation: provider-neutral action contracts for prompt-to-video,
  image-to-video, and video edits
- web retrieval: Firecrawl, Jina, browser/screenshot capture
- file storage: S3, R2, GCS, local artifact store
- document parsing: PDF, HTML, CSV, spreadsheet ingestion
- notifications: Slack, email, webhook; custom webhook actions can start with
  connector `http`
- data export/import: Google Sheets, Airtable, CSV, database query adapters
- media processing: resize, transcode, extract frames, transcription

These should live under `utils` unless a domain-specific plugin needs a tighter
contract.

### P1: Media Buying Plugin

Started as `plugins/media-buying/plugin.yaml`.

Providers to plan:

- Meta Marketing API
- Google Ads
- TikTok Ads
- LinkedIn Ads
- Microsoft Ads
- Outbrain
- Taboola
- custom/internal campaign tools through the generic HTTP/Webhook connector

Resource schemas:

- ad account
- campaign
- ad set or ad group
- ad
- creative
- audience
- landing page
- conversion event
- performance snapshot
- budget change
- experiment

Action groups:

- account.list
- campaign.create/update/pause/resume
- ad-set.create/update
- ad-group.create/update
- creative.upload/create
- ad.create/update
- audience.sync
- insight.fetch
- conversion-event.fetch
- budget.update

Templates:

- launch paid campaign
- generate creative variants
- diagnose campaign performance
- budget reallocation review
- landing page and ad creative experiment

Important rule: do not expose one generic `campaign.create` action unless it is
a local abstraction configured by the user. First-party provider actions should
be provider-specific so schemas, constraints, and outputs are precise.

Current state: the media-buying plugin now defines provider-specific executable
actions for Meta Ads, Google Ads, and Taboola, including
`meta.campaign.create`, `meta.ad_set.create`, `meta.ad_creative.create`,
`google.campaign.create`, and `taboola.item.create`. Outbrain actions are
marked `deferred-partner-api` because endpoint-level campaign/report contracts
are gated. User-owned `webhook.media_*` actions are `project-local-http` until
a project supplies static HTTP connector config.

### P1: GTM And RevOps Plugin

Use `GTM` here as go-to-market systems, not Google Tag Manager.

Providers to plan:

- HubSpot
- Salesforce
- Pipedrive
- Apollo
- Clay
- Clearbit or enrichment provider
- Outreach
- Salesloft
- Gmail/Google Workspace
- Microsoft 365

Resource schemas:

- account
- company
- contact
- lead
- opportunity or deal
- sequence
- task
- touchpoint
- enrichment record
- pipeline snapshot

Action groups:

- provider-native CRM upserts or creates, such as `hubspot.crm.companies.batch_upsert`
- provider-native lead/prospect reads, such as `apollo.people.search`
- provider-native enrichment, such as `apollo.people.enrich` and `clay.table.webhook.submit`
- provider-native outbound enrollment, such as `outreach.sequence_state.create`
- provider-native cadence membership, such as `salesloft.cadence_membership.create`
- provider-native mail/calendar sends through Google Workspace and Microsoft 365
- provider-native pipeline reads, such as `salesforce.opportunities.query`
- user-owned GTM webhooks, such as `webhook.pipeline.fetch`

Templates:

- account research
- lead enrichment and scoring
- outbound sequence preparation
- CRM hygiene pass
- pipeline risk review

Current state: the GTM/RevOps plugin now defines provider-specific executable
actions such as
`hubspot.crm.companies.batch_upsert`,
`salesforce.lead.upsert_by_external_id`, `apollo.people.enrich`,
and `outreach.sequence_state.create`. Clearbit remains deferred until a public
standard endpoint is verified, Clay workflow result ingestion remains an
inbound resource flow, and `webhook.*` actions are `project-local-http` until a
project supplies static HTTP connector config.

### P1: Analytics And Measurement Plugin

Providers to plan:

- GA4 Data API
- Google Search Console
- Google Ads reporting
- Meta insights
- BigQuery
- Snowflake
- Postgres
- Google Sheets

Resource schemas:

- metric snapshot
- report
- attribution touchpoint
- conversion event
- search performance snapshot
- paid performance snapshot

Action groups:

- metric.fetch
- report.fetch
- query.run
- conversion.import
- snapshot.record

This plugin becomes the shared measurement layer for SEO, media buying, and
GTM templates.

### P1: Publishing And CMS Plugin

Providers to plan:

- WordPress
- Ghost
- Webflow
- Shopify
- Contentful
- Sanity
- custom Git/repository publishing

Action groups:

- post.create/update/publish
- media.upload
- page.create/update/publish
- product-content.update
- redirect.create
- canonical.update

Current WordPress and Ghost wrappers have first post-create connectors. Media,
update, publish-state, and richer CMS objects still need contracts.

### P2: SEO Plugin Expansion

SEO is now one plugin, not the product itself.

Needed next integrations:

- Google Search Console for query/page performance and indexing signals
- GA4 for landing-page engagement and conversion context
- PageSpeed or CrUX style performance evidence
- CMS publishing through the publishing plugin
- crawl imports through utility/core actions
- SERP and competitor research migrated from hidden MCP tools to connectors

Templates to add:

- new content brief
- technical crawl triage
- internal link opportunity review
- refresh performance follow-up
- search performance opportunity finder
- publishing QA

## UI Implications

The UI should stay generic:

- plugin catalog
- capabilities
- providers
- connections
- actions
- action call audit
- resources
- workflow templates
- run plans
- runs
- context, learnings, experiments, decisions
- artifacts

Avoid per-workflow pages. Plugin nav should point into generic pages with
filters such as `plugin_slug`, `resource_key`, `template_key`, or `action_ref`.

Credential setup, action readiness, and run detail action-call visibility are
now wired through generic UI surfaces. The next UI integration gap is broader
generic browsing and filtering for action-call audit history across a project,
without adding workflow-specific pages. The catalog already exposes action
availability signals: executable, missing connector, missing credential,
disabled plugin, disabled provider, missing budget, and budget blocked.

## Cleanup And Refactor Map

1. Keep provider operations on generic action connectors.
2. Add connector config whenever a plugin action becomes executable.
3. Tighten schemas for each executable action.
4. Keep `auth.status` and `auth.test` agent-visible; keep secret setup local-admin only.
5. Maintain credential setup/test/revoke UI as a local-admin exception; do not
   expose secret setup through agent MCP tools.
6. Keep removed vendor MCP names unknown to MCP clients.
7. Decide whether `integration_credentials` remains only an internal encrypted blob store or is migrated into the new credential model.
8. Expand WordPress and Ghost from post-create actions to media/upload/update coverage.
9. Keep sitemap fetch available both as a setup helper and as `utils.sitemap.fetch`
   for run-plan use.
10. Expand custom HTTP/Webhook connector examples and project-local plugin install affordances.
11. Expand first-party templates after connectors exist.
12. Treat `action_calls` as the primary external-operation audit ledger for new work.

## Delivery Order

### Phase 1: Existing Integrations On The Clean Path

Initial connector migration and vendor MCP removal are complete for Firecrawl,
Jina, Reddit, DataForSEO, Ahrefs, WordPress, and Ghost. Remaining Phase 1 cleanup:

- keep removed provider-specific MCP tool names unknown to clients
- keep action availability signals visible in catalog/UI surfaces
- add remaining DataForSEO operation contracts where templates need them
  beyond keyword research, SERP analysis, and PAA extraction
- keep focused tests for connector validation, daemon-side credential resolution,
  run-plan grants, and redacted action-call audit

### Phase 2: Credential Setup UX

Complete for the baseline API-key/local-admin path. Connections now provides
generic store/test/revoke controls, renders schema-driven safe setup fields,
uses the REST-only UI console token for the narrow setup lane, and clears
secret input after save. Remaining work belongs to provider-specific breadth:
OAuth start/return flows and richer account metadata display.

### Phase 3: Publishing Plugin

The publishing plugin now promotes WordPress/Ghost wrappers into first
post-create actions. Next, add media/upload/update contracts and templates. Use
generic resources and action audit instead of hard-coded SEO publishing flows.

### Phase 4: Media Buying And GTM Plugin Scaffolds

Add manifests, resource schemas, templates, and initial connector stubs for the
highest-priority providers. Start with one provider per domain, but design action
refs so additional providers slot in without changing templates or UI.

### Phase 5: Analytics And Measurement

Add measurement connectors that SEO, media buying, and GTM templates can all use
for context and experiment evaluation.

## Acceptance Criteria For Any Integration

An integration is not considered complete until:

- it has a provider manifest
- auth setup and `auth.test` are supported, or the provider explicitly needs no auth
- no plaintext secret can be returned through REST, MCP, UI JSON, resources, artifacts, or audit
- executable actions have connector config
- executable actions have meaningful input and output schemas
- the connector is decision-free
- run plans can grant exact action refs
- action calls are audited with redaction, status, cost, and provider refs
- generic UI surfaces can render provider, action, connection, resource, template, run, and audit state
- docs explain setup, schemas, and limitations
