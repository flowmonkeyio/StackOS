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
may retain bounded sanitized provider lifecycle or monetary fields and explicitly
requested business-detail read snapshots under the generic executor contract,
but it is never the finance record.

## Initial external backend

The initial `local-json` backend is an external, host-owned workspace:

```text
finance/
├── finance.json                 # sole authoritative local financial data
├── FINANCE.md                   # operating guide and navigation
├── schemas/
│   └── finance-v1.schema.json    # structural contract, not financial records
├── receipts/YYYY/MM/            # optional retained receipt originals
└── attachments/[type/]YYYY/MM/  # emails, statements and complete exports
```

During authorized prerequisite setup of a **new** workspace, copy the
[JSON template](templates/finance-workspace/finance.json), its
[schema](templates/finance-workspace/schemas/finance-v1.schema.json), and the
[operating guide](templates/finance-workspace/FINANCE.md), preserving their layout.
`finance.json` owns all local financial facts, sourced business settings,
proposals, approvals, corrections and relationships. It starts at revision zero
with empty collections, not invented business data. Markdown is guidance or a
derived explanation; CSV is import/export interchange, never a second editable
master. Original receipts, statements and emails are retained evidence. Stripe
still owns actual Stripe state; JSON retains dated observations and provider refs.

Use one designated host writer; specialists propose changes. The
[local persistence protocol](references/local-workspace-contract.md#single-writer-and-retry-safe-persistence)
owns validation, concurrency, original custody and readback. The host needs
filesystem authority for that directory; StackOS grants none. QuickBooks, Google Sheets,
bank feeds, and another authoritative backend are future work, not aliases for
this local format. Initialize this first-version workspace once; subsequent runs
read and update the existing JSON rather than copying the empty template again.

This layout illustrates the selected local backend, not a mandated company tree.
Follow the root/scoped instruction router and keep financial settings in the
master. The local contract owns sourced account aliases, exact source locators,
coverage, corrections and optional packaged host helpers. Those helpers validate
mechanics and generate derived views; they do not establish complete books.
Installed helper/reference paths resolve from the package origin, without a
StackOS source checkout. Separate infrastructure setup from financial
prerequisites and actual workflow operation.

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
| [Payment follow-ups](workflows/payment-request-followups.yaml) | One billing agent follows operator-provided or documented instructions to resend through Stripe or send an authored SMTP email; alternatively reconcile a payment already received. | IMAP reads replies, SMTP sends. Existing Stripe/settlement gates remain; no extra email approval gate. See the shared [follow-up protocol](references/approval-matrix.md#follow-up-approvals). |
| [Cashflow management](workflows/cashflow-management.yaml) | Produce 13 dated base/downside weeks: opening cash, expected receipts, actual cash outflows, closing cash, earmarked reserves and spendable cash. | A reserve is not itself a bank outflow. A reviewed reserve version is applied once; no automatic tax/forecast loop. |
| [Tax estimates](workflows/tax-estimates.yaml) | Gather annual taxpayer inputs and current official sources; calculate and document applicable estimates, or produce an explicitly incomplete packet. | Preparation can proceed before an advisor is selected. CPA/EA review then owner approval are required before adoption; no filing, remittance or payment. |

The complete cross-workflow conditions and reference-only handoff payloads are
in [workflow-handoffs.md](references/workflow-handoffs.md). Workflow, tracker,
resource, and artifact results must contain only safe external/provider/action/
approval/proof references and bounded status. They do not contain receipt
bytes, customer text, amounts, invoice detail, forecast entries, tax math, or
approval scope content.

The existing provider transport audit and SMTP delivery metadata are the narrow
exception described in [backend ownership](references/backend-contract.md#business-and-project-scope),
not a second financial master or permission to copy customer content into summaries.

## Provider transport

### Stripe

The `stripe` provider is a narrow API transport: customer resolution/creation,
bounded existing Product/Price discovery, draft invoice and line creation,
explicit finalize/send, read-only invoice, payment, charge, balance-transaction,
refund, and balance evidence, plus the reviewed received-payment routes needed
to report/attach one payment already received or mark an invoice paid out of
band. These routes never initiate a charge or move money. It has no catalog
write, subscription, charge, payout, transfer, refund creation, credit,
write-off, webhook, tax, or money-movement action. Its canonical contract is
[`docs/integration-contracts/stripe.md`](../../docs/integration-contracts/stripe.md).

For customer identification, catalog selection, and invoice handoff, selected
existing reads support `include_business_details=true`: customer retrieval returns
name/email/description; Product reads return name/description/unit label; Price
reads return nickname/lookup key; invoice retrieval returns invoice number, memo,
customer name/email and provider hosted-page/PDF links; invoice-item listing returns
line descriptions; charge retrieval returns its description/receipt link. Default
responses still expose only bounded reconciliation data. These are confidential
business snapshots in the existing action output/audit, not credentials or a
second financial master. Read the response file; `raw` mode alone does not select these details. Never
copy their contents into tracker/workflow/resource/artifact state or share a
customer-facing link outside the authorized recipient scope.

An already-paid customer's invoice uses the existing issuance path stopped
after finalization, followed by a separately approved `settlement-only` run.
The immutable request can include the optional printed issue date (`effective_at`)
and manual lines or selected Price refs/quantities; reread current Price terms,
currency, line amounts and draft totals into the approved version before finalizing.
After verifying payment linkage and paid/remaining-balance state, retrieve the
invoice with business details to return its available hosted link or PDF link
to the owner. No send or new charge is required to obtain that link. Missing
links, unidentified customers and ambiguous payments remain specific gaps, not
permission to invent facts. See the
[exact handoff steps](../../docs/integration-contracts/stripe.md#explicit-business-details-and-document-handoff).

Connect a restricted Stripe API key through the local Connections UI; never
paste it into chat or a workspace file. Use the existing payload-secret mechanism
for customer-identifying action inputs. For stable operation keys, ambiguous
writes, exact currency and test-versus-live send evidence, follow the
[backend recovery contract](references/backend-contract.md#duplicate-handling-and-retries).

The action connector has no business approval policy. Existing workflow grants
and technical gates constrain execution; the orchestrator must separately compare
the exact account, customer, invoice version, line items, amounts, recipient and
action against the external owner decision. StackOS does not automatically
invalidate approvals when an external proposal changes.

The [invoice approval protocol](references/approval-matrix.md#invoice-approvals)
owns primary-email hashes, externally verified Dashboard To/CC settings and
their reuse/recheck rules. Unknown recipients hold the send, not draft work.

For “the customer wired money” or another already-received payment, use the
existing follow-ups workflow in `settlement-only` mode, not a seventh workflow.
The [settlement approval protocol](references/approval-matrix.md#received-payment-settlement-approvals)
owns route selection and distinct report/attach/paid-out-of-band gates. The
[backend recovery contract](references/backend-contract.md#duplicate-handling-and-retries)
owns source matching, partials, unknown outcomes and current PaymentRecord
availability; [reconciliation guidance](references/backend-contract.md#corrections-and-reconciliation)
owns missing payment links. Settlement records money already received; it does
not charge or send a reminder.

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
Neither substitutes for the separately authorized activation below.

## Signoff and activation

Use the generic [release signoff](../../docs/release-signoff.md) for test slices,
packaged/native activation evidence and shared-runtime coordination. Automated
checks use isolated fixtures, never live customers, mailboxes or financial work.

For source-package closeout, rehearse a synthetic month in a disposable host
workspace and have a separate agent inspect the resulting records/arithmetic:

- Retain originals, extract multiple receipts from one source, recognize a
  duplicate upload, isolate an unreadable item, and prepare supported rows with
  a missing statement. Receipt-only setup must progress without tax inputs.
- Check an exact two-line invoice and changed/unknown recipients; stale approval
  must not authorize a send. Suppress follow-up after payment. Exercise the
  selected Stripe/SMTP contact route without real delivery.
- Exercise full/partial bank receipts, an existing succeeded Stripe payment,
  duplicate-source rejection and report-success/attach-failure recovery. Prove
  exact InvoicePayment linkage and external write/readback, or the bounded hold
  required by the [recovery contract](references/backend-contract.md#duplicate-handling-and-retries).
  Deferred actions must reject direct and granted calls without HTTP and must
  not block unrelated executable routes.
- Build the 13-week cash rollforward and annual tax packet, then resume the
  rehearsal and apply a reviewed reserve version only once. Verify the
  [cashflow](references/local-workspace-contract.md#cashflow-packet) and
  [tax](references/local-workspace-contract.md#tax-packet) invariants independently.
- Prove first-time setup, schema/ID/reference checks, immutable approval targets,
  revision/hash conflicts, atomic replacement and readback under the
  [writer protocol](references/local-workspace-contract.md#single-writer-and-retry-safe-persistence).
  Unrelated updates require rebase but not a fresh unchanged proposal approval;
  material changes do. Derived views cannot change JSON; exports identify their
  source revision/digest.
- Rehearse a specialist lacking toolbox or filesystem capability: it returns a
  proposal to the capable main agent under the same grants, while required
  independent review stays independent. Verify resolved preset reference paths.

Record which provider responses and owner/advisor decisions were simulated.
Static assertions do not prove agent behavior, OCR, inbox delivery or scheduling.

Production activation is separate and applies only to selected routes:

- Verify the operator-selected external workspace is writable, backed up and
  access-controlled; create/re-read a non-production record without resetting
  an existing workspace or introducing a second financial master.
- For IMAP, use `account.test` and an operator-owned test message to prove the
  [store-before-ack handoff](references/imap-host-handoff-contract.md). Private-CA
  changes additionally require Account persistence/replacement/clearing,
  isolation/default-root preservation, invalid-PEM/private-key rejection and
  synthetic TLS trusted/untrusted/wrong-host/expired-leaf handshake coverage.
- For Stripe, probe the selected restricted key and applicable permissions in
  an explicitly authorized test account. Live issuance/settlement follows the
  [approval matrix](references/approval-matrix.md); production customers are not
  fixtures. SMTP follows the operator's sending instruction and active grant,
  with acceptance recorded separately from delivery.
- Cashflow/tax reliance requires current source coverage and the applicable
  advisor/owner review. Preparation is not filing, remittance or payment.

## Initial non-goals

This release does not add a StackOS finance ledger, finance resources/artifacts,
database, filesystem connector, accounting/tax engine, finance UI, scheduler,
QuickBooks/Google Sheets adapter, bank feed, AP/vendor payments, payroll,
customer email outside the selected invoice/follow-up routes, tax portal, filing, remittance,
or entity-election action. Engineering verification uses fixtures and local
temporary workspaces only; it does not contact a live mailbox, Stripe customer,
or tax service.
