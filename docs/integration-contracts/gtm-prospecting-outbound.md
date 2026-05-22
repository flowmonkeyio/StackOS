# GTM Prospecting, Enrichment, and Outbound Contract Audit

Status: official-doc-backed contract review, not execution signoff.

Scope reviewed: Apollo, Clay, Clearbit/Clearbit by HubSpot, Outreach, Salesloft, Gmail/Google Workspace, and Microsoft Graph for Microsoft 365 mail/calendar. Current scaffold actions are contract-only; no provider connector exists in `content_stack/actions` for these providers.

## Provider Docs Ledger

Official sources used:

- Apollo developer hub: https://docs.apollo.io/
- Apollo People API Search: https://docs.apollo.io/reference/people-api-search
- Apollo Bulk Organization Enrichment: https://docs.apollo.io/reference/bulk-organization-enrichment
- Apollo API pricing and credit-consuming endpoints: https://docs.apollo.io/docs/api-pricing
- Clay "Does Clay have an API?": https://university.clay.com/docs/using-clay-as-an-api
- Clearbit by HubSpot credits and usage: https://help.clearbit.com/hc/en-us/articles/22910775621399-Clearbit-by-HubSpot-Understanding-Credits-and-Usage
- Clearbit API request accounting: https://help.clearbit.com/hc/en-us/articles/115015390748-What-Counts-as-an-API-Request
- HubSpot API usage guidelines and limits: https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines
- Outreach REST API overview: https://developers.outreach.io/api
- Outreach OAuth: https://developers.outreach.io/api/oauth
- Outreach making requests, pagination, and JSON:API behavior: https://developers.outreach.io/api/making-requests
- Outreach common patterns: https://developers.outreach.io/api/common-patterns/
- Outreach sequence state API reference: https://developers.outreach.io/api/reference/tag/Sequence-State/
- Salesloft API basics: https://developers.salesloft.com/docs/platform/api-basics/
- Salesloft OAuth authorization code: https://developers.salesloft.com/docs/platform/api-basics/oauth-authentication/
- Salesloft API key authentication: https://developers.salesloft.com/docs/platform/api-basics/api-key-authentication/
- Salesloft filtering, paging, sorting: https://developers.salesloft.com/docs/platform/api-basics/filtering-paging-sorting/
- Salesloft rate limits: https://developers.salesloft.com/docs/platform/api-basics/rate-limits/
- Salesloft create cadence membership: https://developers.salesloft.com/docs/api/cadence-memberships-create/
- Gmail send message: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send
- Gmail usage limits: https://developers.google.com/workspace/gmail/api/reference/quota
- Google Calendar events.insert: https://developers.google.com/workspace/calendar/api/v3/reference/events/insert
- Google Calendar usage limits: https://developers.google.com/workspace/calendar/api/guides/quota
- Google Calendar error handling: https://developers.google.com/workspace/calendar/api/guides/errors
- Microsoft Graph sendMail: https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0
- Microsoft Graph create event: https://learn.microsoft.com/en-us/graph/api/calendar-post-events?view=graph-rest-1.0
- Microsoft Graph API usage basics: https://learn.microsoft.com/en-us/graph/use-the-api
- Microsoft Graph throttling guidance: https://learn.microsoft.com/en-us/graph/throttling
- Microsoft Graph service throttling limits: https://learn.microsoft.com/en-us/graph/throttling-limits

## Critical Findings

