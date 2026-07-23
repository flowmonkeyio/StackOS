# HubSpot Sales And Marketing Integration Contract

Status: implementation contract frozen on 2026-07-22. This is the canonical
HubSpot provider ledger for StackOS. The executable source remains the GTM
plugin manifest and connector; this document explains and constrains that
contract rather than creating a second runtime registry.

## Delivery Boundary

StackOS integrates the customer lifecycle that sales and marketing teams need:
CRM metadata, customer records, typed relationships, sales activities,
sales-side revenue objects, capture and segments, campaigns and email assets,
consent, events, bounded bulk jobs, signed ingress, and one-to-one
transactional delivery.

The design deliberately does not mirror the HubSpot API. Public actions are
object-, scope-, and risk-specific; one connector owns shared HTTP, paging,
batch, error, and safe-reference mechanics. There is no public
`hubspot.records.*` action with an `object_type` escape hatch and no connector
per endpoint.

Bulk marketing blasts, commerce/payment fulfillment, arbitrary custom-object
mutation, developer-account webhook administration, custom workflow-action
registration, and beta revenue mutations are outside the executable phase.
They remain explicit deferred rows when their future boundary is useful.

## Official Contract Sources

| Area | Official source | Verified | Confidence |
| --- | --- | --- | --- |
| App creation and configuration | [Create an app](https://developers.hubspot.com/docs/apps/developer-platform/build-apps/create-an-app), [app configuration](https://developers.hubspot.com/docs/apps/developer-platform/build-apps/app-configuration) | 2026-07-22 | verified |
| Credentials and private distribution | [Manage apps](https://developers.hubspot.com/docs/apps/developer-platform/build-apps/manage-apps-in-hubspot) | 2026-07-22 | verified |
| OAuth and token lifecycle | [Manage OAuth tokens](https://developers.hubspot.com/docs/api-reference/latest/authentication/manage-oauth-tokens) | 2026-07-22 | verified |
| OAuth install and optional scopes | [Working with OAuth](https://developers.hubspot.com/docs/apps/developer-platform/build-apps/authentication/oauth/working-with-oauth) | 2026-07-22 | verified |
| Scopes and entitlements | [Scopes](https://developers.hubspot.com/docs/apps/developer-platform/build-apps/authentication/scopes) | 2026-07-22 | verified |
| Static account credentials | [Service keys](https://developers.hubspot.com/docs/apps/developer-platform/build-apps/authentication/account-service-keys) | 2026-07-22 | verified; public beta |
| CRM objects and search | [Contacts](https://developers.hubspot.com/docs/api-reference/latest/crm/objects/contacts/guide), [CRM search](https://developers.hubspot.com/docs/api-reference/latest/crm/search/guide) | 2026-07-22 | verified |
| Properties and owners | [Properties](https://developers.hubspot.com/docs/api-reference/latest/crm/properties/guide), [Owners](https://developers.hubspot.com/docs/api-reference/latest/crm/owners/guide) | 2026-07-22 | verified |
| Pipelines and associations | [Pipelines](https://developers.hubspot.com/docs/api-reference/latest/crm/pipelines/guide), [Associations](https://developers.hubspot.com/docs/api-reference/latest/crm/associations/associate-records/guide) | 2026-07-22 | verified |
| Sales activities | [Notes](https://developers.hubspot.com/docs/api-reference/latest/crm/activities/notes/guide), [Tasks](https://developers.hubspot.com/docs/api-reference/latest/crm/activities/tasks/guide), [Calls](https://developers.hubspot.com/docs/api-reference/latest/crm/activities/calls/guide), [Meetings](https://developers.hubspot.com/docs/api-reference/latest/crm/activities/meetings/guide) | 2026-07-22 | verified |
| Sales revenue objects | [Products](https://developers.hubspot.com/docs/api-reference/latest/crm/objects/products/guide), [Line items](https://developers.hubspot.com/docs/api-reference/latest/crm/objects/line-items/guide), [Quotes](https://developers.hubspot.com/docs/api-reference/latest/crm/objects/quotes/guide), [Goals](https://developers.hubspot.com/docs/api-reference/latest/crm/objects/goals/guide) | 2026-07-22 | verified |
| Lists and forms | [Lists](https://developers.hubspot.com/docs/api-reference/latest/crm/lists/guide), [Forms](https://developers.hubspot.com/docs/api-reference/latest/marketing/forms/guide) | 2026-07-22 | verified |
| Campaigns and marketing email | [Campaigns](https://developers.hubspot.com/docs/api-reference/latest/marketing/campaigns/guide), [Marketing email](https://developers.hubspot.com/docs/api-reference/latest/marketing/marketing-emails/guide) | 2026-07-22 | verified |
| Consent | [Communication preferences](https://developers.hubspot.com/docs/api-reference/latest/communication-preferences/guide) | 2026-07-22 | verified |
| Marketing and behavioral events | [Marketing events](https://developers.hubspot.com/docs/api-reference/latest/marketing/marketing-events/guide), [Custom event occurrences](https://developers.hubspot.com/docs/api-reference/latest/events/send-event-data/guide) | 2026-07-22 | verified |
| Imports and exports | [Imports](https://developers.hubspot.com/docs/api-reference/latest/crm/imports/guide), [Exports](https://developers.hubspot.com/docs/api-reference/latest/crm/exports/guide), [start export](https://developers.hubspot.com/docs/api-reference/latest/crm/exports/create-export), [export details](https://developers.hubspot.com/docs/api-reference/latest/crm/exports/get-export) | 2026-07-22 | verified against `2026-03` paths |
| Transactional email | [Transactional email](https://developers.hubspot.com/docs/api-reference/latest/marketing/transactional-emails/guide) | 2026-07-22 | verified |
| Webhooks and workflow actions | [Configure webhooks](https://developers.hubspot.com/docs/apps/developer-platform/add-features/configure-webhooks), [Webhooks API](https://developers.hubspot.com/docs/api-reference/latest/webhooks/guide), [Validate requests](https://developers.hubspot.com/docs/apps/developer-platform/build-apps/authentication/request-validation), [Custom workflow actions](https://developers.hubspot.com/docs/api-reference/latest/automation/workflow-actions/custom-action-guide) | 2026-07-22 | verified |
| Limits and errors | [Platform usage guidelines](https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines) | 2026-07-22 | verified |

## Authentication And Callback

The production/multi-account path is OAuth authorization code through the
central StackOS lifecycle:

- Authorization endpoint: `https://app.hubspot.com/oauth/authorize`.
- Token, refresh, introspection, and revoke family:
  `https://api.hubspot.com/oauth/2026-03/*`.
- Client authentication: form body.
- PKCE: unavailable in the currently documented HubSpot flow; StackOS still
  owns state, one-time callback consumption, concurrency, refresh, and failed
  reconnect preservation.
- Callback registered in HubSpot:
  `https://auth.stackos.flowmonkey.io/api/v1/auth/oauth/callback`.
- The public callback relay forwards the unchanged query to the local daemon
  callback at `http://127.0.0.1:5180/api/v1/auth/oauth/callback`; the browser
  then returns to the generic StackOS Connections view.
- The public page was live over HTTPS and matched the reviewed local
  `index.html` byte-for-byte on 2026-07-22. That proves callback transport, not
  a released StackOS OAuth runtime or a completed HubSpot installation.
- Token responses use the plural `scopes` array and `hub_id`; the central
  lifecycle persists granted scopes and safe account metadata daemon-side.

HubSpot service keys are valid bearer credentials for a single account and
REST APIs, but they are public beta, do not support webhooks, and do not have a
documented scope-introspection contract. StackOS therefore does not advertise
them as execution-ready in this phase: action scope checks must remain
provider-verified rather than operator-asserted. The legacy manual OAuth-token
form is removed from the public setup contract because a short-lived access
token without its refresh lifecycle is not a working connection.

### Operator setup path

The console/CLI walkthrough, exact app configuration, callback transport,
connection steps, ingress prerequisites, live proof, repair, and cleanup are in
[`oauth-provider-setup.md`](../oauth-provider-setup.md#hubspot). The intended
initial registration is one privately distributed HubSpot app with OAuth:

1. Create an App project with the HubSpot CLI; choose `private` distribution
   and `oauth` authentication.
2. Configure CRM Core as required scopes, the union of supported feature scopes
   as optional scopes, and the exact fixed callback. Add Webhooks or Custom
   Workflow Action app features only when those ingress capabilities are being
   configured.
3. Upload the HubSpot project, then open **Project Components** -> the app ->
   **Auth** for its client ID and client secret. Store them only through the
   local StackOS Connections form.
4. On **Distribution**, allowlist the intended account. Start consent from
   StackOS, not from a copied install link, so the one-use StackOS state is
   present.
5. Select only needed optional bundles, connect, verify returned account/scopes,
   and prove a read before any write.

Private OAuth distribution supports up to 10 allowlisted production accounts,
excluding developer test accounts. Marketplace publication is a later product
decision, not a prerequisite for this delivery. HubSpot static authentication
is single-account and remains outside the execution-ready StackOS methods.

The HubSpot OAuth contract requires a non-empty refresh token, positive
`expires_in`, returned scope evidence, and `hub_id` before an authorization-code
exchange can make a connection `connected`. Refresh responses must carry the
documented refresh token and positive expiry before rotated state is committed.
When HubSpot returns refreshed scope evidence, it replaces the stored scope set;
when the optional field is absent, StackOS retains the previously verified scope
set and its known status. An incomplete first exchange fails closed; an
incomplete reconnect leaves the prior working credential unchanged.

### OAuth capability bundles

`CRM Core` is the only required install bundle. Operators select extra bundles
on the connection profile; bundles with scopes use incremental authorization.
The Webhooks bundle is a scope-free operator checklist and does not require new
consent. HubSpot receives selected licensed scopes as `optional_scope`, so an
account can connect even when it cannot grant a licensed feature. Scope state
is computed from the actual returned scopes, never from the requested bundle
name.

| Bundle / readiness group | Requested scopes | Availability |
| --- | --- | --- |
| CRM Core | `crm.objects.contacts.read`, `crm.objects.contacts.write`, `crm.objects.companies.read`, `crm.objects.companies.write`, `crm.objects.deals.read`, `crm.objects.deals.write`, `crm.objects.owners.read`, `crm.schemas.contacts.read`, `crm.schemas.companies.read`, `crm.schemas.deals.read` | all accounts |
| Sales | `crm.objects.leads.read`, `crm.objects.leads.write`, `crm.objects.products.read`, `crm.objects.products.write`, `crm.objects.line_items.read`, `crm.objects.line_items.write`, `crm.objects.quotes.read`, `crm.objects.goals.read` | feature-specific; only executable Sales actions are requested; deferred sequence scopes are not requested |
| Marketing | `forms`, `crm.lists.read`, `crm.lists.write`, `marketing.campaigns.read`, `marketing.campaigns.write`, `marketing-email`, `subscriptions-definition-read`, `subscriptions-status-read`, `subscriptions-status-write`, `crm.objects.marketing_events.read`, `crm.objects.marketing_events.write`, `analytics.behavioral_events.send`, `behavioral_events.event_definitions.read_write` | campaigns require Marketing Professional/Enterprise; definitions require Marketing Enterprise; behavioral sends require a supported Professional/Enterprise hub |
| Bulk | `crm.export` | OAuth installer must be a Super Admin; deferred imports do not request `crm.import` until their multipart artifact path is executable |
| Webhooks | no additional OAuth scope beyond the CRM object read scopes for selected webhook subscriptions | app configuration, public ingress, signature validation, and exact event allowlist are operator checks |
| Custom Workflow Automation | `automation` | eligible Professional/Enterprise hub plus app registration, public action URL, signature validation, and exact definition allowlist |
| Transactional Communications | `transactional-email` | Marketing Professional/Enterprise plus Transactional Email add-on |

The global `oauth` scope is HubSpot-managed and may appear in granted scopes;
StackOS does not use it to imply action readiness.
The connection also exposes a non-secret
`transactional_email_entitlement_confirmed` field. It is an operator assertion
for the exact connected portal, not a substitute for a returned OAuth scope or
provider proof. StackOS fails closed while it is false; HubSpot's actual send
response remains authoritative after it is true.
Deferred actions do not expand an OAuth bundle: sequence, forecast, and deal
split scopes are documented on their unavailable action rows but are requested
only after those actions gain an executable, verified contract.

## Shared Request And Response Boundaries

The action tables use these compact boundary names:

- `metadata-read`: bounded `limit`/`after` plus the action's fixed object or
  relationship; returns normalized metadata, opaque safe refs, paging, and
  response-file metadata.
- `record-search`: bounded filters, sorts, selected properties, `limit <= 200`,
  and `after`; returns records with opaque account/type-bound safe refs and no
  reusable raw HubSpot IDs.
- `batch-upsert`: a provider-verified unique `id_property`, at most 100
  explicit property bags, safe-ref property values, and stable
  request/idempotency context; relationship changes remain separate actions;
  returns per-row success/failure and safe refs.
- `typed-association`: two opaque safe record refs plus one verified
  association-label ref; create/remove returns relationship state and audit
  context.
- `asset-read`/`asset-write`: bounded provider-native asset fields with safe
  refs; writes rely on action-call idempotency and explicit write grants.
- `job-create`/`job-status`/`job-result`: synthetic file/property mapping input
  or opaque job ref; returns async state, counts, partial failures, and
  response-file/download metadata without exposing signed URLs as durable refs.
- `side-effect-send`: one approved template, one explicit communication target,
  substitutions/custom properties, consent and entitlement context, and an
  idempotency key; returns provider status/request evidence plus opaque
  message/event refs and any partial state in a raw side-effect envelope.

All executable rows use OAuth. `connector=hubspot` means the single shared
connector; `deferred` rows have no connector. Proof owners are abbreviated as
`manifest`, `oauth`, `connector`, `refs`, `communication`, or `ingress` and map
to focused tests named for those surfaces.

## CRM Core Action Ledger

| Public action ref | Endpoint and method | Boundary | Exact scopes | Safe refs / risk | Entitlement | Availability, connector, proof |
| --- | --- | --- | --- | --- | --- | --- |
| `hubspot.crm.contacts.properties.list` | `GET /crm/properties/2026-03/contacts` | metadata-read | `crm.schemas.contacts.read` | property refs; read | all | executable; hubspot; manifest+connector+refs |
| `hubspot.crm.companies.properties.list` | `GET /crm/properties/2026-03/companies` | metadata-read | `crm.schemas.companies.read` | property refs; read | all | executable; hubspot; manifest+connector+refs |
| `hubspot.crm.deals.properties.list` | `GET /crm/properties/2026-03/deals` | metadata-read | `crm.schemas.deals.read` | property refs; read | all | executable; hubspot; manifest+connector+refs |
| `hubspot.crm.owners.list` | `GET /crm/owners/2026-03` | metadata-read | `crm.objects.owners.read` | owner refs; read | all | executable; hubspot; manifest+connector+refs |
| `hubspot.crm.deals.pipelines.list` | `GET /crm/pipelines/2026-03/deals` | metadata-read | `crm.objects.deals.read` | pipeline/stage refs; read | all | executable; hubspot; manifest+connector+refs |
| `hubspot.crm.contact_company.labels.list` | `GET /crm/associations/2026-03/contacts/companies/labels` | metadata-read | contacts+companies read | label refs; read | all | executable; hubspot; connector+refs |
| `hubspot.crm.contact_deal.labels.list` | `GET /crm/associations/2026-03/contacts/deals/labels` | metadata-read | contacts+deals read | label refs; read | all | executable; hubspot; connector+refs |
| `hubspot.crm.company_deal.labels.list` | `GET /crm/associations/2026-03/companies/deals/labels` | metadata-read | companies+deals read | label refs; read | all | executable; hubspot; connector+refs |
| `hubspot.crm.contacts.search` | `POST /crm/objects/2026-03/contacts/search` | record-search | `crm.objects.contacts.read` | contact refs; read | all | executable; hubspot; connector+refs |
| `hubspot.crm.contacts.batch_upsert` | `POST /crm/objects/2026-03/contacts/batch/upsert` | batch-upsert | `crm.objects.contacts.write` | contact/association refs; write | all | executable; hubspot; connector+refs |
| `hubspot.crm.companies.search` | `POST /crm/objects/2026-03/companies/search` | record-search | `crm.objects.companies.read` | company refs; read | all | executable; hubspot; connector+refs |
| `hubspot.crm.companies.batch_upsert` | `POST /crm/objects/2026-03/companies/batch/upsert` | batch-upsert | `crm.objects.companies.write` | company/association refs; write | all | executable; hubspot; connector+refs |
| `hubspot.crm.deals.list` | `GET /crm/objects/2026-03/deals` | record-search | `crm.objects.deals.read` | deal refs; read | all | executable; hubspot; connector+refs |
| `hubspot.crm.deals.search` | `POST /crm/objects/2026-03/deals/search` | record-search | `crm.objects.deals.read` | deal refs; read | all | executable; hubspot; connector+refs |
| `hubspot.crm.deals.batch_upsert` | `POST /crm/objects/2026-03/deals/batch/upsert` | batch-upsert | `crm.objects.deals.write` | deal/association refs; write | all | executable; hubspot; connector+refs |
| `hubspot.crm.leads.properties.list` | `GET /crm/properties/2026-03/leads` | metadata-read | `crm.objects.leads.read` | property refs; read | Sales Pro/Enterprise | executable when granted; hubspot; manifest+connector+refs |
| `hubspot.crm.leads.search` | `POST /crm/objects/2026-03/leads/search` | record-search | `crm.objects.leads.read` | lead refs; read | Sales Pro/Enterprise | executable when granted; hubspot; connector+refs |
| `hubspot.crm.leads.batch_upsert` | `POST /crm/objects/2026-03/leads/batch/upsert` | batch-upsert | `crm.objects.leads.write` | lead/association refs; write | Sales Pro/Enterprise | executable when granted; hubspot; connector+refs |
| `hubspot.crm.custom_objects.search` | object schema varies | record-search | `crm.objects.custom.read`, `crm.schemas.custom.read` | typed custom refs; read | Enterprise | deferred; `deferred-custom-object-schema`; custom schemas need an operator-approved object contract |
| `hubspot.crm.custom_objects.batch_upsert` | object schema varies | batch-upsert | `crm.objects.custom.write`, `crm.schemas.custom.read` | typed custom refs; write | Enterprise | deferred; `deferred-custom-object-schema`; arbitrary custom mutation is not exposed |

### Typed relationship actions

Each row uses the corresponding fixed `PUT` association endpoint and the
`POST .../batch/labels/archive` label-removal endpoint under
`/crm/objects/2026-03` or `/crm/associations/2026-03`. Removal preserves other
labels on the relationship. The connector accepts only safe refs and a label
ref previously returned for that fixed direction.

| Public action refs | Scopes | Risk | Availability / proof |
| --- | --- | --- | --- |
| `hubspot.crm.contact_company.associate`, `hubspot.crm.contact_company.dissociate` | contacts+companies write | write | executable; hubspot; connector+refs+audit |
| `hubspot.crm.contact_deal.associate`, `hubspot.crm.contact_deal.dissociate` | contacts+deals write | write | executable; hubspot; connector+refs+audit |
| `hubspot.crm.company_deal.associate`, `hubspot.crm.company_deal.dissociate` | companies+deals write | write | executable; hubspot; connector+refs+audit |

## Sales Action Ledger

| Public action ref | Endpoint and method | Boundary | Scope | Safe refs / risk | Entitlement | Availability, connector, proof |
| --- | --- | --- | --- | --- | --- | --- |
| `hubspot.crm.notes.create` | `POST /crm/objects/2026-03/notes` | asset-write | `crm.objects.contacts.write` | association refs; write | all | executable; hubspot; connector+refs |
| `hubspot.crm.tasks.create` | `POST /crm/objects/2026-03/tasks` | asset-write | `crm.objects.contacts.write` | association/owner refs; write | all | executable; hubspot; connector+refs |
| `hubspot.crm.calls.create` | `POST /crm/objects/2026-03/calls` | asset-write | `crm.objects.contacts.write` | association/owner refs; write | all | executable; hubspot; connector+refs |
| `hubspot.crm.meetings.create` | `POST /crm/objects/2026-03/meetings` | asset-write | `crm.objects.contacts.write` | association/owner refs; write | all | executable; hubspot; connector+refs |
| `hubspot.sales.sequences.list` | Sequences API read | asset-read | `automation.sequences.read` | sequence/user refs; read | Sales/Service Pro/Enterprise and seat | deferred; `deferred-sequence-live-contract`; requires seat-qualified live proof |
| `hubspot.sales.sequences.enroll`, `hubspot.sales.sequences.unenroll` | Sequences enrollment API | asset-write | `automation.sequences.enrollments.write` | contact/sequence/user refs; high | Sales/Service Pro/Enterprise and seat | deferred; `deferred-sequence-live-contract`; enrollment conflict and seat behavior require live proof |
| `hubspot.sales.products.properties.list` | `GET /crm/properties/2026-03/products` | metadata-read | `crm.objects.products.write` | property refs; read | all | executable; HubSpot authorizes this metadata endpoint with the product write scope; hubspot; connector+refs |
| `hubspot.sales.line_items.properties.list` | `GET /crm/properties/2026-03/line_items` | metadata-read | `crm.objects.line_items.read` | property refs; read | all | executable; hubspot; connector+refs |
| `hubspot.sales.quotes.properties.list` | `GET /crm/properties/2026-03/quotes` | metadata-read | `crm.objects.quotes.read` | property refs; read | all | executable; hubspot; connector+refs |
| `hubspot.sales.goal_targets.properties.list` | `GET /crm/properties/2026-03/goal_targets` | metadata-read | `crm.objects.goals.read` | property refs; read | Sales Starter+ | executable when granted; hubspot; connector+refs |
| `hubspot.sales.products.search` | `POST /crm/objects/2026-03/products/search` | record-search | `crm.objects.products.read` | product refs; read | all | executable; hubspot; connector+refs |
| `hubspot.sales.products.batch_upsert` | `POST /crm/objects/2026-03/products/batch/upsert` | batch-upsert | `crm.objects.products.write` | product refs; write | all | executable; hubspot; connector+refs |
| `hubspot.sales.line_items.search` | `POST /crm/objects/2026-03/line_items/search` | record-search | `crm.objects.line_items.read` | line-item/deal/product refs; read | all | executable; hubspot; connector+refs |
| `hubspot.sales.line_items.batch_upsert` | `POST /crm/objects/2026-03/line_items/batch/upsert` | batch-upsert | `crm.objects.line_items.write` | line-item/deal/product refs; write | all | executable; hubspot; connector+refs |
| `hubspot.sales.line_items.associate_deal`, `hubspot.sales.line_items.dissociate_deal` | fixed 2026-03 default association endpoints | typed-association | line-items+deals write | line-item/deal refs; write | all | executable; hubspot; connector+refs+audit |
| `hubspot.sales.quotes.search` | `POST /crm/objects/2026-03/quotes/search` | record-search | `crm.objects.quotes.read` | quote refs; read | all | executable; hubspot; connector+refs |
| `hubspot.sales.goal_targets.list` | `GET /crm/objects/2026-03/goal_targets` | record-search | `crm.objects.goals.read` | goal-target/owner refs; read | Sales Starter+ | executable when granted; hubspot; connector+refs |
| `hubspot.sales.forecasts.list` | `GET /crm/objects/v3/forecasts` | record-search | `crm.objects.forecasts.read` | forecast refs; read | supported Pro+; public beta | deferred; `deferred-provider-beta`; no production claim for beta API |
| `hubspot.sales.deal_splits.list`, `hubspot.sales.deal_splits.upsert` | Deal splits API | asset-read/write | `crm.dealsplits.read_write` | deal/split/owner refs; high | Sales Enterprise | deferred; `deferred-enterprise-mutation`; requires live entitlement and partial-state proof |

## Marketing Action Ledger

| Public action ref | Endpoint and method | Boundary | Scope | Safe refs / risk | Entitlement | Availability, connector, proof |
| --- | --- | --- | --- | --- | --- | --- |
| `hubspot.marketing.forms.list` | `GET /marketing/v3/forms` | asset-read | `forms` | form refs; read | all | executable; hubspot; connector+refs |
| `hubspot.marketing.forms.submissions.list` | `GET /form-integrations/v1/submissions/forms/{formId}` | asset-read | `forms` | form/submission/field refs; sensitive read | all | executable; hubspot; connector+refs; submitted values are marked sensitive and page URLs drop query/fragment data |
| `hubspot.marketing.segments.list` | `POST /crm/lists/2026-03/search` | asset-read | `crm.lists.read` | contact-segment refs; read | all | executable; hubspot; connector+refs; fixed to contact object type `0-1` |
| `hubspot.marketing.segments.memberships.list` | `GET /crm/lists/2026-03/{listId}/memberships` | asset-read | `crm.lists.read` | segment/contact refs; read | all | executable; hubspot; connector+refs |
| `hubspot.marketing.segments.memberships.add` | `PUT /crm/lists/2026-03/{listId}/memberships/add` | asset-write | `crm.lists.write` | segment/contact refs; write | all | executable only for provider-verified `MANUAL` or `SNAPSHOT` segments; hubspot; connector+refs |
| `hubspot.marketing.segments.memberships.remove` | `PUT /crm/lists/2026-03/{listId}/memberships/remove` | asset-write | `crm.lists.write` | segment/contact refs; write | all | executable only for provider-verified `MANUAL` or `SNAPSHOT` segments; hubspot; connector+refs |
| `hubspot.marketing.contacts.status.update` | Marketing contacts API | asset-write | `crm.objects.contacts.write` | contact refs; high/cost | product/account limits | deferred; `deferred-marketing-contact-cost`; contract requires an explicit billing-impact acknowledgement, but execution remains unavailable until entitlement, cost, and partial-state behavior are proven |
| `hubspot.marketing.campaigns.list` | `GET /marketing/campaigns/2026-03` | asset-read | `marketing.campaigns.read` | campaign/owner/brand refs; read | Marketing Pro/Enterprise | executable when granted; hubspot; bounded public campaign fields; connector+refs |
| `hubspot.marketing.campaigns.get` | `GET /marketing/campaigns/2026-03/{campaignGuid}` | asset-read | `marketing.campaigns.read` | campaign/asset refs; read | Marketing Pro/Enterprise | executable when granted; asset metrics require both `start_date` and `end_date`; connector+refs |
| `hubspot.marketing.campaigns.create` | `POST /marketing/campaigns/2026-03` | asset-write | `marketing.campaigns.write` | campaign refs; write | Marketing Pro/Enterprise | executable for the documented writable property allowlist; hubspot; connector+refs |
| `hubspot.marketing.campaigns.update` | `PATCH /marketing/campaigns/2026-03/{campaignGuid}` | asset-write | `marketing.campaigns.write` | campaign ref; write | Marketing Pro/Enterprise | executable for the documented writable property allowlist; hubspot; connector+refs |
| `hubspot.marketing.emails.list` | `GET /marketing/emails/2026-03` | asset-read | `marketing-email` | email/campaign/template/folder/subscription/brand refs; read | account feature dependent | executable; the canonical email ref retains provider-declared transactional eligibility and lifecycle state; `include_stats` exposes numeric post-send aggregates but omits recipient configuration; hubspot; connector+refs |
| `hubspot.marketing.emails.get` | `GET /marketing/emails/2026-03/{emailId}` | asset-read | `marketing-email` | email asset ref; read | account feature dependent | executable; refreshes canonical transactional/lifecycle metadata; numeric statistics only and no recipient values; hubspot; connector+refs |
| `hubspot.marketing.emails.create` | `POST /marketing/emails/2026-03` | asset-write | `marketing-email` | campaign/brand/template refs or validated stable template path; write | account feature dependent | executable for draft creation only; recipients, schedule, and publish state are not accepted; hubspot; connector+refs |
| `hubspot.marketing.emails.update` | `PATCH /marketing/emails/2026-03/{emailId}` | asset-write | `marketing-email` | email/campaign/brand refs; write | account feature dependent | executable only after a provider read reports `isPublished=false` and exact `DRAFT` state; scheduled, ambiguous, recipient, and publishing changes fail closed; hubspot; connector+refs |
| `hubspot.marketing.emails.publish` | publish endpoint | asset-write | `marketing-email` | email asset ref; high | Marketing Enterprise or transactional add-on | deferred; `deferred-publish-live-proof`; lifecycle proof required |
| `hubspot.marketing.emails.bulk_send` | recipient delivery | side-effect-send | `marketing-email` | segment/contact refs; critical | product dependent | deferred; `deferred-bulk-marketing-send`; intentionally excluded; normal one-to-one transactional delivery belongs to `communication.send` |
| `hubspot.marketing.subscription_types.list` | `GET /communication-preferences/2026-03/definitions` | asset-read | `subscriptions-definition-read` | subscription refs; read | all | executable; hubspot; connector+refs |
| `hubspot.marketing.contact_preferences.get` | contact email lookup plus `GET /communication-preferences/2026-03/statuses/{subscriberIdString}` | asset-read | `crm.objects.contacts.read`, `subscriptions-status-read` | contact/subscription refs; sensitive read | all | executable; the connector resolves the primary email internally and never returns it; hubspot; connector+refs |
| `hubspot.marketing.contact_preferences.update` | contact email lookup plus `POST /communication-preferences/2026-03/statuses/{subscriberIdString}` | asset-write | `crm.objects.contacts.read`, `subscriptions-status-write` | contact/subscription refs; high/legal | all | executable only with legal basis, explanation, explicit legal-change confirmation, and affirmative consent proof for `SUBSCRIBED`; subscriber email is never returned; hubspot; connector+refs+audit |
| `hubspot.marketing.events.list` | `GET /marketing/marketing-events/2026-03` | asset-read | `crm.objects.marketing_events.read` | event/app refs; read | all | executable with bounded paging; the list endpoint is treated as metadata-only, while custom-property values and raw provider/external IDs are omitted; hubspot; connector+refs |
| `hubspot.marketing.events.upsert` | `POST /marketing/marketing-events/2026-03/events/upsert` | asset-write | `crm.objects.marketing_events.write` | stable external account/event keys in, event ref out; write | all | executable for one event per action call; only the same HubSpot app can update events it created; hubspot; connector+refs+audit |
| `hubspot.marketing.behavioral_events.definitions.list` | `GET /events/2026-03/event-definitions` | asset-read | `behavioral_events.event_definitions.read_write` | definition/property refs; read | qualifying Enterprise hub | executable when granted; optional property metadata returns only opaque reusable refs; hubspot; connector+refs |
| `hubspot.marketing.behavioral_events.send` | `POST /events/2026-03/send` | asset-write | `analytics.behavioral_events.send` | definition/contact/property refs; high/customer behavior | supported Professional/Enterprise hub | executable only for a provider-verified contact definition, explicit timestamp, stable occurrence key, tracking authority, and legal-basis context; no email or raw ID input; hubspot; connector+refs+audit |

### User-facing marketing and behavioral event sequence

1. Connect the Marketing bundle. Readiness remains action-specific: marketing
   event reads/writes, definition discovery, and behavioral sends each require
   their exact scope and the portal must satisfy the provider entitlement.
2. Use `hubspot.marketing.events.list` to obtain opaque event/app refs and
   aggregate registration/attendance counts. It does not return attendee
   identities, custom-property values, or raw HubSpot and external IDs.
3. Use `hubspot.marketing.events.upsert` for one app-owned event with stable
   `external_account_key` and `external_event_key` values. StackOS action
   idempotency plus HubSpot's external identifiers make retries safe; the
   result exposes an opaque event ref and explicit partial/incomplete state.
4. Use `hubspot.marketing.behavioral_events.definitions.list` with
   `include_properties=true` to obtain the provider-verified definition and
   property refs needed for a send. A definition must target contacts before
   the send path will accept it.
5. Use `hubspot.marketing.behavioral_events.send` with one definition ref, one
   contact ref, an ISO 8601 timestamp, property-ref/value pairs, a stable
   occurrence key, and explicit tracking/legal-basis confirmation. StackOS
   converts the occurrence key into a deterministic provider UUID, so an
   executor replay does not call HubSpot twice and a provider-level replay is
   deduplicated. The connector never accepts or returns a contact email, raw
   contact ID, fully qualified event name, property API name, or provider UUID.

These actions record customer activity only. They do not choose a campaign,
segment, workflow, lead score, or follow-up. Inbound provider events remain
subject to the shared ingress/request policy and never select a workflow.

## Bulk, Ingress, And Communication Ledger

| Contract | Endpoint / owner | Scope | Risk and safe refs | Availability, execution mode, proof |
| --- | --- | --- | --- | --- |
| `hubspot.bulk.exports.create` | `POST /crm/exports/2026-03/export/async`; shared HubSpot connector | `crm.export`; OAuth installer must be a HubSpot Super Admin | high-impact data-export start; static object type plus provider-verified property refs; opaque job ref | executable; one `VIEW` export; connector+refs+provider async; caller idempotency required |
| `hubspot.bulk.exports.status` | `GET /crm/exports/2026-03/export/{exportId}` | `crm.export` | job-status with account-bound opaque job ref; read | executable; reports truthful provider state, timestamps, record count, and the original safe selection |
| `hubspot.bulk.exports.result` | `GET /crm/exports/2026-03/export/async/tasks/{taskId}/status`, then bounded download only when `COMPLETE` | `crm.export` | job-result with opaque job ref; read; customer-data artifact | executable; pending/partial state is preserved; the five-minute signed URL is consumed in-process and never returned or stored; result becomes a generic generated asset and artifact record |
| `hubspot.bulk.imports.create`, `.status`, `.cancel`, `.errors` | `POST /crm/imports/2026-03` and companion Imports API reads; create requires `multipart/form-data` with an artifact plus `importRequest` | `crm.import` | artifact/job refs; high/partial | deferred; `deferred-multipart-artifact-bridge`; no connector until the generic action executor owns bounded multipart artifact streaming |
| HubSpot webhook ingress | `POST /api/v1/ingress/hubspot/{project_id}/{profile_key}` through generic `ingressEndpoint`; HubSpot v3 HMAC plus five-minute timestamp | app webhook configuration | account/app/subscription/object/event refs; incoming external data | executable ingress adapter; exact configured public URI and raw body are signed; batches are bounded to 100; only exact connection allowlists create an agent request; never starts a run plan |
| `hubspot.webhooks.subscriptions.configure` | `/webhooks/2026-3/{appId}` app configuration | developer/app authority, not ordinary installed-portal OAuth | app/subscription/property refs; high | deferred/operator-owned; `deferred-app-configuration`; the manifest points operators to `ingressEndpoint.routes` instead of accepting a developer key |
| HubSpot custom workflow-action invocation | same StackOS ingress route; provider-documented v3 signature and synchronous `SUCCESS`/`FAIL_CONTINUE` response | app client secret and exact definition allowlist | account/definition/execution/workflow/object refs; incoming external data | executable ingress adapter after manual app registration; callback ID dedupes replays; creates at most one agent request and does not select a workflow |
| `hubspot.automation.workflow_actions.register` | `POST /automation/actions/2026-03/{appId}` | `automation` plus app authority | app/definition refs; high | deferred; `deferred-app-configuration`; publication changes the developer app globally and has no connector in this phase |
| `hubspot.transactional.single_email.send` | `POST /marketing/transactional/2026-03/single-email/send` | `crm.objects.contacts.read`, `transactional-email` | side-effect-send; contact/template refs; high; direct provider escape hatch | executable underlying action; the current object-shaped `eventId.id` becomes the canonical event ref while `eventId.created` is safe metadata; hubspot; connector+communication+audit |
| Normal HubSpot transactional delivery | `communication.send` resolves the HubSpot profile/target/action | same exact scopes plus add-on confirmation | full provider-safe raw side-effect evidence and StackOS/provider idempotency | executable only when policy, target, scope, entitlement, and transactional template are ready; communication proof |

### User-facing bulk export sequence

1. Connect HubSpot with the Bulk bundle. The HubSpot user installing the OAuth
   app must be a Super Admin to grant `crm.export`.
2. Use the existing properties actions for the object being exported. Choose
   one supported standard object and pass only its account-bound property refs;
   raw property API names and custom-object IDs are not accepted.
3. Call `gtm.hubspot.bulk.exports.create` with a stable idempotency key, export
   name, file format, optional associated standard object types, and
   `export_authorized=true`. StackOS starts one full-object `VIEW` export and
   returns an opaque `provider-object:*` job ref. It does not create a second
   StackOS job or scheduler.
4. Call `gtm.hubspot.bulk.exports.status` with that job ref. The result reports
   the provider state (`queued`, `processing`, `complete`, `failed`,
   `canceled`, or another explicit mapped state), timestamps, record count, and
   the original safe object/property selection. HubSpot permits up to 30
   exports in a rolling 24-hour period and processes one at a time; StackOS
   does not bypass or shadow that queue.
5. Call `gtm.hubspot.bulk.exports.result`. While HubSpot is pending or
   processing, the action returns that state and any safe aggregate error
   classifications without claiming a file exists. When complete, StackOS
   immediately consumes the short-lived signed result URL with a caller-bounded
   byte limit, stores the file under generated assets, registers a draft
   `hubspot-export` artifact, and returns its artifact ref, filename, MIME type,
   size, and SHA-256. Large HubSpot exports may be zipped or partitioned; the
   provider-delivered file is retained as-is.
6. The signed URL, raw HubSpot job ID, provider property names, and exported
   customer rows never appear in action output, audit metadata, tracker
   evidence, or artifact metadata. Imports remain visibly unavailable until
   the shared multipart artifact bridge exists; callers must not approximate
   them with ad hoc connector uploads.

### User-facing transactional delivery sequence

1. Connect HubSpot with the Transactional Communications bundle and verify the
   Transactional Email add-on on that exact portal. Set the non-secret
   entitlement confirmation only after verification.
2. Discover/read the HubSpot marketing email asset through the existing email
   actions. StackOS issues an account-bound `provider-object:*` ref and records
   whether HubSpot identified it as transactional.
3. Configure one communication profile with a HubSpot provider facet and one
   named communication target whose surface is an opaque HubSpot contact ref.
   The target policy names the allowed profile/target and records
   `transactional_use_confirmed`, `consent_or_relationship_confirmed`, legal
   basis plus explanation, and `marketing_contact_state` set to a known
   `marketing` or `non-marketing` state.
4. Call `communication.send` with the target key and
   `content.template_ref`; pass only scalar `content.template_data` needed by
   the provider template. Use a stable `intent_id` when the business intent may
   be retried.
5. StackOS resolves the primary email inside the connector, sends exactly one
   provider template with an empty `contactProperties` map, derives HubSpot
   `sendId` deterministically from the StackOS idempotency key, and records one
   action audit plus one outbound communication message after provider
   acceptance.
6. The raw communication result reports `pending`, `processing`, `complete`,
   `canceled`, or `unknown`, safe message/event refs, correlation evidence,
   timestamps, and incomplete-response state. It never returns the recipient
   email or raw HubSpot contact, template, status, or event IDs.

The lower-level `gtm.hubspot.transactional.single_email.send` action exists for
deliberately provider-specific work. It requires the same confirmations and
safe refs. It is not the normal agent path. Bulk marketing blasts remain the
explicit deferred `hubspot.marketing.emails.bulk_send` action and are never
approximated by looping over this one-to-one flow.

## Focused Workflow Map

The GTM plugin composes provider actions into small jobs. It does not expose one
HubSpot mega-workflow, and selecting a provider bundle does not automatically
run anything.

| Workflow template | HubSpot capability | Guardrail |
| --- | --- | --- |
| `gtm.crm-hygiene-pass` | CRM Core metadata, searches, bounded batch upserts, and typed relationship repair | Stable batch key; explicit approval for writes; truthful per-row partial results |
| `gtm.pipeline-risk-review` | CRM Core pipeline/deal evidence; optional Sales reads for products, line items, quotes, and goal targets | Read-oriented; Sales branch is optional and cannot block the base review |
| `gtm.marketing-program-lifecycle` | Marketing forms, segments, campaigns, draft email assets, consent, and event evidence | Consent mutation has its own approval; no publish or bulk-send action |
| `gtm.crm-export-handoff` | Bulk export create, status, and bounded result artifact | One provider job per intent; no shadow scheduler; explicit retention decision |
| `gtm.customer-follow-up` | CRM Core activity writes plus optional one-to-one Transactional Communications | `communication.send` only through target `hubspot-customer-follow-up`; no raw-recipient or bulk loop |

Webhooks/Automation has no workflow template. Signed ingress may create one
agent request after verification and allowlisting, but it never selects,
creates, starts, or executes a run plan. Sequence enrollment remains deferred
and is not approximated by customer follow-up.

## Capability Readiness

Readiness is not a provider-wide boolean. The generic view computes connection
and OAuth-scope state from the credential and returned scopes. External app,
ingress, entitlement, target, consent, and registration prerequisites are shown
as an **operator checklist**; StackOS does not claim those items are
automatically verified or turn the group green from static manifest entries.
Provider action results and explicit operator evidence remain authoritative.

| Group | Computed scope state / operator checklist | Partial / repair examples |
| --- | --- | --- |
| CRM Core | connected account plus all baseline scopes | reconnect for missing scopes; refresh portal-aware metadata before use |
| Sales | selected Sales scopes; tier and seat remain provider/operator evidence | leads or sequences can remain unavailable without tier/seat; deferred actions remain unavailable |
| Marketing | selected Marketing scopes; feature entitlement remains provider/operator evidence | campaigns, event definitions, or email lifecycle can be independently unavailable |
| Bulk | `crm.export`; imports remain deferred | add Bulk scope or use a non-bulk workflow |
| Webhooks | no extra OAuth consent; checklist covers app ID, enabled signed ingress, public HTTPS route, app secret, subscription configuration, and event allowlist | repair app/ingress/signature/allowlist; app-wide subscription administration remains manual |
| Custom Workflow Automation | `automation`; checklist covers eligible tier, app registration, public action URL, signature validation, and definition allowlist | reconnect for automation scope or repair registration/allowlist |
| Transactional Communications | `transactional-email`; checklist covers add-on, communication profile/target, consent/legal-basis policy, and transactional template | reconnect for scope, configure the checklist, or use another delivery route |

## Safe References And Mapping Invariants

- Provider object IDs are stored only in the daemon's account-scoped provider
  reference map. Agents and reusable workflows receive opaque
  `provider-object:*` refs.
- Each mapping binds project, provider, verified HubSpot `hub_id`, object type,
  and provider object ID. Resolution checks all four dimensions.
- A ref created under another project, portal, object type, or connection is
  rejected. A provider `404` marks the mapping stale before it can be reused.
- Provider responses may be retained in file-backed action evidence, but
  normalized action output replaces reusable IDs and provider URLs with safe
  refs. Tokens, app secrets, signed download URLs, and authorization headers
  never enter output, tracker evidence, or logs.

## Paging, Errors, Idempotency, And Audit

- CRM object lists cap at 100 per page; CRM object search caps at 200 and
  10,000 results per query. Contact-segment search uses the current
  `/crm/lists/2026-03` contract, caps `count` at 500, and returns the provider
  `offset`; membership reads cap at 100 and return `after`. HubSpot sunset the
  legacy Lists v1 API on April 30, 2026, so no legacy or `/crm/v3/lists` path is
  registered.
- Marketing-event and behavioral-definition reads cap at 100 per page. One
  marketing event is upserted per action call. Behavioral occurrences accept
  at most 50 provider-verified property refs; a caller occurrence key is
  converted to a deterministic HubSpot `uuid` and never exposed in output.
- Export creation accepts one standard object, 1-100 provider-verified
  property refs, and at most four associated standard object types. HubSpot's
  rolling 30-per-24-hours and one-active-export limits remain provider-owned.
  Completed downloads are streamed with a caller-bounded byte ceiling and
  short-lived signed URLs are neither returned nor persisted.
- Batch writes cap at 100 rows and preserve HubSpot multi-status/partial
  outcomes. They do not report all-or-nothing success when individual rows
  failed.
- `400`, `401`, `403`, `404`, `409`, `423`, `429`, and `5xx` responses become
  `ActionConnectorError` with redacted provider status/error, correlation or
  request ID, retryability, and repair guidance. `Retry-After` is preserved.
- The generic action executor owns intent idempotency and audit. The connector
  uses provider idempotency fields only where HubSpot documents them and never
  invents business decisions or automatic destructive retries.
- Webhook batches are authenticated before JSON parsing and completely
  validated before writes. HubSpot v3 uses the configured public ingress URL,
  raw request body, and timestamp; untrusted `Host` headers never define the
  signed URI. Custom workflow-action invocations use the same documented v3
  method/URI/body/timestamp contract and dedupe on the opaque callback
  execution ref. Legacy v1/v2 ingress is rejected.
- Authenticated but non-allowlisted events may be retained as non-triggering
  audit context. Unknown portals/apps, malformed or oversized payloads, invalid
  signatures, and expired timestamps create no resources or requests. Ingress
  never creates, selects, starts, or executes a run plan.
- One enabled HubSpot credential route binds one developer app ID to one
  verified HubSpot account in one StackOS project. Reusing the same app-level
  Target URL across independently routed StackOS projects is not supported in
  this streamlined phase; use a deliberately owned app/profile route instead
  of adding cross-project dispatch logic.
- MCP and REST external action outputs remain file-backed by default. The
  transactional side effect keeps its full provider-safe raw evidence under
  the communications contract.

## Setup And Repair Metadata

The GTM manifest must expose:

- credential label: `HubSpot OAuth app`;
- setup note covering a HubSpot project-based OAuth app, selected capability
  bundles, and the fixed callback;
- official homepage, signup, development console, app/auth configuration,
  billing, scopes, and API documentation URLs with `verified_at=2026-07-22`
  and per-URL confidence;
- callback URL and local relay target;
- fallback guidance for account-gated app screens;
- repair actions for reconnect/scope upgrade, wrong portal, missing
  tier/seat/add-on, unhealthy ingress, stale refs, and deferred actions.
- optional signed-ingress fields for numeric app ID, enablement, exact CRM
  event allowlist, and exact custom workflow-action definition IDs; the generic
  ingress route is shown only when enablement is explicit.

No app registration, live HubSpot mutation, DNS/deployment change, or release
is performed by this implementation contract.
