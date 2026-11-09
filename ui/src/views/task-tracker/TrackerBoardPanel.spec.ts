import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { TrackerStatus, TrackerTask } from '@/lib/task-tracker/types'
import { trackerStatus } from '@/design/status'
import { UiIcon } from '@/components/ui'
import { agentStatsFixture } from '@/views/home/agentStatsFixtures'
import { buildTaskProgressRows } from './viewModel'
import TrackerBoardPanel from './TrackerBoardPanel.vue'
import TaskTrackerCommandPanel from './TaskTrackerCommandPanel.vue'

const agent = { name: 'worker', instructions: 'Execute the assigned contract.' }
const automaticMetadata = { delivery: { status: 'running', job_ref: 'synthetic:never-execute', delivery_agent: agent, review_agent: agent } }

describe('Work board presentation', () => {
  it('retains last actual execution and suppresses task-level blocking while a sibling executes', async () => {
    const metrics = agentStatsFixture().totals
    const execution = { invocation_id: 'actual-call', ticket_key: 'child', agent: 'sdlc_delivery_reviewer', host_key: 'claude-code', model: 'recorded-model', started_at: '2026-11-08T01:00:00Z', live_usage: null }
    metrics.last_execution = execution
    const record = { ...task('in-progress'), metadata_json: automaticMetadata, agent_stats: metrics, active_execution: execution }
    record.ticket_summary = { ...record.ticket_summary!, blocked_by_titles: ['Actual prerequisite title'] }
    const rows = () => buildTaskProgressRows([structuredClone(record)], new Map(), { search: '', status: 'all', workflow: '', assignee: '' })
    const wrapper = mount(TrackerBoardPanel, { props: { rows: rows() } })
    expect(wrapper.find('.tracker-board__blocked').exists()).toBe(false)
    expect(wrapper.get('[data-active-execution] img').attributes('src')).toBe('/images/claude-code.webp')
    await wrapper.setProps({ rows: rows().map(row => ({ ...row, task: { ...row.task, active_execution: null } })) })
    expect(wrapper.get('[data-last-execution]').text()).toContain('Last: Delivery reviewer')
    expect(wrapper.get('.tracker-board__blocked').attributes('title')).toBe('Blocked by: Actual prerequisite title')
    expect(wrapper.find('.vue-flow__handle').exists()).toBe(false)
  })
  it('shows stopped delivery without moving the task column and clears it after resume', async () => {
    const metadata = { delivery: { ...automaticMetadata.delivery, status: 'stopped', ticket_key: 'fix-chart', reason: 'Browser access denied.' } }
    const record = { ...task('in-progress'), metadata_json: metadata }
    const rows = () => buildTaskProgressRows([structuredClone(record)], new Map(), { search: '', status: 'all', workflow: '', assignee: '' })
    const wrapper = mount(TrackerBoardPanel, { props: { rows: rows() } })
    const card = wrapper.get('[aria-label="In Progress"] button')
    expect(card.text()).toContain('Automated')
    expect(card.text()).toContain('Stopped')
    expect(card.get('.tracker-board__subtitle').attributes('title')).toBe('Browser access denied.')
    expect(card.text()).not.toContain('Blocked by:')
    record.metadata_json.delivery.status = 'running'
    await wrapper.setProps({ rows: rows() })
    expect(wrapper.get('[aria-label="In Progress"] button').text()).not.toContain('Delivery stopped')
    expect(wrapper.find('.task-delivery-stop').exists()).toBe(false)
  })
  it('indicates persisted automatic delivery and removes the badge when task metadata changes', async () => {
    const agent = { name: 'worker', instructions: 'Execute the assigned contract.' }
    const record = task('in-progress')
    const rows = () => buildTaskProgressRows([structuredClone(record)], new Map(), { search: '', status: 'all', workflow: '', assignee: '' })
    const wrapper = mount(TrackerBoardPanel, { props: { rows: rows() } })
    expect(wrapper.get('button').text()).not.toContain('Automated')
    for (const status of ['queued', 'running', 'stopped', 'complete']) {
      record.metadata_json = { delivery: { status, job_ref: 'task-delivery:1:1', delivery_agent: agent, review_agent: agent } }
      await wrapper.setProps({ rows: rows() })
      const badge = wrapper.get('.ui-badge')
      expect(badge.text()).toBe('Automated')
      expect(badge.element.parentElement?.classList.contains('tracker-board__identity')).toBe(true)
      expect(badge.classes()).toContain('text-info-fg')
      expect(badge.element.tagName).toBe('SPAN')
    }
    record.metadata_json = { delivery: { status: 'running' } }
    await wrapper.setProps({ rows: rows() })
    expect(wrapper.findAll('.ui-badge').some(badge => badge.text() === 'Automated')).toBe(false)
  })

  it('renders all three projected fields with exact values, independent missing data and zero', async () => {
    const metrics = agentStatsFixture().totals
    const record = { ...task('in-progress'), metadata_json: automaticMetadata, agent_stats: metrics }
    const rows = () => buildTaskProgressRows([{ ...record, agent_stats: structuredClone(metrics) }], new Map(), { search: '', status: 'all', workflow: '', assignee: '' })
    const wrapper = mount(TrackerBoardPanel, { props: { rows: rows() } })
    expect(wrapper.findAll('dt').map(label => label.text())).toEqual(['Input', 'Cache', 'Output'])
    expect(wrapper.get('[data-usage-field="input"] dd').attributes('aria-label')).toContain('30 tokens')
    metrics.reported_usage.input = 0
    metrics.reported_usage.remaining_input = 0
    await wrapper.setProps({ rows: rows() })
    expect(wrapper.get('[data-usage-field="input"] dd').text()).toBe('0')
    metrics.coverage.usage_status = 'partial' as never
    metrics.reported_usage.cache_read = null
    metrics.reported_usage.remaining_input = null
    await wrapper.setProps({ rows: rows() })
    expect(wrapper.get('[data-usage-field="cache_read"] dd').text()).toBe('N/A')
    expect(wrapper.get('[data-usage-field="output"] dd').text()).toBe('30')
    expect(wrapper.text()).not.toContain('Partial')
    metrics.reported_usage.input = null
    metrics.reported_usage.output = null
    await wrapper.setProps({ rows: rows() })
    expect(wrapper.findAll('dd[tabindex]').map(item => item.text())).toEqual(['N/A', 'N/A', 'N/A'])
    metrics.launch_attempts = 0
    await wrapper.setProps({ rows: rows() })
    expect(wrapper.get('button').text()).not.toContain('No recorded usage')
  })

  it('omits usage for manual workflow tasks despite measured tokens and shows non-engineering automation', async () => {
    const record = { ...task('in-progress'), task_type: 'workflow', source_kind: 'workflow', source_json: { template_key: 'seo.content-refresh' }, agent_stats: agentStatsFixture().totals }
    const rows = () => buildTaskProgressRows([structuredClone(record)], new Map(), { search: '', status: 'all', workflow: '', assignee: '' })
    const wrapper = mount(TrackerBoardPanel, { props: { rows: rows() } })
    expect(wrapper.find('.agent-usage-summary').exists()).toBe(false)
    expect(wrapper.text()).not.toMatch(/Input|Cache|Output|No recorded usage/)
    record.metadata_json = automaticMetadata
    await wrapper.setProps({ rows: rows() })
    expect(wrapper.get('.agent-usage-summary').classes()).toContain('agent-usage-summary--compact')
    record.agent_stats.reported_usage.input = null
    record.agent_stats.reported_usage.remaining_input = null
    record.agent_stats.reported_usage.cache_read = null
    record.agent_stats.reported_usage.output = null
    await wrapper.setProps({ rows: rows() })
    expect(wrapper.findAll('.agent-usage-summary dd').map(item => item.text())).toEqual(['N/A', 'N/A', 'N/A'])
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

  it('shows ordinary assignment and counts only complete tickets without labeling the task blocked', () => {
    const record = { ...task('in-progress'), owner: 'delivery.agent', source_kind: 'workflow', source_json: { template_key: 'engineering.tracked-delivery' } }
    record.ticket_summary = { ...record.ticket_summary!, total_count: 3, terminal_count: 3, blocked_count: 1, status_counts: { complete: 1, failed: 1, skipped: 1 } }
    const rows = buildTaskProgressRows([record], new Map(), { search: '', status: 'all', workflow: '', assignee: '' })
    const wrapper = mount(TrackerBoardPanel, { props: { rows } })
    const card = wrapper.get('button')
    expect(card.get('.active-execution-line').text()).toBe('Assigned: delivery.agent')
    expect(card.find('.tracker-board__source').exists()).toBe(false)
    expect(card.text()).not.toContain('engineering.tracked-delivery')
    expect(card.find('time').exists()).toBe(false)
    expect(card.find('[data-active-execution]').exists()).toBe(false)
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
    const mark = wrapper.get('.active-execution-line img')
    expect(mark.attributes('src')).toBe(logo)
    expect(mark.attributes('alt')).toBe(owner)
    expect(wrapper.get('.active-execution-line').attributes('title')).toBe(`Assigned: ${owner}`)
  })

  it('keeps a human assignment instead of guessing an agent brand', () => {
    const record = { ...task('in-progress'), owner: 'Claude Monet' }
    record.ticket_summary = { ...record.ticket_summary!, blocked_count: 1 }
    const rows = buildTaskProgressRows([record], new Map(), { search: '', status: 'all', workflow: '', assignee: '' })
    const wrapper = mount(TrackerBoardPanel, { props: { rows } })
    expect(wrapper.get('.active-execution-line').text()).toBe('Assigned: Claude Monet')
    expect(wrapper.find('.active-execution-line img').exists()).toBe(false)
    expect(wrapper.get('button').text()).not.toMatch(/manual|Blocked/)
  })

  it('renders only Board and Graph, with the task picker exclusive to the graph', async () => {
    const wrapper = mount(TaskTrackerCommandPanel, { props: {
      viewMode: 'board', activeTaskKey: 'board-task', taskOptions: [], search: '', workflowFilter: '', workflowOptions: [],
      assigneeFilter: '', assigneeOptions: [], taskRowsCount: 1, tasksCount: 1, filtersActive: false, taskAvailable: true, taskId: 1, completedCount: 12, totalCount: 20,
    } })
    expect(wrapper.findAll('[role="tab"]').map(tab => tab.text())).toEqual(['Board', 'Graph'])
    expect(wrapper.find('[aria-label="Selected task"]').exists()).toBe(false)
    expect(wrapper.find('[aria-label="Search tasks"]').exists()).toBe(true)
    await wrapper.setProps({ viewMode: 'graph' })
    expect(wrapper.find('[aria-label="Selected task"]').exists()).toBe(true)
    expect(wrapper.find('[aria-label="Search tasks"]').exists()).toBe(false)
    const metrics = agentStatsFixture().totals
    await wrapper.setProps({ taskAgentStats: metrics })
    expect(wrapper.find('.tracker-command-panel__usage').exists()).toBe(false)
    await wrapper.setProps({ taskAutomated: true })
    expect(wrapper.get('.tracker-command-panel__usage').findAll('dt').map(label => label.text())).toEqual(['Input', 'Cache', 'Output'])
    expect(wrapper.get('[data-usage-field="cache_read"] dd').text()).toBe('40')
    await wrapper.setProps({ taskAgentStats: null, taskAutomated: true })
    expect(wrapper.findAll('.tracker-command-panel__usage dd').map(item => item.text())).toEqual(['N/A', 'N/A', 'N/A'])
    expect(wrapper.get('.ui-badge').text()).toBe('Automated')
    expect(wrapper.get('.ui-badge').classes()).toContain('text-info-fg')
    await wrapper.setProps({ taskDelivery: { status: 'stopped', ticketKey: 'fix-chart', reason: 'Browser access denied.' } })
    expect(wrapper.get('.tracker-command-panel__progress').text()).toContain('Stopped')
    expect(wrapper.get('.tracker-command-panel__progress').attributes('title')).toBe('Browser access denied.')
    expect(wrapper.text()).toContain('12/20 complete')
    expect(wrapper.text()).not.toContain('dependencies')
    expect(wrapper.find('.tracker-command-panel__graph-count').exists()).toBe(false)
    await wrapper.setProps({ taskAvailable: false })
    expect(wrapper.find('.tracker-command-panel__usage').exists()).toBe(false)
    expect(wrapper.find('.task-delivery-stop').exists()).toBe(false)
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
