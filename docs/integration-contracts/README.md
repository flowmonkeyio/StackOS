# Integration Contracts

This directory is the source of truth for provider contract reviews before a
StackOS action becomes executable.

StackOS provider work has two states:

- `contract-only`: provider/action/resource/template metadata exists, but no
  daemon connector is registered and catalog availability must remain
  `not_executable`.
- `executable`: provider action has static connector config, daemon-side auth
  resolution, validation, redaction, audit, tests, and approval/grant coverage.

## Contract Reviews

| Review | Scope | Status |
| --- | --- | --- |
| [Current Connectors](current-connectors.md) | OpenAI Images, Firecrawl, Jina, Reddit, DataForSEO, Ahrefs, WordPress, Ghost, sitemap, HTTP | Executable surface audited; follow-up corrections required. |
| [GTM CRM](gtm-crm.md) | HubSpot, Salesforce, Pipedrive CRM and pipeline contracts | Contract-only; action names and schemas must stay provider-native. |
| [GTM Prospecting And Outbound](gtm-prospecting-outbound.md) | Apollo, Clay, Clearbit/Clearbit by HubSpot, Outreach, Salesloft, Google Workspace, Microsoft 365 | Contract-only; endpoint-specific actions required before execution. |
| [Media Buying](media-buying.md) | Meta Marketing API, Google Ads API, Outbrain, Taboola, user-owned media webhooks | Contract-only; provider-specific schemas and action splits required. |

## Delivery Gate

Before adding `config.connector` to any action:

1. Link official provider docs in the relevant contract review.
2. Use provider-specific action refs and schemas.
3. Define safe setup fields and daemon-only credential handling.
4. Add connector code with doc links near provider-specific calls.
5. Add validation, redaction, audit, rate-limit/error, pagination, and budget
   tests as appropriate.
6. Prove MCP/REST/UI availability reports the right setup state.
7. Run a stale-ref scan across manifests, workflow templates, tests, and docs.
8. Confirm every workflow action contract exists in the owning plugin manifest.

If any item is missing, keep the action `contract-only`.
