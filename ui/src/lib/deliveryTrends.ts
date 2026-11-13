import type { SchemaDeliveryTrendMetricsOut } from '@/api'
import { formatDurationMinutes } from '@/lib/stackos/time'

export type DeliveryMetric = keyof SchemaDeliveryTrendMetricsOut
export const deliveryMetrics = [
  { key: 'completed_tasks', label: 'Completed tasks', token: 'accent-primary' },
  { key: 'completed_tickets', label: 'Completed tickets', token: 'info-default' },
  { key: 'first_review_acceptance', label: 'First-review acceptance', token: 'success-default' },
  { key: 'changes_requested', label: 'Changes requested', token: 'warning-default' },
  { key: 'stops', label: 'Stops', token: 'warning-default' },
  { key: 'work_time', label: 'Recorded work time', token: 'chart-work' },
] as const

// These are presentations of the one repository projection, never new event,
// duration or rate calculations. In particular, do not average daily rates.
export function deliveryValue(metrics: SchemaDeliveryTrendMetricsOut, key: DeliveryMetric): number | null {
  if (key === 'work_time') return metrics.work_time.total_seconds
  if (key === 'first_review_acceptance') return metrics.first_review_acceptance.rate
  // Retained transitions can prove incidents without proving earlier lifetime
  // provenance. Preserve those known counts; an unsupported zero is a gap.
  if (metrics[key].coverage.status === 'unavailable' && metrics[key].total === 0) return null
  return metrics[key].total
}

export function formatDeliveryValue(value: number | null, key: DeliveryMetric, exact = false): string {
  if (value == null) return 'N/A'
  if (key === 'first_review_acceptance') return `${(value * 100).toLocaleString('en-US', { maximumFractionDigits: exact ? 2 : 0 })}%`
  if (key === 'work_time') return exact ? `${value.toLocaleString('en-US', { maximumFractionDigits: 3 })} seconds of summed task work time` : value === 0 ? '0m' : formatDurationMinutes(value / 60)
  return value.toLocaleString('en-US')
}

export function deliveryCoverage(metrics: SchemaDeliveryTrendMetricsOut, key: DeliveryMetric): string {
  const coverage = metrics[key].coverage
  const issues = coverage.issues?.map(issue => issue === 'retained_revision_history_only' ? 'Earlier history is unavailable' : issue.replaceAll('_', ' ')) ?? []
  const detail = `Across retained history: ${coverage.recorded.toLocaleString('en-US')} supported; ${coverage.unavailable.toLocaleString('en-US')} unavailable${issues.length ? `. ${issues.join('; ')}` : ''}`
  return `${coverage.status === 'partial' ? 'Partial coverage' : coverage.status === 'unavailable' ? 'Unavailable history' : coverage.status === 'empty' ? 'No recorded events' : 'Recorded coverage'}. ${detail}`
}

export function activityDayLabel(date: string): string {
  return new Date(`${date}T12:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
}
