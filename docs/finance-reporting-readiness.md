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

The complete `make signoff` passed on native ARM64 Python 3.12.16 using an
isolated environment synchronized from the frozen lockfile. This gate includes
lint/formatting, typecheck, the complete Python suite, UI unit tests and UI build.
External network access was denied by a process sandbox; localhost remained
available for integration tests. The original unrestricted attempt had been
stopped by the execution approval layer on an external Anthropic request whose
source was not established. No network exception or alternate transmission route
was used. An intermediate offline run under x86_64 Python passed 5,628 tests but
failed five ARM64-specific installer tests; rerunning on the supported native
interpreter resolved all five without changing production guards or test fixtures.

The final source cleanup and generated bundle have not been installed. No live
ledger mutation is part of this cleanup. PR-description synchronization remains
blocked by the GitHub connector's `403 Resource not accessible by integration`;
these repository docs contain the current scope and verification record.

Finish the UI build **before** desktop packaging: the wheel captures `ui_dist` at
packaging start. Compare packaged `ui_dist/index.html` and its referenced JS asset
with the completed source build before replacing the app. See
[release signoff](release-signoff.md) for the aggregate gate and
[desktop distribution](desktop-distribution.md) for build/install steps.
