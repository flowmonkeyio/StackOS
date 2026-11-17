import { describe, expect, it } from 'vitest'
import type { TrackerTask } from './types'
import { isAutomaticDeliveryTask, taskDeliveryState } from './delivery'

const agent = { name: 'worker', instructions: 'Execute the assigned contract.' }
const delivery = { status: 'queued', job_ref: 'task-delivery:1:2', delivery_agent: agent, review_agent: agent }
const task = (metadata_json: unknown) => ({ metadata_json }) as Pick<TrackerTask, 'metadata_json'>

describe('automatic delivery task indication', () => {
  it.each([
    'file:///private/tmp/native-result.json',
    'file:/private/tmp/native-result.json',
    'file://localhost/private/tmp/native-result.json',
    'file://workstation/share/native-result.json',
    'FILE:///C:/Users/operator/native-result.json',
    'C:/Users/operator/native-result.json',
    String.raw`C:\Users\operator\native-result.json`,
    String.raw`\\workstation\share\native-result.json`,
    '/private/tmp/native-result.json',
  ])('masks local representation %s in retained metadata and lightweight delivery', (path) => {
    const link = 'https://docs.example.test/help?q=stop#details'
    const reason = `Native failed at "${path}" (again ${path}). See ${link}`
    const metadata = { delivery: { ...delivery, status: 'stopped', reason } }
    const before = structuredClone(metadata)
    const full = taskDeliveryState(task(metadata))!
    expect(full.reason).toBe(`Native failed at "[local path]" (again [local path]). See ${link}`)
    expect(taskDeliveryState({ delivery: { ...full, reason } })).toEqual(full)
    expect(metadata).toEqual(before)
  })
  it.each([null, undefined, [], {}, 42, '  '])('handles absent or malformed legacy reason %j', (reason) => {
    const full = taskDeliveryState(task({ delivery: { ...delivery, status: 'stopped', reason } }))!
    expect(full.reason).toBeNull()
    expect(taskDeliveryState({ delivery: { ...full, reason } as never })?.reason).toBeNull()
  })
  it('retains remote links and already redacted secrets', () => {
    const reason = 'api_key=[redacted]; http://example.test/help https://example.test/C:/docs?next=/docs&example=C:/docs'
    expect(taskDeliveryState(task({ delivery: { ...delivery, status: 'stopped', reason } }))?.reason).toBe(reason)
  })
  it('keeps legacy prose bounded and masks local filesystem paths', () => {
    const reason = 'Native failed at /private/tmp/native.json ' + 'x'.repeat(700)
    const full = taskDeliveryState(task({ delivery: { ...delivery, status: 'stopped', reason } }))!
    expect(full.reason).toHaveLength(500)
    expect(full.reason).toContain('[local path]')
    expect(full.reason).not.toContain('/private/tmp')
    expect(taskDeliveryState({ delivery: { ...full, reason } })).toEqual(full)
  })
  it('projects the same bounded context from full metadata and lightweight delivery', () => {
    const stop_context = { code: 'no_ready_work', facts: ['explicit_blocker', 'dependency_wait'],
      phase: null, evidence_refs: ['tracker-ticket:1:2'], next_owner: 'main_agent',
      next_action: 'reconcile_prerequisites', resume_condition: 'prerequisites_ready',
      private: { path: '/private/tmp/native', prompt: 'frozen instructions' } }
    const full = taskDeliveryState(task({ delivery: { ...delivery, status: 'stopped', stop_context } }))!
    const light = taskDeliveryState({ delivery: { ...full, stop_context } as never })!
    expect(full).toEqual(light)
    expect(full.stop_context?.facts).toEqual(['explicit_blocker', 'dependency_wait'])
    expect(JSON.stringify(full)).not.toContain('private')
    expect(taskDeliveryState({ delivery: { ...full, status: 'running' } })?.stop_context).toBeUndefined()
    for (const malformed of [null, [], {}, { code: 'surprise' }, { code: 'unknown', evidence_refs: ['/private/tmp/native'] }]) {
      expect(taskDeliveryState(task({ delivery: { ...delivery, status: 'stopped', reason: 'Legacy', stop_context: malformed } }))?.reason).toBe('Legacy')
      expect(taskDeliveryState({ delivery: { ...full, stop_context: malformed } as never })?.stop_context).toBeUndefined()
    }
  })
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
