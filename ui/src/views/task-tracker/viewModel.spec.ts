import { describe, expect, it } from 'vitest'

import type {
  TrackerSnapshot,
  TrackerTask,
  TrackerTicket,
} from '@/lib/task-tracker/types'

import {
  buildFilteredTrackerSnapshot,
  buildTaskProgressRows,
  groupTicketsByTask,
  mergeFocusedTrackerSnapshot,
  selectGraphVisibleTickets,
  ticketMatchesControls,
} from './viewModel'

describe('task tracker view model', () => {
  it('groups tickets in display order and projects task progress', () => {
    const task = makeTask({ key: 'delivery', source_json: { run_plan_key: 'release' } })
    const blocked = makeTicket({
      id: 2,
      key: 'build',
      task_key: task.key,
      order_index: 2,
      blocker_reason: 'waiting for review',
    })
    const complete = makeTicket({
      id: 1,
      key: 'scope',
      task_key: task.key,
      order_index: 1,
      status: 'complete',
    })

    const groups = groupTicketsByTask([blocked, complete])
    const rows = buildTaskProgressRows([task], groups, emptyFilters())

    expect(groups.get(task.key)?.map((ticket) => ticket.key)).toEqual(['scope', 'build'])
    expect(rows[0]).toMatchObject({
      completedCount: 1,
      blockedCount: 1,
      terminalCount: 1,
      totalCount: 2,
      percent: 50,
      workflowLabel: 'release',
      currentDetail: '1 blocked',
    })
  })

  it('applies workflow, assignee, status, and search controls without component state', () => {
    const task = makeTask({ source_json: { template_key: 'engineering.delivery' } })
    const ticket = makeTicket({
      status: 'in-progress',
      assignee: 'codex',
      outcome: 'Validated connection flow',
    })

    expect(
      ticketMatchesControls(ticket, task, {
        search: 'connection FLOW',
        status: 'in-progress',
        workflow: 'engineering.delivery',
        assignee: 'codex',
      }),
    ).toBe(true)
    expect(
      ticketMatchesControls(ticket, task, {
        search: '',
        status: 'complete',
        workflow: 'engineering.delivery',
        assignee: 'codex',
      }),
    ).toBe(false)
  })

  it('keeps blockers visible when the graph is filtered to blocked tickets', () => {
    const blocker = makeTicket({ key: 'scope', status: 'complete' })
    const blocked = makeTicket({ key: 'build', blocked_by: ['scope'] })
    const unrelated = makeTicket({ key: 'docs' })

    const visible = selectGraphVisibleTickets([blocker, blocked, unrelated], {
      statuses: [],
      blocks: ['blocked'],
    })

    expect(visible.map((ticket) => ticket.key)).toEqual(['scope', 'build'])
  })

  it('projects a focused graph and replaces only the focused task during a merge', () => {
    const task = makeTask({ id: 1, key: 'delivery' })
    const otherTask = makeTask({ id: 2, key: 'docs', title: 'Docs' })
    const visible = makeTicket({ id: 10, key: 'build', task_key: task.key })
    const hidden = makeTicket({ id: 11, key: 'ship', task_key: task.key })
    const otherTicket = makeTicket({ id: 12, key: 'write', task_key: otherTask.key })
    const base = makeSnapshot({ tasks: [task, otherTask], tickets: [visible, hidden, otherTicket] })
    const focused = makeSnapshot({
      tasks: [{ ...task, title: 'Updated delivery' }],
      tickets: [visible, hidden],
      dependencies: [
        { id: 1, ticket_key: visible.key, depends_on_ticket_key: hidden.key, dependency_type: 'hard' },
      ],
      links: [
        makeLink({ id: 1, ticket_id: visible.id }),
        makeLink({ id: 2, ticket_id: hidden.id }),
        makeLink({ id: 3, task_id: task.id }),
      ],
      graph: {
        nodes: [makeNode(visible.key), makeNode(hidden.key)],
        edges: [
          {
            id: 'dependency:build:ship',
            type: 'dependency',
            source: `ticket:${visible.key}`,
            target: `ticket:${hidden.key}`,
            label: null,
            data: {},
          },
        ],
        warnings: [],
        layout_hints: {},
      },
    })

    const projected = buildFilteredTrackerSnapshot(base, task, [visible], focused)
    const merged = mergeFocusedTrackerSnapshot(base, focused)

    expect(projected?.tickets).toEqual([visible])
    expect(projected?.dependencies).toEqual([])
    expect(projected?.links.map((link) => link.id)).toEqual([1, 3])
    expect(projected?.graph?.nodes.map((node) => node.id)).toEqual(['ticket:build'])
    expect(projected?.graph?.edges).toEqual([])
    expect(merged.tasks.map((item) => item.title)).toEqual(['Docs', 'Updated delivery'])
    expect(merged.tickets.map((ticket) => ticket.key)).toEqual(['write', 'build', 'ship'])
  })
})

