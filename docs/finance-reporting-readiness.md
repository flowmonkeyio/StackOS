# Finance invoice reporting

## Current behavior

Recovered ledger records remain source evidence until reporting treatment and date
basis are explicitly adopted. The console must not infer cash payments, zero
revenue, complete coverage, tax treatment or business-use allocation from invoices.

When valid approved reporting metadata unanimously uses one recognition/date basis,
the existing console filters initially select that basis. Mixed or absent metadata
leaves the existing default unchanged; subsequent user filter selections win.
Supported partial amounts appear in the existing metric cards as
`Incomplete · unposted`. The period caption identifies missing invoice dates and
source coverage while keeping payment matching and posting separate.

Undated documents use sanitized original filenames rather than internal receipt
IDs. Merchant labels may use unanimous active linked receipt evidence. Labels cover
the complete filtered selection, including records outside the current page.
Legacy USD minor units use two decimal places for display only, marked
`display units only`. Stale/conflicting precision never uses that fallback;
accounting totals never consume the display-only convention. Supported comparison
charts remain available even when the current period has no supported totals.
The dedicated analysis section owns requests; existing results/history remain
accessible from Overview.

## Data and permission boundaries

Reporting changes do not rewrite ledger facts or approve accounting policy.
Adopting a reporting basis uses the existing reviewed preview/commit path and
requires its normal authority. Preserve original sources, amounts, dates,
unresolved gaps and prepared/unposted status. Do not substitute vendor payment
dates for invoice issue dates or infer bank matching, complete coverage, revenue,
profit, allocation or tax treatment from invoice evidence alone.

See [Finance operations](../plugins/finance/README.md),
[project storage](architecture.md#project-finance-files) and
[plugin enablement and recovery](plugins.md) for their contracts. Browser setup
permission does not grant agent/MCP mutation authority or permission to supply
plugin configuration. Offline recovery remains an explicit operator action.

## Assessing reporting readiness

Read the current project guidance, master and original evidence before acting.
A current export or passing software test does not establish financial completeness.

| Area | Required distinction |
| --- | --- |
| Invoice dates | Obtain authoritative issue-date evidence or explicitly adopt another supported basis. Payment dates, billing-period ends and filenames are not interchangeable with issue dates. |
| Payment matching | Match paying-account transactions to original evidence. A vendor's paid status is not a bank match; an invoice and its payment must not become duplicate expenses. |
| Source coverage | Assess accounts, opening/closing balances, source windows and missing periods. Unsupported periods remain gaps. |
| Categories and posting | Review category, allocation and basis decisions against evidence. Actual posting requires an authorized ledger destination and supported posting contract; relabeling prepared entries does not post books. |
| Revenue and profit | Require relevant revenue/settlement sources and a supported recognition basis. An empty collection does not prove zero revenue. |
| Persistence evidence | Reconcile approvals, write/readback receipts and one-writer evidence. Approval-target digests alone do not establish every historical write's execution. |

Project-specific assessments, amounts, source counts, approvals and export
revisions belong in that project's evidence. Recheck the current revision and
export manifest before relying on a previously verified snapshot.

## Verification

Use the maintained Finance projection, storage, recovery and consuming-screen
tests selected by [integration testing](integration-testing.md). Keep source,
generated-bundle and installed-app evidence distinct. Follow
[release signoff](release-signoff.md) for the combined candidate and
[desktop distribution](desktop-distribution.md) for packaging/install proof.
Finish the UI build before packaging, then compare packaged assets with that
completed build. Source tests do not establish live financial completeness or
authorize ledger mutations.
