# Finance Workflow Approval Matrix

The external record owns exact version/digest, scope, actor/time and decision.
StackOS owns the existing technical gate. An action-level gate precedes that
action; a step-level gate controls that step. Neither binds every payload field.

In `local-json`, these are `reviews` records in `finance.json`, linked to the
exact proposal and versioned material dependencies. Match `target_version` for
versioned targets (including settlement proposals); ordinary unversioned
preparation records bind by ID and material digest without inventing a version.
A proposal digest
does not include whole-document revision/hash, unrelated records or subsequent
action observations. File revision/hash protects the sole writer from lost
updates; re-read/rebase after any change. Only a material change to the approved
scope invalidates that approval. Keep billing versions immutable and record
provider observations, mutation attempts and review decisions separately in the
same JSON document. Never keep a second approval master in Markdown or a packet
export. See the [digest protocol](local-workspace-contract.md#versions-and-digests).

| Workflow | Routine agent work | Decision boundary |
| --- | --- | --- |
| Receipt intake | Retain, extract, deduplicate, quarantine and verify custody. | Ask only for material ambiguity; continue unrelated items. |
| Bookkeeping preparation | Propose sourced categories, match, reconcile and preserve gaps. | Owner resolves missing business purpose or material corrections; no routine row approval. |
| Payment request | Resolve customer, prepare/recover complete draft, compare proposal. | One owner decision can cover exact finalization and initial send, filling both technical gates. |
| Follow-ups and received-payment settlement | Read lifecycle/history and suppress ineligible cases; prepare one received-payment match/allocation and digest. | Exact resend needs owner-followup-resend. Report, attach, and paid-out-of-band settlement each use their separate owner gate; one invoice/source allocation per mutation occurrence. |
| Cashflow | Prepare sourced rollforwards and scenarios with visible uncertainty. | Ask for material assumptions; no payment authority. |
| Tax estimates | Gather annual facts/current sources, prepare estimates or incomplete packet. | CPA/EA then owner approve adoption of the same packet version. |

## Invoice approvals

Keep `owner-invoice-finalization` on `finance.stripe.invoices.finalize` and
`owner-invoice-send` on `finance.stripe.invoices.send`.
The owner may approve both once. Record that same external decision under both
gates through the existing authorized approval path; agents never self-approve.

The immutable proposal covers account/customer/invoice, currency, every line,
description hashes, terms, subtotal and total. Independently review it, then
reread and compare immediately before each mutation.
Material changes require a fresh decision/new occurrence with pending gates,
preserving the superseded record. Never reuse an approved run for another invoice.

Compare both current_customer_email_sha256 and invoice_customer_email_sha256
with the exact approved primary email. Stripe exposes primary-email-fields-only;
Dashboard extra To/CC billing recipients need an external verified settings
scope. Missing evidence or a mismatch blocks send/resend, preserving preparation.

Keep that verification on the existing external billing record: approved primary
email and exact UTF-8 hash; additional To/CC addresses or explicit `verified-none`
(never an empty field treated as none); applicable account/customer/invoice scope;
verifier, verification time and source evidence; validity/recheck condition; and
the recipient-settings version bound to the immutable request/decision version.
`unknown` is not approved. One customer-level setting may cover multiple invoices
only where its recorded scope and current evidence do so.

Before each send/resend compare both provider primary hashes and confirm the
external settings evidence still applies. Reuse current verified settings without
reinterviewing the owner per invoice. Expired evidence, account/customer changes,
Dashboard or invoice recipient edits, a settings-version change or uncertainty
invalidates that reuse: reverify and obtain a fresh approval occurrence for any
materially changed recipient scope. Do not claim permanent API visibility or
cache an external observation forever. Settlement-only does not send and needs
no recipient-setting approval.

## Follow-up approvals

`owner-followup-resend` cannot override fresh suppression: not due, paid/zero,
void, uncollectible, pending/processing, disputed, paused, corrected, active
promise, cooldown/recent contact or another reminder owner.
Compare the exact decision version and reread immediately before send.
Changed state yields no-send or a fresh decision, not automatic approval reuse.
An open invoice does not prove delivery; unknown outcomes stay unresolved.

## Received-payment settlement approvals

Use the existing distinct action gates: `owner-payment-record` for
`finance.stripe.payment-records.report`, `owner-payment-attachment` for
`finance.stripe.invoices.attach-payment`, and `owner-external-settlement` for
`finance.stripe.invoices.mark-paid-out-of-band`. They are separate from
`owner-followup-resend`; a settlement-only occurrence has no resend authority.

The external approval must bind the current external source identity, source/
account/customer/invoice match, currency and received state, one allocation,
selected route/action, and external record version/digest. The control reviewer
checks that exact evidence but is not an approver. Material source, allocation,
route, account/invoice, currency, received-state, or record-version change
requires a fresh owner occurrence.

The normal direct-bank route is report then attach. Paid-out-of-band is an
explicit alternative only for one verified source that exactly settles the
current full remaining invoice balance; never report+mark a source. Partial
direct payment needs an exact supported report-and-attach allocation; split,
overpayment, mismatch, stale-record, duplicate uncertainty, or unknown write
stops for recovery/owner decision and suppresses resend. Unknown report recovery
follows the [backend contract](backend-contract.md#duplicate-handling-and-retries):
retain the exact payment-reference digest before reporting, inspect retained
action audit/response files, then independently retrieve a surviving known ref
and verify current allocation evidence before the separate attachment gate.
PaymentRecord listing is temporarily unavailable in StackOS; a missing verified
ref holds recovery for owner/provider resolution, not a retry or alternate route.
Missing, ambiguous or incomplete evidence never authorizes a replacement report.
Same-key replay requires the verified retained window,
strictly shorter than 24 hours; uncertainty otherwise returns to the owner.

## Tax review

Preparation can proceed without a selected advisor. Complete packets await
review; incomplete taxpayer data remains visibly incomplete.
`cpa-ea-tax-review` precedes `owner-tax-packet-approval` for the same packet.
Source review alone is not calculation approval. Changed packets require new
review. No filing, payment, payroll or election action is granted.

## Existing audit

Inspect actual action calls and provider outcomes. Reference strings or output
schema success do not prove an action occurred. Existing permissions/audit
remain the runtime boundary; no action-evidence DSL or finance state is added.
