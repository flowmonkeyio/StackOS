import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { TrackerStatus, TrackerTask } from '@/lib/task-tracker/types'
import { trackerStatus } from '@/design/status'
import { UiIcon } from '@/components/ui'
import { formatAbsoluteDateTime } from '@/lib/stackos/time'
import { buildTaskProgressRows } from './viewModel'
import TrackerBoardPanel from './TrackerBoardPanel.vue'
import TaskTrackerCommandPanel from './TaskTrackerCommandPanel.vue'

describe('Work board presentation', () => {
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

  it('shows real owner/source/date and counts only complete tickets in its checklist chip', () => {
    const record = { ...task('in-progress'), owner: 'delivery.agent', source_kind: 'workflow', source_json: { template_key: 'engineering.tracked-delivery' } }
    record.ticket_summary = { ...record.ticket_summary!, total_count: 3, terminal_count: 3, blocked_count: 1, status_counts: { complete: 1, failed: 1, skipped: 1 } }
    const rows = buildTaskProgressRows([record], new Map(), { search: '', status: 'all', workflow: '', assignee: '' })
    const wrapper = mount(TrackerBoardPanel, { props: { rows } })
    const card = wrapper.get('button')
    expect(card.get('.tracker-board__owner').text()).toBe('DA')
    expect(card.get('.tracker-board__owner').attributes('title')).toBe('delivery.agent')
    expect(card.get('.tracker-board__source').text()).toBe('engineering.tracked-delivery')
    expect(card.get('.tracker-board__source').findComponent(UiIcon).props('name')).toBe('git-branch')
    expect(card.get('time').attributes('datetime')).toBe(record.created_at)
    expect(card.get('time').attributes('title')).toBe(`Created ${formatAbsoluteDateTime(record.created_at)}`)
    expect(card.get('time').text()).toMatch(/^Created /)
    expect(card.text()).toContain('Blocked')
    expect(card.find('.text-warning-fg svg').exists()).toBe(true)
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
    expect(card.get('.tracker-board__source').text()).toBe('manual')
    expect(card.get('.tracker-board__source').findComponent(UiIcon).props('name')).toBe('file-text')
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