- Apollo's official prospecting operation is People API Search for net-new people; it does not return email addresses or phone numbers, requires a master API key, and has a 50,000 displayed-record cap. Current action contracts therefore use `apollo.people.search`, `apollo.people.enrich`, `apollo.people.bulk_enrich`, `apollo.organization.enrich`, and `apollo.organization.bulk_enrich`.
- `clay.enrichment.run` should not look like a normal synchronous API action. Clay's public docs say Clay does not have a traditional API for most customers. The generally documented integration path is table webhooks in, Clay workflow processing, then HTTP actions out. Enterprise-only People and Company API lookups need a contracted endpoint and schema before StackOS can model them as executable.
- Clearbit standalone API docs are now partly support-oriented, while current Clearbit by HubSpot credit docs and HubSpot limits govern many customers. Current contracts name the active surface as `clearbit-by-hubspot.enrichment.run`.
- Outreach exposes JSON:API resources and relationships; adding a prospect to a sequence is modeled as creating a `sequenceState` resource, so the first-party contract is `outreach.sequence_state.create`.
- Salesloft uses cadences and the relevant documented write is `POST /v2/cadence_memberships`, requiring `person_id` and `cadence_id` visible to the authenticated user. Current contracts use `salesloft.cadence_membership.create`.
- `google-workspace.touchpoint.record` and `microsoft-365.touchpoint.record` blur internal audit recording with external provider writes. Gmail/Graph mail actions send email; Calendar/Graph calendar actions create events. StackOS touchpoint resources should be internal records created after a provider send/create result, not a provider action named "record" unless the connector only writes internal StackOS state.
- All current schemas use `additionalProperties: true` inside provider-specific arrays. That is acceptable for a placeholder contract but not executable. Every executable provider action needs a strict provider-normalized schema, idempotency/provenance fields, and output status classification.

## Auth and Safe Setup Fields

Apollo:

- Auth: API key/Bearer token. Some operations require a master API key, including People API Search.
- Safe setup fields: `workspace_ref`, `api_plan_ref`, `master_key_allowed` boolean, `credit_budget_ref`, `default_page_size`.
- Do not store Apollo keys or raw usage responses in templates. Daemon-held credentials only.

Clay:

- Auth/setup: documented broad path is webhook endpoint per table plus optional outbound HTTP action from Clay. Enterprise People/Company API requires a customer-specific contract.
- Safe setup fields: `workspace_ref`, `table_ref`, `webhook_endpoint_ref`, `return_destination_ref`, `auto_delete_enabled`, `enterprise_api_enabled`.
- Do not model Clay as a generic API-key enrichment service unless the project has enterprise endpoint docs and auth details.

Clearbit/Clearbit by HubSpot:

- Auth/setup: legacy Clearbit enrichment uses Clearbit platform credentials; many current customers are Clearbit by HubSpot/Breeze Intelligence with credits and HubSpot account linkage. HubSpot API calls have HubSpot rate and error behavior.
- Safe setup fields: `hubspot_portal_ref`, `clearbit_workspace_ref`, `credit_budget_ref`, `field_mapping_ref`, `processed_at_field_enabled`.
- Provider naming must disclose which contract is active: legacy Clearbit, Clearbit by HubSpot, or HubSpot-native enrichment.

Outreach:

- Auth: OAuth 2.0 authorization code for normal REST API access. Outreach production OAuth credentials require app publishing/review; development credentials are limited and should not be used for end users.
- Safe setup fields: `instance_ref`, `oauth_app_ref`, `scopes_ref`, `sequence_ref`, `mailbox_policy_ref`, `throttle_policy_ref`.
- Tokens and refresh tokens remain daemon-held. Templates should only carry safe refs.

Salesloft:

- Auth: OAuth authorization code is preferred/required for partners. API keys are customer-only and are not approved for partner apps. Salesloft also documents client credentials for private admin-enabled app use.
- Safe setup fields: `team_ref`, `oauth_app_ref`, `scopes_ref`, `cadence_ref`, `user_ref`, `send_policy_ref`.
- Store only safe refs. Note refresh-token rotation: a refresh response revokes old refresh tokens, so connector credential storage must be atomic.

Google Workspace:

- Auth: OAuth with least-privilege scopes. Gmail send can use `gmail.send`; Calendar event insert can use `calendar.events` or narrower eligible scopes depending on the app model.
- Safe setup fields: `workspace_ref`, `user_ref`, `calendar_ref`, `send_as_ref`, `oauth_client_ref`, `domain_wide_delegation_ref`, `quota_project_ref`.
- Domain-wide delegation is a separate admin-approved setup and must be explicit.

Microsoft 365:

- Auth: Microsoft Graph OAuth with delegated or application permissions. `Mail.Send` is least-privileged for `sendMail`; `Calendars.ReadWrite` is needed for user calendar event creation.
- Safe setup fields: `tenant_ref`, `user_ref`, `mailbox_ref`, `calendar_ref`, `app_registration_ref`, `permission_mode`, `national_cloud_ref`.
- Application permissions can send/create on behalf of users at much higher blast radius and should require explicit approval gates.

