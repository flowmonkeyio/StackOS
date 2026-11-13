import { enableAutoUnmount, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import AgentActivityChart from './AgentActivityChart.vue'
import DeliveryTrendChart from './DeliveryTrendChart.vue'
import AgentActivitySection from './AgentActivitySection.vue'
import { deliveryMetrics } from '@/lib/deliveryTrends'
import { agentStatsFixture, emptyDeliveryTrendsFixture } from './agentStatsFixtures'

enableAutoUnmount(afterEach)
const frame = () => new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
const mocks = vi.hoisted(() => ({ created: vi.fn(), updated: vi.fn(), destroyed: vi.fn() }))
vi.mock('chart.js', () => ({
  Chart: class {
    static register = vi.fn()
    data: unknown
    options: unknown
    scales = { x: { getValueForPixel: () => 1 } }
    constructor(_canvas: unknown, config: { data: unknown; options: unknown }) { this.data = config.data; this.options = config.options; mocks.created(config) }
    update = mocks.updated
    resize = vi.fn()
    destroy = mocks.destroyed
  }, BarController: {}, BarElement: {}, CategoryScale: {}, LinearScale: {}, LineController: {}, LineElement: {}, PointElement: {},
}))
afterEach(() => { vi.clearAllMocks(); vi.restoreAllMocks(); vi.unstubAllGlobals(); delete document.documentElement.dataset.theme })

describe('Daily tokens', () => {
  it('selects All/Input/Cache/Output without summing incomplete splits or fetching', async () => {
    const fixture = agentStatsFixture()
    fixture.daily[0]!.reported_usage = { ...fixture.daily[0]!.reported_usage, total: 158, input: 150, remaining_input: 20, cache_read: 80, output: 8, input_with_unknown_cache: 50 }
    const wrapper = mount(AgentActivityChart, { props: { days: fixture.daily, totals: fixture.totals, timezone: 'UTC' } })
    await frame()
    expect(mocks.created.mock.calls[0]![0].data.datasets[0].data).toEqual([158])
    expect(mocks.created.mock.calls[0]![0].data.datasets[0].maxBarThickness).toBe(8)
    const buttons = wrapper.findAll('button')
    expect(buttons.map(button => button.text())).toEqual(['All 100', 'Input 30', 'Cache 40', 'Output 30'])
    for (const [index, expected] of [[1, 20], [2, 80], [3, 8]]) {
      await buttons[index!]!.trigger('click'); await frame()
      const chart = mocks.updated.mock.contexts.at(-1) as { data: { datasets: { data: number[] }[] } }
      expect(chart.data.datasets[0]!.data).toEqual([expected])
    }
    await wrapper.get('canvas').trigger('focus')
    expect(wrapper.get('[role="tooltip"]').text()).toContain('Total: 158')
    expect(wrapper.get('[role="tooltip"]').text()).toContain('50 input tokens with unknown cache split')
    expect(wrapper.findAll('tbody td').map(item => item.text())).toEqual(['158', '20', '80', '8'])
  })
  it('preserves measured zero, missing buckets and exact visible keyboard/pointer values', async () => {
    const fixture = agentStatsFixture()
    fixture.daily.push({ ...fixture.undated, date: '2026-11-07', by_agent: [], by_model: [], by_agent_model: [] })
    fixture.daily[0]!.reported_usage.total = 0
    const wrapper = mount(AgentActivityChart, { attachTo: document.body, props: { days: fixture.daily, totals: fixture.totals, timezone: 'UTC' } })
    await frame()
    const config = mocks.created.mock.calls[0]![0]
    expect(config.data.datasets[0].data).toEqual([0, null])
    const canvas = wrapper.get('canvas')
    ;(canvas.element as HTMLCanvasElement).focus(); await nextTick()
    expect(wrapper.get('[role="tooltip"]').text()).toContain('Total: 0')
    await canvas.trigger('keydown', { key: 'End' })
    expect(wrapper.get('[role="tooltip"]').text()).toContain('Total: N/A')
    await canvas.trigger('keydown', { key: 'Home' })
    await canvas.trigger('mousemove', { clientX: 100 })
    expect(wrapper.get('[role="tooltip"]').text()).toContain('2026-11-06')
    ;(canvas.element as HTMLCanvasElement).blur(); await canvas.trigger('mousemove', { clientX: 100 })
    expect(wrapper.get('[role="tooltip"]').text()).toContain('2026-11-07')
    await wrapper.get('.activity-chart__plot').trigger('mouseleave')
    expect(wrapper.find('[role="tooltip"]').exists()).toBe(false)
  })
  it('bounds year preparation and skips geometry updates for pointer/focus selection', async () => {
    const fixture = agentStatsFixture()
    const days = Array.from({ length: 365 }, (_, index) => ({ ...fixture.daily[0]!, date: new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10) }))
    const wrapper = mount(AgentActivityChart, { props: { days, totals: fixture.totals, timezone: 'UTC' } })
    await frame()
    const config = mocks.created.mock.calls[0]![0]
    expect(config.data.datasets[0].data).toHaveLength(365)
    expect(config.options.animation).toBe(false); expect(config.options.responsive).toBe(false)
    mocks.updated.mockClear()
    await wrapper.get('canvas').trigger('focus'); await wrapper.get('canvas').trigger('keydown', { key: 'End' }); await frame()
    expect(mocks.updated).not.toHaveBeenCalled()
    expect(wrapper.get('[role="tooltip"]').text()).toContain('2025-12-31')
    mocks.destroyed.mockClear()
    wrapper.unmount(); expect(mocks.destroyed).toHaveBeenCalledOnce()
  })
})

