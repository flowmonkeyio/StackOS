import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { agentStatsFixture } from '@/views/home/agentStatsFixtures'
import type { ActiveExecution } from '@/lib/task-tracker/types'
import AgentUsageSummary from './AgentUsageSummary.vue'
import LiveUsageStrip from './LiveUsageStrip.vue'
import ActiveExecutionLine from './ActiveExecutionLine.vue'
import type { SchemaUsageSampleOut } from '@/api'

function execution(): ActiveExecution {
  return {
    invocation_id: 'current', agent: 'Worker', host_key: 'codex', model: 'selected', ticket_key: 'child', started_at: '2026-11-08T01:00:00Z',
    live_usage: { usage: { status: 'measured', unit: 'tokens', measured: agentStatsFixture().totals.reported_usage }, measured_at: '2026-11-08T01:00:05Z', samples: [0, 5].map((seconds) => ({ at: `2026-11-08T01:00:0${seconds}Z`, input: 100 + seconds, cache_read: 80 + seconds, output: 5 + seconds, total: 105 + seconds * 2, cache_write: null, reasoning: null, remaining_input: null })) },
  }
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('compact measured usage fitting', () => {
  it('keeps three slots and the footer on the same call through sparse, measured and missing history, then clears them on exit', async () => {
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(300)
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockImplementation(function (this: HTMLElement) { return this.textContent!.trim().length * 6 })
    const metrics = agentStatsFixture().totals
    const active = execution()
    const summary = mount(AgentUsageSummary, { props: { metrics, compact: true, execution: { ...active, live_usage: null } } })
    const strip = mount(LiveUsageStrip, { props: { execution: { ...active, live_usage: null } } })
    const footer = mount(ActiveExecutionLine, { props: { execution: active } })
    const sample = (second: number, cache: number | null = 80 + second): SchemaUsageSampleOut => ({ at: `2026-11-08T01:00:0${second}Z`, input: 100 + second * 2, cache_read: cache, output: 5 + second, total: 105 + second * 3, cache_write: null, reasoning: null, remaining_input: null })
    const states: [SchemaUsageSampleOut[], string[]][] = [
      [[], ['empty', 'empty', 'empty']],
      [[sample(0)], ['point', 'point', 'point']],
      [[sample(0), sample(1), sample(2)], ['trace', 'trace', 'trace']],
      [[sample(3, null), sample(4, null)], ['empty', 'empty', 'trace']],
      [[sample(3, null), sample(4, null), sample(5)], ['point', 'point', 'trace']],
    ]
    for (const [samples, expected] of states) {
      const last = samples.at(-1)
      const current = { ...active, live_usage: last ? { ...active.live_usage!, measured_at: last.at, samples, usage: { ...active.live_usage!.usage, measured: { ...metrics.reported_usage, ...last } } } : null }
      await summary.setProps({ execution: current })
      await strip.setProps({ execution: current })
      await footer.setProps({ execution: current })
      await flushPromises()
      for (const wrapper of [summary, strip]) {
        const charts = wrapper.findAll('.usage-trace')
        expect(charts.map(chart => chart.attributes('data-state'))).toEqual(expected)
        expect(wrapper.findAll('circle')).toHaveLength(expected.filter(state => state === 'point').length)
        expect(wrapper.findAll('path')).toHaveLength(expected.filter(state => state === 'trace').length)
      }
      expect(summary.findAll('dd').map(item => item.text())).toEqual(['30', '40', '30'])
      expect(footer.find('[data-active-execution]').exists()).toBe(true)
      expect(strip.find('section').exists()).toBe(true)
      if (!samples.length) expect(strip.findAll('dd').map(item => item.text())).toEqual(['N/A', 'N/A', 'N/A'])
    }
    const zero = { ...sample(0), input: 0, cache_read: 0, output: 0, total: 0 }
    const zeroCall = { ...active, invocation_id: 'new-zero-call', live_usage: { ...active.live_usage!, samples: [zero], usage: { ...active.live_usage!.usage, measured: { ...metrics.reported_usage, ...zero, remaining_input: 0 } } } }
    await strip.setProps({ execution: zeroCall })
    expect(strip.findAll('dd').map(item => item.text())).toEqual(['0', '0', '0'])
    expect(strip.findAll('circle')).toHaveLength(3)
    for (const wrapper of [summary, strip, footer]) await wrapper.setProps({ execution: null })
    expect(summary.findAll('.usage-trace')).toHaveLength(0)
    expect(strip.find('section').exists()).toBe(false)
    expect(footer.find('[data-active-execution]').exists()).toBe(false)
    for (const wrapper of [summary, strip, footer]) wrapper.unmount()
  })

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