## Operation Contract Review

Lead/prospect search:

- Apollo supports people search and organization search. People Search is prospecting, not enrichment, and does not return email/phone. It should write a `lead` candidate resource with `source=apollo.people.search`, search filters, page metadata, and missing enrichment fields.
- Outreach and Salesloft are not primary prospecting data providers in this scaffold. They can fetch existing prospects/people, sequence/cadence membership, mailings, calls, tasks, and events, but using them as lead-source providers needs separate read actions.
- Clay can ingest leads through table webhooks and return workflow results. It is not a normal synchronous lead search API unless the Enterprise People/Company API is contracted.
- Clearbit/Clearbit by HubSpot is enrichment-oriented, not outbound sequence management.

Enrichment:

- Apollo enrichment should be split by object and cardinality: person, bulk person, organization, bulk organization. Bulk organization enrichment is explicitly limited to up to 10 companies per request and consumes credits.
- Clay enrichment should be modeled as `clay.table.webhook.submit` plus an asynchronous callback/import resource, or as `clay.enterprise.person.lookup`/`clay.enterprise.company.lookup` only when enterprise API docs are attached to the project.
- Clearbit by HubSpot enrichment must account for credits. A credit is counted when Clearbit appends data to a record, with monthly refresh/no rollover in current Clearbit by HubSpot docs.

Sequence/cadence add:

- Outreach should use provider-specific JSON:API payloads and relationship identifiers. A generic `prospects` array is not enough. The connector must know whether it creates prospects first, updates prospects, creates sequence states, or calls an Outreach sequence-related endpoint.
- Salesloft should create cadence memberships, not "sequences". Official docs require a visible `person_id` and `cadence_id`, with optional `user_id` behavior bounded by cadence ownership/team permissions.
- Both providers need an approval gate before mutation: audience, message/copy refs, suppression/exclusion checks, owner/user, mailbox/send policy, and rollback/removal plan.

Touchpoint/email/calendar:

- Gmail `users.messages.send` sends mail and returns a Gmail `Message`; it has real recipient and quota implications. It is not just a record action.
- Google Calendar `events.insert` creates an event and can send guest notifications via `sendUpdates`; using `none` can have adverse sync effects for external calendars.
- Microsoft Graph `sendMail` returns `202 Accepted` and no body; this means accepted for processing, not delivered. It saves to Sent Items by default unless configured otherwise.
- Microsoft Graph event creation returns `201 Created` plus an event object and supports transaction IDs for idempotency.
- StackOS should separately persist internal `touchpoint` resources containing provider result refs, accepted/sent/created status, participants as safe refs, body/content refs, and audit provenance.

## Pagination, Rate Limits, and Errors

Apollo:

- People Search uses `page` and `per_page`; display cap is 50,000 records, 100 records per page, up to 500 pages. Narrow filters are required for large prospecting.
- Official endpoint pages list 401, 403, 422, and 429 outcomes. People Search master-key failures are 403. Enrichment and search can consume credits depending on endpoint.
- Connector must preserve page inputs/outputs and stop before caps; retries should honor 429 behavior and credit budgets.

Clay:

- Webhook/table workflows are asynchronous. Expect delayed results, out-of-band callbacks, and possible row lifecycle behavior such as auto-delete.
- Public docs do not provide a universal pagination/rate-limit/error contract for a traditional API. Execution must remain blocked until project-specific Clay endpoint docs or webhook delivery semantics are attached.

Clearbit/Clearbit by HubSpot:

- Legacy Clearbit support docs count successful/enrichment-style requests by status; Clearbit by HubSpot uses credits that refresh monthly and do not roll over.
- HubSpot API limits include rate-limit headers, 429 JSON errors with `policyName`, and special behavior for OAuth headers and search APIs.
- Connector must distinguish API rate exhaustion from enrichment credit exhaustion.

Outreach:

