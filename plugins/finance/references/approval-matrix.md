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
| Follow-ups and received-payment settlement | Read lifecycle/history and prepare the instructed Stripe resend or authored email; alternatively prepare a received-payment match/allocation. | Follow the operator's instruction or documented sending policy. Existing Stripe/settlement gates remain; no additional email approval gate. |
| Cashflow | Prepare sourced rollforwards and scenarios with visible uncertainty. | Ask for material assumptions; no payment authority. |
| Tax estimates | Gather annual facts/current sources, prepare estimates or incomplete packet. | CPA/EA then owner approve adoption of the same packet version. |

## Invoice approvals

Keep `owner-invoice-finalization` on `finance.stripe.invoices.finalize` and
`owner-invoice-send` on `finance.stripe.invoices.send`.
The owner may approve both once. Record that same external decision under both
gates when it explicitly covers both finalization and initial send. The agent may
record an existing explicit owner decision through `runPlan.update`, including
a verbal or conversation approval already retained in the external finance
record. Recording the owner's decision is not making an approval decision.
Do not ask the owner again when the exact approved scope remains unchanged.

Use the existing `run_plan_id`, `approval_key`, `approval_status`, `decided_by`
and `decision_json` arguments; identify the owner in `decided_by` and keep
`decision_json` limited to safe existing external decision/evidence refs and
the original decision time when needed. Preserve the actual actor, original
decision time, scope and evidence in the external record. The technical gate's
recording time must not replace that source history. Reuse the same decision
refs for both covered gates; no new approval handoff, record format or owner
conversation is required. Independently verify existing evidence as the
workflow requires, then read back the recorded gate before execution.

Agents never self-approve: they record the owner's explicit decision, not their
own judgment. An agent must not invent an owner decision, infer approval from a
standing policy or broaden its scope.
Recording approval does not execute an action or satisfy missing recipient,
payment-presentation, provider-state or delivery evidence. Those checks remain
separate, and a material scope change still requires a fresh owner decision.

The immutable proposal covers account/customer/invoice, expected customer
billing-detail digests when material, currency, every line,
description hashes or selected Price refs/quantities, terms, optional printed
issue date, payment presentation when applicable, subtotal and total when known.
For direct-deposit-only delivery, bind the exact footer hash, Stripe payment
method list or null, and externally reviewed Pay online link state/evidence to
the immutable version. Stripe's payment-method readback does not establish link
visibility; an unknown or visible link blocks send. A catalog request may be prepared
before a quote with named amount/total gaps, but approval/finalization requires
the independently reread current Price terms/currency and draft line amounts,
subtotal and total captured into a new digest-bound version. `effective_at`
must match the readback exactly when requested; a missing or null provider value
does not match. Independently review it, then reread and compare immediately
before each mutation.
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

The [external delivery evidence contract](local-workspace-contract.md#payment-presentation-proof-and-delivery-reconciliation)
defines deterministic presentation checks and reconciliation of a prior manual
Dashboard no-link send. Manual completion requires an exact-version owner send
decision recorded before delivery, every recipient, and independently verified
retained delivery evidence. Reconciliation performs no send and records no
API-send action call. A manual choice or uncertain result remains unresolved.

## Follow-up approvals

Sending instructions do not replace checking current state: not due, paid/zero,
void, uncollectible, pending/processing, disputed, paused, corrected, active
promise, cooldown/recent contact or another reminder owner.
Compare the exact decision version and reread immediately before send.
Changed state yields no-send or a fresh decision, not automatic approval reuse.
An open invoice does not prove delivery; unknown outcomes stay unresolved.

The same billing/collections agent handles both routes. Follow the operator's
current instruction or documented settings for channel, account, recipients and
wording; ask only when these leave a material ambiguity. Do not guess a channel
or silently substitute another. Normal operation assumes a live Stripe account.

`stripe-resend` uses `finance.stripe.invoices.send` and its existing
`owner-followup-resend` gate. `smtp-email` uses
`communications.smtp.email.send` with the normal active-step grant; it adds no
separate approval interview. IMAP reads replies, SMTP sends. Keep the selected
route/account and actual action `input_json` in `collection_decision.outreach`
in the same `finance.json`; use the connector's current input contract rather
than inventing finance-specific email restrictions. Existing review/digest and
attempt records apply to either route.

Record an existing explicit owner decision for the particular resend or
settlement action through the same `runPlan.update` path without another
approval conversation when its exact scope is unchanged. An initial-send
approval does not authorize a resend. A decision must explicitly cover each
requested action and occurrence; unchanged invoice content alone is not
permission for repeated customer contact or a different settlement action.

Check invoice state and recent contacts/replies together so the two channels do
not duplicate a follow-up. When IMAP evidence is needed, use the existing
[host handoff](imap-host-handoff-contract.md) to retain the original and inspect
reply headers; a preview is not complete reply evidence. No mailbox
acknowledgement is needed for follow-up review.

Record the actual result: SMTP acceptance is not inbox delivery; partial
acceptance identifies recipients already contacted. Resolve an uncertain send
before retrying or switching channels—an open invoice alone cannot prove email
failure. Settlement-only remains recording work, not a sending instruction.

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
