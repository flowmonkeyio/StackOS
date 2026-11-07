import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AgentActivitySection from './AgentActivitySection.vue'
import AgentUsageSummary from '@/components/domain/AgentUsageSummary.vue'
import { localActivityFilters, type ActivityGroup } from '@/lib/agentActivity'
import { agentStatsFixture } from './agentStatsFixtures'

function render(summary = agentStatsFixture(), extra = {}) {
  return mount(AgentActivitySection, { props: { summary, loading: false, error: null, filters: localActivityFilters(), ...extra }, global: { stubs: { AgentActivityChart: true } } })
}
describe('Agent usage presentation', () => {
  it.each(['model', 'agent_model', 'agent'] as const)('uses a plain %s label when the row has no supported details', group => {
    const fixture = agentStatsFixture()
    fixture.rows[0]!.host_key = null
    fixture.rows[0]!.review_outcomes = {}
    if (group === 'agent') fixture.rows[0]!.model = null
    const wrapper = render(fixture, { filters: { ...localActivityFilters(), group_by: group as ActivityGroup } })
    const row = wrapper.get('table tbody tr')
    expect(row.find('details').exists()).toBe(false)
    expect(row.find('summary').exists()).toBe(false)
    expect(row.text()).toContain(group === 'model' ? 'recorded-sol' : 'Same-name')
    expect(row.text()).toContain('70')
    wrapper.unmount()
  })

  it.each(['host', 'review', 'model', 'project'] as const)('retains a disclosure with supported %s details', async detail => {
    const fixture = agentStatsFixture()
    fixture.rows[0]!.host_key = detail === 'host' ? 'codex' : null
    fixture.rows[0]!.review_outcomes = detail === 'review' ? { repair: 1 } : {}
    const wrapper = render(fixture, {
      global: detail === 'project',
      filters: { ...localActivityFilters(), group_by: (detail === 'model' ? 'agent' : 'model') as ActivityGroup },
    })
    const disclosure = wrapper.get('table tbody tr details')
    expect(disclosure.get('summary').text()).toBeTruthy()
    await disclosure.get('summary').trigger('click')
    expect(disclosure.get('.agent-activity__details').text()).toContain({ host: 'Codex', review: 'Changes requested: 1', model: 'recorded-sol', project: 'Fixture project' }[detail])
    wrapper.unmount()
  })

  it('renders the shared Input/Cache/Output composition and bounded recorded review details', async () => {
    const fixture = agentStatsFixture()
    const wrapper = render(fixture, { global: true })
    expect(wrapper.findComponent(AgentUsageSummary).props('metrics')).toEqual(fixture.totals)
    expect(wrapper.findAll('.agent-usage-summary dd[tabindex]').map(item => item.text())).toEqual(['70', '40', '30'])
    expect(wrapper.text()).toContain('Cache is included in Input')
    expect(wrapper.text()).toContain('57.1%')
    expect(wrapper.text()).toContain('America/')
    const table = wrapper.get('table[aria-label="Agent activity breakdown"]')
    expect(table.findAll('th').map(item => item.text())).toEqual(['Selected model', 'Attempts', 'Input', 'Cache', 'Output'])
    expect(table.findAll('th').every(item => item.attributes('scope') === 'col')).toBe(true)
    expect(table.text()).toContain('recorded-sol')
    expect(table.get('[aria-label^="Cache:"]').attributes('aria-label')).toContain('40 tokens')
    await wrapper.findAll('button').find(item => item.text() === 'See token details')!.trigger('click')
    const detail = wrapper.get('[aria-label="Token details"]')
    expect(detail.text()).toContain('Cache write: 10')
    expect(detail.text()).toContain('Reasoning: 5')
    expect(detail.text()).toContain('Changes requested: 1')
    expect(detail.text()).toContain('Reviews passed: 1')
    expect(wrapper.text()).not.toMatch(/classified|review_agent|delivery_agent|No recorded review/)
    expect(wrapper.findComponent({ name: 'AgentActivityChart' }).props('days')).toEqual(fixture.daily)
    expect(wrapper.text()).not.toContain('Partial')
    wrapper.unmount()
  })

  it('emits functional date/group changes without client aggregation', async () => {
    const wrapper = render()
    await wrapper.get('input[type="date"]').setValue('2026-11-01')
    await wrapper.findAll('button').find(item => item.text() === 'Agent + model')!.trigger('click')
    expect(wrapper.emitted('filters')).toEqual([[{ start_date: '2026-11-01' }], [{ group_by: 'agent_model' }]])
    wrapper.unmount()
  })

  it('retains the chart view while a new date/group scope loads', async () => {
    const wrapper = render()
    wrapper.findComponent({ name: 'AgentActivityChart' }).vm.$emit('update:view', 'agents')
    await wrapper.setProps({ summary: null, loading: true })
    await wrapper.setProps({ summary: agentStatsFixture(), loading: false })
    expect(wrapper.findComponent({ name: 'AgentActivityChart' }).props('view')).toBe('agents')
    wrapper.unmount()
  })

  it('keeps independently missing cache truthful while retaining known parents and measured zero', async () => {
    const fixture = agentStatsFixture()
    fixture.totals.reported_usage.cache_read = null
    fixture.totals.reported_usage.remaining_input = null
    fixture.rows[0]!.reported_usage.cache_read = null
    fixture.totals.reported_usage.cache_write = null
    fixture.totals.reported_usage.reasoning = null
    fixture.totals.review_outcomes = {}
    fixture.totals.coverage.fields!.cache_read!.unavailable_attempts = 4
    fixture.totals.reported_usage.output = 0
    const wrapper = render(fixture)
    expect(wrapper.findAll('.agent-usage-summary dd[tabindex]').map(item => item.text())).toEqual(['70', '0'])
    expect(wrapper.text()).not.toMatch(/Not reported|not reported|not available|Cache split/)
    expect(wrapper.find('[aria-label="Input details"]').exists()).toBe(false)
    expect(wrapper.findAll('button').some(item => item.text() === 'See token details')).toBe(false)
    expect(wrapper.get('table').findAll('th').map(item => item.text())).toEqual(['Selected model', 'Attempts', 'Input', 'Output'])
    expect(wrapper.text()).not.toContain('57.1%')
    fixture.totals.reported_usage.cache_read = 10
    fixture.totals.coverage.fields!.cache_read!.unavailable_attempts = 2
    await wrapper.setProps({ summary: structuredClone(fixture) })
    expect(wrapper.text()).not.toContain('Cache missing for 2 attempts')
    expect(wrapper.get('[data-usage-field="cache_read"] dd[tabindex]').attributes('title')).toContain('Cache missing for 2 attempts')
    expect(wrapper.text()).not.toContain('Partial')
    wrapper.unmount()
  })

  it('shows honest loading/error/empty/undated states and prepares compact reuse', async () => {
    const wrapper = render(undefined, { summary: null, loading: true })
    expect(wrapper.find('[aria-label="Loading Agent activity"]').exists()).toBe(true)
    await wrapper.setProps({ loading: false, error: 'Request failed' })
    expect(wrapper.text()).toContain('Request failed')
    const fixture = agentStatsFixture()
    fixture.undated.launch_attempts = 1
    await wrapper.setProps({ summary: fixture })
    expect(wrapper.text()).toContain('Previously loaded Agent activity')
    expect(wrapper.text()).toContain('1 attempt has no recorded start date')
    const empty = agentStatsFixture({ rows: [], daily: [], totals: agentStatsFixture().undated })
    await wrapper.setProps({ summary: empty, error: null })
    expect(wrapper.text()).toContain('No recorded usage in this date range')
    expect(wrapper.findComponent({ name: 'AgentActivityChart' }).exists()).toBe(false)
    const unknown = agentStatsFixture()
    unknown.totals.reported_usage = { unit: 'tokens', input: null, output: null, total: null, cache_read: null, cache_write: null, reasoning: null, remaining_input: null, input_with_unknown_cache: null }
    unknown.daily[0]!.reported_usage = structuredClone(unknown.totals.reported_usage)
    await wrapper.setProps({ summary: unknown })
    expect(wrapper.findComponent({ name: 'AgentActivityChart' }).exists()).toBe(false)
    expect(wrapper.find('.agent-usage-summary').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Not reported')
    const compact = mount(AgentUsageSummary, { props: { metrics: fixture.totals, compact: true } })
    expect(compact.findAll('dt').map(item => item.text())).toEqual(['Input', 'Cache', 'Output'])
    expect(compact.get('[data-usage-field="input"] dd[tabindex]').attributes('title')).toContain('70 tokens')
    await compact.setProps({ metrics: empty.totals })
    expect(compact.findAll('dd[tabindex]').map(item => item.text())).toEqual(['0', '0', '0'])
    compact.unmount(); wrapper.unmount()
  })
})