- Outreach implements JSON:API and requires `Content-Type: application/vnd.api+json`.
- Collections return `data`, `links`, and sometimes `meta`. Cursor pagination is recommended with `page[size]` and `count=false`; offset pagination exists and `page[limit]` must not exceed 1000.
- Errors are JSON:API-style top-level `errors`. Connector should normalize these without dropping `id`, `title`, `details`, and HTTP status.

Salesloft:

- Paging uses `page` and `per_page`; default is generally 25, range is generally 1 to 100, with each endpoint documenting exact support.
- Rate limits are team-level, cost-based, currently 600 cost per minute, and deep page indexes above 100 cost more.
- Invalid sorts return 422. 429/rate-limit behavior must be normalized with team-level remaining/cost context when available.

Google Workspace:

- Gmail quotas are quota-unit based. Current docs list 1,200,000 units per minute per project and 6,000 units per minute per user per project; `messages.send` costs 100 units and messages have a 500-recipient limit.
- Calendar current docs list 10,000 requests per minute per project and 600 per minute per user per project, plus operational limits for burst writes to a single calendar.
- Calendar rate errors may be 403 or 429 and should use truncated exponential backoff. Gmail also recommends truncated exponential backoff for time-based quota errors.

Microsoft Graph:

- Graph responses include `request-id`; throttling returns 429 with `Retry-After` when available. If no `Retry-After`, use exponential backoff.
- Graph batch requests can partially throttle individual operations even when the batch returns 200.
- Graph mail send returns 202 and can still fail delivery later due to Exchange Online limitations and throttling; provider result state must not be recorded as delivered.

## Provider-Specific Action Refs

Recommended refs before execution:

- `apollo.people.search` - read/cost depending on plan, returns candidates without email/phone.
- `apollo.people.enrich` and `apollo.people.bulk_enrich` - cost, returns person enrichment.
- `apollo.organization.enrich` and `apollo.organization.bulk_enrich` - cost, bulk capped per Apollo docs.
- `clay.table.webhook.submit` - write/cost/asynchronous, sends rows to a specific table webhook.
- `clay.workflow.result.ingest` - internal/callback import, records completed Clay results.
- `clay.enterprise.person.lookup` and `clay.enterprise.company.lookup` - only if enterprise API docs and auth are attached.
- `clearbit-by-hubspot.enrichment.run` or `hubspot.breeze-intelligence.enrichment.run` - choose one based on the contracted surface; do not use generic `clearbit-compatible`.
- `outreach.prospect.upsert` - if creating/updating Outreach prospects is in scope.
- `outreach.sequence_state.create` - creates an Outreach `sequenceState` resource for an approved prospect, sequence, and mailbox.
- `salesloft.person.upsert` - if creating/finding people is in scope.
- `salesloft.cadence_membership.create` - creates a Salesloft cadence membership.
- `google-workspace.gmail.message.send` - external email send.
- `google-workspace.calendar.event.create` - external calendar event create.
- `microsoft-365.graph.mail.send` - external email send via Graph.
- `microsoft-365.graph.calendar.event.create` - external calendar event create via Graph.
- `stackos.touchpoint.record` - internal resource creation after provider action, not a Gmail/Graph provider action.

## Input and Output Principles

- Inputs should use safe refs in reusable templates: `lead_ref`, `contact_ref`, `company_ref`, `account_ref`, `sequence_ref`, `cadence_ref`, `mailbox_ref`, `calendar_ref`, `body_ref`, `copy_ref`, `suppression_list_ref`, and `approval_ref`.
- Executable run plans may resolve provider IDs inside the daemon, but templates should not require raw provider IDs.
- Provider actions must be strict about required fields: Apollo filters and pagination; Salesloft `person_ref` and `cadence_ref`; Gmail/Graph body refs and recipient refs; Calendar/Graph start/end/time zone refs.
- Outputs should normalize to: `provider`, `action_ref`, `external_object_ref`, `status`, `status_detail`, `rate_limit`, `cost_or_credit_usage`, `idempotency_key`, `provenance`, and sanitized raw-response refs if stored as artifacts.
- Do not store raw email bodies or PII-heavy provider payloads directly in broad resources when a `body_ref` or artifact ref is enough.

## Resource Mapping

