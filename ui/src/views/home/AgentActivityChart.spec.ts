import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import AgentActivityChart from './AgentActivityChart.vue'
import { agentStatsFixture } from './agentStatsFixtures'

describe('Agent activity chart labels', () => {
  it('visibly pairs every bar with its full group and exact count, including global project identity', () => {
    const row = agentStatsFixture().rows[0]!
    const wrapper = mount(AgentActivityChart, { props: { global: true, rows: [
      row, { ...row, project_id: 2, project_name: 'Other project', agent_name: 'reviewer', model: 'astra', launch_attempts: 2 },
    ] } })
    const groups = wrapper.findAll('li')
    expect(groups[0]!.text().replace(/\s+/g, ' ')).toContain('Fixture project #1 · same-name · delivery_agent · codex · recorded-sol')
    expect(groups[0]!.text()).toContain('4 launch attempts')
    expect(groups[1]!.text().replace(/\s+/g, ' ')).toContain('Other project #2 · reviewer · delivery_agent · codex · astra')
    expect(groups[1]!.text()).toContain('2 launch attempts')
    expect(groups[0]!.get('[aria-hidden="true"] > div').attributes('style')).toContain('width: 100%')
    expect(groups[1]!.get('[aria-hidden="true"] > div').attributes('style')).toContain('width: 50%')
    expect(wrapper.text()).not.toContain('table order')
  })

  it('updates identities/counts on scope refresh and keeps zero, unknown and the eight-group limit explicit', async () => {
    const row = agentStatsFixture().rows[0]!
    const wrapper = mount(AgentActivityChart, { props: { rows: Array.from({ length: 9 }, () => row) } })
    expect(wrapper.findAll('li')).toHaveLength(8)
    expect(wrapper.text()).toContain('First 8 groups')
    await wrapper.setProps({ rows: [{ ...row, agent_name: null, selected_role: null, host_key: null, model: null, launch_attempts: 0 }] })
    expect(wrapper.findAll('li')).toHaveLength(1)
    expect(wrapper.text()).toContain('Unknown agent · Unknown role · Unknown host · Unknown model')
    expect(wrapper.text()).toContain('0 launch attempts')
    expect(wrapper.text()).not.toContain('recorded-sol')
    expect(wrapper.text()).not.toContain('Fixture project')
    expect(wrapper.get('[aria-hidden="true"] > div').attributes('style')).toContain('width: 0%')
  })
})
