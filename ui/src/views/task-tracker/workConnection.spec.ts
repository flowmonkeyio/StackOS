import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { ActiveExecutionOutConnection_stateAnyOf0 as Connection, WorkDurationOutCoverage } from '@/api'
import { usageFeedKey } from '@/composables/useUsageFeed'
import { executionStatus } from '@/lib/task-tracker/execution'
import { taskDeliveryState } from '@/lib/task-tracker/delivery'
import type { UsageFeedState } from '@/lib/usageUpdates'
import ActiveExecutionLine from '@/components/domain/ActiveExecutionLine.vue'
import TaskDeliveryStop from '@/components/domain/TaskDeliveryStop.vue'

const execution = { invocation_id: 'owned', ticket_key: 'build', agent: 'sdlc_delivery', host_key: 'codex', model: 'selected', started_at: '2026-11-10T00:00:00Z', live_usage: null, stop_requested: false, connection_state: null }
const agent = { name: 'worker', instructions: 'Assigned work' }
const delivery = (extra: Record<string, unknown>) => taskDeliveryState({ metadata_json: { delivery: { status: 'stopped', job_ref: 'owned-job', delivery_agent: agent, review_agent: agent, ticket_key: 'build', reason: 'Connection interrupted', ...extra } } })
afterEach(() => vi.useRealTimers())

describe('Work connection observations', () => {
  it('distinguishes runtime loss from a healthy usage feed, then recovers without changing identity or recorded work time', async () => {
    vi.useFakeTimers({ toFake: ['Date'] }).setSystemTime(new Date('2026-11-10T00:14:00Z'))
    const feed = ref<UsageFeedState>({ status: 'live', receivedAt: Date.now() })
    const item = { status: 'in-progress' as const, work_duration: {
      coverage: WorkDurationOutCoverage.complete, total_seconds: 840,
      observed_at: '2026-11-10T00:14:00Z', endpoint_at: '2026-11-10T00:14:00Z',
      recorded_intervals: 1, unavailable_intervals: 0, issues: [], live: [execution],
    } }
    const wrapper = mount(ActiveExecutionLine, { props: { execution: { ...execution, connection_state: Connection.connected }, item }, global: { provide: { [usageFeedKey as symbol]: feed } } })
    expect(wrapper.get('.usage-live-indicator').text()).toBe('Live')
    vi.setSystemTime(new Date('2026-11-10T00:15:00Z'))
    feed.value = { ...feed.value, receivedAt: Date.now() }
    for (const [connection_state, label] of [[Connection.disconnected, 'Disconnected'], [Connection.needs_attention, 'Outcome unknown'], [Connection.connected, 'Live']] as const) {
      const observed = { ...execution, connection_state }
      await wrapper.setProps({ execution: observed })
      expect(wrapper.get('.usage-live-indicator').text()).toBe(label)
      expect(wrapper.get('.active-execution-line__elapsed').text()).toBe(label === 'Live' ? '15m' : '14m')
      if (label !== 'Live') expect(executionStatus('in-progress', observed).label).toBe(label)
    }
    await wrapper.setProps({ execution: { ...execution, connection_state: Connection.disconnected, stop_requested: true } })
    expect(wrapper.get('.usage-live-indicator').text()).toBe('Stop pending')
    expect(wrapper.find('.usage-live-indicator--live').exists()).toBe(false)
    feed.value = { ...feed.value, status: 'reconnecting' }
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.usage-live-indicator').attributes('title')).toContain('last received')
    await wrapper.setProps({ execution: null, lastExecution: execution })
    expect(wrapper.find('.usage-live-indicator').exists()).toBe(false)
    expect(wrapper.text()).toContain('Last: Delivery')
    expect(wrapper.text()).toContain('14m')
    wrapper.unmount()
  })

  it('retains uncertainty and unconfirmed stops after the invocation exits, until explicit reconciliation', async () => {
    const unknown = delivery({ uncertain_invocation: 'owned' })
    expect(executionStatus('failed', null, unknown).label).toBe('Outcome unknown')
    const wrapper = mount(TaskDeliveryStop, { props: { delivery: unknown, detailed: true } })
    expect(wrapper.text()).toContain('Outcome unknown')
    expect(wrapper.text()).toContain('Confirm the native outcome')
    const projected = taskDeliveryState({ delivery: { status: 'stopped', ticketKey: 'build', reason: 'Connection interrupted', needs_attention: true, stop_unconfirmed: false } })
    expect(executionStatus('failed', null, projected).label).toBe('Outcome unknown')
    const stopping = delivery({ needs_attention: true, cancellation: { status: 'stop_unconfirmed' } })
    expect(executionStatus('aborted', null, stopping).label).toBe('Stop pending')
    await wrapper.setProps({ delivery: stopping })
    expect(wrapper.text()).toContain('Stop pending')
    expect(wrapper.text()).toContain('not confirmed')
    expect(executionStatus('aborted', null, taskDeliveryState({ delivery: { status: 'stopped', ticketKey: 'build', reason: null, needs_attention: true, stop_unconfirmed: true } })).label).toBe('Stop pending')
    const reconciled = delivery({ needs_attention: false, uncertain_invocation: null, cancellation: { status: 'stop_unconfirmed' }, reconciliation: { confirmed_stopped: true } })
    await wrapper.setProps({ delivery: reconciled })
    expect(wrapper.text()).toContain('Delivery stopped')
    expect(wrapper.text()).not.toContain('Outcome unknown')
    expect(executionStatus('complete', null, delivery({ status: 'complete' })).label).toBe('Complete')
    wrapper.unmount()
  })
})
