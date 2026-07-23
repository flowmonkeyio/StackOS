# Integration Contracts

This directory is the source of truth for provider contract reviews before a
StackOS action becomes executable.

StackOS provider work has three states:

- `deferred`: provider/action/resource/template metadata exists, but the action
  is intentionally not executable yet. The action config must include
  `execution_mode` and a `deferred_reason`.
- `executable`: provider action has static connector config, daemon-side auth
  resolution, validation, redaction, audit, tests, and approval/grant coverage.
- `project-local`: the built-in catalog declares a project-owned integration
  point, but execution requires that project to install static connector config.

## Contract Reviews

| Review | Scope | Status |
| --- | --- | --- |
| [Connector Quality Gate](connector-quality.md) | Every registered executable connector | Required review matrix for validation, errors, pagination/status, rate limits/budget, provider docs, and audit depth. |
| [Current Connectors](current-connectors.md) | Executable connector inventory plus the built-in OAuth/auth-protocol classification, including interactive, client-credentials, mixed, manual-only, and deferred providers | Executable surface audit, provider documentation ledger, and link to the canonical OAuth provider matrix. |
| [GTM CRM](gtm-crm.md) | HubSpot, Salesforce, Pipedrive CRM and pipeline contracts | First executable connector pass delivered; keep schemas provider-native. |
| [HubSpot](hubspot.md) | Canonical HubSpot sales-and-marketing OAuth, action, safe-ref, readiness, ingress, and communication ledger | Implementation contract frozen; executable and deferred rows are explicit. |
| [GTM Prospecting And Outbound](gtm-prospecting-outbound.md) | Apollo, Clay, Clearbit/Clearbit by HubSpot, Outreach, Salesloft, Google Workspace, Microsoft 365 | First executable connector pass delivered except explicit deferred actions. |
| [Media Buying](media-buying.md) | Meta Marketing API, Google Ads API, Outbrain, Taboola, custom media tool contracts | Meta, Google Ads, and Taboola first pass executable; Outbrain and custom media tool actions deferred until provider docs or project-local HTTP connector config exist. |
| [Media Generation](media-generation.md) + [Runbook](media-generation-runbook.md) | Image and video generation root providers: OpenAI GPT Image, Reve, Google Veo/Nano Banana, Ideogram, ByteDance Seedance/Seedream, Alibaba WAN, Kuaishou Kling, xAI Grok Imagine | Provider shortlist, capability facts, registration map, and the required runbook for turning media capabilities into executable StackOS actions; OpenAI image actions, Reve image actions, Google Gemini image/Veo video actions, Ideogram image actions, BytePlus Seedream/Seedance actions, Alibaba Wan video, Kling video, and xAI provider-specific image/video actions are executable, while provider-neutral `utils.video.generate` remains deferred until a backend abstraction is deliberately selected. |
| [Communications](communications.md) | Telegram Bot API, Slack Web API, SMTP, IMAP, and generic agent request inbox | Telegram Bot API, Slack Web API actions/signed ingress, SMTP send, IMAP mailbox/message lifecycle, and generic `agent_requests` REST/CLI/MCP operations are executable through the clean action/operation path. |
| [Trackbooth](trackbooth.md) | Trackbooth Agent API StackOS action inventory, schema resolver, and connector execution bridge | Manual `trackbooth.catalog.sync` refreshes live inventory into generated `trackbooth.api.*` actions; catalog/describe/read/write helpers remain for diagnostics. |

## Current Integration Status

| Domain | Status |
| --- | --- |
| `core` | Generic project memory, context, learnings, experiments, decisions, and local daemon primitives are the stable foundation. |
| `utils` | OpenAI Images, Reve image actions, Google Gemini image/Veo video actions, Ideogram image actions, BytePlus Seedream/Seedance actions, Alibaba Wan video, Kling video, xAI Imagine image/video actions, Firecrawl scrape/crawl/map, Jina Reader, Reddit, sitemap, static HTTP connector paths, and the local [mock provider](../integration-testing.md) are executable through `action.run` for one explicit call or `action.execute` in run plans; provider-neutral video generation and async extract/status-depth remain deferred until status and artifact contracts exist. |
| `seo` | DataForSEO keyword/SERP/PAA, Serper search, Ahrefs competitor/backlink, Google Search Console, Google Analytics 4, and Google Tag Manager read-only measurement actions are executable. Crawl imports, Google write/publish scopes, and broader DataForSEO/Ahrefs breadth still require explicit action contracts. |
| `publishing` | WordPress and Ghost post-create actions are executable. Media upload, update, scheduling, taxonomy, and richer CMS operations still need contracts. |
| `media-buying` | Meta Ads, Google Ads, and Taboola first actions are executable. Outbrain and custom media tools remain deferred or project-local until provider/project contracts are supplied. |
| `gtm` | First CRM, enrichment, outbound, and workspace actions are executable where provider contracts are documented. Clearbit/inbound/custom project-local actions remain explicit deferred modes. |
| `communications` | First-party plugin for Telegram bot, Slack bot, SMTP send, IMAP mailbox/message lifecycle, and generic agent request triggers. Telegram Bot API, Slack Web API actions/signed ingress, SMTP, IMAP, and core agent request queue operations are executable through shared manifests/connectors/operations; broader chat/mail providers, Slack Socket Mode/history/files/admin actions, and OAuth/XOAUTH2 mail auth remain deferred. |
| `trackbooth` | Trackbooth Agent API bridge installs fixed catalog sync/search/describe/read/write StackOS actions; manual sync upserts generated direct actions from the live server, with daemon-held API-key auth and safe custom API URL config. |

Provider-specific direct MCP tools are not part of the current architecture.
Provider calls should enter through plugin action manifests, daemon connectors,
direct-action policy or run-plan grants, and `action.run` or `action.execute`.

## Delivery Gate

Before adding or changing `config.connector` on any action:

1. Link official provider docs in the relevant contract review.
2. Use provider-specific action refs and schemas.
3. Define safe auth method fields and daemon-only credential handling.
4. Add connector code with doc links near provider-specific calls.
5. Update the [Connector Quality Gate](connector-quality.md) row for validation,
   safe errors, pagination/status, rate limits/budget, docs, and signoff.
6. Add validation, redaction, audit, rate-limit/error, pagination, and budget
   tests as appropriate.
7. Prove MCP/REST/CLI entrypoint behavior or availability through the shared
   operation/action registry; do not add provider-specific MCP tools.
8. Run a stale-ref scan across manifests, workflow templates, tests, and docs.
9. Confirm every workflow action contract exists in the owning plugin manifest.

If any item is missing, use an explicit deferred execution mode rather than an
empty or misleading connector config.

## Integration Completion Rule

An integration is complete only when it has a provider manifest, typed auth
setup or explicit no-auth state, daemon-only credential resolution, executable
actions with meaningful schemas, decision-free connector code, run-plan grant
coverage, redacted action-call audit, generic UI visibility, tests, and docs
for setup and limitations.
