# Media Buying Integration Contract Audit

Status: first executable connector pass delivered on 2026-05-22 for Meta Ads,
Google Ads, and Taboola. Outbrain and user-owned webhooks remain explicit
deferred modes until endpoint-level contracts or project-local static HTTP
config exist.

Reviewed scaffold: `plugins/media-buying/plugin.yaml` and workflow templates under `plugins/media-buying/workflows/`. This audit intentionally owns only this document.

## Contract Boundary

StackOS should keep media-buying integrations as static provider/action/template contracts. Agents choose strategy and pass explicit inputs in run plans. Tools validate inputs, resolve daemon-held credentials, call one provider operation, return normalized safe JSON, and record action-call audit. Provider connectors must not infer campaign structure, allocate budgets, pick audiences, or transform broad campaign wishes into spend-bearing objects.

Secrets and raw tokens stay daemon-side. Reusable templates should reference safe `*_ref` values such as `account_ref`, `customer_ref`, `campaign_ref`, `ad_set_ref`, `ad_group_ref`, `creative_ref`, and `conversion_event_ref`. Provider object ids may appear only in connector-owned resource provenance or action-call metadata after redaction.

## Official Docs Ledger

### Meta Marketing API

- Main Marketing API and structure: https://developers.facebook.com/docs/marketing-api/ and https://developers.facebook.com/docs/marketing-api/campaign-structure/
- OAuth/access tokens and permissions: https://developers.facebook.com/docs/facebook-login/guides/access-tokens/ and https://developers.facebook.com/docs/permissions/reference/ads_management/
- Campaigns: https://developers.facebook.com/docs/marketing-api/reference/ad-account/campaigns/ and https://developers.facebook.com/docs/marketing-api/reference/ad-campaign-group/
- Ad sets: https://developers.facebook.com/docs/marketing-api/reference/ad-account/adsets/ and https://developers.facebook.com/docs/marketing-api/reference/ad-campaign/adsets/
- Ads: https://developers.facebook.com/docs/marketing-api/reference/ad-account/ads/ and https://developers.facebook.com/docs/marketing-api/reference/adgroup/
- Ad creatives: https://developers.facebook.com/docs/marketing-api/reference/ad-account/adcreatives/ and https://developers.facebook.com/docs/marketing-api/reference/ad-creative/
- Insights/reporting: https://developers.facebook.com/docs/marketing-api/insights/
- Conversions API: https://developers.facebook.com/docs/marketing-api/conversions-api/ and https://developers.facebook.com/docs/marketing-api/conversions-api/parameters/
- Rate limits/errors: https://developers.facebook.com/docs/graph-api/overview/rate-limiting/ and https://developers.facebook.com/docs/graph-api/guides/error-handling/

Implication: Meta actions must model the hierarchy `ad account -> campaign -> ad set -> ad -> creative` precisely. Ad creative creation and asset upload are different operations, and budget updates are not one generic target because campaign budget optimization and ad set budgets have different fields and constraints.

### Google Ads API

- OAuth and credential setup: https://developers.google.com/google-ads/api/docs/oauth/overview and https://developers.google.com/google-ads/api/docs/oauth/credential-management
- Customer/account access: https://developers.google.com/google-ads/api/docs/account-management/listing-accounts and https://developers.google.com/google-ads/api/docs/account-management/get-account-hierarchy
- Campaigns/ad groups/ads: https://developers.google.com/google-ads/api/docs/campaigns/overview, https://developers.google.com/google-ads/api/docs/campaigns/create-campaigns, https://developers.google.com/google-ads/api/docs/campaigns/create-ad-groups, and https://developers.google.com/google-ads/api/docs/ads/overview
- Assets/creatives: https://developers.google.com/google-ads/api/docs/assets/overview and https://developers.google.com/google-ads/api/docs/assets/creation
- Reporting/GAQL: https://developers.google.com/google-ads/api/docs/reporting/overview and https://developers.google.com/google-ads/api/docs/query/overview
- Budgets: https://developers.google.com/google-ads/api/docs/campaigns/budgets/overview and https://developers.google.com/google-ads/api/docs/campaigns/budgets/restrictions-errors
- Conversion events/uploads: https://developers.google.com/google-ads/api/docs/conversions/overview, https://developers.google.com/google-ads/api/docs/conversions/upload-clicks, and https://developers.google.com/google-ads/api/docs/conversions/enhanced-conversions/overview
- Mutates/errors/quotas: https://developers.google.com/google-ads/api/docs/mutating/overview, https://developers.google.com/google-ads/api/docs/get-started/handle-errors, and https://developers.google.com/google-ads/api/docs/best-practices/quotas

