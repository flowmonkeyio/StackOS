# Finance reporting readiness fix

## Problem and scope

Recovered legacy records are valid source evidence but do not automatically carry
the reviewed metadata required for accounting recognition. The overview previously
rendered several empty charts and repeated `Unavailable` cards, raw undated receipt
identifiers, and duplicate analysis-request controls. This made incomplete reporting
setup look like lost invoices or a runtime failure.

## Changes

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

## Verification and remaining gaps

- 283 backend management/readiness/REST tests passed; touched Python Ruff and mypy
  checks passed.
- Full UI test command, type-check and production UI build passed. New regressions
  cover selection-wide counts, prior-period charts, detail markers, precision review,
  filename labels, and safe linked-merchant fallback.
- Independent review found no remaining blocker in this diff.
- Repository-wide `make signoff` remains blocked by existing lint errors in retained
  release/report scripts; this patch does not claim complete repository signoff.
- Recognition totals still require evidence-backed reporting setup and confirmed
  coverage. The fix explains that gap rather than fabricating financial decisions.
- Existing desktop development build/install flow remains documented in
  [desktop distribution](desktop-distribution.md). No bundled Finance data is added.
