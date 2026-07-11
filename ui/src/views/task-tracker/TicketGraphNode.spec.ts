import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { TrackerVueNodeData } from '@/lib/task-tracker/graphModel'

import TicketGraphNode from './TicketGraphNode.vue'

describe('TicketGraphNode', () => {
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
