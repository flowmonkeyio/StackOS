import type { SchemaTrackerAgentStatsOut } from '@/api'

export type AgentStats = SchemaTrackerAgentStatsOut
export type AgentStatsRow = AgentStats['rows'][number]
export type AgentStatsMetrics = AgentStats['totals']
export type AgentStatsDay = AgentStats['daily'][number]
export type UsageField = 'input' | 'cache_read' | 'output' | 'cache_write' | 'reasoning'
export type ActivityView = 'tokens' | 'agents'
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
  const names: Record<string, string> = { success: 'Reviews passed', repair: 'Changes requested', blocked: 'Reviews blocked', failed: 'Reviews failed', validation_failed: 'Invalid review results' }
  return Object.entries(metrics.review_outcomes ?? {}).filter(([key]) => names[key]).map(([key, count]) => `${names[key]}: ${count}`).join(' · ')
}

export function agentLabel(name: string | null): string {
  if (!name) return 'Agent not recorded'
  const plain = name.replace(/^sdlc_/, '').replaceAll('_', ' ')
  return plain.charAt(0).toUpperCase() + plain.slice(1)
}

export function groupLabel(row: AgentStatsRow, group: 'agent' | 'model', global = false): string {
  if (group === 'model') return row.model ?? 'Model not recorded'
  return `${agentLabel(row.agent_name)}${global ? ` · ${row.project_name ?? `Project #${row.project_id}`}` : ''}`
}

export interface ActivitySeries { key: string; label: string; values: (number | null)[]; token: string }
export const activityColorTokens = ['accent-primary', 'info-default', 'success-default', 'warning-default', 'danger-default', 'fg-subtle']

// Project backend daily projections only. No receipt parsing or regrouping here.
export function activitySeries(days: AgentStatsDay[], view: ActivityView, global = false): ActivitySeries[] {
  if (view === 'tokens') {
    // The repository subtracts cache only for paired measurements. Subtracting
    // aggregate Cache from inclusive Input would invent a remainder for old receipts.
    return [
      { key: 'cache_read', label: 'Cache', token: 'accent-primary' },
      { key: 'remaining_input', label: 'New input', token: 'info-default' },
      { key: 'output', label: 'Output', token: 'success-default' },
    ].filter(item => days.some(day => day.launch_attempts > 0 && day.reported_usage[item.key as 'cache_read' | 'remaining_input' | 'output'] != null))
      .map(item => ({ ...item, values: days.map(day => day.reported_usage[item.key as 'cache_read' | 'remaining_input' | 'output']) }))
  }
  const identities = new Map<string, AgentStatsRow>()
  for (const day of days) for (const row of day.by_agent) if (row.reported_usage.total != null) identities.set(row.group_key, row)
  return [...identities].map(([key, row], index) => ({
    key, label: groupLabel(row, 'agent', global), token: activityColorTokens[index % activityColorTokens.length]!,
    values: days.map(day => {
      const item = day.by_agent.find(candidate => candidate.group_key === key)
      return !item ? (day.reported_usage.total == null ? null : 0) : item.reported_usage.total
    }),
  }))
}

export function localActivityFilters(now = new Date()): AgentStatsFilters {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 6)
  const localDate = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
  return { start_date: localDate(start), end_date: localDate(now), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, group_by: 'model' as ActivityGroup }
}
