import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import AgentActivityChart from './AgentActivityChart.vue'
import { agentStatsFixture, groupedAgentStatsFixture } from './agentStatsFixtures'

const mocks = vi.hoisted(() => ({ created: vi.fn(), updated: vi.fn(), destroyed: vi.fn(), active: vi.fn() }))
vi.mock('chart.js', () => ({
  Chart: class {
    static register = vi.fn()
    data: unknown
    options: unknown
    constructor(_canvas: unknown, config: { data: unknown; options: unknown }) { this.data = config.data; this.options = config.options; mocks.created(config) }
    update = mocks.updated
    destroy = mocks.destroyed
    setActiveElements = mocks.active
  }, BarController: {}, BarElement: {}, CategoryScale: {}, LinearScale: {}, Tooltip: {},
}))
afterEach(() => { vi.clearAllMocks(); vi.restoreAllMocks(); delete document.documentElement.dataset.theme })
function render() { return mount(AgentActivityChart, { props: { days: agentStatsFixture().daily, timezone: 'America/Los_Angeles' } }) }

describe('Daily stacked usage chart', () => {
  it.each(['agent', 'model', 'agent_model'] as const)('uses %s identities in bars, legend, pointer tooltip and keyboard values', async group => {
    const fixture = groupedAgentStatsFixture(group)
    const wrapper = mount(AgentActivityChart, { props: { days: fixture.daily, timezone: 'UTC', group, view: 'agents' } })
    const config = mocks.created.mock.calls[0]![0]
    const labels = config.data.datasets.map((item: { label: string }) => item.label)
    expect(wrapper.findAll('.activity-chart__legend-label').map(item => item.text())).toEqual(labels)
    config.options.plugins.tooltip.external({ tooltip: { opacity: 1, dataPoints: [{ dataIndex: 0 }] } })
    await nextTick()
    expect(wrapper.findAll('.activity-chart__tooltip-label').map(item => item.text())).toEqual(labels)
    expect(wrapper.get('[role="tooltip"] footer').text()).toBe('Tokens: 600')
    await wrapper.get('canvas').trigger('focus')
    expect(wrapper.get('[aria-live="polite"]').text()).toContain('Tokens: 600 tokens')
    for (const label of labels) expect(wrapper.get('[aria-live="polite"]').text()).toContain(label)
    await wrapper.setProps({ group: group === 'model' ? 'agent_model' : 'model' })
    expect(mocks.updated).toHaveBeenCalled()
    expect(mocks.created).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('renders N/A in retained daily slots and exact keyboard values while chart samples stay null', async () => {
    const days = agentStatsFixture().daily
    days.push({ ...agentStatsFixture().undated, date: '2026-11-07', by_agent: [], by_model: [], by_agent_model: [] })
    const wrapper = mount(AgentActivityChart, { props: { days, timezone: 'UTC' } })
    const config = mocks.created.mock.calls[0]![0]
    expect(config.data.datasets.map((item: { data: (number | null)[] }) => item.data)).toEqual([[30, null], [40, null], [30, null]])
    expect(wrapper.findAll('tbody tr:last-child td').map(item => item.text())).toEqual(['N/A', 'N/A', 'N/A', 'N/A'])
    expect(wrapper.get('tbody tr:last-child [aria-label^="Total:"]').attributes('title')).toBe('Total: N/A')
    await wrapper.get('canvas').trigger('focus')
    await wrapper.get('canvas').trigger('keydown', { key: 'End' })
    expect(wrapper.get('[aria-live="polite"]').text()).toContain('Tokens: N/A')
    expect(wrapper.get('[aria-live="polite"]').text()).toContain('Input: N/A')
    expect(wrapper.get('[aria-live="polite"]').text()).not.toContain('0 tokens')
    wrapper.unmount()
  })

  it('keeps exact labels and mixed paired values aligned in bars, hover, keyboard and daily rows', async () => {
    const days = agentStatsFixture().daily
    days[0]!.reported_usage = { ...days[0]!.reported_usage, input: 150, cache_read: 80, remaining_input: 20, output: 8, total: 158, input_with_unknown_cache: 50 }
    const wrapper = mount(AgentActivityChart, { props: { days, timezone: 'UTC' } })
    const config = mocks.created.mock.calls[0]![0]
    expect(config.data.datasets.map((item: { label: string; data: number[] }) => [item.label, item.data])).toEqual([['Input', [20]], ['Cache', [80]], ['Output', [8]]])
    expect(wrapper.get('[aria-label^="Input:"]').attributes('aria-label')).toBe('Input: 20 tokens.')
    await wrapper.get('canvas').trigger('focus')
    expect(wrapper.get('[aria-live="polite"]').text()).toContain('Available tokens: 108 tokens')
    expect(wrapper.get('[aria-live="polite"]').text()).toContain('Input: 20')
    expect(wrapper.get('[aria-label^="Total:"]').text()).toBe('158')
    wrapper.unmount()
  })
  it('offers exactly two views with equal measured totals, exact values and a fixed daily table', async () => {
    const wrapper = render()
    const config = mocks.created.mock.calls[0]![0]
    expect(config.data.datasets.map((item: { data: number[] }) => item.data)).toEqual([[30], [40], [30]])
    expect(config.data.datasets.map((item: { label: string }) => item.label)).toEqual(['Input', 'Cache', 'Output'])
    expect(config.options.scales.x.stacked).toBe(true)
    expect(config.options.scales.y.stacked).toBe(true)
    expect(wrapper.findAll('button').map(item => item.text())).toEqual(['Tokens', 'Agents'])
    expect(wrapper.findComponent({ name: 'UiSelect' }).exists()).toBe(false)
    config.options.plugins.tooltip.external({ tooltip: { opacity: 1, dataPoints: [{ dataIndex: 0 }] } })
    await nextTick()
    expect(wrapper.get('[role="tooltip"] footer').text()).toBe('Tokens: 100')
    expect(wrapper.findAll('[role="tooltip"] li')).toHaveLength(3)
    await wrapper.get('canvas').trigger('focus')
    expect(wrapper.find('[role="tooltip"]').exists()).toBe(false)
    expect(wrapper.get('[aria-live="polite"]').text()).toContain('Tokens: 100 tokens')
    expect(wrapper.get('[aria-live="polite"]').classes()).toContain('sr-only')
    expect(wrapper.get('.activity-chart__selection').text()).toBe('Nov 6 · 100 tokens')
    expect(wrapper.get('.activity-chart__selection').text()).not.toContain('Cache:')
    expect(wrapper.get('[aria-live="polite"]').text()).toContain('Cache: 40')
    const headers = wrapper.findAll('th[scope="col"]').map(item => item.text())
    expect(headers).toEqual(['Date', 'Input', 'Cache', 'Output', 'Total'])
    await wrapper.findAll('button')[1]!.trigger('click')
    expect(wrapper.text()).toContain('Tokens by agent per day')
    expect(wrapper.get('[aria-label="Chart legend"]').text()).toBe('same-name')
    expect(wrapper.get('.activity-chart__legend-label').attributes('title')).toBe('same-name')
    const chart = mocks.updated.mock.contexts.at(-1) as { data: { datasets: { data: number[] }[] } }
    expect(chart.data.datasets.map(item => item.data)).toEqual([[100]])
    expect(wrapper.findAll('th[scope="col"]').map(item => item.text())).toEqual(headers)
    expect(mocks.created).toHaveBeenCalledOnce()
    wrapper.unmount()
    expect(mocks.destroyed).toHaveBeenCalledOnce()
  })

  it('keeps keyboard day selection stable while a pointer is over another bar', async () => {
    const days = agentStatsFixture().daily
    days.push({ ...structuredClone(days[0]!), date: '2026-11-07' })
    const wrapper = mount(AgentActivityChart, { attachTo: document.body, props: { days, timezone: 'UTC' } })
    const canvas = wrapper.get('canvas')
    ;(canvas.element as HTMLCanvasElement).focus()
    await canvas.trigger('keydown', { key: 'End' })
    mocks.created.mock.calls[0]![0].options.onHover({}, [{ index: 0 }])
    await nextTick()
    expect(wrapper.get('[aria-live="polite"]').text()).toContain('2026-11-07')
    await canvas.trigger('keydown', { key: 'Home' })
    expect(wrapper.get('[aria-live="polite"]').text()).toContain('2026-11-06')
    const chart = mocks.updated.mock.contexts.at(-1) as { options: { plugins: { tooltip: { enabled: boolean } } } }
    expect(chart.options.plugins.tooltip.enabled).toBe(false)
    ;(canvas.element as HTMLCanvasElement).blur()
    await nextTick()
    expect(chart.options.plugins.tooltip.enabled).toBe(false)
    wrapper.unmount()
  })

  it('keeps every agent value and the total available in hover details until leaving the plot', async () => {
    const days = agentStatsFixture().daily
    const row = days[0]!.by_agent[0]!
    days[0]!.by_agent = Array.from({ length: 12 }, (_, index) => ({ ...structuredClone(row), group_key: `agent-${index}`, agent_name: `Recorded agent ${index}` }))
    days[0]!.reported_usage.total = 1200
    const wrapper = mount(AgentActivityChart, { props: { days, timezone: 'UTC', view: 'agents' } })
    const config = mocks.created.mock.calls[0]![0]
    config.options.plugins.tooltip.external({ tooltip: { opacity: 1, dataPoints: [{ dataIndex: 0 }] } })
    await nextTick()
    expect(config.options.plugins.tooltip.enabled).toBe(false)
    expect(wrapper.findAll('[role="tooltip"] li')).toHaveLength(12)
    expect(wrapper.get('[role="tooltip"] footer').text()).toBe('Tokens: 1,200')
    expect(wrapper.findAll('[role="tooltip"] li').at(-1)!.text()).toContain('Recorded agent 11')
    config.options.plugins.tooltip.external({ tooltip: { opacity: 0, dataPoints: [] } })
    await nextTick()
    expect(wrapper.find('[role="tooltip"]').exists()).toBe(true)
    await wrapper.get('.activity-chart__plot').trigger('mouseleave')
    expect(wrapper.find('[role="tooltip"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('omits unsupported cache, distinguishes a partial stack and preserves zero/theme lifecycle', async () => {
    vi.spyOn(window, 'getComputedStyle').mockImplementation(() => ({ fontFamily: 'Inter', getPropertyValue: (key: string) => `${document.documentElement.dataset.theme ?? 'light'}:${key}` }) as CSSStyleDeclaration)
    const wrapper = render()
    const days = agentStatsFixture().daily
    days[0]!.reported_usage = { ...days[0]!.reported_usage, cache_read: null, remaining_input: null, input_with_unknown_cache: 70, output: 0, total: 70 }
    await wrapper.setProps({ days })
    const chart = mocks.updated.mock.contexts.at(-1) as { data: { datasets: { data: (number | null)[]; backgroundColor: string }[] }; options: { plugins: { tooltip: { external: (context: unknown) => void } } } }
    expect(chart.data.datasets.map(item => item.data)).toEqual([[0]])
    chart.options.plugins.tooltip.external({ tooltip: { opacity: 1, dataPoints: [{ dataIndex: 0 }] } })
    await nextTick()
    expect(wrapper.get('[role="tooltip"] footer').text()).toBe('Available tokens: 0')
    await wrapper.get('canvas').trigger('focus')
    expect(wrapper.get('[aria-live="polite"]').text()).toContain('Available tokens: 0 tokens')
    expect(wrapper.get('.activity-chart__selection').text()).toContain('available measurements')
    expect(wrapper.get('.activity-chart__selection').text()).not.toMatch(/Shown|Reported total|Cache:/)
    expect(wrapper.text()).not.toMatch(/Not reported|Cache split|New input/)
    expect(wrapper.findAll('th[scope="col"]').map(item => item.text())).toEqual(['Date', 'Output', 'Total'])
    document.documentElement.dataset.theme = 'dark'
    await vi.waitFor(() => expect(chart.data.datasets[0]!.backgroundColor).toBe('dark:--color-success-default'))
    wrapper.unmount()
  })
})
