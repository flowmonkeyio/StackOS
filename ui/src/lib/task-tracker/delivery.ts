import type { TrackerTask } from './types'

/** The persisted handoff identifies the delivery method even after execution ends. */
export function isAutomaticDeliveryTask(task: Pick<TrackerTask, 'metadata_json'> | null | undefined): boolean {
  const metadata = task?.metadata_json
  if (!isRecord(metadata)) return false
  const delivery = metadata.delivery
  return isRecord(delivery)
    && typeof delivery.status === 'string'
    && ['queued', 'running', 'stopped', 'complete'].includes(delivery.status)
    && nonemptyString(delivery.job_ref)
    && isFrozenAgent(delivery.delivery_agent)
    && isFrozenAgent(delivery.review_agent)
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