Implication: Google Ads needs separate actions for customer listing, campaign budget, campaign, ad group, asset, ad group ad, GAQL reporting, conversion action, and conversion upload. The first executable connector uses provider-native REST mutate/search/upload calls; expansion still needs stricter enum coverage, mutate tests, and quota/error handling.

### Outbrain Amplify API

- Developer overview: https://developer.outbrain.com/home-page/amplify-api/
- Advertiser API usage/auth/rate limits/errors: https://www.outbrain.com/help/advertisers/amplify-api/
- Official Apiary reference entry point: https://amplifyv01.docs.apiary.io/
- Conversion import help: https://www.outbrain.com/help/advertisers/offline-conversions/

Implication: Outbrain is beta/partner-gated and uses `OB-TOKEN-V1` tokens that expire after 30 days. The current `auth_type: api-key` is too vague unless StackOS models a daemon-side token refresh/login flow or treats this as a stored short-lived API token with rotation diagnostics. Outbrain entities are Marketer, Budget, Campaign, PromotedLink, and PerformanceBy*; there is no ad-set/ad-group equivalent.

### Taboola Backstage API

- Request/auth basics: https://developers.taboola.com/backstage-api/reference/request-basics
- Campaign overview/object/update: https://developers.taboola.com/backstage-api/reference/campaigns-overview, https://developers.taboola.com/backstage-api/reference/campaign-fields-overview, and https://developers.taboola.com/backstage-api/reference/update-a-campaign
- Campaign items/ads: https://developers.taboola.com/backstage-api/reference/create-a-campaign-item and https://developers.taboola.com/backstage-api/reference/video-items-overview
- Campaign reporting: https://developers.taboola.com/backstage-api/reference/campaign-summary-report
- Conversion rules: https://developers.taboola.com/backstage-api/reference/conversion-rule-quick-reference
- Targeting/dictionaries: https://developers.taboola.com/backstage-api/reference/audience-targeting-quick-reference and https://developers.taboola.com/backstage-api/reference/dictionary-overview

Implication: Taboola uses OAuth2 bearer access tokens and account-scoped paths. The current scaffold uses `auth_type: oauth`. Campaign items are the creative/ad units; a campaign create/update contract must not imply Meta-style ad sets. Updates are partial and pause/resume uses `is_active`, while `status` is read-only.

### User-Owned HTTP Webhooks

- StackOS internal executor notes: `docs/action-executor.md`

Implication: webhook actions are project-local HTTP contracts, not provider APIs. They may adapt to a user's own campaign tooling, but must require static connector config, fixed host allowlisting, method/path/headers declared in metadata, daemon-side secret injection, idempotency keys for writes, timeout/retry policy, and response redaction before execution. Media webhook refs should stay media-specific so they do not collide with GTM or other domains.

## Current Scaffold Findings

