# Google Indexing API

Reviewed 2026-09-28. The SEO plugin exposes the separate `google-indexing`
provider for explicit URL notifications and their history. It shares the daemon
auth, action grants, response files, and action-call audit owners.

## Setup and eligibility

Create a Google Cloud project, enable the Indexing API, and create a service
account. Verify the site and add the service-account email as a delegated site
owner in Search Console. FullUser access alone does not meet Google's documented
owner requirement. [Google prerequisites](https://developers.google.com/search/apis/indexing-api/v3/prereqs).

StackOS accepts only the `service-account` Account method with secret
`service_account_json`. The shared signer requests exactly
`https://www.googleapis.com/auth/indexing`. Interactive OAuth, imported bearer
tokens and delegated Workspace subjects are unavailable for this provider.
Project attachment and recorded scope evidence are required before execution.
Keys, tokens, renewal and redaction remain under the
[shared service-account contract](google-service-accounts.md).

`account.test` acquires a token without making an Indexing request. Success
reports `verification: token_acquisition_only`; resource, API and site access
remain `unverified`. It publishes no sample URL and invents no metadata probe.

Google supports pages containing `JobPosting`, or livestream `BroadcastEvent`
embedded in `VideoObject`. Ordinary articles are unsupported. Before a removal
notification, the page must return 404/410 or contain `noindex`. Agents and
operators establish eligibility and removal readiness; the connector accepts
Google's URL/type fields and does not crawl pages or infer eligibility.
[Supported content and removal conditions](https://developers.google.com/search/apis/indexing-api/v3/using-api).

## Executable actions

| Action | Risk | Input and fixed request | Result |
| --- | --- | --- | --- |
| `seo.indexing.url-notifications.publish` | destructive for both types | `{url, type}`, where type is `URL_UPDATED` or `URL_DELETED`; POST JSON to `https://indexing.googleapis.com/v3/urlNotifications:publish` | `notification_received: true`, URL/type and returned notification metadata. This confirms receipt, never indexing or removal. |
| `seo.indexing.url-notifications.metadata.get` | read | `{url}`; GET `https://indexing.googleapis.com/v3/urlNotifications/metadata` with an encoded `url` query and empty body | Returned notification history, including empty history when Google returns an empty object. It does not report current indexing status. |
| `seo.indexing.batch.publish` | destructive for both types | `{urls, type}`; 1–100 URLs with one `URL_UPDATED` or `URL_DELETED` type | One notification receipt or safe error/unknown outcome per URL. |
| `seo.indexing.batch.metadata.get` | read | `{urls}`; 1–100 URLs | One notification-history result or safe error/unknown outcome per URL. |

All actions validate absolute HTTP(S) URLs and reject unsupported fields
before HTTP. They use one selected Account and the fixed Indexing scope. Publish
uses the existing direct confirmation or explicit active step grant; the risk
never changes with the input type. Outputs retain `indexing_status: unverified`.
There is no resource import or workflow-specific policy hidden in the connector.
[Publish reference](https://developers.google.com/search/apis/indexing-api/v3/reference/indexing/rest/v3/urlNotifications/publish),
[metadata reference](https://developers.google.com/search/apis/indexing-api/v3/reference/indexing/rest/v3/urlNotifications/getMetadata).

## HTTP batches

Each batch action uses one Account and a single fixed operation for all URLs.
Publish also uses one notification type. Every URL and generated request size is
validated before token acquisition or provider HTTP. The connector encodes all
methods, paths, query/body fields, boundaries and index-based Content-IDs; caller
endpoints, headers, methods, raw MIME and nested batches are unsupported.

The fixed outer endpoint is `https://indexing.googleapis.com/batch`. Inner
requests use the single-action paths above. Each complete encoded inner HTTP
request, including request line, headers and body, is bounded to 1,000,000 bytes.
The 100-item limit applies to both actions. StackOS sends one envelope without
automatic chunking. Google counts quota per inner call and can process items
in any order; a batch has no transaction or all-or-nothing guarantee.
[Google batch limits and endpoint](https://developers.google.com/search/apis/indexing-api/v3/using-api#batching).

Results restore input order and retain every index, operation, known HTTP
status, `success`/`error`/`unknown` outcome, result or bounded safe Google error,
and safe request IDs. A complete summary reports total/succeeded/failed/unknown
and partial success. Known receipts survive unrelated extra parts or a missing
closing boundary; a protocol diagnostic makes those envelopes fail even if all
requested items have validated receipts. Duplicate, missing or malformed
requested entries remain unknown. Inner and outer quota errors retain available
finite nonnegative numeric `Retry-After` advice.

Parsing and provider-specific result normalization happen before integration
audit. The small shared `google_batch.py` module owns MIME encoding, correlation
and safe Google diagnostics; provider modules retain endpoints, limits, input
mapping and result interpretation. Raw MIME never enters action output or audit.

Any failed/unknown item or protocol defect raises a structured action error,
including when Google returns outer HTTP 200. The full summary survives the
immediate `provider_error` and durable failed action `response_json`; failed
actions do not create response files. Successful actions use the normal full
response file. Outer rejection yields failed items, while transport/server
failure yields unknown items. No batch envelope retries automatically, including
metadata batches. Repeating a whole batch can repeat successful notifications;
inspect receipts and reconcile uncertain URLs before explicitly resubmitting.
Existing replay deduplicates successful calls only.

## Failures, quota and proof

Publish sends one attempt with no automatic retry. Transport errors, server
errors and malformed success responses report `outcome_unknown: true`,
`retry_safe: false` and `reconcile_before_retry: true`. Typed errors retain safe
Google status/details and advisory `Retry-After` where provided, in the returned
`provider_error` and failed-action audit. Check notification history and the
underlying page before explicitly deciding to resend. Existing successful-intent
replay is reused; failed or uncertain calls are not deduplicated. Metadata reads
use the existing bounded read retry policy. No response text is treated as a
successful receipt, and no credential values appear in output or audit.

The initial publish allowance is 200 per day for testing; Google documents 180
metadata requests per minute and 380 total requests per minute. Production quota
approval and the project's actual Google quota remain authoritative. These
actions have no monetary budget, local quota ledger, scheduler or pagination.
[Google quota and approval](https://developers.google.com/search/apis/indexing-api/v3/quota-pricing).

Synthetic-key and mocked HTTP proof covers fixed-scope acquisition and renewal,
token-only setup, scope/project/provider denials, exact requests, notification
semantics, errors without mutation retries, direct/granted MCP, successful
replay, response files and safe failed audit. Batch proof includes 100-item
complete outputs/errors, encoded byte bounds, reordered/corrupt/extra parts,
unknown outcomes and preserved receipts before audit. The shared extraction
retains Search Console regression coverage. Live Google access and indexing
outcomes remain unverified.