function emptyFilters() {
  return { search: '', status: 'all' as const, workflow: '', assignee: '' }
}

function makeTask(overrides: Partial<TrackerTask> = {}): TrackerTask {
  return {
    id: 1,
    project_id: 1,
    tracker_id: 1,
    key: 'delivery',
    title: 'Delivery',
    goal: 'Ship safely',
    description: 'Tracked delivery',
    status: 'in-progress',
    priority_key: 'p1',
    lane_key: 'implementation',
    owner: null,
    task_type: 'delivery',
    order_index: 1,
    source_kind: 'manual',
    source_json: null,
    definition_of_done_json: [],
    constraints_json: [],
    expected_outcomes_json: [],
    completion_evidence_json: null,
    context_json: null,
    metadata_json: null,
    created_by: null,
    created_at: '2026-07-10T12:00:00Z',
    updated_at: '2026-07-10T12:00:00Z',
    started_at: null,
    completed_at: null,
    ...overrides,
  }
}

function makeTicket(overrides: Partial<TrackerTicket> = {}): TrackerTicket {
  return {
    id: 1,
    project_id: 1,
    tracker_id: 1,
    task_id: 1,
    task_key: 'delivery',
    parent_ticket_id: null,
    parent_ticket_key: null,
    run_plan_id: null,
    run_plan_step_id: null,
    run_id: null,
    agent_request_id: null,
    key: 'build',
    title: 'Build the feature',
    goal: 'Deliver behavior',
    status: 'not-started',
    kind: 'ticket',
    assignee: null,
    priority_key: 'p1',
    lane_key: 'implementation',
    order_index: 1,
    blocker_reason: null,
    outcome: null,
    effort: null,
    source_kind: 'manual',
    source_json: null,
    definition_of_done_json: [],
    constraints_json: [],
    expected_changes_json: [],
    allowed_paths_json: [],
    completion_evidence_json: null,
    context_json: null,
    metadata_json: null,
    created_by: null,
    claimed_at: null,
    created_at: '2026-07-10T12:00:00Z',
    updated_at: '2026-07-10T12:00:00Z',
    started_at: null,
    completed_at: null,
    dependency_keys: [],
    blocked_by: [],
    reference_count: 0,
    link_count: 0,
    ...overrides,
  }
}

function makeSnapshot(overrides: Partial<TrackerSnapshot> = {}): TrackerSnapshot {
  return {
    tracker: {
      id: 1,
      project_id: 1,
      key: 'default',
      name: 'Default',
      description: '',
      rev: 1,
      created_at: '2026-07-10T12:00:00Z',
      updated_at: '2026-07-10T12:00:00Z',
    },
    lanes: [],
    priorities: [],
    tasks: [],
    tickets: [],
    dependencies: [],
    links: [],
    graph: null,
    ...overrides,
  }
}

function makeNode(ticketKey: string) {
  return {
    id: `ticket:${ticketKey}`,
    type: 'ticket' as const,
    parent_id: null,
    label: ticketKey,
    status: 'not-started',
    lane_key: 'implementation',
    priority_key: 'p1',
    data: {},
  }
}

function makeLink(overrides: Partial<TrackerSnapshot['links'][number]>) {
  return {
    id: 1,
    task_id: null,
    ticket_id: null,
    link_kind: 'context',
    ref: null,
    run_plan_id: null,
    run_plan_step_id: null,
    run_id: null,
    agent_request_id: null,
    resource_record_id: null,
    artifact_id: null,
    action_call_id: null,
    title: null,
    metadata_json: null,
    created_at: '2026-07-10T12:00:00Z',
    ...overrides,
  }
}