- Good: Meta Ads, Google Ads, and Taboola now have provider-specific daemon connectors instead of one generic REST adapter.
- Good: workflow templates separate approval gates from action contracts and keep secrets daemon-side.
- Resolved in the scaffold: Google Ads now has customer, budget, campaign, ad group, asset, ad, reporting, conversion action, and conversion upload contracts, and the templates can reference Google explicitly.
- Resolved in the scaffold: Taboola is OAuth-based, Outbrain has a daemon-managed token-lifecycle note, Meta budget updates are split by campaign and ad-set surface, reports use provider-native reporting refs, Meta conversions are represented, and user-owned webhook refs are media-specific.
- Still deferred: Outbrain endpoint-level campaign/report contracts are partner/API-doc gated, and user-owned webhooks need project-local static HTTP connector config.
- Still required before expanding execution: stricter provider enum coverage, more mocked provider tests, rate-limit/error classification, and richer pagination handling. Conversion uploads require callers to pass already-normalized provider events; StackOS does not hash or normalize PII inside this connector.

## Action Ref Recommendations

Keep provider-specific refs. Do not introduce `campaign.create` as a first-party action.

- Meta: `meta.account.list`, `meta.campaign.create`, `meta.campaign.update`, `meta.campaign.pause`, `meta.campaign.resume`, `meta.ad_set.create`, `meta.ad_set.update`, `meta.ad.create`, `meta.ad.update`, `meta.ad_creative.create`, `meta.asset.upload`, `meta.insights.fetch`, `meta.campaign_budget.update`, `meta.ad_set_budget.update`, `meta.conversions.send`.
- Google Ads: `google.customer.list`, `google.campaign_budget.create`, `google.campaign_budget.update`, `google.campaign.create`, `google.ad_group.create`, `google.asset.create`, `google.ad_group_ad.create`, `google.report.search`, `google.conversion_action.create`, `google.conversion_upload.clicks`.
- Outbrain: `outbrain.marketer.list`, `outbrain.campaign.create`, `outbrain.campaign.update`, `outbrain.campaign.pause`, `outbrain.campaign.resume`, `outbrain.budget.update`, `outbrain.promoted_link.create`, `outbrain.promoted_link.update`, `outbrain.report.fetch`, `outbrain.conversion_import.create` if official partner docs confirm the exact endpoint.
- Taboola: `taboola.account.get`, `taboola.campaign.create`, `taboola.campaign.update`, `taboola.campaign.pause`, `taboola.campaign.resume`, `taboola.item.create`, `taboola.item.update`, `taboola.report.fetch`, `taboola.conversion_rule.create`, `taboola.conversion_rule.update`.
- Webhook: prefer project-owned refs such as `webhook.media_campaign.create`, `webhook.media_campaign.update`, `webhook.media_budget.update`, and `webhook.media_performance.fetch` only after static HTTP connector config exists.

Use underscores in action keys for provider terms that are single concepts in code (`ad_set`, `ad_group`, `ad_creative`).

## Input Principles

Inputs must be explicit, bounded, and provider-specific:

- Require safe account/customer refs and one operation object per action.
- Require desired status explicitly; default provider activation should be conservative, usually paused/draft where the provider supports it.
- Require budget currency, amount micros/minor units where the provider expects them, period, delivery model, and cap semantics. Do not accept free-form `budget`.
- Require schedule/time zone, landing page/destination refs, tracking template or UTM policy, and conversion event refs when optimization depends on conversion tracking.
- Require reporting windows with concrete `start_date`/`end_date`, provider level/dimension, metrics, breakdowns/segments, attribution/conversion options, and pagination/async controls.
- Require idempotency or client request key for every write if the provider or webhook connector can support it; otherwise record the lack of idempotency as risk.
- For conversion events, require event source, event time, dedupe id/order id, consent/privacy flags, and hashing/normalization provenance. The agent must not receive raw PII.

## Output Principles

Outputs should be normalized but preserve provider diagnostics:

- Return safe refs, provider object type, normalized status, approval/review state, and immutable audit metadata.
- Include provider request id/trace id when available: Meta `fbtrace_id`, Outbrain `AMPLIFY-REQUEST-ID`, Google request id, Taboola error body fields.
- Include rate-limit headers and retry-after/backoff hints in metadata, redacted.
- Include partial failure details for batched Google mutates and reporting pagination cursors without exposing credentials.
- Store raw provider response only after redaction and size limits; templates should consume compact summaries and resource refs.

