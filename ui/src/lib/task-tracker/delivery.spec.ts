import { describe, expect, it } from 'vitest'
import type { TrackerTask } from './types'
import { isAutomaticDeliveryTask, taskDeliveryState } from './delivery'

const agent = { name: 'worker', instructions: 'Execute the assigned contract.' }
const delivery = { status: 'queued', job_ref: 'task-delivery:1:2', delivery_agent: agent, review_agent: agent }
const task = (metadata_json: unknown) => ({ metadata_json }) as Pick<TrackerTask, 'metadata_json'>

describe('automatic delivery task indication', () => {
  it('projects a stopped execution independently and discards its old reason after resume', () => {
    const metadata = { delivery: { ...delivery, status: 'stopped', ticket_key: 'fix-chart', reason: 'Browser access denied.' } }
    const before = structuredClone(metadata)
    expect(taskDeliveryState(task(metadata))).toEqual({ status: 'stopped', ticketKey: 'fix-chart', reason: 'Browser access denied.' })
    expect(metadata).toEqual(before)
    expect(taskDeliveryState(task({ delivery: { ...metadata.delivery, status: 'running' } }))).toEqual({ status: 'running', ticketKey: 'fix-chart', reason: null })
    expect(taskDeliveryState(task({ delivery: { ...delivery, ticket_key: {}, reason: [] } }))).toEqual({ status: 'queued', ticketKey: null, reason: null })
  })
  it.each(['queued', 'running', 'stopped', 'complete'])('retains the method for %s execution', (status) => {
    expect(isAutomaticDeliveryTask(task({ delivery: { ...delivery, status } }))).toBe(true)
  })

  it.each([
    undefined, null, [], 'automatic', {}, { delivery: null }, { delivery: [] }, { delivery: true },
    { delivery: 'automatic' }, { delivery: {} }, { delivery: { status: 'running' } },
    { delivery: { ...delivery, status: 'unknown' } }, { delivery: { ...delivery, status: ['running'] } },
    { delivery: { ...delivery, job_ref: '' } }, { delivery: { ...delivery, job_ref: '  ' } },
    { delivery: { ...delivery, job_ref: 1 } }, { delivery: { ...delivery, delivery_agent: null } },
    { delivery: { ...delivery, delivery_agent: [] } }, { delivery: { ...delivery, review_agent: {} } },
    { delivery: { ...delivery, review_agent: { name: 'reviewer', instructions: '' } } },
  ])('ignores absent or malformed metadata: %j', (metadata) => {
    expect(isAutomaticDeliveryTask(task(metadata))).toBe(false)
  })

  it('does not infer a handoff from project defaults or other task identity', () => {
    expect(isAutomaticDeliveryTask(task({ config_json: { delivery } }))).toBe(false)
    expect(isAutomaticDeliveryTask(null)).toBe(false)
    expect(isAutomaticDeliveryTask(undefined)).toBe(false)
  })
})
