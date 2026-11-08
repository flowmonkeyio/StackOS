import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { agentStatsFixture } from '@/views/home/agentStatsFixtures'
import type { ActiveExecution } from '@/lib/task-tracker/types'
import AgentUsageSummary from './AgentUsageSummary.vue'

function execution(): ActiveExecution {
  return {
    invocation_id: 'current', agent: 'Worker', host_key: 'codex', model: 'selected', ticket_key: 'child', started_at: '2026-11-08T01:00:00Z',
    live_usage: { usage: { status: 'measured', unit: 'tokens', measured: agentStatsFixture().totals.reported_usage }, measured_at: '2026-11-08T01:00:05Z', samples: [0, 5].map((seconds) => ({ at: `2026-11-08T01:00:0${seconds}Z`, input: 100 + seconds, cache_read: 80 + seconds, output: 5 + seconds, total: 105 + seconds * 2, cache_write: null, reasoning: null })) },
  }
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('compact measured usage fitting', () => {
  it('remeasures updated values and invocation history at a fixed width, then releases its resize observer', async () => {
    const disconnect = vi.fn()
    let resize!: () => void
    vi.stubGlobal('ResizeObserver', class {
      constructor(callback: () => void) { resize = callback }
      observe() {}
      disconnect = disconnect
    })
    let available = 260
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(() => available)
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockImplementation(function (this: HTMLElement) {
      return this.textContent!.trim().length * 6
    })
    const metrics = agentStatsFixture().totals
    const wrapper = mount(AgentUsageSummary, { props: { metrics, compact: true, execution: execution() } })
    await flushPromises()
    expect(wrapper.findAll('.usage-trace')).toHaveLength(3)
    const long = structuredClone(metrics)
    Object.assign(long.reported_usage, { input: 2e15, remaining_input: 1e15, cache_read: 1e15, output: 1e15, total: 3e15 })
    await wrapper.setProps({ metrics: long })
    await flushPromises()
    expect(wrapper.findAll('.usage-trace')).toHaveLength(0)
    await wrapper.setProps({ metrics })
    await flushPromises()
    expect(wrapper.findAll('.usage-trace')).toHaveLength(3)
    available = 180
    resize()
    await flushPromises()
    expect(wrapper.findAll('.usage-trace')).toHaveLength(0)
    available = 260
    resize()
    await flushPromises()
    expect(wrapper.findAll('.usage-trace')).toHaveLength(3)
    await wrapper.setProps({ execution: null })
    expect(wrapper.findAll('.usage-trace')).toHaveLength(0)
    await wrapper.setProps({ execution: execution() })
    await flushPromises()
    expect(wrapper.findAll('.usage-trace')).toHaveLength(3)
    wrapper.unmount()
    expect(disconnect).toHaveBeenCalledOnce()
  })
})
