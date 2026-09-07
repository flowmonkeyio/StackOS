# Finance Operations Plugin

The finance plugin packages a small, agent-operated finance department for a
US solo business. It coordinates receipt intake, prepared bookkeeping, Stripe
invoicing, collections follow-up, cashflow planning, and tax-estimate packet
preparation. It deliberately does **not** make StackOS the finance system of
record.

Financial records, original evidence, approval records, invoice details,
forecast contents, and tax calculations stay in the selected external backend.
The first backend is the host-managed local JSON workspace described in
[the backend contract](references/backend-contract.md) and
[local workspace contract](references/local-workspace-contract.md). StackOS
provides reusable workflows, daemon-held provider authentication, scoped action
execution, technical approval gates, safe references, and the existing generic
action audit. That audit is non-authoritative transport/recovery evidence; it
may retain bounded sanitized provider lifecycle or monetary fields under the
generic executor contract, but it is never the finance record.

## Initial external backend

The initial `local-json` backend is an external, host-owned workspace:

```text
finance/
├── finance.json                 # sole authoritative local financial data
├── FINANCE.md                   # operating guide and navigation
├── schemas/
│   └── finance-v1.schema.json    # structural contract, not financial records
└── attachments/
    └── YYYY/
        └── MM/
            ├── rcpt_<stable-id>-original.eml
            └── rcpt_<stable-id>-receipt.pdf
```

During authorized setup of a **new** workspace, copy the
[JSON template](templates/finance-workspace/finance.json), its
[schema](templates/finance-workspace/schemas/finance-v1.schema.json), and the
[operating guide](templates/finance-workspace/FINANCE.md), preserving their layout.
`finance.json` owns all local financial facts, sourced business settings,
proposals, approvals, corrections and relationships. It starts at revision zero
with empty collections, not invented business data. Markdown is guidance or a
derived explanation; CSV is import/export interchange, never a second editable
master. Original receipts, statements and emails are retained evidence. Stripe
still owns actual Stripe state; JSON retains dated observations and provider refs.

Use one designated host writer: specialists return proposed record changes,
the writer validates schema and semantic checks, checks the current revision,
atomically persists the document, and re-reads it. Follow the original-evidence,
hash, deduplication, correction, and custody rules. The host agent has the filesystem
authority for that directory; StackOS has none. QuickBooks, Google Sheets,
bank feeds, and another authoritative backend are future work, not aliases for
this local format. Initialize this first-version workspace once; subsequent runs
read and update the existing JSON rather than copying the empty template again.

