import type { ActiveExecution } from './types'

export function activeExecutionTitle(execution: ActiveExecution): string {
  return `${execution.agent} · ${execution.model ?? 'Model not recorded'} · Started ${new Date(execution.started_at).toLocaleString()} · ${execution.host_key}`
}
