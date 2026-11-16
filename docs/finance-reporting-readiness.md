# Finance invoice reporting

## Current behavior

Recovered ledger records remain source evidence until reporting treatment and date
basis are explicitly adopted. The console must not infer cash payments, zero
revenue, complete coverage, tax treatment or business-use allocation from invoices.

When valid approved reporting metadata unanimously uses one recognition/date basis,
the existing console filters initially select that basis. Mixed or absent metadata
leaves the existing default unchanged; subsequent user filter selections win.
Supported partial amounts appear in the existing metric cards as
`Incomplete · unposted`. The existing period caption identifies missing invoice
dates/source coverage and keeps payment matching and posting separate. The rejected
blue reporting-setup section is removed; no replacement section or controls exist.

Undated documents use sanitized original filenames rather than internal receipt
IDs. Merchant labels may use unanimous active linked receipt evidence. Labels cover
the complete filtered selection, including records outside the current page.
Legacy USD minor units use two decimal places for display only, marked
`display units only`. Stale/conflicting precision never uses that fallback;
accounting totals never consume the display-only convention. Supported comparison
charts remain available even when the current period has no supported totals.
There is one analysis-request control; existing results/history remain accessible.

Unused readiness-count projections from the removed callout are deleted from the
plugin query, response schema, generated client and presentation helper. Existing
row exclusions, coverage evidence, labels and supported metrics remain canonical.

## Data and permission boundaries

The code changes do not rewrite ledger facts or approve accounting policy. The
owner separately authorized invoice-based incomplete reporting. That local-data
adoption used the existing reviewed preview/commit path, preserving original
sources, amounts, dates, unresolved gaps and prepared/unposted status. Vendor
payment dates were not substituted for invoice issue dates. No bank matching,
complete coverage, revenue, profit, allocation or tax treatment was inferred.

The scope of PR #22 also includes explicit plugin enablement, guarded recovery and
verified build inputs. See [Finance operations](../plugins/finance/README.md),
[project storage](architecture.md#project-finance-files) and
[desktop distribution](desktop-distribution.md) for their contracts. Browser setup
permission does not grant agent/MCP mutation authority or permission to supply
plugin config.

## Open Finance work

The following is a **dated local assessment from 2026-11-16**, not bundled
project data or a claim that all sources have been collected. Re-read the
current master, project guidance and original evidence before acting. A current
export or a passing software test does not establish financial completeness.

| Open work | Evidence gap and next step |
| --- | --- |
| Invoice dates | Eight MongoDB invoices do not state an invoice issue date in their retained originals. Obtain authoritative issue-date evidence for each invoice, or explicitly adopt a different supported reporting basis. Do not substitute payment dates, billing-period ends or filenames. |
| Payment matching | Identify the paying accounts and obtain their transaction exports or statements, including relevant dates and balances. Match transactions to original evidence without treating a vendor's paid status as a bank match or duplicating an invoice and its payment. |
| Source coverage | Assemble the account register and assess each account's opening and closing balances, source windows and missing periods. Include later retained sources in an updated coverage assessment and leave unsupported periods as gaps. |
| Categories and posting | Review and adopt appropriate category, allocation and basis decisions from the available evidence. All active bookkeeping entries at the assessment were `prepared/unposted`; actual posting requires an authorized ledger destination and a supported posting contract. Changing a status label does not post books. |
| Revenue and profit | Obtain relevant revenue, settlement and customer source evidence and adopt an appropriate recognition basis. Calculate profit only when compatible revenue and expense evidence supports it; an empty collection does not prove zero revenue. |
| Historical persistence evidence | Reconcile available approval occurrences, write and readback receipts, and one-writer evidence for changes since the last retained write proof. Approval-target digests in the master alone do not establish every historical write's execution. Preserve existing approvals within their actual scope. |

On 2026-11-16, the four existing derived CSV views were regenerated from master
revision 43 and independently checked as current. The master hash and 46 retained
originals were verified unchanged. This closes the stale-export maintenance
item for that snapshot only; it does not close the evidence gaps above. The
current revision and export manifest must be checked again before later use.

## Latest installed checkpoint (before this cleanup)

Development build `2.1.44 / 20261110T214635Z` was installed and verified on the
actual desktop. It selected the approved invoice basis, showed USD 2,485.00 in the
existing Spend card as `Incomplete · unposted`, and showed no blue section.
Twenty-three supported invoice entries were adopted; eight MongoDB entries still
lack verified invoice issue dates. Revenue/profit remained unavailable. These
counts and amounts describe that local checkpoint, not bundled product data or a
universal expected result. Doctor passed; original records/evidence were preserved.

The superseded `20261110T211726Z` installation used the rejected explanatory
section. It is not evidence for the final user experience or a current build.

## Verification

The installed invoice-reporting candidate passed 284 focused backend tests and UI
checks/typecheck/build. An independent review of PR head `60d78b29` subsequently
passed 55 focused Python and 101 UI tests; a separate security review passed 59
middleware/recovery/runtime tests and four downloader tests. Those runs did not
constitute complete release signoff: aggregate lint failed in retained release
scripts and hosted checks were absent.

The cleanup removes unused readiness code, fixes retained-script lint without
weakening lint rules, and consolidates these docs. It passed `make lint`, 284
focused Finance Python tests, UI tests and a rebuilt UI bundle. Independent
cleanup review found no actionable regression and confirmed that retained-script
changes preserve behavior.

At the pre-merge checkpoint, `make signoff` passed on native ARM64 Python 3.12.16 using an
isolated environment synchronized from the frozen lockfile. This gate includes
lint/formatting, typecheck, the complete Python suite, UI unit tests and UI build.
External network access was denied by a process sandbox; localhost remained
available for integration tests. The original unrestricted attempt had been
stopped by the execution approval layer on an external Anthropic request whose
source was not established. No network exception or alternate transmission route
was used. An intermediate offline run under x86_64 Python passed 5,628 tests but
failed five ARM64-specific installer tests; rerunning on the supported native
interpreter resolved all five without changing production guards or test fixtures.

The final reconciled source and generated bundle have not been installed or
validated against live Finance data. No live ledger mutation is part of this
cleanup. The current PR description records the reconciled checks and the
independent baseline failures separately from the historical signoff above.

Finish the UI build **before** desktop packaging: the wheel captures `ui_dist` at
packaging start. Compare packaged `ui_dist/index.html` and its referenced JS asset
with the completed source build before replacing the app. See
[release signoff](release-signoff.md) for the aggregate gate and
[desktop distribution](desktop-distribution.md) for build/install steps.
