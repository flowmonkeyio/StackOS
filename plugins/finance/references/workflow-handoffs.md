# Finance Workflow Handoffs

## Operating loop

The reasoning main agent resolves setup and current work, then invokes the six
existing workflows. A request to process the finance inbox can handle a bounded
set of independent items and produce one decision digest. There is no new
scheduler, department runtime or unattended-execution promise.

Keep per-item source/action checkpoints. One invoice mutation occurrence has
one immutable proposal. Batch recommendations can be presented together without
sharing approval across unrelated invoices.

## Reference-only handoffs

An external handoff has stable `handoff_id`, source/target, the source version
when the target is versioned (otherwise its material snapshot digest),
conditions, time, owner and result. StackOS gets a safe ref/status only.
Handoffs suggest eligible next work; they do not automatically create runs.

For `local-json`, a packet reference resolves to stable record IDs and versions
inside the one `finance.json`. Store the handoff in its `handoffs` collection;
do not copy the financial packet into a separate editable file. The receiving
agent loads the current document and resolves those records, preserving the
referenced version and gaps. Markdown/CSV views, when useful, identify their
source revision and are disposable derived views, not handoff authorities.
For a versioned source, match `source_version`; for ordinary unversioned receipt
or preparation records, use `source_digest_sha256` without inventing a version.
The host verifies the applicable binding against the resolved source record.

| Source | Destination | Conditions and contents |
| --- | --- | --- |
| Receipt intake | Bookkeeping | Retained originals/provenance and accepted, duplicate or quarantine item states; prepare only supported facts. |
| Billing/follow-ups | Bookkeeping | Actual provider observations/attempts and received-payment source/allocation/recovery refs, preserving gross, fee, refund and transfer distinctions. A reported/attached payment is not a ledger posting. |
| Bookkeeping | Cashflow | Source coverage and supported records; carry gaps into forecast uncertainty or incomplete status. |
| Bookkeeping | Tax | Annual facts and sourced adjustments; prepared/unposted is not approved tax treatment. |
| Billing | Follow-ups | Observed sent/open mapping, actual delivery result, terms and reminder owner; unknown delivery remains recovery. |
| Follow-ups | Cashflow | Changed expected timing/confidence with observed date and source; a verified received payment can hand off an actual receipt source once, never as an invoice, payment and payout triple count. |
| Tax | Cashflow reserve | Reviewed, owner-adopted packet version; refresh reserve once, not as duplicate expense/payment. |
| Any workflow | Source repair | Named missing input/decision with supported work retained, not cascading automatic reruns. |

## Cashflow and tax independence

Tax preparation needs annual taxpayer/business inputs; a 13-week forecast is
neither prerequisite nor substitute. Cashflow can precede review with a
configured provisional reserve or explicitly unknown reserve.

Apply a reviewed tax reserve by `tax_packet_version + forecast_version`.
Replacement supersedes the earlier assignment. Reserve is distinct from actual
tax payments. Refresh never automatically launches tax work again; changed
annual inputs or an explicit new tax request can initiate another occurrence.

## Completion truth

Useful partial work is not fully reconciled, approved or delivered merely
because a schema accepts a ref. Inspect actual actions and external records.
A blocked side effect does not block independent source gathering or preparation.

See [local-workspace-contract.md](local-workspace-contract.md) for single-writer
custody and [approval-matrix.md](approval-matrix.md) for business decisions.
