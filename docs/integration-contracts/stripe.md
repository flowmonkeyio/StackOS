# Stripe Finance Transport Contract

Last reviewed: 2026-09-23 (public API presentation/recipient limits and external delivery evidence; temporary list deferral unchanged)

Pinned Stripe API version: `2026-08-26.dahlia`

This contract exposes a deliberately small Stripe transport surface for the
finance workflows. It does not make StackOS a ledger, finance system of record,
collections engine, tax engine, payment initiator, or customer-communication
system. The authoritative finance record remains in the selected external
backend; initially `finance/finance.json` is the sole authoritative local
financial record. `finance/FINANCE.md` is guidance and navigation only; the
attachment tree retains immutable original evidence.

## Official source ledger

| Concern | Official Stripe source | Contract consequence |
| --- | --- | --- |
| Authentication | [Authentication](https://docs.stripe.com/api/authentication) | A restricted secret API key is the only initial method. It is JSON credential payload material (`api_key`) held and resolved by the daemon. |
| Restricted-key permissions | [Restricted API keys](https://docs.stripe.com/keys/restricted-api-keys), [permission reference](https://docs.stripe.com/stripe-apps/reference/permissions) | Map the implemented endpoints and expanded objects to the resource permissions below. Write includes Read; permissions remain provider-enforced. |
| Temporary CLI sandbox | [CLI sandbox](https://docs.stripe.com/cli/sandbox), [restricted keys](https://docs.stripe.com/keys/restricted-api-keys) | An unclaimed CLI sandbox can have endpoint-restricted credentials. Claiming and selecting the correct test-key permissions are owner setup, not a connector permission upgrade. |
| API version | [Versioning](https://docs.stripe.com/api/versioning) | Every request pins `Stripe-Version: 2026-08-26.dahlia`; changing it requires a contract and fixture review. |
| Exact response schemas | [Stripe OpenAPI, pinned-version snapshot](https://github.com/stripe/openapi/blob/9ac29c7795ab21c7711b4bc25bb2dd739552a5fa/latest/openapi.spec3.json) | Selected required observations are checked against the generated schema, not inferred from abbreviated examples. PaymentRecord requires seven amount-state objects; PaymentIntent money observations can be absent. InvoicePayment requires its payment discriminator, but the selected reference is optional. Missing linkage cannot establish a match. |
| Customers | [Create](https://docs.stripe.com/api/customers/create), [update](https://docs.stripe.com/api/customers/update), [retrieve](https://docs.stripe.com/api/customers/retrieve), [list](https://docs.stripe.com/api/customers/list) | Explicit creation, scoped existing-customer billing edits, safe retrieval, and a bounded exact-email list so a workflow can reuse an existing customer without receiving a raw id. |
| Catalog discovery | [List products](https://docs.stripe.com/api/products/list), [retrieve product](https://docs.stripe.com/api/products/retrieve), [list prices](https://docs.stripe.com/api/prices/list), [retrieve price](https://docs.stripe.com/api/prices/retrieve) | Four bounded, read-only actions select an existing account-bound Product/Price. A Price retrieve fixes the reviewed tier and currency-option expansions. Safe refs prove identity, not a durable quote or a snapshot of mutable terms. |
| Invoice lifecycle | [Create](https://docs.stripe.com/api/invoices/create), [update](https://docs.stripe.com/api/invoices/update), [finalize](https://docs.stripe.com/api/invoices/finalize), [send](https://docs.stripe.com/api/invoices/send), [retrieve](https://docs.stripe.com/api/invoices/retrieve), [list](https://docs.stripe.com/api/invoices/list) | Invoice creation requires explicit approved currency and remains draft (`auto_advance=false`). Optional `effective_at` controls the displayed Date of issue, not creation, due, or payment time. A separate draft update can set a footer and selected PaymentIntent methods. Finalization and customer email sending remain separate writes. `send_invoice` plus positive `days_until_due` is the only collection method exposed. The send endpoint has no recipient override, so it cannot send an operator-only review copy. Test-mode sends produce no email. |
| Payment presentation and handoff | [Invoice update parameters](https://docs.stripe.com/api/invoices/update), [Dashboard delivery choices](https://docs.stripe.com/invoicing/dashboard), [manage invoice payment methods](https://support.stripe.com/questions/manage-invoice-payment-methods?locale=en-GB), [ACH Direct Debit](https://docs.stripe.com/payments/ach-direct-debit), [bank transfers](https://docs.stripe.com/payments/bank-transfers) | `payment_settings.payment_method_types` selects PaymentIntent methods; the API documents no control that hides the recipient's Pay online button. `us_bank_account` is ACH debit, and `customer_balance` is Stripe-managed virtual bank transfer; neither means deposit directly into the operator's bank account. An empty method list is not a supported disable-all setting. Dashboard documents a manual **Email invoice without link** customer-delivery choice that sends a PDF only. Verify that choice and the recipient-visible result for the selected account/invoice. The API send endpoint does not document inheriting it. |
| Saved-draft review | [Invoice object](https://docs.stripe.com/api/invoices/object), [create preview invoice](https://docs.stripe.com/api/invoices/create_preview), [Dashboard invoice actions](https://docs.stripe.com/invoicing/dashboard/manage-invoices), [send](https://docs.stripe.com/api/invoices/send) | The Invoice schema explicitly defines `hosted_invoice_url` and `invoice_pdf` as null before finalization. The preview endpoint models an upcoming invoice and accepts no saved invoice ID. The Dashboard documents an overflow **Download PDF** action for non-paid invoice statuses and opens non-subscription drafts in the editor. Verify whether Download PDF is available for the specific draft/account only when Dashboard access is authorized; public API documentation does not establish that operational result. The public API has no saved-draft PDF download or recipient override for a review email. Never finalize solely to obtain a review link or PDF. |
| Invoice lines | [Create invoice item](https://docs.stripe.com/api/invoiceitems/create), [list invoice items](https://docs.stripe.com/api/invoiceitems/list) | A line targets an explicit draft invoice and is either the existing positive manual amount/currency/description form or an existing Price plus explicit nonnegative quantity. Stripe computes a Price line total. The connector rejects unassigned pending writes, mixed manual amount/Price input, subscriptions, negative adjustments, tax behavior, and arbitrary metadata. |
| Disputes | [List](https://docs.stripe.com/api/disputes/list), [retrieve](https://docs.stripe.com/api/disputes/retrieve) | Read-only status evidence for an explicitly selected charge/payment intent or known dispute; evidence documents and customer content are excluded. |
| Correlation and advancement | [Metadata](https://docs.stripe.com/metadata), [finalize parameters](https://docs.stripe.com/api/invoices/finalize), [automatic advancement](https://docs.stripe.com/invoicing/integration/automatic-advancement-collection) | One fixed metadata key carries a random opaque correlation value. Create and finalize both explicitly set `auto_advance=false`; reads expose the observed setting. |
| Recipient and due-term observations | [Invoice object](https://docs.stripe.com/api/invoices/object), [expansion](https://docs.stripe.com/expand), [additional billing recipients](https://support.stripe.com/questions/can-i-specify-additional-recipients-or-add-cc-email-addresses-to-billing-emails), [email settings](https://docs.stripe.com/invoicing/send-email) | Invoice customer name, email, phone and address freeze at finalization. Independent retrieval expands the current customer, hashes the selected current and invoice snapshot fields, and preserves the actual due timestamp. Additional billing To/CC recipients are not API-observable. |
| Invoice payments | [Invoice Payment](https://docs.stripe.com/api/invoice-payment), [list](https://docs.stripe.com/api/invoice-payment/list) | Read-only, invoice-scoped allocation evidence. The current object maps an invoice to a nested payment object such as a Payment Intent or charge. |
| Received-payment recording | [Report PaymentRecord](https://docs.stripe.com/api/payment-record/report), [retrieve](https://docs.stripe.com/api/payment-record/retrieve), [object](https://docs.stripe.com/api/payment-record/object), [GA release](https://docs.stripe.com/changelog/clover/2025-10-29/payment-records) | Report one verified bank payment with a fixed guaranteed outcome and custom Bank transfer method. This records an existing receipt; it does not initiate a payment. |
| PaymentRecord recovery listing — unavailable | [Pinned OpenAPI paths and parameters](https://github.com/stripe/openapi/blob/9ac29c7795ab21c7711b4bc25bb2dd739552a5fa/latest/openapi.spec3.json), [generated SDK resource](https://github.com/stripe/stripe-node/blob/master/src/resources/PaymentRecords.ts), [announcement](https://docs.stripe.com/changelog/dahlia/2026-07-29/list-payment-records) | Published sources declare `GET /v1/payment_records`, but the tested sandbox returned 404. StackOS explicitly defers this action pending a verified availability fix; its retained schema is not executable support. No retries, key broadening or guessed routes. |
| Invoice settlement | [Attach payment](https://docs.stripe.com/api/invoices/attach_payment), [payment application](https://docs.stripe.com/invoicing/apply-payments), [partial payments](https://docs.stripe.com/invoicing/partial-payments), [pay out of band](https://docs.stripe.com/api/invoices/pay) | Attach exactly one existing PaymentIntent or PaymentRecord without an allocation-amount input. The separate mark-paid action fixes `paid_out_of_band=true`; it makes no charge and is never a partial-payment route. |
| Payment verification | [Retrieve PaymentIntent](https://docs.stripe.com/api/payment_intents/retrieve), [PaymentRecord invoice support](https://docs.stripe.com/changelog/clover/2025-10-29/invoicing-payment-records) | Safe status/customer/currency/amount/mode observations support agent matching. Reference existence alone does not prove settled funds or correct allocation. |
| Charge, balance transaction, refund evidence | [Charges](https://docs.stripe.com/api/charges), [balance transactions](https://docs.stripe.com/api/balance_transactions), [refunds](https://docs.stripe.com/api/refunds), [balance](https://docs.stripe.com/api/balance) | Read-only actions expose the gross/fee/net, refund, available, and pending facts needed by an external backend. Refund creation, transfers, payouts, and payment collection are absent. |
| Idempotency | [Idempotent requests](https://docs.stripe.com/api/idempotent_requests), [low-level errors](https://docs.stripe.com/error-low-level) | Every `POST` requires the caller's deterministic, non-sensitive idempotency key (maximum 255 characters). StackOS does not attach keys to `GET` requests. |
| Pagination | [Pagination](https://docs.stripe.com/api/pagination) | Lists are one page only, limit `1..100`, with `starting_after` resolved from an account-bound opaque reference to the final object in the prior page. |
| Errors and rate limits | [Errors](https://docs.stripe.com/api/errors), [low-level errors](https://docs.stripe.com/error-low-level), [rate limits](https://docs.stripe.com/rate-limits) | Errors preserve HTTP status, validated error fields, request id, retry context, reviewed endpoint-error messages and matching authenticated Workbench links. Withheld/normalized message text is explicitly marked. Arbitrary raw bodies, private message text, authorization headers, and key material never leave the daemon. |

## Provider setup and identity

`finance` contributes provider key `stripe` and auth method `api_key`. The
daemon stores the JSON payload, decrypts it only for account testing or action
dispatch, and sends it in the Stripe `Authorization` header. Neither a workflow
input, `finance.json`, `FINANCE.md`, action output, nor audit-visible metadata contains the
key.

Credential parsing trims ordinary pasted edge whitespace and rejects embedded
whitespace, controls and non-ASCII characters before building an Authorization
header. This avoids an HTTP serializer error echoing the Bearer value into a
transport log. Validation assumes no key prefix; Stripe still decides whether
a header-safe key is valid and authorized.

`account.test` calls the harmless `GET /v1/account` probe. It stores only safe
account identity evidence (Stripe account id, display name when available, and
country) for the connection. The probe does not infer live/test mode from an
undocumented account field. Stripe's restricted-key
permissions are provider enforced; StackOS does not invent scope claims.

Probe failures use provider-owned static diagnostics: HTTP 401 means the key
was rejected; HTTP 403 means this operation lacks permission. Only a 403 with
Stripe's known claimable-sandbox restriction marker receives claim guidance.
Neither failure is a transient retry. Rate limits, server failures, and network
failures remain retryable for the read-only probe unless Stripe explicitly
returns `Stripe-Should-Retry: false`. The existing action
`retry_safe` and `outcome_unknown` fields still describe mutation/recovery
safety, not whether repeating a denied request would help.

The normalized result retains safe HTTP status, validated request/error codes,
and fixed repair guidance through `account.test`, Account inventory, and the
existing usage audit. Actions carry the same provider-owned explanation in
their structured error/audit. Arbitrary raw provider messages and claim URLs are never
returned. Error documentation URLs are limited to Stripe error-code docs;
provider-controlled codes and headers are bounded and validated. A successful
read on one endpoint does not establish access to the account probe, balances,
or invoice writes. No fallback probe hides a missing permission, and StackOS
never broadens the saved key's permissions automatically.

For HTTP 404 with Stripe's `Unrecognized request URL` diagnostic, the adapter
returns `reason_code=endpoint_not_recognized` and a normalized `message`. Its
method and static collection path come from the actual request, not the
provider's echoed URL; object-specific paths and query values are omitted.
`message_redacted=true` explicitly identifies normalized or withheld text.
Other free-form messages remain withheld because they can contain credentials,
private claim links or customer data that generic redaction cannot identify.

HTTP failures and successful action metadata also preserve `request_method`,
`request_path`, `request_api_version` and `response_api_version` from the actual
HTTP exchange. The two version fields read the request and response
`Stripe-Version` headers independently; absent or unsafe values are explicit
nulls, never replaced by the configured version. Only bounded date versions
with reviewed release names survive; arbitrary suffixes and preview feature
headers are withheld. The legacy success `api_version` field continues to
identify the configured connector contract and is not evidence of the version
Stripe returned.

Methods are limited to GET/POST. Paths are an exact disclosure allowlist of
static routes already used by the connector and its account probe. Query
values, object-specific paths, unknown routes and percent-encoded spellings
are withheld (`request_path=null`); other headers and the request body are
never copied into these diagnostics. These observations prove what HTTPX sent
and what Stripe returned, not the server's internal dispatch or feature state.

`request_log_url` is retained only for HTTPS on exact `dashboard.stripe.com`,
the reviewed `[acct_.../][test/]workbench/logs?object=req_...` path, and a
request id matching the validated `Request-Id` header. Additional parameters,
fragments, encoded forms, credentials and other destinations are rejected.
The link requires the operator's Stripe login; it grants no access itself.
The same safe packet survives repeated normalization, the compact/raw MCP
error response and the persisted failed ActionCall. No new diagnostic store
or raw-secret recovery endpoint is introduced.

Live verification on 2026-09-05 authenticated the selected test account but
received this unrecognized-route error for `GET /v1/payment_records` despite
the published SDK contract above. The operator explicitly selected temporary
StackOS unavailability until a later verified fix. The action retains its ref
and schema but has `execution_mode=deferred-stripe-payment-record-list`, a
`deferred_reason`, and no connector binding. Normal executable discovery omits
it; description/validation expose deferred availability, and direct or granted
execution returns `execution_deferred` with the reason before dispatch. No
credentials are decrypted and no provider request or ActionCall is created by
that preflight rejection. Historical provider failures remain unchanged in audit.

The recovery-only workflow action contract is optional, so ordinary follow-ups
and known-ref settlement remain usable. Do not retry listing, change keys or
guess a route. Missing verified refs hold for owner/provider resolution, never
a replacement report or silent settlement-route fallback. A successful account
probe is not endpoint coverage; re-enabling needs verified availability evidence.

All action selectors and object outputs use the existing account-bound opaque
`provider-object:<opaque>` mechanism. Raw Stripe customer, product, price, invoice, charge,
balance-transaction, refund, dispute, invoice-item, payment-intent, payment-record, and invoice-payment IDs do not
become workflow inputs or finance workspace fields. A list action's
`next_page_cursor` is simply its final item's typed safe reference—there is no
second cursor record type.

### Restricted-key permissions

For all currently executable Stripe actions, create one restricted key for the
Stripe account being connected, for example named `StackOS Finance`. Configure
the account's own permissions, not the separate Connect permissions for acting
on connected accounts. The connector does not send a `Stripe-Account` header.
Save the generated key in the StackOS Account form, never in chat or a file.

The following maps the current connector to Stripe's documented permission
groups. The identifiers are from Stripe's permission reference; in the
restricted-key Dashboard, select the resource and Read/Write setting.

| Stripe resource | Access | Reference identifier | Current connector use |
| --- | --- | --- | --- |
| Customers | Write | `customer_write` | Create, update approved billing details, find by email, retrieve, and expand the invoice customer. |
| Products | Read | `product_read` | Bounded existing-product discovery and retrieval. |
| Prices | Read | `plan_read` | Bounded existing-Price discovery/retrieval, including its fixed pricing expansions. |
| Invoices | Write | `invoice_write` | Create/update invoice and items, read invoice/items/payments, finalize, send, attach an existing payment, and mark paid out of band. |
| Payment Records | Write | `payment_records_write` | Report a payment already received and retrieve a known record. |
| Accounts | Read | `connected_account_read` | The connection test's `GET /v1/account` identity probe. |
| Balance | Read | `balance_read` | Read available/pending balances and balance transactions. |
| Balance Transaction Sources | Read | `balance_transaction_source_read` | Required by the connector's fixed balance-transaction source expansions. |
| Charges and Refunds | Read | `charge_read` | List/retrieve existing charges and refunds. |
| Payment Intents | Read | `payment_intent_read` | Retrieve an existing payment for verification and invoice attachment. |
| Payment Disputes | Read | `dispute_read` | List/retrieve disputes for reconciliation. |

Write includes Read. Leave unrelated permissions at None, retaining the read
dependencies Stripe automatically implies; in particular, Balance Transaction
Sources implies several other read permissions. No charge/refund, dispute,
transfer, or payout **Write** access is needed. Invoice items and invoice-payment
operations belong to invoice access, not separate permission groups in this
mapping. Returning an invoice's hosted URL does not require Payment Links access.

This is an endpoint-to-documentation mapping, not proof that a newly supplied
restricted key has passed every action. A successful connection test covers
only the account probe; any permission denial must be checked against the
selected action's safe provider error. PaymentRecord listing remains explicitly
unavailable in StackOS; granting Payment Records access does not enable it.

## Action matrix — 36 executable actions, one deferred

| Action ref | Stripe endpoint | Risk | Input boundary |
| --- | --- | --- | --- |
| `finance.stripe.customers.create` | `POST /v1/customers` | write | Explicit customer email and optional name/description; deterministic idempotency key. |
| `finance.stripe.customers.update` | `POST /v1/customers/:id` | write | Account-bound `customer_ref` and approved name, email, phone, billing-address fields or `invoice_settings.custom_fields`; deterministic idempotency key. Custom fields replace the whole list; preserve existing entries explicitly. No payment-source edits. |
| `finance.stripe.customers.retrieve` | `GET /v1/customers/:id` | read | Account-bound `customer_ref`. |
| `finance.stripe.customers.list` | `GET /v1/customers` | read | One bounded page matching one exact `email`; continuation uses the final customer reference. |
| `finance.stripe.customers.tax-ids.list` | `GET /v1/customers/:id/tax_ids` | read | One bounded page for the exact account-bound customer; exhaust pagination before duplicate comparison. |
| `finance.stripe.customers.tax-ids.create` | `POST /v1/customers/:id/tax_ids` | write | Exact customer, provider tax-ID type, secret-marked value and stable idempotency key. The agent checks duplicates first; creation does not establish verification. |
| `finance.stripe.customers.tax-ids.retrieve` | `GET /v1/customers/:id/tax_ids/:id` | read | Account- and customer-bound tax-ID reference; returns actual nullable verification status. |
| `finance.stripe.products.list` | `GET /v1/products` | read | One bounded catalog page, optionally limited by `active`; returns safe Product/default-Price refs. |
| `finance.stripe.products.retrieve` | `GET /v1/products/:id` | read | Account-bound `product_ref`. |
| `finance.stripe.prices.list` | `GET /v1/prices` | read | One bounded catalog page, optionally scoped by Product, active state, currency, or Price type. |
| `finance.stripe.prices.retrieve` | `GET /v1/prices/:id` | read | Account-bound `price_ref`; fixed `tiers` and `currency_options` expansions expose current price structure without computing a quote. |
| `finance.stripe.invoices.create` | `POST /v1/invoices` | write | `customer_ref`, explicit lowercase approved `currency`, `collection_method=send_invoice`, positive `days_until_due`, optional `effective_at`, memo description and opaque `correlation_key`; always draft. `effective_at` is the printed issue date only. |
| `finance.stripe.invoices.update` | `POST /v1/invoices/:id` | write | Account-bound `invoice_ref` and memo `description`, complete `custom_fields`, footer or nonempty `payment_method_types`. Memo-only updates and literal empty memo clearing are supported; omission leaves a field unchanged. Stripe enforces finalized editability. Independently retrieve the same invoice after correction. No finalization or send. |
| `finance.stripe.invoice-items.create` | `POST /v1/invoiceitems` | write | `customer_ref`, explicit `invoice_ref`, and either positive smallest-unit manual `amount` + lowercase ISO `currency` + description, or existing `price_ref` + explicit nonnegative `quantity` with only the supported optional currency/description. |
| `finance.stripe.invoice-items.list` | `GET /v1/invoiceitems` | read | Exactly one of `invoice_ref` or `customer_ref`, bounded pagination; customer-scoped results can include attached and pending items. |
| `finance.stripe.invoices.finalize` | `POST /v1/invoices/:id/finalize` | write | Explicit `invoice_ref`; forced `auto_advance=false`; no send implied. |
| `finance.stripe.invoices.send` | `POST /v1/invoices/:id/send` | write | Explicit finalized `invoice_ref`; approval belongs to the workflow. This is customer delivery with no alternate recipient parameter, never an operator-only review action. |
| `finance.stripe.invoices.retrieve/list` | `GET /v1/invoices/:id`, `GET /v1/invoices` | read | Safe invoice ref or one bounded page with optional status, `customer_ref`, and inclusive `created_gte`/`created_lte` Unix timestamps. Optional `correlation_key` returns per-invoice match evidence. |
| `finance.stripe.invoices.pdf.download` | Fresh `GET /v1/invoices/:id`, then unauthenticated GET of its returned PDF URL | read | Account-bound `invoice_ref`, optional `max_bytes` up to 20 MiB. Requires a finalized invoice and stages verified PDF bytes privately; never finalizes or sends. |
| `finance.stripe.invoices.pdf.cleanup` | Local private transfer cleanup; no provider mutation | write | Exact opaque `transfer_ref`, scoped to project and Stripe account. Use only after host-owned external custody/readback; no arbitrary path input. |
| `finance.stripe.invoice-payments.list` | `GET /v1/invoice_payments` | read | `invoice_ref` and one bounded page. |
| `finance.stripe.payment-intents.retrieve` | `GET /v1/payment_intents/:id` | read | Account-bound `payment_intent_ref`; no creation, confirmation, capture, or charge. |
| `finance.stripe.payment-records.retrieve` | `GET /v1/payment_records/:id` | read | Account-bound `payment_record_ref`; safe amount-state observations and reference digest, not bank evidence. |
| `finance.stripe.payment-records.list` — unavailable | Published `GET /v1/payment_records`; no request dispatched | read | Deferred pending a verified availability fix. Retained `limit`/`page_cursor` schema is not an executable route. Inspect audit/response files and retrieve a known ref; otherwise hold for owner/provider resolution. |
| `finance.stripe.payment-records.report` | `POST /v1/payment_records/report_payment` | write | One positive bank payment, currency, `customer_ref`, observed `initiated_at` and `guaranteed_at`, and secret-marker `payment_reference`; fixed custom processor/method and guaranteed outcome. |
| `finance.stripe.invoices.attach-payment` | `POST /v1/invoices/:id/attach_payment` | write | `invoice_ref` and exactly one `payment_intent_ref` or `payment_record_ref`; no allocation amount or collection controls. |
| `finance.stripe.invoices.mark-paid-out-of-band` | `POST /v1/invoices/:id/pay` | write | Only `invoice_ref`; always `paid_out_of_band=true`, with no payment method, amount, source, or forgiveness override. |
| `finance.stripe.charges.retrieve/list` | `GET /v1/charges/:id`, `GET /v1/charges` | read | Safe charge ref or bounded page, optionally `customer_ref` or `payment_intent_ref` scoped. |
| `finance.stripe.disputes.list/retrieve` | `GET /v1/disputes`, `GET /v1/disputes/:id` | read | List requires exactly one of `charge_ref` or `payment_intent_ref`; retrieve requires `dispute_ref`. No dispute mutation. |
| `finance.stripe.balance-transactions.retrieve/list` | `GET /v1/balance_transactions/:id`, `GET /v1/balance_transactions` | read | Safe balance transaction ref or bounded page with gross/fee/net and balance-type facts; fixed `expand[]=source` on retrieve and `expand[]=data.source` on list. |
| `finance.stripe.refunds.retrieve/list` | `GET /v1/refunds/:id`, `GET /v1/refunds` | read | Safe refund ref or bounded page, optionally charge scoped. No refund creation. |
| `finance.stripe.balance.retrieve` | `GET /v1/balance` | read | No input; safe available and pending balance buckets only. |

By default the connector returns an allowlisted lifecycle/reconciliation
projection plus safe refs. Selected read actions also support the explicit
`include_business_details=true` option described below. This distinguishes
necessary business content from credentials without returning arbitrary provider
payloads. Metadata bags, payment method details, bank/card data and client
secrets remain excluded. Provider-reference display names remain empty even
when a detailed read returns a customer name or invoice number.
Customer-identifying textual inputs such as email, name, phone, every billing
address field including country, invoice description, and footer text must use
the existing exact `{"$secret_ref":"secret_..."}` payload
marker so their values are materialized only inside provider dispatch and are
redacted if echoed by Stripe. Idempotency keys must never contain those values.

The sole memo-input exception is literal `description: ""`, which clears the
memo without carrying business content. Nonempty memo text uses a secret marker
and is limited to 1,500 characters. Customer `invoice_settings.custom_fields`
and invoice `custom_fields` accept a complete ordered list of up to four
name/value pairs (40/140 characters respectively), each string secret-marked.
An omitted list is unchanged; `[]` clears it. The connector performs one explicit
provider request and does not silently read, merge, deduplicate or retry writes.

### Customer identifiers and issued-invoice corrections

Use the existing account/customer mapping. Exhaust all
[`tax_ids` pages](https://docs.stripe.com/api/tax_ids/customer_list) and compare
the approved type and exact UTF-8 value SHA-256 before
[creating](https://docs.stripe.com/api/tax_ids/customer_create) an identifier.
A match is reused with no POST; a new identifier requires a stable idempotency
key and independent retrieval. Preserve the actual nullable `verification`
object and its status; [Stripe verification is asynchronous](https://docs.stripe.com/billing/customer/tax-ids).
Never convert successful creation into `verified`. Unknown creation outcomes
require reconciliation before any retry.

Before replacing custom fields, retrieve the existing ordered list with business
details, merge the approved change externally, preserve every unrelated entry,
then submit the full list and independently read it back. Customer defaults and
the invoice itself are separate objects: verify the invoice's own `custom_fields`
and `customer_tax_ids`, not just the customer's current settings. Default safe
reads expose ordered name/value hashes and tax-ID type/value hashes; tax-ID
reads also expose customer-bound refs and actual verification observations.
Invoice reads expose `description_sha256`. Compare these exact independent
readbacks and include them in `finance_delivery.invoice_snapshot_digest`.
Historical snapshots/digests/approvals remain unchanged; corrected material
requires new evidence, not rewriting history.

[Finalization freezes custom fields](https://docs.stripe.com/invoicing/customize)
and [customer tax-ID snapshots; memo edits are restricted for most finalized
invoices](https://docs.stripe.com/invoicing/integration/workflow-transitions).
For an authorized issued-invoice correction, use its existing ref with the
explicit update action and report Stripe's actual result. A provider refusal
does not authorize a replacement invoice, void, refinalization or resend.
Updating customer defaults affects future invoices and does not establish an
issued-invoice correction.

Invoice-item descriptions are omitted from default reads; the detailed list
read can return their text for line inspection. The default list output
`description_sha256` hashes the provider's exact UTF-8 text without trimming or
Unicode normalization. Empty text has the SHA-256 of empty bytes; null or
missing descriptions produce null. The agent hashes the approved external line
text the same way before comparison. Use the independent list read for the
comparison, not a POST response echo: the existing generic payload redactor
replaces occurrences of resolved secret text throughout POST responses, and a
very short description can also redact substrings inside a digest, safe ref,
or field name. The invoice-scoped list read has no description payload secret
and recovers the actual item refs plus amount/currency/digest for comparison.

### Explicit business details and document handoff

The following existing read actions accept `include_business_details` (boolean,
default `false`). It selects a connector output projection; it is **not** a
Stripe query parameter, an auth override, or a new action grant.

| Action | Additional `business_details` fields |
| --- | --- |
| `finance.stripe.customers.retrieve` | `name`, `email`, `description`, and invoice custom-field defaults for a selected customer. |
| `finance.stripe.customers.tax-ids.list` / `.retrieve` | Tax-ID value; verification name/address remain hashes in the safe projection. |
| `finance.stripe.products.list` | `name`, `description`, `unit_label` on each returned product. |
| `finance.stripe.products.retrieve` | `name`, `description`, `unit_label` for the selected product. |
| `finance.stripe.prices.list` | `nickname`, `lookup_key` on each returned Price. |
| `finance.stripe.prices.retrieve` | `nickname`, `lookup_key` for the selected Price. |
| `finance.stripe.invoices.retrieve` | `number`, `description`, `customer_name`, `customer_email`, `custom_fields`, `hosted_invoice_url`, `invoice_pdf`; customer tax IDs remain type/value hashes. |
| `finance.stripe.invoice-items.list` | `description` on each returned item; existing page limits/cursors still apply. |
| `finance.stripe.charges.retrieve` | `description`, `receipt_url` for a selected charge. |

For example, invoke `finance.stripe.invoices.retrieve` with
`{"invoice_ref":"provider-object:<observed-ref>","include_business_details":true}`
using the selected Account's normal action context. The action's `data` retains
its existing lifecycle fields plus `business_details`; invoice-item pages put
`business_details` on each `data.items[]` entry. Inspect the returned response
file's `response.output_json.data` before any further provider call. A detailed
charge read also exposes a safe `customer_ref` when Stripe supplies one.

Business details are confidential task data, not provider authentication. Request
them only for the current owner's customer identification, catalog selection,
invoice inspection, or document handoff. Do not turn routine balance/reconciliation
polling into a bulk customer or catalog export. Customer lists and all write
responses keep their default projection. An exact-email lookup may return several customer refs; retrieve
the relevant candidates to disambiguate, then inspect their customer-scoped
charge pages. Do not assume that an email, name or equal amount proves a match.

These fields follow the normal shared credential/payload-secret redaction and
file-backed output path, including the non-authoritative action audit and
idempotent readback. They are not copied into provider-reference display names,
workflow results, resources, artifacts or tracker notes. The external backend
remains the financial master. `response_mode=raw` changes the envelope, not
the selection: callers must explicitly request business details.

An invoice's `hosted_invoice_url` is a customer-facing page where payment may
be possible, while `invoice_pdf` is a document URL. Both are provider
observations, not constructed from an invoice ref. Stripe's Invoice schema
explicitly documents both fields as null before finalization. Missing or
unavailable links are not evidence of failed payment. The
`create_preview` endpoint models an upcoming invoice that has not been saved;
it does not produce a preview/download of this saved draft. A Dashboard review
link for a saved draft is a different, authenticated surface: this connector
does not return one, and no official stable detail URL pattern has been verified
for construction. The operator can locate and inspect the saved draft manually
in Stripe Dashboard. Its invoice-management documentation describes an overflow
**Download PDF** action for non-paid statuses and says non-subscription drafts
open the editor. Check the selected draft/account to establish whether that
manual download works; the docs do not guarantee it for every draft. StackOS
cannot hand over an API-backed draft PDF or direct Dashboard link. If a required
review artifact cannot be obtained through the verified Dashboard path, report
the gap and hold the draft. Never finalize an invoice merely to obtain a review
link or PDF. “Copy invoice link” can currently return an
available Stripe-hosted customer URL or PDF URL, not a draft Dashboard link.
Validation accepts
provider-returned HTTPS hosts without inventing a Stripe-only hostname rule;
this does not claim that Stripe offers custom domains for every invoice surface.
Invoice retrieve/list returns URL metadata only; it neither downloads invoice PDF
bytes nor proves that a host/browser fetch succeeded. The explicit PDF download
action below performs the byte transfer. Generic
credential/signed-URL redaction still applies: never share a redacted or invalid
URL as a working link. Default invoice reads expose top-level
`hosted_invoice_url_state` and `invoice_pdf_state`: `not_returned`,
`draft_unavailable`, `unavailable`, `available`, `invalid`, or `redacted`.
Invoice reads also return `recipient_pay_online_button_state=unverified` on
every status and `draft_dashboard_link_state=not_exposed_by_stripe_api` for a
draft. Neither field is proof that the Pay online button is absent or that a
usable draft link exists.
The actual URL remains opt-in under `business_details`, preserving a provider
null as null; invalid or redaction-changing URLs return null with `invalid` or
`redacted` state. Valid URLs retain their exact value.
Links can expire or be revoked; reread Stripe when a fresh
link is needed and do not claim an HTTP fetch succeeded unless actually checked.
An invoice link can reveal customer/invoice data to its recipient. Return it in
the owner's requested private handoff, not a public channel or unrelated client
project. A receipt URL is not an invoice URL. Stripe's invoice send endpoint has
no recipient override. An operator-only review copy can use a real PDF manually
downloaded from the selected draft in Dashboard, if verified available, and a
separately approved independent channel. Never change the
customer's billing email to route a review copy or call
`finance.stripe.invoices.send` for that purpose. If no actual artifact/channel
is available, report the limitation without claiming a review copy was sent.

### Public API capability boundary

The 2026-09-23 review compared the [pinned OpenAPI snapshot](https://github.com/stripe/openapi/blob/9ac29c7795ab21c7711b4bc25bb2dd739552a5fa/latest/openapi.spec3.json)
with the then-current public `master` snapshot. Both declare
`2026-08-26.dahlia` and have SHA-256
`2c31317cdff103e4495b5b3501004d9ddc0af61f43b0ab819e2db392eef008f6`.
These are public API limits, independent of which subset StackOS exposes:

| Requested capability | Exact public contract | Consequence |
| --- | --- | --- |
| Email a review copy to another address | `POST /v1/invoices/{invoice}/send` has only the transport `expand` property and rejects additional properties. | No `to`, `cc`, `bcc`, recipient override, or attachment-selection parameter. Changing the customer's billing email is not a review-copy operation. |
| Send without a payment link | The same send schema has no delivery-mode or hide-link field. Invoice `rendering` update properties are `amount_tax_display`, `pdf.page_size`, `template`, and `template_version`. | Footer, payment methods, and a rendering-template identity do not establish email button suppression. Dashboard delivery evidence cannot prove API-send presentation. |
| Retrieve the actual saved draft PDF | `Invoice.invoice_pdf` is null until finalized. The public paths have no invoice PDF/download/render endpoint; `create_preview` takes no saved invoice ID. | A preview calculation, synthetic PDF, quote PDF, or invented URL cannot be described as the actual draft artifact. |
| Enumerate every billing recipient | Customer and Invoice expose a primary email, without additional billing To/CC fields. | Primary-email readback cannot prove additional recipients are absent. |

This review does not test private Dashboard endpoints or imply access to an
undocumented feature. An API-only run preserves these unresolved operational
checks. It never opens a browser, guesses hidden parameters, finalizes for an
artifact, sends a probe, or replaces an exact saved-draft artifact with a
different document. Owner-only artifact delivery also needs an independently
verified attachment-capable transport; the current SMTP action has no
attachment input and cannot be presented as an established PDF route.

### Download a finalized invoice PDF

Call `finance.stripe.invoices.pdf.download` with the existing account-bound
`invoice_ref`. It freshly retrieves the same invoice, checks finalized status
and finalization evidence, and uses only Stripe's returned `invoice_pdf`.
Drafts, null/missing links and identity mismatches fail without a finalize or
send. Retry by retrieving a fresh URL through this action; never construct a
PDF URL from an invoice ref or reuse an expired link as evidence of success.

The PDF fetch uses a separate client with no Stripe authorization header,
cookies or environment proxy credentials. It follows Stripe's returned HTTP(S)
URL and redirects without a hostname, IP or port allowlist. URL userinfo and
fragments are stripped before requests so a URL cannot introduce credentials.
Normal HTTP transport/TLS validation, bounded redirects, response type/content
checks and a 20 MiB maximum remain. Both `application/pdf` and
`application/octet-stream` are supported; either must contain the PDF header
and end marker. Unavailable/expired URLs, timeouts,
oversized responses and non-PDF bodies return safe errors. Diagnostics identify
`pdf_phase`, `pdf_hostname` and `pdf_redirect_hop`, never the signed URL, query
or redirect Location. Finalized status alone does not guarantee network success.

Successful output gives `transfer_ref`, `invoice_ref`, `local_path`, fixed
`filename=invoice.pdf`, `mime_type`, `bytes`, `sha256`, `staged_at` and
`custody=temporary-private-staging`. The private project-scoped staging tree is
excluded from public media serving and media-artifact inputs. It creates no
StackOS finance resource or artifact and does not put PDF bytes or the signed URL
in action output. File-backed action envelopes retain safe transport metadata.

The host copies that exact file into the external finance workspace under its
existing writer/custody rules, verifies hash and byte count, and records invoice
provenance there. Only then call `finance.stripe.invoices.pdf.cleanup` with the
same opaque transfer ref. Cleanup never accepts a filesystem path and cannot
remove another project's or account's transfer. A staged PDF is not proof of
customer email delivery, recipient completeness, payment, or no-link email
presentation. Owner-only sharing still requires a separately verified
attachment-capable route and the exact intended recipient scope.

For “find this customer's existing payment, create its invoice, and give me the
link,” compose the current paths:

1. Select the correct attached Stripe Account and live/test mode; perform the
   exact-email customer lookup and disambiguate candidates/current payments.
2. Verify the succeeded PaymentIntent, customer/currency/account, refund and
   dispute state, prior InvoicePayment linkage and external source allocation.
   Obtain the invoice's service/line details; a payment does not supply missing
   contractual or tax facts.
3. Use payment-request to discover/retrieve any approved existing Price, create/recover
   the reviewed draft and exact manual or Price/quantity lines, and pass the
   approved optional `effective_at` as the printed issue date. Reread its exact
   value and the draft totals before finalization with `auto_advance=false`,
   stopping **before send**.
4. Use the separately approved `settlement-only` occurrence to attach the
   already-received payment. Do not create/confirm a PaymentIntent, charge again,
   report the Stripe payment as a new bank receipt, or send a reminder.
5. Reread invoice lifecycle and InvoicePayment linkage. When fully paid, verify
   `status=paid` and `amount_remaining=0`; a link alone proves neither.
6. Retrieve that invoice with `include_business_details=true`, read the returned
   response file, and share the provider's available hosted invoice/PDF link in
   the authorized handoff. No `invoices.send` call is needed just to get a link.

This composes existing workflows; there is no new retroactive-invoicing engine.
Whole-payment/single-invoice restrictions, source evidence, grants and owner
approvals are unchanged. The unavailable PaymentRecord list is not used for a
known succeeded PaymentIntent. These fields follow the official
[Invoice](https://docs.stripe.com/api/invoices/object),
[Customer](https://docs.stripe.com/api/customers/object),
[Invoice Item](https://docs.stripe.com/api/invoiceitems/object), and
[Charge](https://docs.stripe.com/api/charges/object) objects; see also
[hosted invoice pages](https://docs.stripe.com/invoicing/hosted-invoice-page).
Do not retry a POST just to obtain a cleaner response. Multiple matching items
remain ambiguous and require review. A digest supports exact equality checks;
it is neither a semantic match nor a claim that the text is anonymized. Invoice
reads expose `total` and `subtotal` separately from `amount_due`, because credit
or other adjustments can make the current amount due differ from the invoice's
economic total. Invoice creation requires the approved currency even though
Stripe permits a customer-default currency when omitted. Independently verify
the draft currency before adding any line. A manual line's currency, or a
catalog line's selected Price currency option where available, must match the
approved invoice currency; do not reject a Price solely because its base
currency differs when its selected option is applicable.

### Selected observation and reconciliation contract

The projection validates the critical fields it consumes, not the entire raw
Stripe object. Customer, Product, Price, Invoice, InvoiceItem, Charge, BalanceTransaction, Refund
and Dispute can no longer pass with only `id` and `object`: selected required
money, mode, lifecycle and identity fields must be present and correctly typed.
Published charge/dispute lifecycle enums are checked; optional/nullable fields
and the explicit deleted-Customer variant keep their documented semantics.
Invoice `amount_paid_off_stripe` stays optional because the current object docs
describe expansion-dependent visibility. No workflow requires it or substitutes
zero when it is absent. Removed legacy Invoice top-level `charge` and
`payment_intent` pointers are not used; InvoicePayments own payment linkage.

InvoicePayment's selected payment reference is optional in the pinned schema.
`payment_ref_state=missing` preserves a valid allocation observation without
inventing linkage. Explicit null is conservatively retained as `null`
unavailable evidence, not claimed to be schema-declared nullable; an available
valid reference becomes an account-bound opaque ref. Malformed present values
still fail. Money/status facts and list continuation survive missing/null
linkage, but neither state proves a source match or absence of attachment.

Balance transactions expose `balance_type`. Their fixed source expansion maps
only supported expanded `charge`, `refund` and `dispute` objects to
`source_type`, `source_ref` and `source_state=available`. Missing, null,
unexpanded and unsupported observations have explicit states with no invented
ref or guessed ID-prefix type. Refunds expose the optional
`failure_balance_transaction_ref`; disputes expose `balance_transaction_refs`
from their required transaction array. These existing safe refs let the agent
retrieve the related evidence without exposing raw provider IDs. An unavailable
link remains a reconciliation exception, not proof of complete coverage.

### Recipient and effective due-term checks

`invoices.retrieve` always requests `expand[]=customer` in its single read.
The safe response contains `invoice_customer_email_sha256` from the invoice's
`customer_email` and `current_customer_email_sha256` from the expanded current
Customer's `email`. Customer reads also expose `email_sha256`, `name_sha256`,
`phone_sha256`, and `address_field_sha256` for the exact address fields that
Stripe returns. These digests use exact UTF-8 bytes without case folding,
trimming or normalization. The default projection retains no raw customer
email, name, phone, metadata or address; explicitly requested business details
remain subject to the disclosure boundary above. Missing, empty, deleted or
unexpanded values never become a claimed match.

An existing customer update changes only supplied approved fields; omitted
fields are not cleared. The agent independently retrieves the same customer
after the write and compares every requested field's digest with the immutable
external source. The update response echo is insufficient proof because
payload-secret redaction can alter it. Address field digests distinguish
provider missing/null states from an approved value; unknown or mismatched
readback stops further invoice work. After preparing the draft, independently
retrieve the invoice as a second API call and compare its own
`invoice_customer_name_sha256` and `invoice_customer_address_field_sha256`
snapshot against the same approved name/address. A correct current customer
does not prove the invoice presents those details. Repeated `action.execute`
reads fetch current provider state when no explicit replay key is supplied.
An explicit StackOS `idempotency_key` requests replay of that observation;
omit it for an independent verification read. The connector sends no Stripe
`Idempotency-Key` header on GET. Approved name, email, phone, billing-address
and invoice custom-field defaults are supported customer edits. Customer tax
IDs use their separate actions. Payment sources, customer balance, other tax
settings and delivery preferences are outside these actions.

Invoice reads separately return `invoice_customer_name_sha256`,
`invoice_customer_phone_sha256`, `invoice_customer_address_sha256`, and
`invoice_customer_address_field_sha256` when Stripe supplies those snapshot
fields. Compare each approved field with the invoice snapshot as well as the
current customer before finalization and send. Stripe documents that draft
invoice customer details follow the customer until finalization, when they
freeze. A successful customer update does not prove an already-finalized
invoice displays the corrected details. Missing/null observations remain
unproven; the connector never returns the raw fields by default.

For a draft presentation update, `invoices.retrieve` returns
`footer_sha256` and `payment_settings.payment_method_types`. A requested
footer is verified by hashing the external exact UTF-8 text; a requested method
list is compared as the provider returned it. Missing or null is not a match
to a requested value. The selected `card`, `us_bank_account` and
`customer_balance` values are Stripe payment methods for its invoice
PaymentIntent, not the operator's direct deposit instructions. `us_bank_account`
is ACH debit and `customer_balance` is a Stripe-managed virtual bank transfer,
not direct deposit to the operator's business bank account. An empty method list
cannot be used as a disable-all control. The footer is only supplied text and
may be displayed on the customer invoice. Neither setting proves that Stripe's
invoice email or Hosted Invoice Page omits a Pay online button; the API exposes
no documented hide-button control. The readback field
`recipient_pay_online_button_state=unverified` makes this limit explicit.
Stripe Dashboard supports per-invoice payment-method management, but the
operator must verify the actual recipient-visible presentation for the selected
account/invoice and send route. Stripe separately documents the Dashboard
**Email invoice without link** choice as a manual customer send with a PDF only.
Check that option for this saved invoice/account and the actual recipient-visible
result before using it. The option by itself is not rendering proof or an
operator-only review copy. The API `invoices.send` endpoint has no choice
parameter and does not document inheriting the Dashboard selection; Dashboard
evidence alone does not verify an API send. A direct-deposit-only proposal must
bind separate evidence that the button is absent for the exact chosen route.
`payment_presentation.review_evidence_ref` resolves to a typed external
provider observation under the [local workspace contract](../../plugins/finance/references/local-workspace-contract.md),
including the exact invoice/account, billing version/digest, recipient version,
invoice snapshot, retained original, verifier, verification time and validity.
The host's `validate_payment_presentation` checks current identity, route,
freshness and original bytes before delivery. A nonempty opaque ref or an
agent's assertion does not satisfy it. A visible or unknown button state holds
the send while drafts can retain unknown presentation.

Until the exact API route has verified no-link presentation, hold
`finance.stripe.invoices.send` for direct-deposit-only delivery. Manual Dashboard
delivery remains a separate route that the agent cannot execute in an API-only
session. A pending choice or uncertain delivery records an exception/recovery
hold, with no API send. Workflow 0.5.3 supports read-only reconciliation of an
already completed manual send through `reconcile_manual_invoice_delivery`:
exact invoice/version and recipients, prior matching owner send authorization,
and independent retained delivery evidence are required. Its safe result uses
`status=manual-sent`, `delivery_state=manual-sent`, and
`delivery_route=stripe-dashboard-email-without-link`, with
`manual_delivery_reconciliation_ref`, `delivery_evidence_ref`, and
`owner_send_approval_ref`. The final recorded result retains that
provenance. It issues no send and invents no API action-call record. Missing,
mismatched or uncertain proof remains unresolved. Frozen older runs retain
their original output contract; follow the documented contract-drift recovery
before continuing, without recreating invoices or replaying creation steps.
Separate finalization approval is an issuance decision, never permission to
finalize merely to generate a review PDF. If absence is verified after an
independently approved finalization, that new evidence changes the immutable
version and requires fresh owner send approval for the exact version.

Stripe documents that `invoice.customer_email` also follows the customer until
finalization and then freezes. A current customer email may therefore differ
from the finalized invoice snapshot. Compare both observed hashes with the
approved external primary recipient before finalization, initial send or
resend. A mismatch or missing observation stops that action for recipient
resolution; never silently pick one field or infer the effective address from
the invoice/customer reference. Independent retrieval is required because
create/finalize/send echoes and list responses need not expand the customer.

The output explicitly reports `recipient_scope=primary-email-fields-only`.
Stripe's additional Billing To/CC settings can only be inspected in its
Dashboard, not the API or Stripe Sigma. These hashes therefore do not enumerate
or guarantee every actual recipient. The external business approval must carry
the verified recipient-setting scope, including any additional recipients or
confirmed absence of overrides. When those settings are unknown, preserve the
draft and request that verification rather than claiming full recipient proof.
The read does not lock provider settings against a later concurrent change.

For effective due terms compare the observed `collection_method` and `due_date`
(a nonnegative Unix timestamp or null) with the approved proposal. The current
Invoice object does not expose a `days_until_due` field: that is a creation
input, not a value to invent on reads. Retain the requested terms externally,
review the actual resulting due date, and reread it before each mutation. A
null date or changed terms do not imply the approved due date. Unsupported
collection-method or malformed due-date values fail projection safely.

When supplied at creation, `effective_at` is separately read from the Invoice
as a nonnegative Unix timestamp and compared exactly with the approved printed
issue date. It neither replaces `created`, `due_date`, nor any payment time.
Missing or provider-null `effective_at` is unavailable evidence, not a match;
it leaves finalization pending for correction/review. Retain the requested date
and the independently observed draft line amounts, subtotal, and total in the
immutable external billing version before approval, rather than treating a
mutable Price ref or an action audit as the approved financial fact.

The optional `correlation_key` must be a freshly generated random 32-character
lowercase hexadecimal value for one invoice occurrence. It must not encode
customer names, email, or business content. Creation writes only
`metadata[stackos_correlation]`; arbitrary metadata remains unsupported.
Supplying the expected key to retrieve/list returns `correlation_matches` for
each invoice. Missing or different metadata returns false; without an expected
key the comparison field is absent. All provider rows and the original
continuation cursor remain present: this is a local equality observation, not
a Stripe metadata search/filter or a uniqueness guarantee. The agent checks all
bounded pages, customer/account identity, dates, and the immutable external
proposal before accepting a recovery candidate. Multiple matches are ambiguous.

Dispute output is limited to a typed safe ref, status, reason, amount, currency,
creation time, live/test mode, related charge/payment-intent refs and balance
transaction refs. Provider
evidence bags, metadata, communications, addresses, and documents are excluded.
Provider permissions must include the selected read endpoints; the connector
does not infer a dispute-free invoice from missing access or an incomplete page.

The generic action-call audit records the sanitized action request and
allowlisted response, inline or in a generated response file under the normal
output policy. Because Stripe reads and invoice actions need monetary and
lifecycle evidence, this audit may contain bounded values such as amount,
currency, fee, balance, invoice status, and dates. It remains non-authoritative
transport/recovery evidence: the selected external backend owns the financial
record. No duplicate finance audit or storage layer is added. Field-level
request/response projection does not exist in the current generic executor and
is not claimed by this connector.

## Write recovery and approval boundary

### Recognizing payments already received

The optional `settlement-only` occurrence of
`finance.payment-request-followups` owns payment matching and settlement.
`followup-only` remains the default. Invoice issuance receives no settlement
grant, and a settlement occurrence never sends or resends an invoice.

For a verified direct-bank receipt, the normal route reports a PaymentRecord,
then attaches that same record to one invoice. The report maps the positive
minor-unit amount to `amount_requested[value]`, the currency to
`amount_requested[currency]`, and the customer to `customer_details[customer]`.
It fixes `outcome=guaranteed`, `payment_method_details[type]=custom`,
`payment_method_details[custom][display_name]=Bank transfer`, and
`processor_details[type]=custom`. Observed timestamps map to `initiated_at`
and `guaranteed[guaranteed_at]`; initiation cannot follow guarantee. The bank
reference uses the existing secret-marker boundary and maps only to
`processor_details[custom][payment_reference]`. No raw bank reference, custom
display text, description, metadata bag, payment method, or client secret is
returned. Independent retrieval exposes a reference digest for exact
comparison; use that read after a POST echo rather than retrying a write to
obtain cleaner output.

Missing custom processor details leave the reference digest unavailable.
Explicit `processor_details.custom=null` is also conservatively treated as
unavailable evidence, not asserted to be schema-declared nullable. Neither
observation permits digest matching or a replacement report.

PaymentRecords expose amount-state objects, not a synthetic `paid` status.
Agents independently compare the source evidence, positive amount, currency,
customer, account/mode, guaranteed/received amount, refunds/disputes, and all
relevant invoice allocations. A transfer notice or a customer's claim alone
is not proof of cleared funds. Matching, duplicate-source detection and
financial judgment remain workflow/agent responsibilities, not a new connector
decision engine. The external invoice/payment record retains source identity,
allocation history, immutable proposal/approval, per-operation keys and
current reconciliation state.

An already received Stripe payment instead uses PaymentIntent retrieval plus
charge/refund/dispute evidence and attachment of that existing succeeded
PaymentIntent. Never create or confirm a PaymentIntent to represent money
already received at the business bank. A schema-valid PaymentIntent can lack
optional money/currency observations; the connector preserves their absence.
The agent must hold settlement until matching evidence is available, never
substitute zero or infer an amount from status alone. One payment is attached whole to one
invoice in this release. Partial receipts are supported when the payment is no
larger than the current remaining balance; splits, overpayments, pending,
reversed, ambiguous, mismatched or already allocated payments stop for review.
After each action, re-read the invoice and its InvoicePayments to verify the
specific payment linkage and remaining amount. An invoice marked paid by some
other actor is not proof that this source receipt was applied.

The separate mark-paid-out-of-band action is an explicitly selected full-balance
alternative. It cannot identify a particular external payment or specify an
amount, so the external record must retain that proof. Never combine report
and mark-paid for one receipt, mark a partial deposit fully paid, or fall back
to mark-paid after a report/attach error. Unknown or mismatched balances hold
follow-ups; no new payment, revenue or bank inflow is created merely because
an existing receipt is linked to an invoice.

The actions have three separate technical gates: `owner-payment-record`,
`owner-payment-attachment`, and `owner-external-settlement`. One external owner
decision can cover report and attach of the same immutable receipt allocation,
but each technical gate is recorded independently. Matching the actual
external version and current provider state before every mutation remains
mandatory; the generic gate does not bind payload fields. There is no atomic
cross-system lock, and this package does not expose detach, reversal or refund
as an undo operation.

Restricted-key permissions must cover the selected PaymentIntent/PaymentRecord
reads and invoice/payment-record writes. Missing access is a setup or recovery
condition, not permission to use a different route. No existing credential is
broadened by this source-package change.

### Unknown report or attachment outcomes

Every report and attachment has its own stable operation key. If report
succeeds but attachment fails, preserve the returned record ref and resume
from it; do not report another PaymentRecord. If attachment is unknown,
inspect the invoice's complete payment pages for that exact safe payment ref
before deciding whether anything remains to do.

Before an original report, retain the source identity, immutable operation
parameters/key and exact `payment_reference_sha256` in the external backend.
Hash the payment reference's UTF-8 bytes without trimming or Unicode
normalization. A receipt-file digest is a different field and cannot substitute
for this comparison. The raw payment reference still crosses dispatch only as
the existing payload-secret marker.

For an unknown report, inspect the existing action audit, response files and
external record first. Retrieve a surviving safe PaymentRecord ref.
`payment-records.list` is temporarily unavailable in StackOS and rejects before
provider dispatch; do not attempt pagination. If no verified ref survives, hold
this lost-reference branch for owner/provider resolution. Retained audit and
known-ref retrieval remain usable. Do not retry the unavailable action, guess a
replacement URL/version, broaden keys or silently switch settlement routes.

Compare the exact retained reference digest, account/live-test mode, customer,
currency, received and guaranteed amounts, refund/failure/cancellation state,
and all relevant allocation evidence. Independently retrieve the surviving
record, persist its verified safe ref under the external single-writer
contract, then resume only the still-needed separately approved attachment.
A digest or matching amount alone is not identity or bank-custody proof.
PaymentRecord retrieval does not expose global invoice allocation history;
reconcile the external allocation record and relevant complete InvoicePayments pages, and
hold when their coverage cannot establish the selected source is eligible.

Missing refs, ambiguous identity, missing digest or uncertain evidence remain
unresolved and never prove the report did not happen. Do not
make a replacement report or fresh key from those states. Optional exact
request/key replay remains possible only inside a verified safe window shorter
than Stripe's minimum 24-hour retention. A cached error, uncertain key age,
expired window or unresolved match requires owner/provider resolution. Reads
have no owner-mutation gate; the existing report and attachment gates are
unchanged, and no credential permission is broadened automatically.

### Shared write rules

An agent must supply a distinct deterministic, non-sensitive key for each
distinct intended `POST`; a retry uses the same key and identical parameters.
The key must not contain an email address or other personal identifier. Stripe
stores the first result (including a `500`) for a key for at least 24 hours.
After that retention window, a pruned key can execute as a new request, so the
agent must reconcile through an authoritative retrieve/list action instead of
blindly replaying it.

A send returning successfully proves Stripe accepted that explicit send
request. With `livemode=false`, Stripe emits no email: workflow summaries use
`test-accepted`, retain the normal approval/action proof and record no real
customer contact, live reminder cooldown or live-delivery handoff. Live-mode
acceptance does not prove recipient delivery. An invoice retrieval returning
`open` cannot resolve an unknown send because that status can exist both before
and after sending. Keep the send outcome unresolved until provider-specific
evidence resolves it; do not generate a replacement operation key.

Stripe's `Stripe-Should-Retry: false` stops further read attempts before
dispatch; `true` permits only the existing bounded read retry loop. Missing or
invalid headers use the established status policy. Other providers retain
their existing policy, and the header never authorizes an automatic write retry.

HTTP 409 or `idempotency_key_in_use` returns `idempotency_conflict`;
`idempotency_error` returns `idempotency_mismatch`. For a POST, both preserve
`outcome_unknown=true` and `retry_safe=false`: the original operation may still
be running or may have used different parameters. Retain and compare its exact
key/parameters/audit, reconcile the original write and never use a fresh key to
bypass the conflict.

StackOS intentionally does not perform automatic `POST` transport retries. If
a timeout, connection failure, or `5xx` makes an action outcome ambiguous, the
action returns `outcome_unknown=true` with recovery guidance. For a known
invoice, retrieve the invoice before any further operation. For a create where
there is no target reference yet, reconcile from an authoritative bounded list
using the workflow's non-secret business evidence, or—only while the key is
inside Stripe's retention window and the provider recovery explicitly permits
it—replay the exact same Stripe idempotency key with identical parameters.
Never issue a new mutation with a new key merely to check whether the first one
succeeded.

`action.run` still requires the generic direct-write confirmation, and
workflow execution still requires the run-step grant. Finance workflows add
the human approval gates around invoice finalization/send, payment recording,
attachment, external settlement and exceptions. This
connector neither approves nor bypasses them.

The StackOS action-level gate is bound to the action ref, not every Stripe
payload field. The selected external backend therefore retains the
business-scoped approval record identifying the intended safe customer/invoice
ref, recipient, terms, and decision. Before execution, the agent must match the
concrete action input to that record; a technical approval status alone is not
payload authorization. See
[`plugins/finance/references/approval-matrix.md`](../../plugins/finance/references/approval-matrix.md).

One external owner decision may authorize both finalization and send for the
same reviewed invoice version. The agent still records the two distinct
technical gates; approving finalization alone leaves send pending. A material
proposal/recipient/terms change requires a fresh approval occurrence/run with
pending gates. The runtime does not automatically revoke an approved gate or
bind its decision JSON to payload fields. Existing grants and approval checks
remain mandatory.

A failure projecting the response or persisting a provider object reference
after HTTP success is a post-dispatch failure. Stripe POST errors retain
`outcome_unknown=true` and `retry_safe=false`, even when the failure is local;
raw exception/provider content is not exposed. Recovery checks the prior audit
and provider state. Successful idempotent replays can return the original audit
call from an earlier run; audit scope alone is not proof of the current invoice
version or external workspace write.

## Explicit non-goals

- No production credential, customer, invoice, email, payment, refund, payout,
  transfer, tax remittance, filing, or webhook is exercised in engineering
  verification.
- No refund creation, payment-intent creation/confirmation, charge creation,
  transfer, payout, dispute mutation, credit note, subscription, tax calculation, or
  webhook ingress action is exposed.
- No StackOS finance domain table, resource, ledger, repository, or filesystem
  connector is introduced. Existing generic provider-reference and action-audit
  primitives remain platform infrastructure, not finance records.
- No workflow takes a raw Stripe object id or tries to infer customer,
  bookkeeping, tax, collections, payment, or approval policy from a connector
  response.

## Verification evidence

Fixture-only tests must prove the pinned version/header/auth shape, POST
idempotency forwarding, no automatic mutation retry, sanitized 4xx/429/5xx and
transport errors, opaque reference output/resolution, one-page continuation,
account probe persistence, grant denial, and failed action-call audit rows.
Live Stripe verification is an explicit operator/release follow-up, not part
of this delivery.

Recovery fixtures additionally prove invoice/customer/date query mapping,
correlation matches without metadata disclosure, invoice-item pagination and
description privacy, dispute scope and safe output, explicit manual
finalization, and preserved ambiguity after post-HTTP normalization failure.
MCP fixtures prove a granted dispute read, separate finalization/send gates
using one external decision, and pending gates for a revised occurrence.
Settlement fixtures additionally cover fixed no-charge/report forms, exclusive
safe payment selectors, PaymentIntent/PaymentRecord privacy and malformed
critical fields, partial/full allocation readback, unknown-result recovery,
and the three distinct settlement action gates with fresh pending approvals
for a revised occurrence. Provider transport is mocked throughout; source
checks do not establish real bank custody or account-specific Stripe readiness.

PaymentRecord-list deferral fixtures cover discovery/description, validation,
direct execution and valid step grants: the unavailable reason survives, no
HTTP request or ActionCall is created, and executable actions keep their
connector bindings. Optional deferral must not block ordinary follow-up
readiness. Known-ref PaymentRecord reads retain reference-hash/privacy and
projection coverage. Actual follow-up MCP fixtures cover retained-ref recovery
and the missing-ref hold without replacement reports; they must not silently
re-enable listing to claim workflow success. Installed activation remains
separate from isolated source/mock verification.

Diagnostic regressions exercise provider errors through an enabled read in
repository execution and compact/raw MCP, preserving the message and correlated
log link in failed action audit. Historical list404 evidence is not rewritten
as a success. Malicious/private message tails, unsafe or mismatched links,
missing request ids and repeated normalization retain the no-secret boundary.

The full action audit uses OpenAPI commit
`9ac29c7795ab21c7711b4bc25bb2dd739552a5fa`, snapshot SHA-256
`2c31317cdff103e4495b5b3501004d9ddc0af61f43b0ab819e2db392eef008f6`.
The earlier transport audit checked 30 declared method/path pairs against that
snapshot. Customer/invoice updates and the private PDF download/cleanup actions
plus customer tax-ID list/create/retrieve bring the current inventory to 37
declared actions, 36 executable; PaymentRecord
listing remains explicitly deferred. PDF download composes the existing invoice
GET with its provider-returned document URL; cleanup is local, not a new Stripe
endpoint. The new paths have focused mocked transport, manifest and MCP tests.
Red-first repairs cover
retry veto/idempotency conflicts, explicit nondefault invoice currency,
catalog selection/Price expansions, effective issue-date readback, required
observations, missing allocation linkage, and typed reconciliation refs. Public
MCP tests check structured errors and failed/success action audit;
actual-template fixtures check the agent-facing currency and recovery paths.
Mock/source correctness does not establish endpoint availability or live email
delivery. Independent reviewers cover transport, action contracts and guidance.
These use mocked provider responses, not a claim that the live route is fixed.
