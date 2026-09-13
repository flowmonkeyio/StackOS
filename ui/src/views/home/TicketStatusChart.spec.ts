import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import TicketStatusChart from './TicketStatusChart.vue'
import { ticketCountsFixture } from './overviewFixtures'

interface UpdatedChart {
  data: { datasets: Array<{ data: number[]; backgroundColor: string }> }
  options: { scales: { x: { max: number } } }
}

const mocks = vi.hoisted(() => ({ created: vi.fn(), updated: vi.fn(), destroyed: vi.fn() }))
vi.mock('chart.js', () => ({
  Chart: class {
    static register = vi.fn()
    data: unknown
    options: unknown
    constructor(_canvas: unknown, config: { data: unknown; options: unknown }) {
      this.data = config.data
      this.options = config.options
      mocks.created(config)
    }
    update = mocks.updated
    destroy = mocks.destroyed
  },
  BarController: {},
  BarElement: {},
  CategoryScale: {},
  LinearScale: {},
  Tooltip: {},
}))

afterEach(() => {
  vi.clearAllMocks()
  vi.restoreAllMocks()
  delete document.documentElement.dataset.theme
})

describe('compact ticket status snapshot', () => {
  it('renders one proportional strip with seven datasets and no visible scale or duplicate total', () => {
    const wrapper = mount(TicketStatusChart, {
      props: { counts: ticketCountsFixture(), total: 4 },
    })
    const config = mocks.created.mock.calls[0]?.[0]
    expect(config.data.labels).toEqual(['Tickets'])
    expect(config.data.datasets.map((dataset: { label: string; data: number[]; stack: string }) => ({
      label: dataset.label, data: dataset.data, stack: dataset.stack,
    }))).toEqual([
      { label: 'Not Started', data: [0], stack: 'tickets' },
      { label: 'In Progress', data: [1], stack: 'tickets' },
      { label: 'Complete', data: [2], stack: 'tickets' },
      { label: 'Deferred', data: [0], stack: 'tickets' },
      { label: 'Aborted', data: [0], stack: 'tickets' },
      { label: 'Failed', data: [1], stack: 'tickets' },
      { label: 'Skipped', data: [0], stack: 'tickets' },
    ])
    expect(config.options.scales.x).toMatchObject({ display: false, stacked: true, min: 0, max: 4 })
    expect(config.options.scales.y).toMatchObject({ display: false, stacked: true })
    expect(config.options.plugins.legend).toEqual({ display: false })
    expect(config.data.datasets.every((dataset: { barThickness: number; minBarLength?: number }) =>
      dataset.barThickness === 24 && !dataset.minBarLength,
    )).toBe(true)
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
    expect(wrapper.get('canvas').attributes('aria-label')).toContain('4 tickets')
    expect(wrapper.text()).not.toContain('7 days')
    wrapper.unmount()
  })

  it('shows seven exact count columns and keeps accessible selection independent of canvas', async () => {
    const wrapper = mount(TicketStatusChart, { props: { counts: ticketCountsFixture(), total: 4 } })
    expect(wrapper.findAll('[role="group"] button')).toHaveLength(7)
    expect(wrapper.findAll('[role="group"] button strong').map((count) => count.text()))
      .toEqual(['0', '1', '2', '0', '0', '1', '0'])
    await wrapper.get('button[aria-label="In Progress: 1 tickets"]').trigger('click')
    expect(wrapper.emitted('select')?.[0]).toEqual(['in-progress'])
    await wrapper.setProps({ selectedStatus: 'in-progress' })
    expect(
      wrapper.get('button[aria-label="In Progress: 1 tickets"]').attributes('aria-pressed'),
    ).toBe('true')
    wrapper.unmount()
    expect(mocks.destroyed).toHaveBeenCalledTimes(1)
  })

  it('maps a selected strip segment by dataset index, not the shared row index', () => {
    const wrapper = mount(TicketStatusChart, { props: { counts: ticketCountsFixture(), total: 4 } })
    const click = mocks.created.mock.calls[0]?.[0].options.onClick
    click({}, [{ datasetIndex: 2, index: 0 }])
    expect(wrapper.emitted('select')).toEqual([['complete']])
    click({}, [])
    click({}, [{ datasetIndex: 99, index: 0 }])
    expect(wrapper.emitted('select')).toHaveLength(1)
    wrapper.unmount()
  })

  it('keeps all zero statuses selectable in the empty state and updates the total scale', async () => {
    const wrapper = mount(TicketStatusChart, { props: { counts: ticketCountsFixture(), total: 4 } })
    await wrapper.setProps({
      counts: ticketCountsFixture({ 'in-progress': 0, complete: 0, failed: 0 }),
      total: 0,
    })
    expect(wrapper.text()).toContain('No tickets recorded in this scope.')
    expect(wrapper.findAll('button[aria-label$=": 0 tickets"]')).toHaveLength(7)
    await wrapper.get('button[aria-label="Skipped: 0 tickets"]').trigger('click')
    expect(wrapper.emitted('select')?.[0]).toEqual(['skipped'])
    const chart = mocks.updated.mock.contexts[0] as UpdatedChart
    expect(chart.options.scales.x.max).toBe(0)
    expect(chart.data.datasets.every((dataset: { data: number[] }) => dataset.data[0] === 0)).toBe(true)
    await wrapper.setProps({ counts: ticketCountsFixture(), total: 4 })
    expect(chart.options.scales.x.max).toBe(4)
    expect(wrapper.text()).not.toContain('No tickets recorded in this scope.')
    expect(mocks.created).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('refreshes semantic colors on theme changes and releases chart and observer on unmount', async () => {
    vi.spyOn(window, 'getComputedStyle').mockImplementation(() => ({
      fontFamily: 'system-ui',
      getPropertyValue: (key: string) => `${document.documentElement.dataset.theme ?? 'light'}:${key}`,
    }) as CSSStyleDeclaration)
    const wrapper = mount(TicketStatusChart, { props: { counts: ticketCountsFixture(), total: 4 } })
    expect(mocks.created.mock.calls[0]?.[0].data.datasets[2].backgroundColor)
      .toBe('light:--color-success-default')
    document.documentElement.dataset.theme = 'dark'
    await vi.waitFor(() => expect(mocks.updated).toHaveBeenCalledTimes(1))
    expect((mocks.updated.mock.contexts[0] as UpdatedChart).data.datasets[2]?.backgroundColor)
      .toBe('dark:--color-success-default')
    expect(mocks.created).toHaveBeenCalledTimes(1)
    wrapper.unmount()
    document.documentElement.dataset.theme = 'light'
    await Promise.resolve()
    expect(mocks.updated).toHaveBeenCalledTimes(1)
    expect(mocks.destroyed).toHaveBeenCalledTimes(1)
  })
})
