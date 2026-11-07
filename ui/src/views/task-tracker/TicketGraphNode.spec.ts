import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { TrackerVueNodeData } from '@/lib/task-tracker/graphModel'
import { agentStatsFixture } from '@/views/home/agentStatsFixtures'

import TicketGraphNode from './TicketGraphNode.vue'

describe('TicketGraphNode', () => {
  it('renders own projected usage and truthfully refreshes unavailable and partial coverage', async () => {
    const metrics = agentStatsFixture().totals
    const wrapper = mount(TicketGraphNode, {
      props: { data: nodeData({ agentStats: metrics }) }, global: { stubs: { Handle: true } },
    })
    expect(wrapper.get('.ticket-graph-node__usage').findAll('dt').map(label => label.text())).toEqual(['Input', 'Cache', 'Output'])
    expect(wrapper.get('[data-usage-field="input"] dd').attributes('title')).toContain('70 tokens')
    const partial = structuredClone(metrics)
    partial.reported_usage.input = 0
    partial.reported_usage.cache_read = null
    partial.coverage.usage_status = 'partial' as never
    await wrapper.setProps({ data: nodeData({ agentStats: partial }) })
    expect(wrapper.get('[data-usage-field="input"] dd').text()).toBe('0')
    expect(wrapper.get('[data-usage-field="cache_read"] dd').text()).toBe('Not reported')
    expect(wrapper.get('[data-usage-field="output"] dd').text()).toBe('30')
    expect(wrapper.text()).not.toContain('Partial')
    await wrapper.setProps({ data: nodeData({ agentStats: null }) })
    expect(wrapper.get('.ticket-graph-node__usage').findAll('dd').map(value => value.text())).toEqual(['Not reported', 'Not reported', 'Not reported'])
    partial.coverage.usage_status = 'empty' as never
    partial.launch_attempts = 0
    await wrapper.setProps({ data: nodeData({ agentStats: partial }) })
    expect(wrapper.get('.ticket-graph-node__usage').text()).toContain('No recorded usage')
  })

  it('renders status text plus active and recent state affordances', () => {
    const active = mount(TicketGraphNode, {
      props: { data: nodeData({ active: true, status: 'in-progress' }) },
      global: { stubs: { Handle: true } },
    })
    const recent = mount(TicketGraphNode, {
      props: { data: nodeData({ recentlyUpdated: true, status: 'complete' }) },
      global: { stubs: { Handle: true } },
    })

    expect(active.text()).toContain('Active now')
    expect(active.text()).not.toContain('In Progress')
    expect(active.text()).toContain('P1')
    expect(active.text()).toContain('Owner')
    expect(active.text()).toContain('Codex')
    expect(active.text()).toContain('Agent')
    expect(active.text()).toContain('Claude Code')
    expect(active.text()).toContain('Workflow')
    expect(active.text()).toContain('Run 142')
    expect(active.classes()).toContain('ticket-graph-node--active')
    expect(recent.text()).toContain('Complete')
    expect(recent.text()).toContain('Updated')
    expect(recent.classes()).toContain('ticket-graph-node--recent')
  })
})

function nodeData(overrides: Partial<TrackerVueNodeData> = {}): TrackerVueNodeData {
  return {
    label: 'Deliver realtime chart updates',
    status: 'not-started',
    laneKey: 'implementation',
    priorityKey: 'p1',
    itemKind: 'ticket',
    itemKey: 'tracker-chart-live-client',
    subtitle: 'Keep the graph fresh',
    blockedBy: [],
    assignee: 'codex',
    owner: 'codex',
    agent: 'claude-code',
    sourceKind: 'workflow',
    runPlanId: 142,
    raw: {
      id: 'ticket:tracker-chart-live-client',
      type: 'ticket',
      parent_id: null,
      label: 'Deliver realtime chart updates',
      status: 'not-started',
      lane_key: 'implementation',
      priority_key: 'p1',
      data: {},
    },
    ...overrides,
  }
}
