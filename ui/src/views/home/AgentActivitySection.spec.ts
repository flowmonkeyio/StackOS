import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AgentActivitySection from './AgentActivitySection.vue'
import { agentStatsFixture } from './agentStatsFixtures'

function render(summary = agentStatsFixture(), extra = {}) {
  return mount(AgentActivitySection, { props: { summary, loading: false, error: null, ...extra }, global: { stubs: { AgentActivityChart: true } } })
}

describe('Agent activity presentation', () => {
  it('renders backend facts, recorded identities and coverage in an accessible breakdown', () => {
    const fixture = agentStatsFixture()
    fixture.rows.push({ ...fixture.rows[0]!, project_id: 2, project_name: 'Other project', project_slug: 'other', model: 'recorded-astra' })
    const wrapper = render(fixture, { global: true })
    const values = wrapper.findAllComponents({ name: 'UiMetricCard' }).map(card => card.props('value'))
    expect(values).toEqual(['4', '100', '1'])
    expect(wrapper.text()).toContain('Recorded StackOS automation only')
    expect(wrapper.text()).toContain('do not guarantee model execution')
    const table = wrapper.get('table[aria-label="Agent activity breakdown"]')
    expect(table.findAll('tbody tr')).toHaveLength(2)
    expect(table.text()).toContain('Fixture project')
    expect(table.text()).toContain('Other project')
    expect(table.text()).toContain('recorded-sol')
    expect(table.text()).toContain('recorded-astra')
    expect(table.text()).toContain('delivery_agent')
    expect(table.text()).toContain('codex')
    expect(table.findAll('th').every(cell => cell.attributes('scope') === 'col')).toBe(true)
    expect(wrapper.findComponent({ name: 'AgentActivityChart' }).props('rows')).toEqual(fixture.rows)
    wrapper.unmount()
  })

  it('distinguishes measured zero, partial sums and unavailable usage/classification', async () => {
    const fixture = agentStatsFixture()
    fixture.totals.reported_usage.total = 0
    const wrapper = render(fixture)
    expect(wrapper.findAllComponents({ name: 'UiMetricCard' })[1]!.props('value')).toBe('0')
    expect(wrapper.text()).toContain('Complete · 4 of 4 attempts measured')
    fixture.totals.coverage.usage_status = 'partial' as never
    fixture.totals.coverage.usage_measured_attempts = 1
    fixture.totals.coverage.usage_unavailable_attempts = 3
    fixture.totals.coverage.classification_status = 'partial' as never
    fixture.totals.coverage.classification_known_attempts = 1
    fixture.totals.coverage.classification_unknown_attempts = 3
    await wrapper.setProps({ summary: structuredClone(fixture) })
    expect(wrapper.text()).toContain('Partial · 1 of 4 attempts measured')
    expect(wrapper.text()).toContain('Partial · 1 of 4 attempts classified')
    fixture.totals.reported_usage.total = null
    fixture.totals.coverage.usage_status = 'unavailable' as never
    fixture.totals.coverage.classification_status = 'unavailable' as never
    await wrapper.setProps({ summary: structuredClone(fixture) })
    expect(wrapper.findAllComponents({ name: 'UiMetricCard' }).map(card => card.props('value'))).toEqual(['4', 'Unavailable', 'Unavailable'])
    wrapper.unmount()
  })

  it('shows loading, initial error, refresh error and empty scope without false success', async () => {
    const wrapper = render(undefined, { summary: null, loading: true })
    expect(wrapper.find('[aria-label="Loading Agent activity"]').exists()).toBe(true)
    expect(wrapper.findComponent({ name: 'UiMetricCard' }).exists()).toBe(false)
    await wrapper.setProps({ loading: false, error: 'Request failed' })
    expect(wrapper.text()).toContain('Request failed')
    expect(wrapper.text()).not.toContain('No recorded agent')
    await wrapper.setProps({ summary: agentStatsFixture() })
    expect(wrapper.text()).toContain('Previously loaded Agent activity is shown')
    const empty = agentStatsFixture({ rows: [] })
    empty.totals.launch_attempts = 0
    empty.totals.coverage.usage_status = 'empty' as never
    await wrapper.setProps({ summary: empty, error: null })
    expect(wrapper.text()).toContain('No recorded agent launch attempts in this scope')
    expect(wrapper.findComponent({ name: 'AgentActivityChart' }).exists()).toBe(false)
    wrapper.unmount()
  })
})
