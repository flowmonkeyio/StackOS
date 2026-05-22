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
| [Current Connectors](current-connectors.md) | OpenAI Images, Firecrawl, Jina, Reddit, DataForSEO, Ahrefs, WordPress, Ghost, sitemap, HTTP | Executable surface audited; follow-up corrections required. |
| [GTM CRM](gtm-crm.md) | HubSpot, Salesforce, Pipedrive CRM and pipeline contracts | First executable connector pass delivered; keep schemas provider-native. |
| [GTM Prospecting And Outbound](gtm-prospecting-outbound.md) | Apollo, Clay, Clearbit/Clearbit by HubSpot, Outreach, Salesloft, Google Workspace, Microsoft 365 | First executable connector pass delivered except explicit deferred actions. |
| [Media Buying](media-buying.md) | Meta Marketing API, Google Ads API, Outbrain, Taboola, user-owned media webhooks | Meta, Google Ads, and Taboola first pass executable; Outbrain/webhooks deferred. |

## Delivery Gate

Before adding or changing `config.connector` on any action:

1. Link official provider docs in the relevant contract review.
2. Use provider-specific action refs and schemas.
3. Define safe setup fields and daemon-only credential handling.
4. Add connector code with doc links near provider-specific calls.
5. Add validation, redaction, audit, rate-limit/error, pagination, and budget
   tests as appropriate.
6. Prove MCP/REST/UI availability reports the right setup state.
7. Run a stale-ref scan across manifests, workflow templates, tests, and docs.
8. Confirm every workflow action contract exists in the owning plugin manifest.

If any item is missing, use an explicit deferred execution mode rather than an
empty or misleading connector config.
