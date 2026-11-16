import type { TrackerTask } from './types'

export interface TaskDeliveryState {
  status: 'queued' | 'running' | 'stopped' | 'complete'
  ticketKey: string | null
  reason: string | null
}

/** The persisted handoff identifies the delivery method even after execution ends. */
type DeliveryTask = { delivery?: TaskDeliveryState | null; metadata_json?: TrackerTask['metadata_json'] }

export function isAutomaticDeliveryTask(task: DeliveryTask | null | undefined): boolean {
  return taskDeliveryState(task) !== null
}

/** Execution state is separate from the task and workflow lifecycle. */
export function taskDeliveryState(task: DeliveryTask | null | undefined): TaskDeliveryState | null {
  if (task?.delivery !== undefined) return task.delivery
  const metadata = task?.metadata_json
  if (!isRecord(metadata)) return null
  const delivery = metadata.delivery
  if (!isRecord(delivery)
    || typeof delivery.status !== 'string'
    || !['queued', 'running', 'stopped', 'complete'].includes(delivery.status)
    || !nonemptyString(delivery.job_ref)
    || !isFrozenAgent(delivery.delivery_agent)
    || !isFrozenAgent(delivery.review_agent)) return null
  return {
    status: delivery.status as TaskDeliveryState['status'],
    ticketKey: nonemptyString(delivery.ticket_key) ? delivery.ticket_key.trim() : null,
    reason: delivery.status === 'stopped' && nonemptyString(delivery.reason) ? delivery.reason.trim() : null,
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function nonemptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function isFrozenAgent(value: unknown): boolean {
  return isRecord(value) && nonemptyString(value.name) && nonemptyString(value.instructions)
}