Resolve safe durable defaults once through project setup: backend/workspace and
policy refs, selected provider account refs, recording mode, and source routes.
StackOS may retain those safe selectors; the selected policy content, sourced
business facts and mutable financial settings live only in `finance.json`.
Collect those defaults only when applicable to the selected workflow/route; use
the [setup matrix](references/backend-contract.md#setup-and-execution). Receipt
custody does not require tax, advisor or bank inputs. Missing current-work facts
produce one consolidated question list while supported preparation continues.
The external business profile keeps legal form separate from federal/state tax
treatment: an LLC can have an S-corporation election. Do not ask the owner to
re-enter setup on each run or invent missing profile facts. Each occurrence
supplies the current goal/scope and route choices; the agent creates its source
assessment, proposal versions, review packets, and recovery records while working.
`prepared/unposted` never means a general-ledger posting, closed
period, settlement allocation, filing, or remittance.

## Workflow package

### Standalone or agency-root finance

Finance works without an agency. Start from the business directory and set up
only the selected finance workflows. An agency may first use
[`agency.setup` and `agency.project-setup`](../agency/README.md) to retain minimal
nonfinancial agency/client-engagement context, then separately authorize finance
setup at the agency root. The six finance workflows and specialists are the same.

Keep one selected `finance/finance.json` per business, shared across its client
engagements—not one financial master per client or project. The main agent can
run from the agency directory without entering every client project. A descriptive
association grants no cross-project MCP access, credential inheritance, or host
filesystem access. Existing safe workspace refs select the financial master;
financial facts and mutable settings remain there.

Optional `external_project_ref` belongs only to a sourced single-project billing
version or prepared bookkeeping record. It points to a verified nonfinancial
identity from agency context or an operator/external source; agency is not
required. Same customer does not imply same project. Company-wide costs stay
unassigned; uncertain/shared attribution stays absent with a gap. There is no
automatic cost allocation or profitability engine. See the
[business/project scope contract](references/backend-contract.md#business-and-project-scope).

| Workflow | Job and safe completion boundary | Provider actions / gates |
| --- | --- | --- |
| [Receipt intake](workflows/receipt-intake.yaml) | Retain originals, extract one or more receipts per source, detect duplicates, and isolate item exceptions. | IMAP transport is optional; store and re-read before acknowledgement. Chat uploads are supported host inputs. |
| [Bookkeeping close](workflows/bookkeeping-close.yaml) | Categorize supported transactions, match evidence, reconcile available source balances, and retain a `prepared/unposted` packet with explicit gaps. | Bank/card exports and other source records come from the external backend; receipts alone cannot establish complete books. Optional Stripe reads support reconciliation. |
| [Payment request](workflows/payment-request.yaml) | Check customer mapping and exact line terms, create/recover one draft, finalize and send, then record the result. | One explicit owner decision may cover both distinct action gates for the same immutable invoice. Material changes require a fresh approval occurrence. |
| [Payment follow-ups](workflows/payment-request-followups.yaml) | Review existing invoice state and, only when eligible, resend via Stripe; or reconcile one customer payment already received. | Followup-only remains the default. A settlement-only occurrence can report/attach one direct-bank or separately received Stripe payment, or explicitly mark one exact full verified external settlement paid out of band; it never sends a reminder. |
| [Cashflow management](workflows/cashflow-management.yaml) | Produce 13 dated base/downside weeks: opening cash, expected receipts, actual cash outflows, closing cash, earmarked reserves and spendable cash. | A reserve is not itself a bank outflow. A reviewed reserve version is applied once; no automatic tax/forecast loop. |
| [Tax estimates](workflows/tax-estimates.yaml) | Gather annual taxpayer inputs and current official sources; calculate and document applicable estimates, or produce an explicitly incomplete packet. | Preparation can proceed before an advisor is selected. CPA/EA review then owner approval are required before adoption; no filing, remittance or payment. |

The complete cross-workflow conditions and reference-only handoff payloads are
in [workflow-handoffs.md](references/workflow-handoffs.md). Workflow, tracker,
resource, and artifact results must contain only safe external/provider/action/
approval/proof references and bounded status. They do not contain receipt
bytes, customer text, amounts, invoice detail, forecast entries, tax math, or
approval scope content.

## Provider transport

### Stripe

The `stripe` provider is a narrow API transport: customer resolution/creation,
draft invoice and line creation, explicit finalize/send, read-only invoice,
payment, charge, balance-transaction, refund, and balance evidence, plus the
reviewed received-payment routes needed to report/attach one payment already
received or mark an invoice paid out of band. These routes never initiate a
charge or move money. It has no charge, payout, transfer, refund creation,
credit, write-off, webhook, tax, or money-movement action. Its canonical contract is
[`docs/integration-contracts/stripe.md`](../../docs/integration-contracts/stripe.md).

Connect a restricted Stripe API key through the local Connections UI; do not
paste it into chat or a workspace file. Customer-identifying text must use the
existing payload-secret reference mechanism, not plaintext workflow input or an
idempotency key. Invoice-item and dispute reads support recovery and suppression.
Every Stripe `POST` uses a stable business-operation idempotency key. Inspect
prior ActionCalls and reconcile before retrying; a resumed run must not create a
new operation identity for the same attempted mutation. Stripe may prune keys
after at least 24 hours, so key reuse is not permanent duplicate protection.
An `open` invoice does not prove an earlier send succeeded. Successful send
acceptance is not proof of delivery to the recipient's inbox. Sandbox/test-mode
sends send no email: record `test-accepted`, not sent/delivered, and do not create
a real customer contact, live reminder cooldown or live follow-up handoff.
Invoice creation and every line use the explicit approved currency; neither
provider defaults nor a later line substitute for the invoice's currency.
Idempotency conflicts preserve the original key/request for reconciliation;
an in-use key or different-parameter conflict never permits a fresh-key bypass.

The action connector has no business approval policy. Existing workflow grants
and technical gates constrain execution; the orchestrator must separately compare
the exact account, customer, invoice version, line items, amounts, recipient and
action against the external owner decision. StackOS does not automatically
invalidate approvals when an external proposal changes.

Independent invoice reads expose exact hashes of the invoice and current customer
primary emails. Additional Dashboard Billing To/CC recipients are not available
through this API; their externally verified scope must also match the approval.
Unknown recipient settings stop the send while preserving draft preparation.
The existing external billing record retains approved primary and additional
To/CC or explicit `verified-none` versus `unknown`, account/customer/invoice scope,
verifier/time/evidence, validity/recheck condition and the approval-bound settings
version. Reuse current verified setup within scope, rechecking before send and
invalidating on changes, expiry or uncertainty—not reinterviewing per invoice.

For “the customer wired money” or another already-received payment, use the
existing payment follow-ups workflow in `settlement-only` mode—there is no
seventh settlement workflow. The external backend first matches one source
identity and one allocation to the current invoice/account/customer/currency/
received state. Direct-bank settlement normally reports a PaymentRecord then
attaches it; existing succeeded Stripe payments are attached only after their
PaymentIntent, charge/refund/dispute and allocation evidence has been read.
`mark-paid-out-of-band` is an explicit alternative only for a verified source
that exactly clears the current remaining balance. Report, attach, and mark use
the distinct `owner-payment-record`, `owner-payment-attachment`, and
`owner-external-settlement` gates; resend remains separately gated. Never
report+mark the same source, create a new idempotency key after an unknown
write, send in the same settlement-only occurrence, or turn partial/split/
overpayment/mismatch evidence into a credit, refund, or paid invoice.

Before reporting, retain exact UTF-8 `payment_reference_sha256` externally,
separately from the source-file digest. Unknown reports inspect retained action
audit/response files, then independently retrieve a surviving known ref and
compare the exact digest and current payment/account/allocation facts before
attachment. Persist the verified ref externally.

`finance.stripe.payment-records.list` is temporarily unavailable in StackOS:
the tested sandbox rejected its published endpoint with an unrecognized-route
404. The action remains describable, but validation and execution return the
unavailable reason before any provider request. Normal executable discovery
hides it, and this optional recovery action does not block ordinary follow-ups
or known-ref settlement. Do not retry it, change keys or guess another URL/header.
If no verified ref survives, hold for owner/provider resolution; never create a
replacement report or fall back to mark-paid. Re-enable listing only after a
verified provider-availability fix. Follow the
[recovery contract](references/backend-contract.md#duplicate-handling-and-retries).

InvoicePayment rows with missing/null linkage preserve lifecycle facts and page
coverage, but cannot identify a payment or prove settlement. Reconciliation uses
safe BalanceTransaction source, Refund failure-transaction and Dispute transaction
links when available; incomplete or unsupported sources remain explicit gaps.

### IMAP evidence delivery

The communications IMAP connector is transport only. On the IMAP route,
`message.export` reads one UID with verified SSL or STARTTLS and `BODY.PEEK[]`,
then returns a safe staging manifest. The host validates/stores the original
`.eml` and retained attachments in the external workspace, re-reads
`finance.json` and its original-evidence records, and only then invokes the separately granted, epoch-qualified
`mark_seen` action followed by transfer-id-only cleanup. It never treats a
search cursor as receipt progress.

Read the exact limits, URI containment, deduplication, failure, and recovery
contract in [imap-host-handoff-contract.md](references/imap-host-handoff-contract.md).
Manual and chat uploads use the same external record protocol but must not run
IMAP actions or invent an `.eml` source.

## Approval and role guidance

[The approval matrix](references/approval-matrix.md) separates the external
business-scoped approval record from StackOS' technical pre-action or
step-lifecycle gate. A gate is not an agent self-approval and does not bind an
unverified payload; the agent must match the concrete safe object reference to
the external approval record before an action.

The plugin ships five focused specialists and one independent reviewer:

- `stackos.finance.receipt-operator` for original custody and receipt extraction;
- `stackos.finance.bookkeeping-preparer` for categorization and reconciliation;
- `stackos.finance.billing-collections-operator` for reviewed Stripe billing
  and follow-ups;
- `stackos.finance.cashflow-preparer` for cash forecasts;
- `stackos.finance.tax-preparer` for annual tax and installment packets; and
- `stackos.finance.control-reviewer` for independent, read-only control review.

`stackos.finance.department-orchestrator` is main-agent guidance, not a
subagent. Before a run or a resume, it reloads the effective workflow,
extension, role presets, selected backend, applicable setup, route, active
grants, approvals, prior action audit, external handoffs, and recovery state.
Use the strongest available reasoning for the main agent. Dispatch specialists
selectively; a routine receipt does not require every role. Intake and billing
operators follow bounded procedures, while bookkeeping exceptions, cashflow,
tax preparation, and independent review need deeper reasoning. The main agent
adjudicates exceptions, remains the single writer, and gives the owner one
consolidated digest of completed work, decisions and unresolved gaps.

Before dispatch or resumed delegation, perform a capability preflight against
actual mounted tools, project/run/step/account scope, grants/approvals and required
host-file access. A role's recommended tools do not prove session capability.
An incapable specialist returns a proposed packet; a capable main agent may
execute under the same grant/approval and stable operation identity, after
confirming no other executor is still attempting it and reconciling unknown
outcomes. Keep one execution owner and one writer. If neither can act, retain
the packet and name the missing capability. Required review remains independent
and read-only; unavailable evidence leaves review pending, never self-approved.

The source contracts are [agent presets](agent-presets/finance.yaml) and the
[main-agent skill preset](skill-presets/finance.yaml). Repository-local Codex
adaptations are [the finance orchestrator](../../.codex/orchestrator/finance-department-orchestrator.md)
and `.codex/agents/finance-*.toml`; those files are host guidance, not a new
StackOS agent runtime. Consumer projects adapt the package to their own host.

## Invocation and verification

This version runs when invoked by the owner or an already authorized external
trigger. It does not wake itself on a schedule. Each workflow is independently
usable; a broader request may authorize the orchestrator to coordinate several,
but a handoff alone grants no new authority. Continue safe unaffected items and
report partial results when another item needs clarification or review.

Fixture tests prove contracts, auth/grants, transport and recovery. A synthetic
month rehearsal exercises the actual guidance and external record template.
Neither substitutes for an operator-owned live activation check; see the
[production activation gate](../../docs/release-signoff.md#finance-production-activation-gate).

## Initial non-goals

This release does not add a StackOS finance ledger, finance resources/artifacts,
database, filesystem connector, accounting/tax engine, finance UI, scheduler,
QuickBooks/Google Sheets adapter, bank feed, AP/vendor payments, payroll,
customer email outside Stripe invoice delivery, tax portal, filing, remittance,
or entity-election action. Engineering verification uses fixtures and local
temporary workspaces only; it does not contact a live mailbox, Stripe customer,
or tax service.
