import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { agentStatsFixture } from '@/views/home/agentStatsFixtures'
import type { SchemaUsageSampleOut } from '@/api'
import type { ActiveExecution } from '@/lib/task-tracker/types'
import AgentUsageSummary from './AgentUsageSummary.vue'
import LiveUsageStrip from './LiveUsageStrip.vue'

const sample = (seconds: number, cache: number | null = 80): SchemaUsageSampleOut => ({
  at: `2026-11-08T01:00:0${seconds}Z`, input: 100 + seconds * 2, cache_read: cache,
  output: 5 + seconds, total: 105 + seconds * 3, cache_write: null, reasoning: null, remaining_input: null,
})
const execution: ActiveExecution = {
  invocation_id: 'current', agent: 'Worker', host_key: 'codex', model: 'selected',
  ticket_key: 'child', started_at: sample(0).at, live_usage: null,
}

describe('fixed compact lifetime usage', () => {
  it('keeps the same three canvases through empty, first point, line, stop and persisted reload', async () => {
    const metrics = agentStatsFixture().totals
    const wrapper = mount(AgentUsageSummary, { props: { metrics, compact: true } })
    const canvases = wrapper.findAll('canvas').map(item => item.element)
    expect(canvases).toHaveLength(3)
    expect(wrapper.findAll('dt').map(item => item.text())).toEqual(['Input', 'Cache', 'Output'])
    for (const [samples, states] of [
      [[], ['empty', 'empty', 'empty']],
      [[sample(0)], ['point', 'point', 'point']],
      [[sample(0), sample(2)], ['trace', 'trace', 'trace']],
      [[sample(3, null), sample(4, null)], ['empty', 'empty', 'trace']],
      [[sample(3, null), sample(4, null), sample(5)], ['point', 'point', 'trace']],
    ] as [SchemaUsageSampleOut[], string[]][]) {
      await wrapper.setProps({ metrics: { ...metrics, samples } })
      expect(wrapper.findAll('canvas').map(item => item.attributes('data-state'))).toEqual(states)
      expect(wrapper.findAll('canvas').map(item => item.element)).toEqual(canvases)
    }
    const terminal = { ...metrics, samples: [sample(0), sample(2)] }
    await wrapper.setProps({ metrics: terminal })
    expect(wrapper.findAll('canvas').map(item => item.attributes('data-state'))).toEqual(['trace', 'trace', 'trace'])
    expect(wrapper.attributes('title')).toContain('same task or ticket lifetime')
    expect(wrapper.attributes('title')).not.toContain('Worker')
    const reload = mount(AgentUsageSummary, { props: { metrics: JSON.parse(JSON.stringify(terminal)), compact: true } })
    expect(reload.findAll('canvas').map(item => item.attributes('data-state'))).toEqual(['trace', 'trace', 'trace'])
    expect(wrapper.find('svg').exists()).toBe(false)
    reload.unmount(); wrapper.unmount()
  })

  it('reserves slots across N/A, zero and K/M/B while preserving exact accessible values', async () => {
    const fixture = agentStatsFixture()
    const wrapper = mount(AgentUsageSummary, { props: { compact: true, metrics: fixture.undated } })
    const slots = wrapper.findAll('dd').map(item => item.element)
    expect(wrapper.findAll('dd').map(item => item.text())).toEqual(['N/A', 'N/A', 'N/A'])
    for (const [value, text] of [[0, '0'], [999, '999'], [1000, '1K'], [999000, '999K'], [1000000, '1.00M'], [999000000, '999.00M'], [1000000000, '1.00B'], [1e15, '1,000,000.00B']] as const) {
      const metrics = structuredClone(fixture.totals)
      Object.assign(metrics.reported_usage, { input: value * 2, remaining_input: value, cache_read: value, output: value })
      await wrapper.setProps({ metrics })
      expect(wrapper.findAll('dd').map(item => item.text())).toEqual([text, text, text])
      expect(wrapper.findAll('dd').map(item => item.element)).toEqual(slots)
      expect(wrapper.get('dd').attributes('aria-label')).toContain(`Input: ${value.toLocaleString('en-US')} tokens`)
      expect(wrapper.findAll('canvas')).toHaveLength(3)
    }
    const missing = structuredClone(fixture.totals)
    Object.assign(missing.reported_usage, { remaining_input: null, cache_read: null, output: 0 })
    await wrapper.setProps({ metrics: missing })
    expect(wrapper.findAll('dd').map(item => item.text())).toEqual(['N/A', 'N/A', '0'])
    wrapper.unmount()
  })

  it('keeps current-call details separately scoped and default analytics free of mini-canvases', async () => {
    const metrics = { ...agentStatsFixture().totals, samples: [sample(0), sample(2)] }
    const summary = mount(AgentUsageSummary, { props: { metrics, compact: true } })
    expect(summary.findAll('canvas').map(item => item.attributes('data-state'))).toEqual(['trace', 'trace', 'trace'])
    const strip = mount(LiveUsageStrip, { props: { execution } })
    expect(strip.findAll('canvas').map(item => item.attributes('data-state'))).toEqual(['empty', 'empty', 'empty'])
    await strip.setProps({ execution: null })
    expect(strip.find('section').exists()).toBe(false)
    await summary.setProps({ compact: false })
    expect(summary.find('canvas').exists()).toBe(false)
    expect(summary.findAll('dd').map(item => item.text())).toEqual(['30', '40', '30'])
    strip.unmount(); summary.unmount()
  })
})
