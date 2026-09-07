# Backend-Neutral Finance Workspace Contract

## Ownership

The selected external backend owns financial facts, original evidence,
customer records, approvals, calculations and correction history. The initial
backend is `local-json`: host-agent-managed `finance/finance.json` is the single
authoritative local financial document; `FINANCE.md` is guidance/navigation,
and `attachments/YYYY/MM/` holds immutable source evidence. CSV is import/export
interchange, not another master. The colocated JSON Schema defines structure,
not a second data store. QuickBooks or Google Sheets support is future work;
no other backend is implemented by this package.

The JSON authority includes mutable business profiles, operating settings,
account/customer mappings, policy selections, approvals, financial facts and
corrections. StackOS stores only safe selectors/refs and generic control state.
Provider-owned state remains authoritative at the provider; JSON stores dated
observations, never a claim that a local edit changes an invoice or bank balance.
Markdown reports and CSV exports identify their source JSON revision and record
IDs. Editing them does not update the financial record. Resolve conflicts from
original/provider evidence through a reviewed JSON correction, never silently
prefer a report or synchronize two masters.

## Business and project scope

Run from a standalone business directory or an agency root using the existing
finance workflows and one selected external financial master per business.
Reuse that master across client engagements; do not create a finance.json in
each project folder. Resolve its safe workspace selector from the effective
finance extension and its actual settings from the external backend. A navigation
pointer in agency context is not a second configuration owner. Host-file access
and provider connections must be available in the bound finance-owning scope;
neither an agency association nor a folder relationship supplies that authority.

Finance is independently usable: an agency and its projects are optional
context, not prerequisites or a second financial master. When supplied,
`external_project_ref` is a non-empty opaque, source-backed nonfinancial project
identity. It may appear only on a bookkeeping record or a billing version; it
is not a StackOS project ID or a local financial-record ref. It is
not an authority grant.
The host verifies the reference against the supplied agency resource or
operator/external source and its ownership before accepting it. That check never
requires discovering, reading, or operating another StackOS project: the ref
creates no automatic cross-project access and no project registry or mandatory
mapping in finance.

Omit the field for company-wide work. When the project is unknown or
multi-project, leave it absent with an explicit gap. Never infer attribution
from a customer mapping: the same customer can have multiple projects.
Attribution is a single optional label,
not a split allocation, project profitability engine, shared-cost allocation,
or copied financial amount. Do not put it on receipts, billing lines,
settlements, cashflow, tax packets, or provider observations.

StackOS must not own finance-domain tables, repositories, resources,
private evidence storage, a filesystem connector, financial ledger or tax
engine. Existing workflows, grants, credentials, approvals and audit provide
control. Workflow/tracker results contain safe external refs and bounded status,
never customer text, receipt bytes or financial calculations.

The existing generic action executor persists a sanitized request/response
envelope. It may contain bounded monetary/lifecycle fields needed for transport
and recovery: non-authoritative transport evidence, not books. Customer text
and raw credentials remain excluded. Stricter field-level audit projection
would be a separate shared executor capability; no new audit store is added.

## Setup and execution

Set up shared workspace/custody choices once, then collect only the durable
inputs needed by the selected workflow and route. Resolve confirmed defaults
from their external owner instead of repeatedly asking for reference strings.

| Selected work | Applicable setup and current inputs |
| --- | --- |
| Receipt intake | Workspace/custody, sole writer and safe source route; IMAP additionally needs verified TLS and readable staging. No tax profile, advisor, bank statement or accounting basis is a receipt prerequisite. |
| Bookkeeping | Period/account register, statements or exports, coverage and approved category/business-purpose rules. Missing basis limits basis-specific conclusions, not source preparation. |
| Payment request | Customer/account mapping, explicit approved invoice/line currency, invoice terms, recipient settings and exact current request. No household tax inputs. |
| Follow-ups | Current invoice/history, reminder timing/owner/exclusions and recipient settings when sending. Settlement-only instead needs the selected received-payment source and allocation; it does not require send-recipient setup. |
| Cashflow | Dated opening cash, expected receipts, commitments, horizon/scenario conventions and reserve source or explicit uncertainty. No complete tax packet prerequisite. |
| Tax preparation | Relevant annual business/taxpayer inputs and current sources. Legal form is separate from federal/California tax treatment/effective dates; qualified review is required before adoption, not source gathering. |

A run supplies only its current choice: period, billing request, review window
or as-of date. Optional safe overrides replace a selected setup default.
Missing applicable setup produces one consolidated decision list while
independent preparation continues. Unrelated unset template fields do not block
the run. Reuse current verified defaults; recheck changed or uncertain facts,
not every previously answered question on every invoice or receipt.