## Resource Mapping

- `ad-account`: safe provider account/customer/marketer metadata, credential/provider status, currency, time zone, and accessible capabilities.
- `campaign`: provider-specific campaign ref, objective/type, status, budget ref, schedule, tracking, destination policy, and launch approval.
- `ad-set`: Meta-only grouping unless a provider-native equivalent exists. Do not force Outbrain/Taboola into this resource.
- `ad-group`: Google-only grouping and search/display/video campaign substructure.
- `ad`: Meta ad, Google ad group ad, or Taboola/Outbrain item/promoted link only with provider subtype.
- `creative`: creative concept, copy, asset refs, and review notes; provider-created `AdCreative` or Taboola item details should link back to this.
- `conversion-event`: Meta pixel/dataset event, Google conversion action/upload mapping, Taboola conversion rule, Outbrain imported conversion mapping.
- `performance-snapshot`: reporting scope, provider report level/dimension, metric set, date window, attribution/conversion flags, freshness, and source action call.
- `budget-change`: proposed/applied change, before/after values, provider budget surface, approval ref, rollback notes, and observation plan.

## Approval And Risk

- All external create/update/pause/resume/budget/conversion-upload actions are write risk and require operator approval in run plans.
- Reporting reads may still need cost/volume approval because Meta insights, Google GAQL, Outbrain performance reporting, and Taboola reports can exhaust quotas or return large result sets.
- Campaign launch templates should include a tracking-health gate before spend-bearing writes when conversion optimization is part of the plan.
- Budget reallocation should separate recommendation, approval, mutation, and observation. The connector must not compute reallocations.
- Webhooks are high trust because the user's endpoint can perform arbitrary internal work. Treat write webhooks as external writes with allowlisted hosts, static methods, redacted logs, and explicit approval.

## Credential Boundary

- Meta: OAuth tokens and app permissions stay daemon-side. Setup fields may include safe business/account refs and selected API version only.
- Google Ads: OAuth refresh token, developer token, login customer id, and linked manager/customer metadata stay daemon-side. Templates should use `customer_ref`, not raw customer ids.
- Outbrain: username/password must never reach agents. If token generation is supported, only the daemon performs login and stores/rotates `OB-TOKEN-V1`.
- Taboola: OAuth2 client credentials/access tokens stay daemon-side. Provider setup should store safe account refs and token status diagnostics.
- Webhook: API keys, bearer tokens, HMAC secrets, mTLS material, and custom auth headers stay daemon-side. Agents provide only payload refs and approved operation inputs.

## Remaining Execution Gaps

1. Replace remaining broad provider-native property bags with stricter JSON
   schemas backed by metadata discovery or static enums where the docs permit.
2. Add timeout/retry/backoff, pagination, and rate-limit classifications to the
   provider-specific connectors.
3. Add grant tests for read/write separation, approval gates, provider request
   ids, and partial-failure handling.
4. Add more Google Ads mutate tests for budgets/campaigns/ad groups/assets/ads
   and conversions.
5. Keep conversion-event inputs already normalized by the caller; add a separate
   utility action later if StackOS should hash or normalize PII.
6. Add media webhook SSRF/private-network review, redacted structured errors,
   and retry/audit policy before executable HTTP campaign writes.

## Recommended Manifest And Template Corrections

- Keep Meta, Google Ads, Outbrain, Taboola, and media-webhook action refs provider-specific.
- Tighten remaining provider schemas before executable adapters are added.
- Keep conversion-event actions/resources behind explicit approval gates and tracking-health checks.
- For webhook actions, require project-local static HTTP connector metadata before execution and rename refs to media-specific names to avoid colliding with GTM/webhook domains.
