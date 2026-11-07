import type { SchemaTrackerAgentStatsOut } from '@/api'

export type AgentStats = SchemaTrackerAgentStatsOut
export type AgentStatsRow = AgentStats['rows'][number]
export type AgentStatsMetrics = AgentStats['totals']
export type AgentStatsDay = AgentStats['daily'][number]
export type UsageField = 'input' | 'cache_read' | 'output' | 'cache_write' | 'reasoning'
export type ActivityMetric = 'input' | 'cache_read' | 'output' | 'attempts'
export type ActivityColor = 'agent' | 'model' | 'input_type'
export type ActivityGroup = AgentStats['group_by']
export interface AgentStatsFilters {
  start_date: string
  end_date: string
  timezone: string
  group_by: ActivityGroup
}

export const usageFields = [
  { key: 'input', label: 'Input' },
  { key: 'cache_read', label: 'Cache' },
  { key: 'output', label: 'Output' },
] as const

export function formatTokens(value: number | null | undefined, compact = true): string {
  if (value == null) return 'Not reported'
  if (!compact || Math.abs(value) < 1000) return value.toLocaleString('en-US')
  const unit = value >= 1e9 ? 1e9 : value >= 1e6 ? 1e6 : 1e3
  return `${(value / unit).toLocaleString('en-US', { minimumFractionDigits: unit >= 1e6 ? 2 : 0, maximumFractionDigits: 2 })}${unit === 1e9 ? 'B' : unit === 1e6 ? 'M' : 'K'}`
}

export function fieldMissing(metrics: AgentStatsMetrics | null | undefined, field: UsageField): number | null {
  if (!metrics) return null
  const coverage = metrics.coverage.fields?.[field]
  if (coverage) return coverage.unavailable_attempts
  if (field === 'input' || field === 'output') return metrics.coverage.usage_unavailable_attempts
  return metrics.reported_usage[field] == null ? metrics.launch_attempts : null
}

export function fieldCoverage(metrics: AgentStatsMetrics | null | undefined, field: UsageField): string {
  if (metrics?.launch_attempts === 0) return 'No recorded usage'
  const label = field === 'cache_read' ? 'Cache' : field === 'cache_write' ? 'Cache write' : field === 'reasoning' ? 'Reasoning' : field === 'input' ? 'Input' : 'Output'
  if (metrics?.reported_usage[field] == null) return `${label} not reported`
  const missing = fieldMissing(metrics, field)
  return missing ? `${label} missing for ${missing} ${missing === 1 ? 'attempt' : 'attempts'}` : ''
}

export function exactUsage(metrics: AgentStatsMetrics | null | undefined, field: UsageField): string {
  const label = usageFields.find(item => item.key === field)?.label ?? (field === 'cache_write' ? 'Cache write' : 'Reasoning')
  const value = metrics?.reported_usage[field]
  return `${label}: ${formatTokens(value, false)}${value == null ? '' : ' tokens'}. ${fieldCoverage(metrics, field)}`.trim()
}

export function cacheShare(metrics: AgentStatsMetrics): string | null {
  const { input, cache_read } = metrics.reported_usage
  if (!input || cache_read == null || fieldMissing(metrics, 'input') !== 0 || fieldMissing(metrics, 'cache_read') !== 0) return null
  return `${(cache_read / input * 100).toLocaleString('en-US', { maximumFractionDigits: 1 })}%`
}

export function reviewFacts(metrics: AgentStatsMetrics): string {
  const names: Record<string, string> = { success: 'Succeeded', repair: 'Changes requested', blocked: 'Blocked', failed: 'Failed', validation_failed: 'Validation failed' }
  const facts = Object.entries(metrics.review_outcomes ?? {}).map(([key, count]) => `${names[key] ?? key}: ${count}`)
  return `${facts.length ? facts.join(' · ') : 'No recorded review outcomes'}. Explicit review roles only. ${metrics.coverage.classification_known_attempts} attempts classified; ${metrics.coverage.classification_unknown_attempts} unclassified. Not an approval rate.`
}