describe('Compact delivery trends', () => {
  it('renders all six year-long widgets from one projection with bounded series and reduced-motion-safe options', async () => {
    vi.stubGlobal('matchMedia', vi.fn((query: string) => ({ matches: query === '(prefers-reduced-motion: reduce)', addEventListener: vi.fn(), removeEventListener: vi.fn() })))
    expect(window.matchMedia('(prefers-reduced-motion: reduce)').matches).toBe(true)
    const fixture = agentStatsFixture()
    fixture.delivery_trends.daily = Array.from({ length: 365 }, (_, index) => ({ ...structuredClone(fixture.delivery_trends.totals), date: new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10) }))
    fixture.daily = fixture.delivery_trends.daily.map(day => ({ ...structuredClone(fixture.daily[0]!), date: day.date }))
    const wrapper = mount(AgentActivitySection, { props: { summary: fixture, loading: false, error: null, filters: { start_date: '2025-01-01', end_date: '2025-12-31', timezone: 'UTC', group_by: fixture.group_by } } })
    await frame()
    expect(mocks.created).toHaveBeenCalledTimes(7)
    for (const [config] of mocks.created.mock.calls) {
      expect(config.data.datasets).toHaveLength(1)
      expect(config.data.datasets[0].data).toHaveLength(365)
      expect(config.options.animation).toBe(false)
      expect(config.options.responsive).toBe(false)
    }
    expect(wrapper.findAll('canvas')).toHaveLength(7)
  })
  it('retains recorded stop incidents even when earlier provenance is unavailable', async () => {
    const totals = emptyDeliveryTrendsFixture().totals
    totals.stops = { total: 2, coverage: { status: 'unavailable', recorded: 0, unavailable: 1, issues: ['retained_revision_history_only'] } } as typeof totals.stops
    const wrapper = mount(DeliveryTrendChart, { props: { metric: deliveryMetrics[4], totals, days: [{ ...totals, date: '2026-11-01' }] } })
    await frame()
    expect(wrapper.get('.delivery-trend__total').text()).toBe('2')
    expect(wrapper.get('.delivery-trend__note').text()).toBe('History unavailable')
    expect(mocks.created.mock.calls[0]![0].data.datasets[0].data).toEqual([2])
    await wrapper.get('canvas').trigger('focus')
    expect(wrapper.get('[role="tooltip"]').text()).toContain('Earlier history is unavailable')
    expect(wrapper.get('[role="tooltip"]').text()).not.toContain('retained_revision_history_only')
  })
  it('uses the period weighted rate and keeps no-review dates as gaps', async () => {
    const totals = emptyDeliveryTrendsFixture().totals
    totals.first_review_acceptance = { accepted: 1, reviewed: 3, rate: 1 / 3, coverage: { status: 'complete', recorded: 3, unavailable: 0 } } as typeof totals.first_review_acceptance
    const days = [
      { ...structuredClone(totals), date: '2026-11-01' },
      { ...emptyDeliveryTrendsFixture().totals, date: '2026-11-02' },
    ]
    const wrapper = mount(DeliveryTrendChart, { props: { metric: deliveryMetrics[2], totals, days } })
    await frame()
    expect(wrapper.get('.delivery-trend__total').text()).toBe('33%')
    expect(wrapper.get('.delivery-trend__note').text()).toBe('1 of 3 tickets')
    const config = mocks.created.mock.calls[0]![0]
    expect(config.type).toBe('line'); expect(config.data.datasets[0].spanGaps).toBe(false)
    expect(config.data.datasets[0].data).toEqual([1 / 3, null])
    await wrapper.get('canvas').trigger('focus'); await wrapper.get('canvas').trigger('keydown', { key: 'End' })
    expect(wrapper.get('[role="tooltip"]').text()).toContain('N/A')
    expect(wrapper.get('[role="tooltip"]').text()).toContain('0 of 0 tickets')
  })
  it('keeps unavailable counts distinct from zero and shows exact partial summed task time', async () => {
    const totals = emptyDeliveryTrendsFixture().totals
    totals.work_time.total_seconds = 66240
    totals.work_time.coverage = { status: 'partial', recorded: 4, unavailable: 1, issues: ['missing invocation bounds'] } as typeof totals.work_time.coverage
    const wrapper = mount(DeliveryTrendChart, { props: { metric: deliveryMetrics[5], totals, days: [{ ...totals, date: '2026-11-01' }] } })
    await frame()
    expect(wrapper.get('.delivery-trend__total').text()).toBe('18h 24m')
    expect(wrapper.get('.delivery-trend__note').text()).toBe('Partial coverage')
    await wrapper.get('canvas').trigger('focus')
    expect(wrapper.get('[role="tooltip"]').text()).toContain('66,240 seconds of summed task work time')
    expect(wrapper.get('[role="tooltip"]').text()).toContain('missing invocation bounds')
    totals.completed_tasks.coverage.status = 'unavailable' as typeof totals.completed_tasks.coverage.status
    await wrapper.setProps({ metric: deliveryMetrics[0], totals: structuredClone(totals) }); await frame()
    expect(wrapper.get('.delivery-trend__total').text()).toBe('N/A')
  })
})
