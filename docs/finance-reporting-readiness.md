# Finance reporting readiness fix

## Problem and scope

Recovered legacy records are valid source evidence but do not automatically carry
the reviewed metadata required for accounting recognition. The overview previously
rendered several empty charts and repeated `Unavailable` cards, raw undated receipt
identifiers, and duplicate analysis-request controls. This made incomplete reporting
setup look like lost invoices or a runtime failure.

## Changes

Owner scope correction: the unsolicited reporting-setup callout was removed.
This fix must not substitute a new explanatory section for working reporting.
The existing source-gap details remain the place for missing evidence.

The owner then selected invoice-based, incomplete reporting. Initial console
filters now use the unanimous basis/date basis of valid approved reporting
metadata, rather than silently opening cash/transaction reporting for invoice
records. Mixed or absent approved metadata does not choose a new default; later
explicit filter choices remain authoritative. Existing metric cards label partial
amounts `Incomplete · unposted`; the existing period caption identifies missing
invoice dates/source coverage and separates payment matching/posting. No new
controls or reporting sections were added.

- Return readiness counts and readable labels for the complete filtered selection,
  including records outside the visible page.
- Explain missing recognition metadata, currency precision and source coverage.
  Suppress empty overview cards/charts while retaining supported comparison data.
- Show undated documents with sanitized original filenames. Bookkeeping merchant
  labels may use unanimous active linked receipt evidence, never inferred identity.
- Display legacy USD minor units with two decimal places and an explicit
  `display units only` marker. Missing reviewed precision remains a review item.
  Stale/conflicting precision cannot use this fallback. Detail responses retain
  the marker, and accounting totals never consume the display-only convention.
- Keep one analysis request control and preserve existing analysis results/history.

## Data and permission boundaries

This patch does not change ledger records, custody, source amounts, accounting
treatment, date basis, allocation, policy, source coverage or bank matching. It does
not change plugin authorization. Vendor payment evidence is not bank reconciliation;
prepared/unposted entries are not posted books. Missing income is not zero.

Owner-authorized adoption of invoice-reporting metadata is a separate local-data
operation using the existing preview/commit correction path. It preserves original
sources, amounts, dates, unresolved gaps and prepared/unposted status. Vendor payment
dates are not substituted for missing invoice issue dates. Complete coverage, bank
matching, revenue, profit and tax treatment are not inferred.

## Verification and remaining gaps

- 283 backend management/readiness/REST tests passed; touched Python Ruff and mypy
  checks passed.
- The invoice-default follow-up passed 284 backend tests and UI tests/typecheck/build.
  Independent review found no source-code blocker in the approved-basis selection.
- Full UI test command, type-check and production UI build passed. New regressions
  cover selection-wide counts, prior-period charts, detail markers, precision review,
  filename labels, and safe linked-merchant fallback.
- Independent review found no remaining blocker in this diff.
- Installed development build `20261110T211726Z` (`2.1.44`) after preserving the
  previous app. Actual installed-app verification confirmed Running, the readiness
  explanation, readable undated document labels, a single analysis-request control,
  and decimal display-only USD amounts in Expenses & income. `stackos doctor --json`
  returned `ok: true`; the installed runtime's read-only `finance.query` succeeded.
  Existing managed/original Finance file hashes and modes remained unchanged.
- Repository-wide `make signoff` remains blocked by existing lint errors in retained
  release/report scripts; this patch does not claim complete repository signoff.
- Recognition totals still require evidence-backed reporting setup and confirmed
  coverage. The fix explains that gap rather than fabricating financial decisions.
- Existing desktop development build/install flow remains documented in
  [desktop distribution](desktop-distribution.md). No bundled Finance data is added.
