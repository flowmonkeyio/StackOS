# GTM CRM Contract Audit

Status: first executable connector pass delivered on 2026-05-22 and HubSpot
expanded on 2026-07-22. This historical cross-provider review remains useful
for Salesforce and Pipedrive comparisons; the canonical current HubSpot ledger
is [`hubspot.md`](hubspot.md).

## Official Docs Ledger

### HubSpot

| Area | Official docs | Contract notes |
| --- | --- | --- |
| Auth | [Working with OAuth](https://developers.hubspot.com/docs/apps/developer-platform/build-apps/authentication/oauth/working-with-oauth), [current token lifecycle](https://developers.hubspot.com/docs/api-reference/latest/authentication/manage-oauth-tokens), [Scopes](https://developers.hubspot.com/docs/apps/developer-platform/build-apps/authentication/scopes) | StackOS implements interactive authorization code against the current `/oauth/2026-03/token` endpoint. CRM Core is required, selected feature scopes use optional consent, and returned `scopes`/`hub_id` are authoritative. Tokens remain daemon-side. |
| Object create/update/upsert | [Using object APIs](https://developers.hubspot.com/docs/api-reference/legacy/crm/using-object-apis), [latest 2026-03 API reference](https://developers.hubspot.com/docs/reference/api) | Create/update is property-bag based, but upsert is batch-only and requires a custom unique property or contact `email`. Do not model a provider-neutral single-record `upsert` without an `id_property`/match contract. |
| Notes/tasks/activities | [Notes API](https://developers.hubspot.com/docs/guides/api/crm/engagements/notes), [Tasks API](https://developers.hubspot.com/docs/reference/api/crm/engagements/tasks), [Associations v4](https://developers.hubspot.com/docs/guides/api/crm/associations/associations-v4) | Notes and tasks are CRM objects with provider property names (`hs_note_body`, `hs_timestamp`, `hs_task_subject`, etc.) and association type IDs/labels. Safe refs must resolve to HubSpot IDs inside the daemon before calls. |
| Pipeline/deals reads | [Search the CRM](https://developers.hubspot.com/docs/api-reference/latest/crm/search-the-crm), [Using object APIs](https://developers.hubspot.com/docs/api-reference/legacy/crm/using-object-apis) | Deals are object type `0-3`; pipeline reads should be `hubspot.crm.deals.search` or `hubspot.crm.deals.list`, not generic `hubspot.pipeline.fetch`. |
| Pagination | [Search the CRM limits](https://developers.hubspot.com/docs/api-reference/latest/crm/search-the-crm) | Search returns a maximum of 200 objects per page, is capped at 10,000 results for one query, and uses cursor-style paging. |
| Rate limits/errors | [API usage guidelines and limits](https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines), [Object API error handling](https://developers.hubspot.com/docs/api-reference/legacy/crm/using-object-apis) | Handle `429` with policy and request/correlation IDs. OAuth responses omit daily rate-limit headers, so connector output must include whatever headers are present plus retry classification. Batch create can return multi-status errors. |

### Salesforce

| Area | Official docs | Contract notes |
| --- | --- | --- |
| Auth | [OAuth 2.0 Web Server Flow](https://help.salesforce.com/s/articleView?id=xcloud.remoteaccess_oauth_web_server_flow_ca.htm&language=en_US&type=5), [OAuth endpoints](https://help.salesforce.com/s/articleView?id=sf.remoteaccess_oauth_endpoints.htm&language=en_US&type=5) | StackOS implements interactive authorization code with PKCE plus manual-token compatibility. Production, sandbox, and validated My Domain login hosts are supported; trusted `instance_url` metadata and refresh tokens stay daemon-side. |
| Object create/update/upsert | [REST API Developer Guide](https://resources.docs.salesforce.com/latest/latest/en-us/sfdc/pdf/api_rest.pdf), especially sObject Rows and sObject Rows by External ID | CRUD is sObject-specific. Upsert is `PATCH /sobjects/{sObject}/{externalIdField}/{externalIdValue}` and can create unless `updateOnly=true` is used. Contracts must require `sobject`, `external_id_field`, `external_id_ref`/value source, and `update_only` intent. |
| Notes/tasks/activities | [REST API Developer Guide](https://resources.docs.salesforce.com/latest/latest/en-us/sfdc/pdf/api_rest.pdf), [Object Reference](https://resources.docs.salesforce.com/latest/latest/en-us/sfdc/pdf/object_reference.pdf) | Tasks are sObjects; notes may be `Note`, `ContentNote`, or org-specific activity/feed patterns. A generic `salesforce.crm-note.create` is too vague until the connector chooses a supported object and linking model (`ParentId`, `ContentDocumentLink`, etc.). |
| Pipeline/opportunity reads | [REST API Developer Guide Query resource](https://resources.docs.salesforce.com/latest/latest/en-us/sfdc/pdf/api_rest.pdf), [SOQL/SOSL Reference](https://resources.docs.salesforce.com/latest/latest/en-us/sfdc/pdf/salesforce_soql_sosl.pdf) | Salesforce pipeline reads are SOQL over `Opportunity` plus related `Account`, `Task`, and `Event` as needed. Rename `salesforce.pipeline.fetch` to an opportunity/query-specific read action with allowlisted query templates. |
| Pagination | [REST API Developer Guide Query resource](https://resources.docs.salesforce.com/latest/latest/en-us/sfdc/pdf/api_rest.pdf) | Query responses include `done` and `nextRecordsUrl` when more records exist. Connectors should never expose raw arbitrary SOQL from agents; use bounded templates and continue only within run-plan limits. |
| Rate limits/errors | [REST API Developer Guide status codes](https://resources.docs.salesforce.com/latest/latest/en-us/sfdc/pdf/api_rest.pdf), [API limits and monitoring](https://developer.salesforce.com/blogs/2024/11/api-limits-and-monitoring-your-api-usage) | `REQUEST_LIMIT_EXCEEDED` appears with HTTP 403. Capture `Sforce-Limit-Info`, `/limits` diagnostics when granted, request IDs, and structured Salesforce errors. |

### Pipedrive

| Area | Official docs | Contract notes |
| --- | --- | --- |
| Auth | [OAuth 2.0](https://developers.pipedrive.com/docs/api/v1/Oauth), [About the Pipedrive API](https://pipedrive.readme.io/docs/core-api-concepts-about-pipedrive-api) | StackOS implements interactive authorization code with HTTP Basic token exchange, retains manual OAuth-token compatibility, and exposes API token as a separate built-in auth method. Provider-returned `api_domain` is accepted only through the trusted token response and stays in safe credential config. |
| Object create/update/upsert | [Deals](https://developers.pipedrive.com/docs/api/v1/Deals), [Persons](https://developers.pipedrive.com/docs/api/v1/Persons), [Organizations](https://developers.pipedrive.com/docs/api/v1/Organizations) | Pipedrive has create/update endpoints, but no native provider-wide upsert. Upsert must be a StackOS two-step search/read plus create/update connector with explicit matching policy, duplicate handling, and approval. |
| Notes/tasks/activities | [Notes](https://developers.pipedrive.com/docs/api/v1/Notes), [Activities](https://developers.pipedrive.com/docs/api/v1/Activities), [Tasks](https://developers.pipedrive.com/docs/api/v1/Tasks) | Pipedrive models notes and activities separately. Use `pipedrive.note.create` and `pipedrive.activity.create`; do not call these `task.create` unless the connector maps to the Tasks API intentionally. |
| Pipeline/deals reads | [Deals](https://developers.pipedrive.com/docs/api/v1/Deals), [Pipelines](https://developers.pipedrive.com/docs/api/v1/Pipelines), [Stages](https://developers.pipedrive.com/docs/api/v1/Stages) | `pipedrive.deal.fetch` is acceptable only if narrowed to list/search/get semantics. Prefer `pipedrive.deals.list` and `pipedrive.deals.search` with filters for pipeline, stage, owner, status, and update window. |
| Pagination | [Pagination](https://pipedrive.readme.io/docs/core-api-concepts-pagination), [Deals pagination params](https://developers.pipedrive.com/docs/api/v1/Deals) | v2 list endpoints use cursor pagination; maximum page size is 500 on documented list/search endpoints. Connector output should preserve `next_cursor` and stop at explicit run-plan page/record caps. |
| Rate limits/errors | [Rate limiting](https://pipedrive.readme.io/docs/core-api-concepts-rate-limiting), [Responses](https://pipedrive.readme.io/docs/core-api-concepts-responses) | Respect `429`; continued abuse can become `403`. Error shape is `{ success: false, error, error_info, data, additional_data, code? }`. Include endpoint cost where the reference lists it. |

## StackOS Implications

### Provider Auth Type

- HubSpot: `auth_type: oauth`; interactive start, fixed callback, exchange,
  refresh, returned-scope enforcement, and account metadata are implemented by
  the shared core. The public setup method is the provider-specific
  authorization-code profile documented in [`hubspot.md`](hubspot.md).
- Salesforce: `auth_type: oauth`; interactive and manual-compatible methods are
  implemented. The core owns refresh, validates login-host variants, and stores
  trusted instance URL metadata.
- Pipedrive: `auth_type: oauth-or-api-token`; interactive OAuth, manual OAuth
  token, and API token are separate built-in methods. The core owns OAuth
  renewal and trusted `api_domain` handling.

### Safe Setup Fields

Use safe labels and refs only: `portal_ref`, `org_ref`, `company_ref`, `account_ref`, `instance_ref`, `pipeline_ref`, `stage_ref`, `owner_ref`, `field_mapping_ref`, `external_id_policy_ref`, and `record_refs`. Avoid requiring raw provider IDs in reusable workflow templates. Concrete run plans may carry safe refs that the daemon resolves through resources, prior action outputs, or operator-approved mappings.

### Provider-Specific Action Refs

Current first-party CRM refs should stay provider-native:

- `hubspot.crm.companies.batch_upsert` and `hubspot.crm.contacts.batch_upsert` require `id_property` plus explicit input rows.
- `hubspot.crm.notes.create` and `hubspot.crm.tasks.create` should resolve associations from safe `record_refs` daemon-side.
- `hubspot.crm.deals.search` and `hubspot.crm.deals.list` should include bounded filters, selected properties, associations, limit, and cursor/after support before execution.
- `salesforce.account.upsert_by_external_id`, `salesforce.lead.upsert_by_external_id`, and `salesforce.contact.upsert_by_external_id` require an approved external-ID policy and explicit `update_only` intent.
- `salesforce.task.create` is acceptable only if executable schemas map to Salesforce Task fields through safe refs.
- `salesforce.opportunities.query` must use allowlisted SOQL templates; no free-form agent SOQL.
- `pipedrive.deals.list` and `pipedrive.deals.search` need `pipeline_ref`, `stage_ref`, `owner_ref`, `status`, `updated_since`, `limit`, `cursor`, and optional `include_fields`.
- Pipedrive CRM writes should be added only as provider-specific organization/person/note/activity contracts; do not invent native upsert where the provider does not document one.

### Input Shape Principles

- Inputs must be provider-specific at the action boundary. A generic `properties: {}` escape hatch is acceptable only when paired with an allowlisted `field_mapping_ref`, required fields, and validation against provider metadata.
- Every write input needs `provenance`, `idempotency_key` or `request_ref`, `dry_run`/preview support where connector supports it, and approval linkage for external mutation.
- Upsert inputs must state the match key, match source, duplicate policy, and whether create is allowed. Salesforce also needs `update_only`; HubSpot needs `id_property`; Pipedrive needs a search/list preflight policy because native upsert is not documented as a single endpoint.
- Association inputs should use `record_refs` and `association_refs`/`association_type_refs`; the daemon resolves provider IDs and association type IDs.
- Read inputs must include bounded filters, selected fields/properties, page size, max pages/records, sort, and an explicit stale-cache policy.

### Output Shape Principles

Normalize provider responses into safe JSON while preserving audit-critical provider details:

- `provider`, `action`, `operation`, `status`, `external_record_ref`, `object_type`, `created`, `updated`, `archived/deleted` when available.
- `safe_record_refs`, `resolved_associations`, `properties_returned`, and `field_errors` without secrets.
- `pagination.next_cursor`/`next_records_url_ref`, `has_more`, `records_read`, and connector-enforced caps.
- `rate_limit` and `retry` diagnostics from headers/body where available.
- `provider_request_id`, `correlation_id`, or equivalent trace fields; never raw bearer tokens, API keys, refresh tokens, or full credential refs.

### Resources

The current resources are directionally right, but executable connectors need mapping metadata:

- `account`, `company`, `contact`, `lead`: include `provider_refs`, `field_mapping_ref`, `source_system`, `last_synced_at`, and confidence/provenance.
- `opportunity` and `deal`: keep both; Salesforce should primarily populate `opportunity`, Pipedrive and HubSpot can populate `deal`, and `pipeline-snapshot` should aggregate cross-provider rollups.
- `task` and `touchpoint`: distinguish requested follow-up tasks from completed activities/events. For Pipedrive, `activity` may be a better provider output shape than `task`.
- Add a provider mapping resource or artifact for CRM field/property mappings, association type mappings, and external ID policies before any write connector is executable.

### Approval And Risk

- CRM writes remain `risk_level: write` and require human review of fields, record matching, associations, and side effects.
- Upserts are higher risk than creates/updates because a bad match can overwrite existing CRM records; require explicit duplicate policy and preview output.
- Pipeline reads are `read`, but high-volume reads can hit cost/limit budgets; keep `cost_review` or a `rate_limit_budget_review` gate for multi-page fetches.
- Notes/tasks/activities can trigger user-facing notifications, assignments, workflows, or timeline changes in customer CRMs; templates should state these side effects.

### Credential Boundary

Agents receive provider keys, safe auth method refs, granted scope names, and safe diagnostics only. The daemon resolves OAuth tokens/API keys, provider base URLs, instance domains, refresh token flow, and provider object IDs. Action-call audit may record endpoint family, request body shape hash, response status, provider request IDs, and sanitized errors, but never secrets or raw authorization headers.

## Remaining Gaps

- Add provider metadata discovery for HubSpot properties/association types, Salesforce sObject describes/external ID fields, and Pipedrive custom field keys.
- Add field-mapping resources and explicit external ID policies.
- Add pagination/rate-limit harness tests for HubSpot `after`, Salesforce `nextRecordsUrl`, and Pipedrive `cursor`.
- Add write-preview/dry-run flow for upserts and association mutations.
- Add idempotency/audit strategy for retries, partial failures, HubSpot batch multi-status, Salesforce duplicate/external-ID conflicts, and Pipedrive account-limit errors.
- Keep deferred/project-local actions explicit with `execution_mode` and avoid
  empty connector configs.

## Recommended Manifest And Template Corrections

1. Keep Pipedrive OAuth-first and preserve API token as the explicit separate
   built-in auth method; do not merge their ownership or rotation semantics.
2. Keep provider-native deal/opportunity list/search/query action names; do not reintroduce generic pipeline fetch abstractions.
3. Keep HubSpot batch upsert contracts aligned with `id_property` and explicit input rows.
4. Keep Salesforce note creation out of workflow action refs until the object/linking model is documented.
5. Add Pipedrive to `gtm.crm-hygiene-pass` only after adding provider-specific organization/person/note/activity create/update contracts; current hygiene template lists HubSpot and Salesforce only.
6. Continue tightening input schemas: remove broad `additionalProperties: true` where provider docs define fields, and move arbitrary provider fields behind `properties` plus `field_mapping_ref`.
7. Keep schemas provider-native and add mocked execution/redaction tests for
   every new connector operation before expanding the write surface.
