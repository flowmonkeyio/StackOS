import { enableAutoUnmount, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { ref } from 'vue'
import { usageFeedKey } from '@/composables/useUsageFeed'
import type { UsageFeedState } from '@/lib/usageUpdates'
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

describe('Chart inspection lifetime', () => {
  it.each(['tokens', 'trends'] as const)('animates %s data updates without replay on focus, metric/scope changes, or reconnect', async kind => {
    const cancel = vi.fn(), animate = vi.fn(() => ({ cancel, onfinish: null }))
    Object.defineProperty(HTMLCanvasElement.prototype, 'animate', { configurable: true, value: animate })
    const fixture = agentStatsFixture()
    const feed = ref<UsageFeedState>({ status: 'live', receivedAt: Date.now() })
    const options = { global: { provide: { [usageFeedKey as symbol]: feed } } }
    const token = kind === 'tokens' ? mount(AgentActivityChart, { ...options, props: { days: fixture.daily, totals: fixture.totals, timezone: 'UTC' } }) : null
    const trend = kind === 'trends' ? mount(DeliveryTrendChart, { ...options, props: { metric: deliveryMetrics[0], totals: fixture.delivery_trends.totals, days: [{ ...fixture.delivery_trends.totals, date: '2026-11-06' }] } }) : null
    const wrapper = token ?? trend!
    await frame(); expect(animate).not.toHaveBeenCalled()
    if (token) {
      const days = structuredClone(fixture.daily); days[0]!.reported_usage.output = (days[0]!.reported_usage.output ?? 0) + 10
      await token.setProps({ days })
    } else if (trend) {
      const day = structuredClone(fixture.delivery_trends.totals); day.completed_tasks.total += 1
      await trend.setProps({ days: [{ ...day, date: '2026-11-06' }] })
    }
    await frame(); expect(animate).toHaveBeenCalledOnce()
    animate.mockClear(); mocks.updated.mockClear()
    await wrapper.get('canvas').trigger('focus'); await frame()
    expect(animate).not.toHaveBeenCalled(); expect(mocks.updated).not.toHaveBeenCalled()
    feed.value = { ...feed.value, status: 'reconnecting' }; await nextTick(); await frame()
    expect(cancel).toHaveBeenCalledOnce()
    if (token) await token.setProps({ days: structuredClone(fixture.daily) })
    else await trend!.setProps({ days: [{ ...fixture.delivery_trends.totals, date: '2026-11-06' }] })
    await frame(); expect(animate).not.toHaveBeenCalled()
    feed.value = { ...feed.value, status: 'live' }; await nextTick()
    if (token) await token.findAll('button')[1]!.trigger('click')
    else await trend!.setProps({ metric: deliveryMetrics[1] })
    await frame(); expect(animate).not.toHaveBeenCalled()
    wrapper.unmount()
    delete (HTMLCanvasElement.prototype as unknown as { animate?: unknown }).animate
  })
  it.each(['tokens', 'trends'] as const)('retains %s keyboard selection across pointer exit until Escape or blur', async kind => {
    const fixture = agentStatsFixture()
    fixture.daily.push({ ...structuredClone(fixture.daily[0]!), date: '2026-11-07' })
    const wrapper = kind === 'tokens'
      ? mount(AgentActivityChart, { attachTo: document.body, props: { days: fixture.daily, totals: fixture.totals, timezone: 'UTC' } })
      : mount(DeliveryTrendChart, { attachTo: document.body, props: { metric: deliveryMetrics[2], totals: fixture.delivery_trends.totals, days: ['2026-11-06', '2026-11-07'].map(date => ({ ...fixture.delivery_trends.totals, date })) } })
    await frame()
    const canvas = wrapper.get('canvas')
    const plot = wrapper.get(kind === 'tokens' ? '.activity-chart__plot' : '.delivery-trend__plot')
    vi.spyOn(canvas.element, 'getBoundingClientRect').mockReturnValue({ left: 0, width: 200 } as DOMRect)
    ;(canvas.element as HTMLCanvasElement).focus()
    await canvas.trigger('keydown', { key: 'End' })
    expect(wrapper.get('[role="tooltip"]').text()).toContain('2026-11-07')
    await plot.trigger('mouseleave')
    expect(document.activeElement).toBe(canvas.element)
    expect(wrapper.get('[role="tooltip"]').text()).toContain('2026-11-07')
    await canvas.trigger('mousemove', { clientX: 1 })
    expect(wrapper.get('[role="tooltip"]').text()).toContain('2026-11-07')
    await canvas.trigger('keydown', { key: 'Escape' })
    await plot.trigger('mouseleave')
    await canvas.trigger('mousemove', { clientX: 1 })
    expect(wrapper.find('[role="tooltip"]').exists()).toBe(false)
    await canvas.trigger('keydown', { key: 'Home' })
    expect(wrapper.get('[role="tooltip"]').text()).toContain('2026-11-06')
    await canvas.trigger('keydown', { key: 'ArrowRight' })
    expect(wrapper.get('[role="tooltip"]').text()).toContain('2026-11-07')
    await canvas.trigger('keydown', { key: 'ArrowLeft' })
    expect(wrapper.get('[role="tooltip"]').text()).toContain('2026-11-06')
    ;(canvas.element as HTMLCanvasElement).blur()
    await nextTick()
    expect(wrapper.find('[role="tooltip"]').exists()).toBe(false)
    await canvas.trigger('mousemove', { clientX: 150 })
    expect(wrapper.get('[role="tooltip"]').text()).toContain('2026-11-07')
    await plot.trigger('mouseleave')
    expect(wrapper.find('[role="tooltip"]').exists()).toBe(false)
  })
})

describe('Daily tokens', () => {
  it('keeps combined dataset quantities exact and only gives positive components a minimum visible mark', async () => {
    const fixture = agentStatsFixture()
    const quantities = [[30, 40, 30], [1, 1000000, 2], [0.001, 1, 0.002], [0, 0, 0], [null, null, 7]]
    fixture.daily = quantities.map(([input, cache, output], index) => {
      const day = structuredClone(fixture.daily[0]!)
      day.date = `2026-11-0${index + 1}`
      Object.assign(day.reported_usage, { remaining_input: input, cache_read: cache, output })
      return day
    })
    const before = structuredClone(fixture)
    vi.spyOn(window, 'getComputedStyle').mockReturnValue({ getPropertyValue: (name: string) => name, fontFamily: 'sans-serif' } as CSSStyleDeclaration)
    const wrapper = mount(AgentActivityChart, { props: { days: fixture.daily, totals: fixture.totals, timezone: 'UTC' } })
    await frame()
    const config = mocks.created.mock.calls[0]![0]
    expect(config.data.datasets.map((item: { label: string }) => item.label)).toEqual(['Input', 'Cache', 'Output'])
    expect(config.data.datasets.map((item: { stack: string }) => item.stack)).toEqual(['tokens', 'tokens', 'tokens'])
    expect(config.options.scales.x.stacked).toBe(true)
    expect(config.options.scales.y.stacked).toBe(true)
    expect(config.data.datasets.map((item: { backgroundColor: string }) => item.backgroundColor)).toEqual(['--color-info-default', '--color-accent-primary', '--color-success-default'])
    config.data.datasets.forEach((item: { data: unknown[]; minBarLength?: number }, index: number) => {
      expect(item.data).toEqual(quantities.map(row => row[index]))
      expect(item.minBarLength).toBeUndefined()
    })
    const bars = config.data.datasets.map(() => quantities.map((_, index) => ({ x: index * 10, width: 8, y: 100, base: 100, height: 0 })))
    const painted = { data: config.data, chartArea: { top: 0 }, scales: { y: { getPixelForValue: (value: number) => 100 - value / 10000 } }, getDatasetMeta: (index: number) => ({ data: bars[index] }) }
    config.plugins[0].afterDatasetsUpdate(painted)
    quantities.forEach((row, index) => {
      let base = 100
      row.forEach((value, metric) => {
        const bar = bars[metric][index]
        expect(bar.x).toBe(index * 10) // one column per date
        if (value != null && value > 0) {
          expect(bar.base).toBe(base)
          expect(bar.height).toBeGreaterThanOrEqual(2)
          expect(bar.y).toBeGreaterThanOrEqual(0)
          expect(bar.y).toBe(base - bar.height)
          base = bar.y
        } else expect(bar.height).toBe(0)
      })
    })
    expect(bars.map((items: { height: number }[]) => items[1]!.height)).toEqual([2, 96, 2])
    expect(config.data.datasets.map((item: { data: unknown[] }) => item.data)).toEqual([0, 1, 2].map(index => quantities.map(row => row[index])))
    await wrapper.get('canvas').trigger('focus')
    await wrapper.get('canvas').trigger('keydown', { key: 'ArrowRight' })
    expect(wrapper.get('[role="tooltip"]').text()).toMatch(/Input1.*Cache1,000,000.*Output2/)
    await wrapper.findAll('button')[1]!.trigger('click'); await frame()
    const chart = mocks.updated.mock.contexts.at(-1) as { data: { datasets: { minBarLength?: number }[] } }
    expect(chart.data.datasets).toHaveLength(1)
    const retainedBars = structuredClone(bars)
    config.plugins[0].afterDatasetsUpdate(painted)
    expect(bars).toEqual(retainedBars)
    expect(fixture).toEqual(before)
  })
  it('selects All/Input/Cache/Output without summing incomplete splits or fetching', async () => {
    const fixture = agentStatsFixture()
    fixture.daily[0]!.reported_usage = { ...fixture.daily[0]!.reported_usage, total: 158, input: 150, remaining_input: 20, cache_read: 80, output: 8, input_with_unknown_cache: 50 }
    const wrapper = mount(AgentActivityChart, { props: { days: fixture.daily, totals: fixture.totals, timezone: 'UTC' } })
    await frame()
    expect(mocks.created.mock.calls[0]![0].data.datasets.map((item: { data: unknown }) => item.data)).toEqual([[20], [80], [8]])
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
    expect(wrapper.find('details').exists()).toBe(false)
    expect(wrapper.find('table').exists()).toBe(false)
  })
  it('preserves measured zero, missing buckets and exact visible keyboard/pointer values', async () => {
    const fixture = agentStatsFixture()
    fixture.daily.push({ ...fixture.undated, date: '2026-11-07', by_agent: [], by_model: [], by_agent_model: [] })
    fixture.daily[0]!.reported_usage.total = 0
    fixture.daily[0]!.reported_usage.remaining_input = 0
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
  it('retains a partial first-review denominator without qualifiers in text, titles or keyboard details', async () => {
    const totals = emptyDeliveryTrendsFixture().totals
    totals.first_review_acceptance = { accepted: 50, reviewed: 75, rate: 50 / 75, coverage: { status: 'partial', recorded: 75, unavailable: 2 } } as typeof totals.first_review_acceptance
    const before = structuredClone(totals)
    const wrapper = mount(DeliveryTrendChart, { props: { metric: deliveryMetrics[2], totals, days: [{ ...totals, date: '2026-11-01' }] } })
    await frame()
    expect(wrapper.get('.delivery-trend__note').text()).toBe('50 of 75 tickets')
    await wrapper.get('canvas').trigger('focus')
    expect(wrapper.get('[role="tooltip"]').text()).toContain('50 of 75 tickets')
    expect(wrapper.html()).not.toMatch(/Partial|coverage warning/i)
    expect(totals).toEqual(before)
  })
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
      expect(config.data.datasets).toHaveLength(config.data.datasets[0].label === 'Input' ? 3 : 1)
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
    expect(wrapper.get('.delivery-trend__note').text()).toBe('')
    await wrapper.get('canvas').trigger('focus')
    expect(wrapper.get('[role="tooltip"]').text()).toContain('66,240 seconds of summed task work time')
    expect(wrapper.html()).not.toMatch(/Partial|missing invocation bounds/)
    totals.completed_tasks.coverage.status = 'unavailable' as typeof totals.completed_tasks.coverage.status
    await wrapper.setProps({ metric: deliveryMetrics[0], totals: structuredClone(totals) }); await frame()
    expect(wrapper.get('.delivery-trend__total').text()).toBe('N/A')
  })
})
