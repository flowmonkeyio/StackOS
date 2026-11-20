import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AgentActivitySection from './AgentActivitySection.vue'
import { localActivityFilters, type ActivityGroup } from '@/lib/agentActivity'
import { agentStatsFixture, groupedAgentStatsFixture } from './agentStatsFixtures'

function render(summary = agentStatsFixture(), extra = {}) {
  return mount(AgentActivitySection, { props: { summary, loading: false, error: null, filters: localActivityFilters(), ...extra }, global: { stubs: { AgentActivityChart: true, DeliveryTrendChart: true } } })
}
describe('Shared Home usage', () => {
  it('silently updates retained data without replacing the chart or moving focus', async () => {
    const wrapper = render()
    const chart = wrapper.getComponent({ name: 'AgentActivityChart' }).element
    const grouping = wrapper.findAll('button').find(item => item.text() === 'Model')!
    await grouping.trigger('focus')
    for (let i = 0; i < 3; i++) {
      await wrapper.setProps({ loading: true })
      expect(wrapper.text()).not.toMatch(/Refreshing|Loading/)
      expect(wrapper.getComponent({ name: 'AgentActivityChart' }).element).toBe(chart)
      await wrapper.setProps({ loading: false, summary: agentStatsFixture() })
    }
    wrapper.unmount()
  })
  it('passes one accepted aggregate unchanged to all six widgets and token chart', () => {
    const fixture = agentStatsFixture()
    const wrapper = render(fixture)
    const trends = wrapper.findAllComponents({ name: 'DeliveryTrendChart' })
    expect(trends.map(item => item.props('metric').label)).toEqual(['Completed tasks', 'Completed tickets', 'First-review acceptance', 'Changes requested', 'Stops', 'Recorded work time'])
    for (const item of trends) {
      expect(item.props('totals')).toEqual(fixture.delivery_trends.totals)
      expect(item.props('days')).toEqual(fixture.delivery_trends.daily)
    }
    expect(wrapper.getComponent({ name: 'AgentActivityChart' }).props('days')).toEqual(fixture.daily)
    expect(wrapper.text()).not.toMatch(/Top tasks|stop reason|Where input goes/)
    expect(wrapper.text()).not.toContain('Token details')
    expect(wrapper.find('details').exists()).toBe(false)
    wrapper.unmount()
  })
  it.each(['agent', 'model', 'agent_model'] as const)('shows exact distinct %s identities and totals without a second calculation owner', group => {
    const fixture = groupedAgentStatsFixture(group)
    const wrapper = render(fixture)
    const rows = wrapper.findAll('.agent-activity__row')
    expect(rows).toHaveLength(fixture.rows.length)
    expect(rows.map(row => row.get('.agent-activity__row-value').text())).toEqual(fixture.rows.map(row => String(row.reported_usage.total)))
    expect(new Set(rows.map(row => row.get('.agent-activity__swatch').attributes('style'))).size).toBe(rows.length)
    if (group !== 'agent') expect(wrapper.text()).toContain('gpt-6.1-sol')
    wrapper.unmount()
  })
  it('emits one date/group filter and retains the selected token metric through reload', async () => {
    const wrapper = render()
    await wrapper.get('input[type="date"]').setValue('2026-11-01')
    await wrapper.findAll('button').find(item => item.text() === 'Agent + model')!.trigger('click')
    expect(wrapper.emitted('filters')).toEqual([[{ start_date: '2026-11-01' }], [{ group_by: 'agent_model' }]])
    wrapper.getComponent({ name: 'AgentActivityChart' }).vm.$emit('update:field', 'cache_read')
    await wrapper.setProps({ summary: null, loading: true })
    await wrapper.setProps({ summary: agentStatsFixture(), loading: false })
    expect(wrapper.getComponent({ name: 'AgentActivityChart' }).props('field')).toBe('cache_read')
    wrapper.unmount()
  })
  it('labels retained content from the loaded grouping and marks pending regrouping', async () => {
    const wrapper = render()
    await wrapper.setProps({ filters: { ...localActivityFilters(), group_by: 'agent' as ActivityGroup }, loading: true })
    expect(wrapper.get('[aria-label="Usage by model"]').text()).toContain('recorded-sol')
    expect(wrapper.text()).toContain('Updating grouping')
    expect(wrapper.text()).not.toContain('Refreshing')
    wrapper.unmount()
  })
  it('retains all six slots in empty/partial states and keeps stale errors and undated exclusions explicit', async () => {
    const wrapper = render(undefined, { summary: null, loading: true })
    expect(wrapper.find('[aria-label="Loading Agent activity"]').exists()).toBe(true)
    await wrapper.setProps({ loading: false, error: 'Request failed' })
    expect(wrapper.text()).toContain('Request failed')
    const fixture = agentStatsFixture()
    fixture.undated.launch_attempts = 1
    await wrapper.setProps({ summary: fixture })
    expect(wrapper.text()).toContain('Previously loaded Agent activity')
    expect(wrapper.text()).toContain('1 attempt has no recorded start date')
    await wrapper.setProps({ summary: agentStatsFixture({ rows: [], daily: [], totals: fixture.undated }), error: null })
    expect(wrapper.text()).toContain('No recorded usage in this date range')
    expect(wrapper.findAllComponents({ name: 'DeliveryTrendChart' })).toHaveLength(6)
    expect(wrapper.findComponent({ name: 'AgentActivityChart' }).exists()).toBe(true)
    wrapper.unmount()
  })
})