export function groupLabel(row: AgentStatsRow, group: 'agent' | 'model', global = false): string {
  if (group === 'model') return row.model ?? 'Model not recorded'
  return `${row.agent_name ?? 'Agent not recorded'}${global ? ` · ${row.project_name ?? `Project #${row.project_id}`}` : ''}`
}

export interface ActivitySeries { key: string; label: string; values: (number | null)[]; token: string }
export const activityColorTokens = ['accent-primary', 'info-default', 'success-default', 'warning-default', 'danger-default', 'fg-subtle']

// Project backend daily projections only. No receipt parsing or regrouping here.
export function activitySeries(days: AgentStatsDay[], metric: ActivityMetric, color: ActivityColor, global = false): ActivitySeries[] {
  if (metric === 'input' && color === 'input_type') {
    const portions: ActivitySeries[] = [
      { key: 'cache_read', label: 'Cache read', token: 'accent-primary', values: days.map(day => day.reported_usage.cache_read) },
      { key: 'remaining_input', label: 'Remaining input', token: 'info-default', values: days.map(day => day.reported_usage.remaining_input) },
    ]
    if (days.some(day => (day.reported_usage.input_with_unknown_cache ?? 0) > 0)) portions.push({ key: 'input_with_unknown_cache', label: 'Cache split not reported', token: 'fg-subtle', values: days.map(day => day.reported_usage.input_with_unknown_cache) })
    return portions
  }
  const grouping = color === 'agent' ? 'agent' : 'model'
  const source = grouping === 'agent' ? 'by_agent' : 'by_model'
  const identities = new Map<string, AgentStatsRow>()
  for (const day of days) for (const row of day[source]) identities.set(row.group_key, row)
  return [...identities].map(([key, row], index) => ({
    key, label: groupLabel(row, grouping, global), token: activityColorTokens[index % activityColorTokens.length]!,
    values: days.map(day => {
      const item = day[source].find(candidate => candidate.group_key === key)
      return !item ? 0 : metric === 'attempts' ? item.launch_attempts : item.reported_usage[metric]
    }),
  }))
}

export function localActivityFilters(now = new Date()): AgentStatsFilters {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 6)
  const localDate = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
  return { start_date: localDate(start), end_date: localDate(now), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, group_by: 'model' as ActivityGroup }
}

export function usageValue(metrics: AgentStatsMetrics | null | undefined): string {
  return metrics?.reported_usage.total == null ? 'Unavailable' : metrics.reported_usage.total.toLocaleString('en-US')
}

export function usageCoverage(metrics: AgentStatsMetrics | null | undefined): string {
  if (!metrics) return 'Usage not available'
  const c = metrics.coverage
  if (c.usage_status === 'empty') return 'No recorded attempts'
  return `${c.usage_status === 'complete' ? 'Complete' : c.usage_status === 'partial' ? 'Partial' : 'Unavailable'} · ${c.usage_measured_attempts} of ${metrics.launch_attempts} attempts measured`
}

export function usageLabel(metrics: AgentStatsMetrics | null | undefined): string {
  if (metrics?.coverage.usage_status === 'empty') return 'No recorded attempts'
  if (metrics?.reported_usage.total == null) return 'Tokens unavailable'
  return `${usageValue(metrics)} tokens${metrics.coverage.usage_status === 'partial' ? ' · Partial' : ''}`
}

export function rejectCoverage(metrics: AgentStatsMetrics): string {
  const c = metrics.coverage
  if (c.classification_status === 'empty') return 'No recorded attempts'
  return `${c.classification_status === 'complete' ? 'Complete' : c.classification_status === 'partial' ? 'Partial' : 'Unavailable'} · ${c.classification_known_attempts} of ${metrics.launch_attempts} attempts classified`
}

export function rejectValue(metrics: AgentStatsMetrics): string {
  return metrics.coverage.classification_status === 'unavailable'
    ? 'Unavailable'
    : metrics.recorded_reviewer_rejects.toLocaleString('en-US')
}
