import type { SchemaTrackerAgentStatsOut } from '@/api'

export type AgentStats = SchemaTrackerAgentStatsOut
export type AgentStatsRow = AgentStats['rows'][number]
export type AgentStatsMetrics = AgentStats['totals']

export function usageValue(metrics: AgentStatsMetrics): string {
  return metrics.reported_usage.total === null ? 'Unavailable' : metrics.reported_usage.total.toLocaleString('en-US')
}

export function usageCoverage(metrics: AgentStatsMetrics): string {
  const c = metrics.coverage
  if (c.usage_status === 'empty') return 'No recorded attempts'
  return `${c.usage_status === 'complete' ? 'Complete' : c.usage_status === 'partial' ? 'Partial' : 'Unavailable'} · ${c.usage_measured_attempts} of ${metrics.launch_attempts} attempts measured`
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