- `lead`: prospect candidate, source provider, source query ref, qualification state, safe contact/company refs, suppression status.
- `enrichment-record`: provider, fields requested, fields returned, confidence/provenance, credit/cost, freshness, source record refs.
- `sequence`: approved sequence/cadence plan, provider, audience refs, copy refs, owner/user refs, approval state, send policy refs.
- `touchpoint`: internal record for planned, accepted, sent, delivered, bounced, replied, meeting-created, or meeting-held events. Store provider result refs and timestamps, not secrets.
- `artifact`: sanitized provider payload snapshots, large result pages, exported CSVs, and webhook callback bodies when needed for audit.
- `action-call audit`: every external mutation must record credential provider key, safe account/tenant/workspace refs, grants, request hash, response status, and normalized error.

## Approval, Risk, and Credential Boundary

- Prospect search/enrichment can spend credits. Require cost/credit budget approval before Apollo enrichment, Clay workflow submission, and Clearbit/Clearbit by HubSpot enrichment.
- Sequence/cadence adds can trigger outbound communication or scheduled tasks. Require approval of audience, copy refs, suppression checks, owner, send window, and provider-specific throttle policy.
- Gmail and Microsoft Graph mail send are high-risk writes. Require explicit send approval and narrow scopes. A connector must support dry-run/render-only mode before send.
- Calendar event creation can notify guests and alter calendars. Require review of attendees, time zone, `sendUpdates`/notification behavior, and idempotency.
- Credentials remain server-side. Agents may receive provider key, account/workspace/tenant refs, scope status, and safe diagnostics only.
- Application-level Microsoft Graph permissions, Google domain-wide delegation, Outreach production app credentials, and Salesloft customer API keys are elevated setups and should be visible as setup status, not exposed secrets.

## Executable Gaps Before Signoff

- Add real connectors under `content_stack/actions` for each executable provider operation, or keep every action `execution_mode: contract-only`.
- Replace generic schemas with provider-specific input/output schemas and validation tests.
- Add provider grant tests for read, cost, write, send, and calendar-create risk levels.
- Add auth tests for no-secret exposure, expired token refresh, refresh-token rotation, revoked credential behavior, and missing scope diagnostics.
- Add rate-limit/error normalization tests for 401/403/422/429 and provider-specific throttling headers/body shapes.
- Add pagination tests for Apollo page/per_page caps, Outreach cursor/offset links, and Salesloft page/per_page/cost behavior.
- Add idempotency and duplicate-protection behavior for sequence/cadence adds, mail sends, and calendar event creation.
- Add audit records for cost/credit usage, approval refs, request hashes, provider result refs, and sanitized response artifacts.
- Attach official-doc comments or doc refs near connector implementations once they exist; do not paste large doc ledgers into runtime code.

## Recommended Manifest and Template Corrections

- Keep Apollo endpoint-specific prospecting and enrichment actions; do not collapse them into one umbrella action.
- Replace `clay.enrichment.run` with `clay.table.webhook.submit` plus `clay.workflow.result.ingest`; add Enterprise API lookup actions only for projects with contracted docs.
- Keep the named Clearbit by HubSpot provider contract unless a project-specific HubSpot-native enrichment surface is documented.
- Keep Salesloft-specific template language around cadences and cadence memberships.
- Split `google-workspace.touchpoint.record` into `google-workspace.gmail.message.send` and `google-workspace.calendar.event.create`; keep `touchpoint` persistence as an internal StackOS resource operation.
- Split `microsoft-365.touchpoint.record` into `microsoft-365.graph.mail.send` and `microsoft-365.graph.calendar.event.create`; represent Graph `202 Accepted` as accepted/submitted, not delivered.
- Use `outreach.sequence_state.create` for Outreach sequence enrollment; it mirrors the verified JSON:API `sequenceStates` resource instead of a generic sequence-add abstraction.
- Add setup fields for budgets and policies: `credit_budget_ref`, `send_policy_ref`, `suppression_list_ref`, `mailbox_policy_ref`, `throttle_policy_ref`, and `approval_policy_ref`.
- Update outbound templates so provider actions are optional execution steps behind review gates, while agent-authored sequence strategy remains in artifacts/resources and not inside tools.