Source packets, completeness assessments, proposal versions, reviews and write
proofs are produced by workflow steps, not invented prerequisites. An advisor
is needed at review, not before useful preparation begins.

## Logical record contract

Every record has a stable `record_id`, timestamps and additive status history.
A source/provenance block names source kind/ref, observed date, safe account/
provider refs, transport identity and `content_sha256` (SHA-256) for bytes.
Do not silently replace observations with inferences.

For `local-json-v1`, use the typed collections and common fields in
[`finance-v1.schema.json`](../templates/finance-workspace/schemas/finance-v1.schema.json).
Packets are logical groups/versions of records in that same document, not
independent editable packet files. Use integer currency minor units with an
explicit currency on every monetary value. Omit unknown values with a named
gap; do not substitute zero. Keep proposed, observed, reviewed and adopted
states distinct. Schema validation checks shape; the host also checks identity,
reference resolution, immutable versions, arithmetic, provenance and custody.

| Record | Minimum useful contents |
| --- | --- |
| Source coverage | Account, expected/available window, opening/closing evidence, gaps and observation date. |
| Receipt/evidence | One source and one or more receipt records; original paths/hashes/bytes, extracted facts, duplicate links and quarantine. |
| Bookkeeping | Amount/currency/date, category proposal/applied rule, confidence/reason, source and reconciliation links, prepared/unposted status, and optional source-backed `external_project_ref`. |
| Billing version | Immutable customer/mapping, complete lines, currency, terms, total, recipient-settings version/scope and verification, optional source-backed `external_project_ref`, version/digest, provider refs and mutation attempt keys. |
| Collections and received-payment settlement | Invoice lifecycle/due status, dispute, reminder owner, contact/cooldown/promise/pause, decision version, approval and actual send outcome. For a received payment, retain one source identity, customer/invoice/account match evidence, currency/received state, one allocation, selected route, provider refs, recovery key and external write/readback proof. |
| Cashflow | 13-week base/downside cash rollforwards, source/assumption refs, reserves separate from actual payments, unknowns. |
| Tax packet | Annual business/household inputs, current sources, applicable obligations, calculations, payments/withholding, annual liability versus installment and review state. |
| Review/approval | Exact record/version/digest, authority, scope, decision/time and reason. |
| Write proof/handoff | Changed refs, prior/new revision, reread result, destination/conditions and stable handoff_id. |

Use fields on the relevant record rather than a new record family for every
check. Details and numbers remain external.

## Duplicate handling and retries

Compare transport identity for retries, binary hash across sources, then
semantic evidence across representations. Similarity is a review candidate,
not permission to merge. One email may create multiple receipt records sharing
the retained original. A verified duplicate adds provenance.

Changed content under an existing identity, missing originals or stale revision
remains an exception. Preserve supported siblings and continue independent work.
No acknowledgement precedes verified custody.

Provider writes use stable per-operation idempotency keys. Inspect actual
action audit and provider state after an unknown outcome; do not create fresh
keys to bypass uncertainty. Correlation keys are random opaque identifiers,
never customer or invoice text.

An `idempotency_key_in_use` conflict can mean the original request is still
running. A conflict for different parameters requires comparing the original
approved request, key and retained audit. Reconcile that original attempt before
any permitted same-key retry; neither conflict permits a fresh key or modified
replay to bypass duplicate protection.

Received-payment settlement uses the same rule with an additional source
identity and allocation boundary. A verified duplicate is additive provenance,
not a fresh report or attachment. Match the current source, account/customer,
invoice, currency, received state, and one allocation before any provider
write. Preserve unsupported or unsynchronized partial, split, overpayment, mismatch, stale-record, and
unknown-write states as exceptions; do not turn them into a credit, refund, or
inferred paid invoice.

For a direct bank transfer, the normal Stripe route is report and attach of one
PaymentRecord to one invoice; it does not move money. A separately selected
paid-out-of-band mark is available only when a current read proves one verified
external source exactly settles the full remaining invoice balance. Never
report and mark the same source. Before the original report, persist the exact
UTF-8 `payment_reference` SHA-256 as `payment_reference_sha256` externally,
alongside the source and stable operation key. Do not normalize the reference
or substitute a receipt-file/content hash.

