import type { SchemaFinanceProjectionOut } from '@/api'
import { object, objects, refs, text } from '@/lib/finance'

export function reportingReadiness(projection: SchemaFinanceProjectionOut) {
  const summary = object(projection.reporting_readiness)
  const count = (key: string) =>
    typeof summary[key] === 'number' && Number.isSafeInteger(summary[key])
      ? Math.max(0, summary[key] as number)
      : null
  const missing =
    count('missing_metadata_count') ??
    projection.exclusions.filter((row) => row.reason === 'missing_metadata').length
  const supported = count('supported_bookkeeping_count')
  const issues = refs(projection.coverage.issues)
  const policyMissing = issues.includes('missing_or_conflicting_reporting_policy')
  const hasMetrics = (value: unknown) =>
    objects(value).some((row) =>
      ['spend', 'revenue', 'profit'].some((key) => {
        const metric = object(row[key])
        return ['partial', 'complete'].includes(text(metric.state)) && metric.amount != null
      }),
    )
  return {
    missing,
    supported,
    precision: count('missing_precision_count'),
    undated: count('undated_count') ?? projection.undated_or_period_only.length,
    total: count('selected_record_count') ?? projection.page.total,
    needsSetup:
      summary.state === 'needs_review' ||
      missing > 0 ||
      issues.length > 0 ||
      (count('missing_precision_count') ?? 0) > 0 ||
      projection.exclusions.length > 0,
    policyMissing,
    reasons: objects(summary.reasons).map((row) => text(row.reason).replaceAll('_', ' ')),
    coverageIssues: issues.map((issue) => issue.replaceAll('_', ' ')),
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
