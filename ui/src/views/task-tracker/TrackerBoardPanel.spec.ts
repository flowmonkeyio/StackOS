import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { TrackerStatus, TrackerTask } from '@/lib/task-tracker/types'
import { trackerStatus } from '@/design/status'
import { UiIcon } from '@/components/ui'
import { formatAbsoluteDateTime } from '@/lib/stackos/time'
import { agentStatsFixture } from '@/views/home/agentStatsFixtures'
import { buildTaskProgressRows } from './viewModel'
import TrackerBoardPanel from './TrackerBoardPanel.vue'
import TaskTrackerCommandPanel from './TaskTrackerCommandPanel.vue'

describe('Work board presentation', () => {
  it('indicates persisted automatic delivery and removes the badge when task metadata changes', async () => {
    const agent = { name: 'worker', instructions: 'Execute the assigned contract.' }
    const record = task('in-progress')
    const rows = () => buildTaskProgressRows([record], new Map(), { search: '', status: 'all', workflow: '', assignee: '' })
    const wrapper = mount(TrackerBoardPanel, { props: { rows: rows() } })
    expect(wrapper.get('button').text()).not.toContain('Automated')
    for (const status of ['queued', 'running', 'stopped', 'complete']) {
      record.metadata_json = { delivery: { status, job_ref: 'task-delivery:1:1', delivery_agent: agent, review_agent: agent } }
      await wrapper.setProps({ rows: rows() })
      const badge = wrapper.get('.ui-badge')
      expect(badge.text()).toBe('Automated')
      expect(badge.element.parentElement?.className).toBe('tracker-board__identity')
      expect(badge.classes()).toContain('text-neutral-fg')
      expect(badge.element.tagName).toBe('SPAN')
    }
    record.metadata_json = { delivery: { status: 'running' } }
    await wrapper.setProps({ rows: rows() })
    expect(wrapper.find('.ui-badge').exists()).toBe(false)
  })

  it('renders all three projected fields with exact values, independent missing data and zero', async () => {
    const metrics = agentStatsFixture().totals
    const record = { ...task('in-progress'), agent_stats: metrics }
    const rows = () => buildTaskProgressRows([{ ...record, agent_stats: structuredClone(metrics) }], new Map(), { search: '', status: 'all', workflow: '', assignee: '' })
    const wrapper = mount(TrackerBoardPanel, { props: { rows: rows() } })
    expect(wrapper.findAll('dt').map(label => label.text())).toEqual(['Input', 'Cache', 'Output'])
    expect(wrapper.get('[data-usage-field="input"] dd').attributes('aria-label')).toContain('70 tokens')
    metrics.reported_usage.input = 0
    await wrapper.setProps({ rows: rows() })
    expect(wrapper.get('[data-usage-field="input"] dd').text()).toBe('0')
    metrics.coverage.usage_status = 'partial' as never
    metrics.reported_usage.cache_read = null
    await wrapper.setProps({ rows: rows() })
    expect(wrapper.find('[data-usage-field="cache_read"]').exists()).toBe(false)
    expect(wrapper.get('[data-usage-field="output"] dd').text()).toBe('30')
    expect(wrapper.text()).not.toContain('Partial')
    metrics.reported_usage.input = null
    metrics.reported_usage.output = null
    await wrapper.setProps({ rows: rows() })
    expect(wrapper.findAll('dd[tabindex]')).toHaveLength(0)
    metrics.launch_attempts = 0
    await wrapper.setProps({ rows: rows() })
    expect(wrapper.get('button').text()).not.toContain('No recorded usage')
  })

  it('keeps all seven columns, including empty columns, with read-only cards', async () => {
    const rows = buildTaskProgressRows([task('in-progress')], new Map(), { search: '', status: 'all', workflow: '', assignee: '' })
    const wrapper = mount(TrackerBoardPanel, { props: { rows } })
    expect(wrapper.findAll('.tracker-board__column').map(column => column.attributes('aria-label'))).toEqual(Object.values(trackerStatus).map(status => status.label))
    expect(wrapper.findAll('.tracker-board__empty')).toHaveLength(6)
    expect(wrapper.get('[aria-label="Task board"]').attributes('tabindex')).toBe('0')
    expect(wrapper.findAll('.tracker-board__heading h2').map(heading => heading.text())).toEqual(Object.values(trackerStatus).map(status => status.label))
    expect(wrapper.findAll('.tracker-board__heading').map(heading => heading.findComponent(UiIcon).props('name'))).toEqual(Object.values(trackerStatus).map(status => status.icon))
    expect(wrapper.findAll('[draggable="true"]')).toHaveLength(0)
    expect(wrapper.text()).toContain('0/2')
    const card = wrapper.get('button')
    expect(card.attributes('aria-label')).toBe('Open task #1: Build task board')
    expect(card.findAll('button, a, input, select, [role="button"]')).toHaveLength(0)
    await card.trigger('click')
    expect(wrapper.emitted('openTask')).toEqual([['board-task']])
  })

  it('shows real owner/date and counts only complete tickets without labeling the task blocked', () => {
    const record = { ...task('in-progress'), owner: 'delivery.agent', source_kind: 'workflow', source_json: { template_key: 'engineering.tracked-delivery' } }
    record.ticket_summary = { ...record.ticket_summary!, total_count: 3, terminal_count: 3, blocked_count: 1, status_counts: { complete: 1, failed: 1, skipped: 1 } }
    const rows = buildTaskProgressRows([record], new Map(), { search: '', status: 'all', workflow: '', assignee: '' })
    const wrapper = mount(TrackerBoardPanel, { props: { rows } })
    const card = wrapper.get('button')
    expect(card.get('.tracker-board__owner').text()).toBe('DA')
    expect(card.get('.tracker-board__owner').attributes('title')).toBe('delivery.agent')
    expect(card.find('.tracker-board__source').exists()).toBe(false)
    expect(card.text()).not.toContain('engineering.tracked-delivery')
    expect(card.get('time').attributes('datetime')).toBe(record.created_at)
    expect(card.get('time').attributes('title')).toBe(`Created ${formatAbsoluteDateTime(record.created_at)}`)
    expect(card.get('time').text()).toMatch(/^Created /)
    expect(card.text()).not.toContain('Blocked')
    expect(card.get('[title="1 of 3 tickets complete"]').text()).toBe('1/3')
    expect(card.text()).not.toContain('3/3')
  })

  it('omits empty checklist, blocker and owner decorations without losing the accessible task title', () => {
    const record = { ...task('not-started'), owner: null, ticket_summary: null, title: 'Long task title '.repeat(20) }
    const rows = buildTaskProgressRows([record], new Map(), { search: '', status: 'all', workflow: '', assignee: '' })
    const wrapper = mount(TrackerBoardPanel, { props: { rows } })
    const card = wrapper.get('button')
    expect(card.attributes('aria-label')).toBe(`Open task #1: ${record.title}`)
    expect(card.get('.tracker-board__title').attributes('title')).toBe(record.title)
    expect(card.find('.tracker-board__owner').exists()).toBe(false)
    expect(card.text()).not.toContain('0/0')
    expect(card.text()).not.toContain('Blocked')
    expect(card.text()).not.toContain('manual')
  })

  it.each([
    ['Codex', '/images/openai.webp'],
    ['codex-main', '/images/openai.webp'],
    ['Claude', '/images/claude.webp'],
    ['Claude Code', '/images/claude-code.webp'],
    ['Gemini', '/images/gemini.webp'],
    ['Hermes', '/images/hermes.webp'],
    ['Grok', '/images/grok.png'],
    ['Grok Build', '/images/grok.png'],
  ])('uses the shared Overview logo for %s owners', (owner, logo) => {
    const record = { ...task('in-progress'), owner }
    const rows = buildTaskProgressRows([record], new Map(), { search: '', status: 'all', workflow: '', assignee: '' })
    const wrapper = mount(TrackerBoardPanel, { props: { rows } })
    const mark = wrapper.get('.tracker-board__owner img')
    expect(mark.attributes('src')).toBe(logo)
    expect(mark.attributes('alt')).toBe(owner)
    expect(wrapper.get('.tracker-board__owner').attributes('title')).toBe(owner)
  })

  it('keeps a human name as initials instead of guessing an agent brand', () => {
    const record = { ...task('in-progress'), owner: 'Claude Monet' }
    record.ticket_summary = { ...record.ticket_summary!, blocked_count: 1 }
    const rows = buildTaskProgressRows([record], new Map(), { search: '', status: 'all', workflow: '', assignee: '' })
    const wrapper = mount(TrackerBoardPanel, { props: { rows } })
    expect(wrapper.get('.tracker-board__owner').text()).toBe('CM')
    expect(wrapper.find('.tracker-board__owner img').exists()).toBe(false)
    expect(wrapper.get('button').text()).not.toMatch(/manual|Blocked/)
  })

  it('renders only Board and Graph, with the task picker exclusive to the graph', async () => {
    const wrapper = mount(TaskTrackerCommandPanel, { props: {
      viewMode: 'board', activeTaskKey: 'board-task', taskOptions: [], search: '', workflowFilter: '', workflowOptions: [],
      assigneeFilter: '', assigneeOptions: [], taskRowsCount: 1, tasksCount: 1, filtersActive: false, taskAvailable: true, graphSummary: '2 tickets',
    } })
    expect(wrapper.findAll('[role="tab"]').map(tab => tab.text())).toEqual(['Board', 'Graph'])
    expect(wrapper.find('[aria-label="Selected task"]').exists()).toBe(false)
    expect(wrapper.find('[aria-label="Search tasks"]').exists()).toBe(true)
    await wrapper.setProps({ viewMode: 'graph' })
    expect(wrapper.find('[aria-label="Selected task"]').exists()).toBe(true)
    expect(wrapper.find('[aria-label="Search tasks"]').exists()).toBe(false)
    const metrics = agentStatsFixture().totals
    await wrapper.setProps({ taskAgentStats: metrics })
    expect(wrapper.get('.tracker-command-panel__usage').findAll('dt').map(label => label.text())).toEqual(['Input', 'Cache', 'Output'])
    expect(wrapper.get('[data-usage-field="cache_read"] dd').text()).toBe('40')
    await wrapper.setProps({ taskAgentStats: null, taskAutomated: true })
    expect(wrapper.get('.tracker-command-panel__usage').findAll('dd')).toHaveLength(0)
    expect(wrapper.get('.ui-badge').text()).toBe('Automated')
    expect(wrapper.get('.tracker-command-panel__usage').find('.ui-badge').exists()).toBe(false)
    await wrapper.setProps({ taskAvailable: false })
    expect(wrapper.find('.tracker-command-panel__usage').exists()).toBe(false)
  })
})

function task(status: TrackerStatus): TrackerTask {
  return {
    id: 1, project_id: 1, tracker_id: 1, key: 'board-task', title: 'Build task board', goal: '', description: '', status,
    priority_key: 'p2', lane_key: 'implementation', owner: 'Codex', task_type: 'manual', order_index: 1, source_kind: 'manual', source_json: null,
    definition_of_done_json: [], constraints_json: [], expected_outcomes_json: [], completion_evidence_json: null, context_json: null,
    metadata_json: null, created_by: 'codex', created_at: '2026-10-28T00:00:00Z', updated_at: '2026-10-28T00:00:00Z', started_at: null, completed_at: null,
    ticket_summary: { total_count: 2, terminal_count: 2, in_progress_count: 0, blocked_count: 0, status_counts: { failed: 1, skipped: 1 }, assignees: [], search_text: '' },
  }
}
