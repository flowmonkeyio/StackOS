import { describe, expect, it } from 'vitest'
import type { TrackerTask } from './types'
import { isAutomaticDeliveryTask } from './delivery'

const agent = { name: 'worker', instructions: 'Execute the assigned contract.' }
const delivery = { status: 'queued', job_ref: 'task-delivery:1:2', delivery_agent: agent, review_agent: agent }
const task = (metadata_json: unknown) => ({ metadata_json }) as Pick<TrackerTask, 'metadata_json'>

describe('automatic delivery task indication', () => {
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
