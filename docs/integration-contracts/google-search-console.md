# Google Search Console

The SEO plugin supports site inventory, search analytics, sitemap inventory,
indexed-version URL inspection, and explicit sitemap submission. Provider
credentials stay in the shared daemon auth lifecycle.

## HTTP batches

| Action | Risk | Input |
| --- | --- | --- |
| `seo.search-console.batch.read` | read | `requests`: 1–1000 `{operation, input}` objects; operation is `sites.list`, `search_analytics.query`, `sitemaps.list`, or `url.inspect`; input uses the corresponding single-action schema. |
| `seo.search-console.sitemaps.submit.batch` | write | `requests`: 1–1000 `{site_url, sitemap_url}` objects; requires explicit Account write access and the full `webmasters` scope. |

Every item is validated before token acquisition or provider HTTP. A batch uses
one Account and one action grant. The connector builds all HTTP methods, paths,
headers, MIME boundaries and index-based Content-IDs. It accepts no arbitrary
action, endpoint, raw MIME, per-item Account, or nested batch. It sends one
envelope and never divides an oversized request automatically.

Both actions POST to `https://searchconsole.googleapis.com/batch`. This is the
`rootUrl` plus `batchPath` in Google's [Search Console discovery document](https://searchconsole.googleapis.com/$discovery/rest?version=v1).
Inner paths include `/webmasters/v3/...` and `/v1/urlInspection/index:inspect`.
Google counts each inner call against quota. Calls may execute in any order;
batch submission provides no transaction or all-or-nothing guarantee. Choose
separate calls when a later operation depends on an earlier result.
[Google HTTP batch contract](https://developers.google.com/webmaster-tools/v1/how-tos/batch),
[usage limits](https://developers.google.com/webmaster-tools/limits).

Output retains every input index and operation in input order, with HTTP
`status` when known, `outcome` (`success`, `error`, or `unknown`), the result or
bounded safe Google diagnostics, and safe provider request IDs when returned.
The summary includes total/succeeded/failed/unknown counts and `partial_success`.
An HTTP 200 envelope with failed or unknown items raises a structured action
error containing the complete summary in `provider_error` and in the durable
action audit. Successful action outputs use the normal response file; failed
actions remain available through the error and `actionCall.get` audit, without
a failure response file. Low-level integration audit may contain a bounded
preview of normalized data; raw MIME and credentials never enter that preview.

Validated correlated receipts survive unrelated extra parts and recoverable
envelope defects. Missing, duplicate, or malformed requested entries report
unknown outcomes. A bounded `protocol_error` diagnostic makes the batch fail
even when every requested item has a valid receipt; requested-item counts still
reflect those receipts. Extra response parts do not count as requested work.
Finite non-negative numeric `Retry-After` advice is retained on each applicable
item and its provider error. Outer 429 advice also appears in the returned
`provider_error` summary. It is advisory and never schedules another attempt.
An outer rejection marks every item failed; a transport failure or outer server
error marks every item unknown. HTTP redirects are not success. No batch
envelope is retried automatically, including read batches. Repeating a whole
batch may repeat successful parts: inspect the retained item results and
reconcile uncertain writes before choosing another request. Successful explicit
intent replay uses the existing StackOS idempotency path; failures are not
deduplicated. The connector adds no queue, scheduler, or retry policy.

Mocked proof covers all four read mappings, 1000-item submission, validation
bounds, response correlation, partial and outer failures, per-call parsing
before integration audit, no automatic retries, and direct/granted MCP output
and audit. It does not establish live provider interoperability or indexing.

## Sitemap submission

`seo.search-console.sitemaps.submit` takes `site_url` (a URL-prefix property with
a trailing slash, or `sc-domain:example.com`) and an absolute `sitemap_url`.
The wrapper encodes both path parameters and sends an empty-body PUT to Google's
fixed Webmasters endpoint. A successful empty response becomes a receipt with
those two URLs and `submitted: true`.

Submission registers or resubmits the existing sitemap URL. It does not edit
the sitemap, certify Google's processing, or guarantee crawling or indexing.
The action has `risk_level: write` and uses normal direct confirmation or an
explicit active run-step grant. [Google submission contract](https://developers.google.com/webmaster-tools/v1/sitemaps/submit).

## Access and scope evidence

Each Search Console Account has a safe `access_mode` selection:

- Omitted or `readonly`: request `webmasters.readonly`; sitemap writes are denied.
- `sitemap_write`: request `webmasters`; Google property permissions must also
  permit sitemap submission.

Existing Accounts remain read only until explicitly changed. Changing the mode
invalidates acquired service-account tokens, expiry and scope evidence. The
next daemon exchange signs the selected scope. Interactive OAuth mode changes
clear acquired authorization and require fresh Google consent; stale pending
callbacks cannot restore the previous grant. Imported-token methods still
need recorded provider scope evidence; choosing a mode does not establish it.

Google's full `webmasters` grant satisfies the existing read requirements. The
trusted provider contract applies that documented alternative in the shared
resolver without adding fabricated `webmasters.readonly` rows to stored
grants. Unknown or insufficient grants still fail. [Read scope alternatives](https://developers.google.com/webmaster-tools/v1/sitemaps/list),
[Google authorization](https://developers.google.com/webmaster-tools/v1/how-tos/authorizing),
[shared service-account contract](google-service-accounts.md).

## Failures and proof

The submission transport sends one attempt and performs no automatic retry.
Provider HTTP failures preserve redacted status/error details. A transport
failure or server error reports `outcome_unknown: true`, `retry_safe: false`,
and `reconcile_before_retry: true` in the provider error and failed-action audit.
Other failures also avoid claiming retry safety. Inspect the sitemap inventory
before deciding whether to submit again after an uncertain outcome.
Single-submit 429 errors retain available finite nonnegative numeric
`Retry-After` advice in the returned `provider_error` and failed action audit;
the advice never triggers an automatic retry or wait.

Successful requests use existing action idempotency replay. Failed requests are
not deduplicated by that mechanism: repeating one is an explicit caller decision.
No stronger Google idempotency guarantee is assumed.

Synthetic-key and mocked HTTP tests cover mode selection/invalidation, renewed
scope evidence, fresh OAuth consent, full-scope reads, exact bodyless PUTs,
failures without retries, direct/granted MCP execution, successful replay,
response files, project/grant/scope denial, and secret-safe audit. They do not
prove live Google authorization or sitemap processing.
