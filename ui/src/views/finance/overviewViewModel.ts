import type { SchemaFinanceProjectionOut } from '@/api'
import { formatMoney, object, objects, refs, text } from '@/lib/finance'

export type ChartMoney = { minor: string; currency: string; exponent: number }
type MetricKey = 'revenue' | 'spend'
export type TotalRow = {
  id: MetricKey
  label: string
  current: ChartMoney | null
  prior: ChartMoney | null
  currentText: string
  priorText: string
  changeText: string
  currentState: string
  priorState: string
  currentRefs: string[]
  priorRefs: string[]
}
export type TrendRow = {
  id: string
  date: string
  income: ChartMoney | null
  expenses: ChartMoney | null
  incomeText: string
  expensesText: string
  includedRefs: string[]
}
export type OverviewCurrency = {
  currency: string
  currentWindow: string
  priorWindow: string
  compared: boolean
  clamped: boolean
  basis: string
  coverage: string
  priorCoverage: string
  totals: TotalRow[]
  trend: TrendRow[]
  trendPoints: (TrendRow | null)[]
}
function exponent(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 6
}
function money(value: unknown, currency: string): ChartMoney | null {
  const amount = object(value)
  return /^-?\d+$/.test(text(amount.minor)) &&
    amount.currency === currency &&
    exponent(amount.exponent)
    ? { minor: text(amount.minor), currency, exponent: amount.exponent }
    : null
}
function metric(value: unknown, currency: string) {
  const row = object(value)
  const state = row.state === 'complete' || row.state === 'partial' ? row.state : 'unavailable'
  return {
    amount: state === 'unavailable' ? null : money(row.amount, currency),
    state,
    refs: refs(row.included_record_refs),
  }
}
export function exactChange(current: ChartMoney | null, prior: ChartMoney | null): string {
  if (!current || !prior || current.currency !== prior.currency) return 'Unavailable'
  const precision = Math.max(current.exponent, prior.exponent)
  const aligned = (value: ChartMoney) =>
    BigInt(value.minor) * 10n ** BigInt(precision - value.exponent)
  return formatMoney({
    minor: String(aligned(current) - aligned(prior)),
    currency: current.currency,
    exponent: precision,
  })
}
function windowLabel(value: unknown): string {
  const query = object(value)
  return query.start && query.end
    ? `${text(query.start)} through ${text(query.end)}`
    : 'Window unavailable'
}
export function overviewCurrencies(projection: SchemaFinanceProjectionOut): OverviewCurrency[] {
  const currentMetrics = objects(projection.metrics_by_currency)
  const comparison = object(projection.comparison)
  const priorMetrics = objects(comparison.metrics_by_currency)
  const query = object(projection.query)
  return [
    ...new Set(
      [...currentMetrics, ...priorMetrics].map((row) => text(row.currency)).filter(Boolean),
    ),
  ]
    .sort()
    .map((currency) => {
      const current = currentMetrics.find((row) => row.currency === currency) ?? {}
      const prior = priorMetrics.find((row) => row.currency === currency) ?? {}
      const totals: TotalRow[] = (['revenue', 'spend'] as const).map((key) => {
        const a = metric(current[key], currency),
          b = metric(prior[key], currency)
        return {
          id: key,
          label: key === 'revenue' ? 'Income' : 'Expenses',
          current: a.amount,
          prior: b.amount,
          currentText: formatMoney(a.amount),
          priorText: formatMoney(b.amount),
          changeText: exactChange(a.amount, b.amount),
          currentState: a.state,
          priorState: b.state,
          currentRefs: a.refs,
          priorRefs: b.refs,
        }
      })
      const trend: TrendRow[] = objects(projection.trend)
        .filter((row) => row.currency === currency)
        .map((row) => {
          const contribution = (key: MetricKey, value: unknown) =>
            totals.find((total) => total.id === key)?.current && exponent(current.exponent)
              ? money({ minor: value, currency, exponent: current.exponent }, currency)
              : null
          const income = contribution('revenue', row.revenue_minor),
            expenses = contribution('spend', row.spend_minor)
          return {
            id: text(row.date),
            date: text(row.date),
            income,
            expenses,
            incomeText: formatMoney(income),
            expensesText: formatMoney(expenses),
            includedRefs: refs(row.included_record_refs),
          }
        })
        .sort((a, b) => a.date.localeCompare(b.date))
      const trendPoints: (TrendRow | null)[] = []
      for (const row of trend) {
        const previous = trendPoints.at(-1)
        if (
          previous &&
          Date.parse(`${row.date}T12:00:00Z`) - Date.parse(`${previous.date}T12:00:00Z`) > 86400000
        )
          trendPoints.push(null)
        trendPoints.push(row)
      }
      return {
        currency,
        totals,
        trend,
        trendPoints,
        compared: projection.comparison !== null,
        currentWindow: windowLabel(query),
        priorWindow: windowLabel(comparison.query),
        clamped: object(query.comparison).clamped === true,
        basis: `${text(query.recognition_basis)} recognition · ${text(query.date_basis)} dates · ${text(query.timezone)}`,
        coverage: text(object(projection.coverage).state) || 'unavailable',
        priorCoverage: text(object(comparison.coverage).state) || 'unavailable',
      }
    })
}

// Exact amounts remain strings. This shared scale converts bounded coordinates only.
export function drawingScale(values: (ChartMoney | null)[]) {
  const amounts = values.filter((value): value is ChartMoney => value !== null)
  const precision = Math.max(0, ...amounts.map((value) => value.exponent))
  const aligned = (value: ChartMoney) =>
    BigInt(value.minor) * 10n ** BigInt(precision - value.exponent)
  const max = amounts.reduce((largest, value) => {
    const minor = aligned(value),
      abs = minor < 0n ? -minor : minor
    return abs > largest ? abs : largest
  }, 0n)
  const power = Math.max(precision, max.toString().length - 6)
  function coordinate(value: ChartMoney | null): number | null {
    if (!value) return null
    const minor = aligned(value)
    const digits = (minor < 0n ? -minor : minor).toString().padStart(power + 1, '0')
    const whole = power ? digits.slice(0, -power) : digits
    const fraction = power ? digits.slice(-power).slice(0, 15) : ''
    const result = Number(`${minor < 0n ? '-' : ''}${whole}.${fraction || '0'}`)
    return Number.isFinite(result) && !(minor !== 0n && result === 0) ? result : null
  }
  return {
    value: coordinate,
    belowScaleCount: amounts.filter((value) => coordinate(value) === null).length,
    description: `${power === precision ? amounts[0]?.currency || 'Currency' : `10^${power - precision} ${amounts[0]?.currency}`} per chart unit; drawing is approximate. Exact values are in the table.`,
  }
}
export function contributionRows(
  value: unknown,
  projection: SchemaFinanceProjectionOut,
): (Record<string, unknown> & { id: number; spend: string; revenue: string })[] {
  return objects(value).map((row, index) => {
    const precision = projection.metrics_by_currency.find(
      (metric) => metric.currency === row.currency,
    )?.exponent
    const amount = (field: string) =>
      formatMoney({ minor: row[field], currency: row.currency, exponent: precision })
    return { ...row, id: index, spend: amount('spend_minor'), revenue: amount('revenue_minor') }
  })
}
