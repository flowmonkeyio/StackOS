/**
 * Number/money formatting — the numeric sibling of time.ts.
 *
 * Centralizes the `$x.xx` / `N%` formatting that was hand-rolled with
 * `.toFixed()` across CostBudget so spend, budgets, and ratios read the same
 * everywhere.
 */

const USD = new Intl.NumberFormat(undefined, {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

/** "$1,240.00" — null/NaN render as "$0.00". */
export function formatUsd(value: number | null | undefined): string {
  const n = typeof value === 'number' && Number.isFinite(value) ? value : 0
  return USD.format(n)
}

/** "83%" — rounds to whole percent. `value` is already a percentage (0..100+). */
export function formatPercent(value: number | null | undefined, fractionDigits = 0): string {
  const n = typeof value === 'number' && Number.isFinite(value) ? value : 0
  return `${n.toFixed(fractionDigits)}%`
}

/** Compact integers: 1234 -> "1,234". */
export function formatCount(value: number | null | undefined): string {
  const n = typeof value === 'number' && Number.isFinite(value) ? value : 0
  return n.toLocaleString()
}
