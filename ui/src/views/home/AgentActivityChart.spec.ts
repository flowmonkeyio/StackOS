import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import AgentActivityChart from './AgentActivityChart.vue'
import { agentStatsFixture } from './agentStatsFixtures'

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
  it('renders cache + remaining + unknown split without double counting and exposes exact keyboard values', async () => {
    const wrapper = render()
    const config = mocks.created.mock.calls[0]![0]
    expect(config.data.datasets.map((item: { data: number[] }) => item.data)).toEqual([[40], [30]])
    expect(config.options.scales.x.stacked).toBe(true)
    expect(config.options.scales.y.stacked).toBe(true)
    expect(wrapper.get('[aria-label="Chart legend"]').text()).not.toContain('Cache split not reported')
    await wrapper.get('canvas').trigger('focus')
    expect(wrapper.get('[aria-live="polite"]').text()).toContain('2026-11-06 · 70 tokens')
    expect(wrapper.get('[aria-live="polite"]').text()).toContain('Cache read: 40')
    expect(mocks.active).toHaveBeenCalledWith([{ datasetIndex: 0, index: 0 }, { datasetIndex: 1, index: 0 }])
    expect(wrapper.get('table[aria-label="Exact daily usage"]').text()).toContain('70')
    config.options.onHover({}, [{ index: 0 }])
    await nextTick()
    expect(wrapper.get('[aria-live="polite"]').text()).toContain('Remaining input: 30')
    expect(config.options.plugins.tooltip.callbacks.footer([{ dataIndex: 0 }])).toEqual(['Total: 70'])
    wrapper.unmount()
    expect(mocks.destroyed).toHaveBeenCalledOnce()
  })

  it('switches metrics/colors locally, resets incompatible Input type and retains exact attempts/model identity', async () => {
    const wrapper = render()
    const selects = wrapper.findAllComponents({ name: 'UiSelect' })
    selects[0]!.vm.$emit('update:modelValue', 'output')
    await nextTick()
    expect(selects[1]!.props('modelValue')).toBe('model')
    expect(selects[1]!.props('options').map((item: { value: string }) => item.value)).toEqual(['agent', 'model'])
    expect(wrapper.text()).toContain('Output tokens per day')
    expect(wrapper.get('[aria-label="Chart legend"]').text()).toBe('recorded-sol')
    selects[1]!.vm.$emit('update:modelValue', 'agent')
    selects[0]!.vm.$emit('update:modelValue', 'attempts')
    await nextTick()
    expect(wrapper.get('[aria-label="Chart legend"]').text()).toBe('same-name')
    await wrapper.get('canvas').trigger('focus')
    expect(wrapper.get('[aria-live="polite"]').text()).toContain('4 attempts')
    expect(mocks.created).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('keeps keyboard day selection stable while the pointer is still over another bar', async () => {
    const days = agentStatsFixture().daily
    days.push({ ...structuredClone(days[0]!), date: '2026-11-07' })
    const wrapper = mount(AgentActivityChart, { attachTo: document.body, props: { days, timezone: 'America/Los_Angeles' } })
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
    await canvas.trigger('blur')
    expect(chart.options.plugins.tooltip.enabled).toBe(true)
    wrapper.unmount()
  })

  it('emits the incompatible-color reset before a controlling parent updates its metric prop', async () => {
    const wrapper = mount(AgentActivityChart, { props: { days: agentStatsFixture().daily, timezone: 'UTC', metric: 'input', color: 'input_type', 'onUpdate:metric': () => {}, 'onUpdate:color': () => {} } })
    wrapper.findAllComponents({ name: 'UiSelect' })[0]!.vm.$emit('update:modelValue', 'output')
    await nextTick()
    expect(wrapper.emitted('update:metric')?.at(-1)).toEqual(['output'])
    expect(wrapper.emitted('update:color')?.at(-1)).toEqual(['model'])
    wrapper.unmount()
  })

  it('keeps missing cache null, zero measured and updates semantic colors with the theme', async () => {
    vi.spyOn(window, 'getComputedStyle').mockImplementation(() => ({ fontFamily: 'system-ui', getPropertyValue: (key: string) => `${document.documentElement.dataset.theme ?? 'light'}:${key}` }) as CSSStyleDeclaration)
    const wrapper = render()
    const days = agentStatsFixture().daily
    days[0]!.reported_usage = { ...days[0]!.reported_usage, cache_read: null, remaining_input: null, input_with_unknown_cache: 70 }
    await wrapper.setProps({ days })
    const chart = mocks.updated.mock.contexts.at(-1) as { data: { datasets: { data: (number | null)[]; backgroundColor: string }[] } }
    expect(chart.data.datasets.map(item => item.data)).toEqual([[null], [null], [70]])
    document.documentElement.dataset.theme = 'dark'
    await vi.waitFor(() => expect(chart.data.datasets[0]!.backgroundColor).toBe('dark:--color-accent-primary'))
    wrapper.unmount()
    expect(mocks.destroyed).toHaveBeenCalledOnce()
  })
})