For an unknown report, inspect retained action/response-file evidence first,
then retrieve a surviving known ref. `finance.stripe.payment-records.list` is
temporarily unavailable in StackOS pending a verified provider-availability fix:
the tested sandbox returned an unrecognized-route 404 despite the published
contract. It is explicitly deferred and rejects before provider dispatch; do
not retry it, change credentials, or guess another URL/header. If no verified
ref survives, hold lost-reference recovery for owner/provider resolution. Never
create a replacement report, new key, or paid-out-of-band fallback to escape
that hold. This optional recovery action does not block ordinary follow-ups or
known-ref settlement.

For the retrieved record, match the exact reference digest, customer, currency,
guaranteed amount and other amount states against external received-payment
and current allocation evidence. PaymentRecord `amount` is the intended
collection amount, not independent received-funds proof. Persist the verified
ref before attachment. Missing digest, ambiguous identity, changed facts or
unverifiable allocation remain unresolved; absence of a result never proves
the report did not happen. Exact same-key replay is an alternative only within the
verified retained window shorter than 24 hours under this workflow; otherwise return
the precise owner-resolution packet. Financial values and payment references
remain in the external record; StackOS receives only safe refs/status.

## Corrections and reconciliation

Corrections are additive: original/superseded ref, reason, actor/time and new
facts, plus approval when a material business decision requires it.
`prepared/unposted` is not a ledger posting or closed accounting period.
Correct a bookkeeping attribution through that existing additive correction
pattern: preserve the original record, create the supported replacement record,
and retain a `corrections` entry from superseded to replacement ref with its
reason. A consuming attribution view resolves that relationship before totals;
it must not count both records, mutate the original, or invent split amounts.

Reconcile bank/card windows and balances as well as Stripe. Separate gross
receipts, fees, refunds and transfers; do not count an invoice, payment and
payout as three revenues. A partial packet is useful but cannot be called fully
reconciled. Downstream consumers carry its limitations.

Use typed reconciliation links when present: BalanceTransaction `source_ref`
with `source_state=available` and supported `source_type` charge/refund/dispute,
Refund `failure_balance_transaction_ref`, and Dispute `balance_transaction_refs`.
Respect `balance_type` when reconciling balances. Missing, null, unexpanded or
unsupported source observations remain explicit evidence gaps; do not infer a
source from equal amounts or count an unresolved row as reconciled. InvoicePayment
`payment_ref_state=missing` or `null` preserves available allocation facts and
pagination, but missing payment linkage cannot identify a payment, establish no
payment, or prove settlement. Require an available exact link for that match.

Invoice creation and every line must explicitly use the approved currency; do
not inherit a provider default or assume a later line changes the draft currency.
For sandbox/test-mode sends (`livemode=false`), Stripe sends no email. Retain
`test-accepted` rather than sent/delivered, with the usual action/approval proof.
Do not create a real customer contact, live reminder cooldown or live invoice
handoff from test acceptance. Live-mode send acceptance is not inbox-delivery proof.

Do not automatically resend an invoice in the same occurrence after any
settlement work. A synchronized supported partial remains an open balance but
needs a later policy-scoped follow-up decision. An unsynchronized partial,
split, overpayment, mismatch, duplicate uncertainty, or unknown settlement
write suppresses follow-up until it is resolved.

## Approvals and cross-workflow handoffs

External approval records own exact business scope. In the JSON backend a review
binds the target record ID/version/material digest and relevant versioned
dependencies, not the whole-file revision/hash. The latter protects concurrent
writes. An unrelated receipt changes the document revision, requiring reread and
rebase, not a new invoice approval. Material changes to the approved facts still
require a new proposal and decision. Existing StackOS gates are
action/step scoped and do not verify every payload field. The agent compares
the current object with the approved proposal before execution.

One owner decision may cover finalization and initial send of one invoice
version while filling two technical gates. Material change requires a new
decision/new occurrence with pending gates and preserved superseded history.
Tax adoption follows advisor then owner approval.

Settlement approvals are separate from invoice finalization/send and bind the
current external source identity, source/account/customer/invoice match,
currency/received state, allocation, selected route/action, and external
record revision/digest. Reporting a PaymentRecord, attaching a payment, and
marking an invoice paid out of band each need their own existing StackOS
technical gate and matching owner approval occurrence.

A `handoff_id` names the source packet version and conditions. Handoffs propose
next work, never recursive launches. Tax uses annual inputs independently of
cash forecasts. A reviewed tax packet refreshes a forecast reserve once per
version.

## External custody

The host owns local writes. Follow
[local-workspace-contract.md](local-workspace-contract.md) and
[imap-host-handoff-contract.md](imap-host-handoff-contract.md).
The operator owns backup, restore, retention, access permissions, encryption,
deletion and disposal. No credentials belong in the workspace.
