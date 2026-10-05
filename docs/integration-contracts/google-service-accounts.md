# Google Service-Account Contract

Reviewed 2026-09-25. Seven existing providers expose 29 actions. Five add an
explicit `service-account` Account method; existing OAuth and media API-key
methods, action refs, risk levels, grants, outputs and audit owners remain.
There is no new operation, credential store or connector-local token lifecycle.

## Provider Matrix And Official Sources

| Provider / actions | Authentication and resource permission | Official evidence |
| --- | --- | --- |
| `google-search-console` / 4 reads | JSON service account with `webmasters.readonly`; add its email to the Search Console property. Restricted property access supports performance reads; Indexing API ownership is a separate contract. | [Authorization](https://developers.google.com/webmaster-tools/v1/how-tos/authorizing), [service-account quickstart](https://developers.google.com/webmaster-tools/v1/quickstart/quickstart-python), [property permissions](https://support.google.com/webmasters/answer/7687615) |
| `google-analytics` / 4 reads | JSON service account with `analytics.readonly`; enable Admin and Data APIs and grant account/property access, such as Viewer for reports. | [Data quickstart](https://developers.google.com/analytics/devguides/reporting/data/v1/quickstart), [Admin quickstart](https://developers.google.com/analytics/devguides/config/admin/v1/quickstart) |
| `google-tag-manager` / 6 reads | JSON service account with `tagmanager.readonly`; grant Tag Manager account/container access. | [Authorization](https://developers.google.com/tag-platform/tag-manager/api/v2/authorization) |
| `google-ads` / 10 reads/writes | JSON service account with `adwords`; add its email in Ads access settings. No delegated subject. Developer token remains required; existing `manager_account_ref` supplies `login-customer-id`. | [Service accounts](https://developers.google.com/google-ads/api/docs/oauth/service-accounts), [REST authentication](https://developers.google.com/google-ads/api/rest/auth) |
| `google-workspace` / Gmail send, Calendar create | Without a subject, `calendar.events` and an explicit shared calendar without attendees. With an explicitly authorized Workspace `delegated_subject`, existing Gmail/Calendar scopes apply. Gmail, resolved `primary` calendar and attendees require delegation. Personal Gmail needs OAuth. | [Service-account grants and domain-wide delegation](https://developers.google.com/identity/protocols/oauth2/service-account), [Workspace auth](https://developers.google.com/workspace/guides/auth-overview), [Calendar insert restrictions](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert) |
| `google-gemini-image` / 2 media actions | Existing Gemini Developer API key transport, including service-account-bound authorization keys. No JSON key method in StackOS. | [Gemini API key guide](https://ai.google.dev/gemini-api/docs/generate-content/api-key) |
| `google-veo` / 1 video action | Same Developer API key contract; no Vertex AI switch. JSON service-account support for StackOS's reviewed media endpoints is not established. | [Gemini API key guide](https://ai.google.dev/gemini-api/docs/generate-content/api-key), [Veo generation](https://ai.google.dev/gemini-api/docs/video-generation) |

Google PAA is a Firecrawl helper, and Gemini CLI is an agent-host integration;
neither adds a Google provider credential contract. Drive, Docs, Sheets and
YouTube have no current Google action connector in this inventory.

## Shared Acquisition And Evidence

The method declares `auth_type: oauth`, `interactive: false`, JSON payload,
secret `service_account_json`, and `oauth_response/local_required` permission
verification. Workspace's optional subject is safe Account configuration. Ads
also encrypts `developer_token`. Setup uses the existing generic Account panel.
Create a separate named Account to change methods; attachments remain explicit.

The shared daemon validates the bounded service-account JSON and RSA key, signs
RS256 with fixed scopes and audience, and exchanges at Google's fixed token
endpoint without redirects. It does not use ADC, credential-source URLs or an
ambient identity. Token acquisition, renewal, expiry, concurrent locking,
identity invalidation, local revocation, redaction and usage audit remain under
the [canonical auth owner](../auth-providers.md).

Scope provenance distinguishes returned scopes from accepted signed-request
scopes. [OAuth 2.0 section 5.1](https://www.rfc-editor.org/rfc/rfc6749#section-5.1)
permits omission when the granted scope equals the request. Only a successful
reviewed service-account exchange uses this rule. Present empty, malformed or
insufficient scopes never fall back to the requested set. Existing manual-token
behavior is unchanged. Google resource ACLs remain independent.

Search Console, GA4 and GTM retain inventory probes; empty inventories do not
prove access to any selected resource. Ads and Workspace report token acquisition
only and resource access unverified. An exact action can still fail with resource
permission errors after token success. Delegation never comes from a Gmail
`user_ref`; the saved Account must specify it. Calendar restrictions are checked
after resolving the calendar ref and before action HTTP.

## Proof And Remaining Boundaries

Synthetic-key tests cover the shared key/token lifecycle, actual manifest method
loading, Workspace pre-request denials and existing executor transport. Generic
UI tests cover local JSON entry, masked storage, locked methods, subject clearing
and truthful success guidance. The browser fixture uses an isolated local daemon
for save/edit/readback and intercepts only the local Account Test endpoint; it
proves presentation, not Google authorization. It disables traces/video for the
ephemeral key. Native direct/granted action proof and required source signoff
complete integration evidence.

No live Google credential, resource permission, production mailbox send, event
creation or Ads mutation is certified by these fixtures. Existing pagination,
quota, budget and provider-error contracts remain in
[connector quality](connector-quality.md), [media buying](media-buying.md) and
[GTM outbound](gtm-prospecting-outbound.md). Service-account setup does not relax
write approval, run grants, project attachment or exact-Account selection.
