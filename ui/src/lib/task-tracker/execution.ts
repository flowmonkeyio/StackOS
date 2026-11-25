import type { SchemaExecutionIdentityOut } from '@/api'
import { resolveStatus, type StatusDef } from '@/design/status'
import type { ActiveExecution } from './types'
import type { TaskDeliveryState } from './delivery'

export function activeExecutionTitle(execution: SchemaExecutionIdentityOut): string {
  return `${execution.agent} · ${execution.model ?? 'Model not recorded'} · Started ${new Date(execution.started_at).toLocaleString()} · ${execution.host_key}`
}

export function executionRole(agent: string): string {
  return agent.replace(/^sdlc[_-]/i, '').replace(/[_-]+/g, ' ').replace(/^./, value => value.toUpperCase())
}

/** Presentation of existing occurrence/lifecycle facts, never a new execution state. */
export function executionStatus(status: string, execution?: ActiveExecution | null, delivery?: TaskDeliveryState | null): StatusDef {
  if (execution) {
    const label = !execution.live_usage ? 'Starting' : /review/i.test(execution.agent) ? 'Reviewing' : /deliver/i.test(execution.agent) ? 'Delivering' : 'Active now'
    return { ...resolveStatus('tracker', 'in-progress'), label }
  }
  if (delivery?.status === 'stopped') return { ...resolveStatus('delivery', 'stopped'), label: 'Stopped' }
  if (delivery?.status === 'queued') return resolveStatus('run', 'queued')
  return resolveStatus('tracker', status)
}
