import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { TrackerVueNodeData } from '@/lib/task-tracker/graphModel'
import { agentStatsFixture } from '@/views/home/agentStatsFixtures'

import TicketGraphNode from './TicketGraphNode.vue'

describe('TicketGraphNode', () => {
  it('anchors actual prerequisite titles and retains measured Last identity after execution', () => {
    const metrics = agentStatsFixture().totals
    metrics.last_execution = execution
    const wrapper = mount(TicketGraphNode, { props: { data: nodeData({ taskAutomated: true, agentStats: metrics, blockedBy: ['Actual prerequisite title', 'Another long prerequisite title'] }) }, global: { stubs: { Handle: true } } })
    expect(wrapper.get('.ticket-graph-node__blocked').attributes('title')).toBe('Blocked by: Actual prerequisite title, Another long prerequisite title')
    expect(wrapper.get('.ticket-graph-node__blocked').attributes('tabindex')).toBe('0')
    expect(wrapper.get('[data-last-execution]').text()).toContain('Last: Delivery reviewer')
    expect(wrapper.find('[data-active-execution]').exists()).toBe(false)
    expect(wrapper.findAll('canvas')).toHaveLength(3)
  })
  it('keeps selection, in-progress and first-open hints separate from live execution', () => {
    const wrapper = mount(TicketGraphNode, {
      props: { data: nodeData({ active: true, status: 'in-progress' }) },
      global: { stubs: { Handle: true } },
    })
    expect(wrapper.text()).not.toContain('Active now')
    expect(wrapper.find('[data-active-execution]').exists()).toBe(false)
    expect(wrapper.classes()).not.toContain('ticket-graph-node--active')
  })
  it('keeps full ticket context and identities discoverable when compact labels truncate', () => {
    const data = nodeData({
      label: 'A long ticket title with enough detail to exceed two lines in the compact graph card',
      itemKey: 'a-very-long-ticket-key-with-required-scope-and-provenance',
      subtitle: 'A longer description that remains available without expanding the graph card.',
      owner: 'A very long delivery owner name',
      activeExecution: { ...execution, agent: 'A very long project agent name' },
    })
    const wrapper = mount(TicketGraphNode, {
      props: { data },
      global: { stubs: { Handle: true } },
    })
    expect(wrapper.get('.ticket-graph-node__title').attributes('title')).toBe(data.label)
    expect(wrapper.get('.ticket-graph-node__key').attributes('title')).toBe(data.itemKey)
    expect(wrapper.get('.ticket-graph-node__subtitle').attributes('title')).toBe(data.subtitle)
    expect(wrapper.get('[data-active-execution]').attributes('title')).toContain(data.activeExecution!.agent)
    expect(wrapper.get('[data-active-execution]').attributes('tabindex')).toBe('0')
    expect(wrapper.find('.ticket-graph-node__people').exists()).toBe(false)
  })

  it('shows the recorded stop instead of treating focus as a running agent', () => {
    const wrapper = mount(TicketGraphNode, {
      props: { data: nodeData({ active: true, status: 'failed', deliveryStopped: true, deliveryStopReason: 'Browser access denied.' }) },
      global: { stubs: { Handle: true } },
    })
    expect(wrapper.text()).toContain('Stopped')
    expect(wrapper.text()).not.toContain('Active now')
    expect(wrapper.classes()).not.toContain('ticket-graph-node--active')
    expect(wrapper.get('.ticket-graph-node__floating-state').attributes('title')).toBe('Browser access denied.')
  })
  it('omits usage without parent automation identity even with measured tokens', async () => {
    const wrapper = mount(TicketGraphNode, { props: { data: nodeData({ agentStats: agentStatsFixture().totals }) }, global: { stubs: { Handle: true } } })
    expect(wrapper.find('.agent-usage-summary').exists()).toBe(false)
    expect(wrapper.text()).not.toMatch(/Input|Cache|Output|No recorded usage/)
  })
  it('renders own projected usage and truthfully refreshes unavailable and partial coverage', async () => {
    const metrics = agentStatsFixture().totals
    const wrapper = mount(TicketGraphNode, {
      props: { data: nodeData({ agentStats: metrics, taskAutomated: true }) }, global: { stubs: { Handle: true } },
    })
    expect(wrapper.get('.ticket-graph-node__usage').findAll('dt').map(label => label.text())).toEqual(['Input', 'Cache', 'Output'])
    expect(wrapper.get('[data-usage-field="input"] dd').attributes('title')).toContain('30 tokens')
    const partial = structuredClone(metrics)
    partial.reported_usage.input = 0
    partial.reported_usage.cache_read = null
    partial.reported_usage.remaining_input = null
    partial.coverage.usage_status = 'partial' as never
    await wrapper.setProps({ data: nodeData({ agentStats: partial, taskAutomated: true }) })
    expect(wrapper.get('[data-usage-field="input"] dd').text()).toBe('N/A')
    expect(wrapper.get('[data-usage-field="cache_read"] dd').text()).toBe('N/A')
    expect(wrapper.get('[data-usage-field="output"] dd').text()).toBe('30')
    expect(wrapper.text()).not.toContain('Partial')
    await wrapper.setProps({ data: nodeData({ agentStats: null, taskAutomated: true }) })
    expect(wrapper.findAll('.ticket-graph-node__usage dd').map(item => item.text())).toEqual(['N/A', 'N/A', 'N/A'])
    partial.coverage.usage_status = 'empty' as never
    partial.launch_attempts = 0
    await wrapper.setProps({ data: nodeData({ agentStats: partial, taskAutomated: true }) })
    expect(wrapper.findAll('.ticket-graph-node__usage dd').map(item => item.text())).toEqual(['N/A', 'N/A', 'N/A'])
  })

  it('renders status text plus active and recent state affordances', () => {
    const active = mount(TicketGraphNode, {
      props: { data: nodeData({ activeExecution: execution, status: 'in-progress' }) },
      global: { stubs: { Handle: true } },
    })
    const recent = mount(TicketGraphNode, {
      props: { data: nodeData({ recentlyUpdated: true, status: 'complete' }) },
      global: { stubs: { Handle: true } },
    })

    expect(active.text()).toContain('Starting')
    expect(active.text()).not.toContain('In Progress')
    expect(active.text()).toContain('P1')
    expect(active.text()).toContain('Delivery reviewer')
    expect(active.text()).toContain('gpt-6-astra')
    expect(active.text()).not.toContain('Owner')
    expect(active.classes()).toContain('ticket-graph-node--active')
    expect(recent.text()).toContain('Complete')
    expect(recent.get('.ticket-graph-node__floating-state').attributes('title')).toBe('Updated')
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

const execution = { live_usage: null, invocation_id: 'call-review', agent: 'Delivery reviewer', host_key: 'codex', model: 'gpt-6-astra', started_at: '2026-11-08T01:08:00Z', ticket_key: 'tracker-chart-live-client' }
