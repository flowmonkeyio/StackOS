import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import FinanceOverviewCharts from './FinanceOverviewCharts.vue'
import { overviewCurrencies } from './overviewViewModel'
import { chartProjection } from './overviewFixtures'

const mocks = vi.hoisted(() => ({ created: vi.fn(), updated: vi.fn(), destroyed: vi.fn() }))
vi.mock('chart.js', () => ({
  Chart: class {
    static register = vi.fn()
    data: unknown
    options: unknown
    constructor(_canvas: unknown, configuration: { data: unknown; options: unknown }) {
      this.data = configuration.data
      this.options = configuration.options
      mocks.created(configuration)
    }
    update = mocks.updated
    destroy = mocks.destroyed
  },
  BarController: {},
  BarElement: {},
  CategoryScale: {},
  LinearScale: {},
  LineController: {},
  LineElement: {},
  PointElement: {},
  Tooltip: {},
}))
afterEach(() => {
  vi.clearAllMocks()
  vi.restoreAllMocks()
  delete document.documentElement.dataset.theme
  document.body.innerHTML = ''
})
describe('Overview graph lifecycle and exact alternatives', () => {
  it('renders two native graphs with exact fallback tables, safe scale and keyboard inspection', async () => {
    const projection = chartProjection()
    projection.comparison = {
      query: { start: '2026-08-02', end: '2026-08-31' },
      coverage: { state: 'partial' },
      metrics_by_currency: [
        {
          currency: 'USD',
          exponent: 2,
          revenue: {
            state: 'partial',
            amount: { minor: '0', currency: 'USD', exponent: 2 },
            included_record_refs: ['book:prior'],
          },
          spend: { state: 'unavailable', amount: null },
        },
      ],
    }
    const wrapper = mount(FinanceOverviewCharts, {
      props: { currencies: overviewCurrencies(projection) },
      attachTo: document.body,
    })
    expect(wrapper.findAll('canvas')).toHaveLength(2)
    expect(mocks.created.mock.calls.map(([config]) => config.type)).toEqual(['bar', 'line'])
    const totals = mocks.created.mock.calls[0]![0],
      trend = mocks.created.mock.calls[1]![0]
    expect(totals.data.datasets.map((row: { data: unknown }) => row.data)).toEqual([
      [30, 20],
      [0, null],
    ])
    expect(trend.data.labels).toEqual(['2026-09-01', 'Unrecorded interval', '2026-09-03'])
    expect(trend.data.datasets[0].data).toEqual([30, null, 0])
    expect(trend.data.datasets[1].spanGaps).toBe(false)
    expect(totals.options.animation).toBe(false)
    expect(totals.options.plugins.tooltip.callbacks.label({ dataIndex: 0, datasetIndex: 0 })).toBe(
      'Current Income: USD 30.00',
    )
    expect(wrapper.get('table[aria-label="Exact income and expense totals"]').text()).toContain(
      'USD 30.00',
    )
    expect(wrapper.text()).toContain('Prior: 2026-08-02 through 2026-08-31')
    expect(wrapper.text()).not.toContain('%')
    expect(wrapper.get('canvas[role="img"]').attributes('aria-label')).toContain('Exact values')
    const button = wrapper
      .findAll('button')
      .find((row) => row.text() === 'Inspect prior income records')!
    expect(button.classes()).toContain('focus-ring')
    ;(button.element as HTMLButtonElement).focus()
    expect(document.activeElement).toBe(button.element)
    await button.trigger('click')
    expect(wrapper.emitted('inspect')?.[0]).toEqual([['book:prior']])
    await wrapper
      .get('table[aria-label="Exact current finance trend"] tbody tr')
      .trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('inspect')?.[1]).toEqual([['book:income']])
    wrapper.unmount()
    expect(mocks.destroyed).toHaveBeenCalledTimes(2)
  })
  it('updates amounts and semantic theme colors without rebuilding charts, then releases observers', async () => {
    vi.spyOn(window, 'getComputedStyle').mockImplementation(
      () =>
        ({
          fontFamily: 'system-ui',
          getPropertyValue: (key: string) =>
            `${document.documentElement.dataset.theme ?? 'light'}:${key}`,
        }) as CSSStyleDeclaration,
    )
    const projection = chartProjection()
    const wrapper = mount(FinanceOverviewCharts, {
      props: { currencies: overviewCurrencies(projection) },
    })
    await wrapper.setProps({ currencies: overviewCurrencies(projection) })
    expect(mocks.updated).not.toHaveBeenCalled()
    projection.metrics_by_currency[0]!.spend = {
      state: 'partial',
      amount: { minor: '-125', currency: 'USD', exponent: 2 },
    }
    await wrapper.setProps({ currencies: overviewCurrencies(projection) })
    expect(
      (mocks.updated.mock.contexts[0] as { data: { datasets: { data: number[] }[] } }).data
        .datasets[0]!.data,
    ).toEqual([30, -1.25])
    document.documentElement.dataset.theme = 'dark'
    await vi.waitFor(() => expect(mocks.updated).toHaveBeenCalledTimes(4))
    expect(
      (mocks.updated.mock.contexts[2] as { data: { datasets: { backgroundColor: string }[] } }).data
        .datasets[0]!.backgroundColor,
    ).toBe('dark:--color-fg-strong')
    expect(mocks.created).toHaveBeenCalledTimes(2)
    wrapper.unmount()
    document.documentElement.dataset.theme = 'light'
    await flushPromises()
    expect(mocks.updated).toHaveBeenCalledTimes(4)
    expect(mocks.destroyed).toHaveBeenCalledTimes(2)
  })
  it('keeps huge exact labels and identifies drawing omissions instead of showing false zero', () => {
    const projection = chartProjection()
    projection.metrics_by_currency[0]!.revenue = {
      state: 'complete',
      amount: { minor: '1' + '0'.repeat(400), currency: 'USD', exponent: 2 },
    }
    projection.metrics_by_currency[0]!.spend = {
      state: 'partial',
      amount: { minor: '1', currency: 'USD', exponent: 2 },
    }
    const wrapper = mount(FinanceOverviewCharts, {
      props: { currencies: overviewCurrencies(projection) },
    })
    const config = mocks.created.mock.calls[0]![0]
    expect(config.data.datasets[0].data).toEqual([100000, null])
    expect(wrapper.text()).toContain('1 nonzero amount(s) are below drawing precision')
    expect(wrapper.get('table[aria-label="Exact income and expense totals"]').text()).toContain(
      'USD 0.01',
    )
    expect(config.options.plugins.tooltip.callbacks.label({ dataIndex: 1, datasetIndex: 0 })).toBe(
      'Current Expenses: USD 0.01',
    )
    wrapper.unmount()
  })
  it('selects currencies independently, clears disappeared data and destroys removed canvases', async () => {
    const projection = chartProjection()
    projection.metrics_by_currency.push({
      currency: 'JPY',
      exponent: 0,
      revenue: { state: 'complete', amount: { minor: '42', currency: 'JPY', exponent: 0 } },
      spend: { state: 'unavailable', amount: null },
    })
    const wrapper = mount(FinanceOverviewCharts, {
      props: { currencies: overviewCurrencies(projection) },
    })
    expect(wrapper.text()).toContain('Income versus expenses · JPY')
    expect(wrapper.findAll('canvas')).toHaveLength(1)
    wrapper.findComponent({ name: 'UiSelect' }).vm.$emit('update:modelValue', 'USD')
    await flushPromises()
    expect(wrapper.text()).toContain('Income versus expenses · USD')
    expect(wrapper.findAll('canvas')).toHaveLength(2)
    await wrapper.setProps({ currencies: [] })
    expect(wrapper.find('canvas').exists()).toBe(false)
    expect(mocks.destroyed).toHaveBeenCalledTimes(2)
    wrapper.unmount()
    expect(mocks.destroyed).toHaveBeenCalledTimes(2)
  })
  it('shows truthful empty and unknown-income states without fake graphs', () => {
    const projection = chartProjection()
    projection.metrics_by_currency[0]!.revenue = { state: 'unavailable', amount: null }
    projection.metrics_by_currency[0]!.spend = { state: 'unavailable', amount: null }
    projection.trend = []
    const wrapper = mount(FinanceOverviewCharts, {
      props: { currencies: overviewCurrencies(projection) },
    })
    expect(wrapper.find('canvas').exists()).toBe(false)
    expect(mocks.created).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Unavailable income is not zero')
    expect(wrapper.text()).toContain('No supported dated contributions')
    expect(wrapper.text()).toContain('No recorded dates in this currency and window')
    wrapper.unmount()
  })
})
