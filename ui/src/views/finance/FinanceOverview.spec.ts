import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import FinanceOverview from './FinanceOverview.vue'
import { chartProjection } from './overviewFixtures'

describe('cohesive Overview presentation', () => {
  it('retains metrics, source contributions and drill intents alongside complete chart data', async () => {
    const projection = chartProjection()
    projection.breakdowns = {
      category: [
        {
          label: 'Source category',
          currency: 'USD',
          spend_minor: '2000',
          revenue_minor: '3000',
          included_record_refs: ['book:expense'],
        },
      ],
    }
    const wrapper = mount(FinanceOverview, {
      props: { projection },
      global: { stubs: { FinanceOverviewCharts: true } },
    })
    expect(
      wrapper.findAllComponents({ name: 'UiMetricCard' }).map((card) => card.props('value')),
    ).toEqual(['USD 20.00', 'USD 30.00', 'USD 10.00'])
    expect(
      wrapper.findComponent({ name: 'FinanceOverviewCharts' }).props('currencies')[0].totals[0]
        .currentText,
    ).toBe('USD 30.00')
    await wrapper
      .findAll('button')
      .find((row) => row.text() === 'Inspect included records')!
      .trigger('click')
    expect(wrapper.emitted('inspect')?.[0]).toEqual([['book:one']])
    await wrapper
      .get('table[aria-label="category breakdown"] tbody tr')
      .trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('drill')?.[0]?.[0]).toBe('category')
    expect(wrapper.emitted('drill')?.[0]?.[1]).toMatchObject({
      label: 'Source category',
      included_record_refs: ['book:expense'],
    })
    expect(wrapper.get('table[aria-label="Finance trend"]').text()).toContain('USD 30.00')
    wrapper.unmount()
  })
})
