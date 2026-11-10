import type { SchemaFinanceProjectionOut } from '@/api'
import { object, objects, text } from '@/lib/finance'

export function reportingReadiness(projection: SchemaFinanceProjectionOut) {
  const hasMetrics = (value: unknown) =>
    objects(value).some((row) =>
      ['spend', 'revenue', 'profit'].some((key) => {
        const metric = object(row[key])
        return ['partial', 'complete'].includes(text(metric.state)) && metric.amount != null
      }),
    )
  return {
    hasMetrics: hasMetrics(projection.metrics_by_currency),
    hasChartMetrics:
      hasMetrics(projection.metrics_by_currency) ||
      hasMetrics(object(projection.comparison).metrics_by_currency),
  }
}

export function financeRecordLabel(projection: SchemaFinanceProjectionOut, ref: string): string {
  const saved = text(object(projection.record_labels)[ref])
  if (saved) return saved
  const row = projection.rows.find((item) => item.record_ref === ref)
  return row
    ? `${text(row.counterparty) || 'Record'} · ${text(row.date) || 'Date not recorded'}`
    : 'Inspect record details'
}
