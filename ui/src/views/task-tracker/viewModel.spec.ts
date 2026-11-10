// @vitest-environment node

import { describe, expect, it } from 'vitest'

import type { TrackerSnapshot, TrackerTask, TrackerTicket } from '@/lib/task-tracker/types'
import { parseBoardFilters } from './boardFilters'

import {
  buildFilteredTrackerSnapshot,
  buildTaskProgressRows,
  projectBoardRows,
  groupTicketsByTask,
  mergeFocusedTrackerSnapshot,
  selectGraphVisibleTickets,
} from './viewModel'

describe('task tracker view model', () => {
  it('projects Board rank with repository tie ordering while leaving the Graph list newest-first', () => {
    const tasks = [
      makeTask({ id: 1, key: 'old', order_index: 0, created_at: '2026-11-01T00:00:00' }),
      makeTask({ id: 2, key: 'new', order_index: 0, created_at: '2026-11-02T00:00:00Z' }),
      makeTask({ id: 3, key: 'tie', order_index: 0, created_at: '2026-11-02T00:00:00Z' }),
      makeTask({ id: 4, key: 'last', order_index: 7, created_at: '2026-11-03T00:00:00Z' }),
    ]
    const rows = buildTaskProgressRows(tasks, new Map(), emptyFilters())
    expect(projectBoardRows(rows, parseBoardFilters({})).map(row => row.key)).toEqual(['tie', 'new', 'old', 'last'])
    expect(rows.map(row => row.key)).toEqual(['last', 'tie', 'new', 'old'])
  })

  it.each([
    ['past-day', ['today', 'day']],
    ['past-week', ['today', 'day', 'over-day', 'week']],
    ['past-month', ['today', 'day', 'over-day', 'week', 'over-week', 'month']],
    ['all', ['today', 'day', 'over-day', 'week', 'over-week', 'month', 'old', 'unknown', 'invalid', 'future']],
    ['none', []],
  ])('uses actual Complete UTC timestamps for %s, inclusive of the rolling boundary', (completed, expected) => {
    const dates = {
      today: '2026-11-10T11:00:00', day: '2026-11-09T12:00:00Z',
      'over-day': '2026-11-09T11:59:59.999Z', week: '2026-11-03T14:00:00+02:00',
      'over-week': '2026-11-03T11:59:59.999Z', month: '2026-10-11T12:00:00Z',
      old: '2026-10-11T11:59:59.999Z', unknown: null, invalid: 'invalid', future: '2026-11-10T12:00:01Z',
    }
    const tasks = Object.entries(dates).map(([key, completed_at], i) => makeTask({
      id: i + 1, key, order_index: i, status: 'complete', completed_at,
      // Do not accidentally filter on updated/created timestamps.
      created_at: '2020-01-01T00:00:00Z', updated_at: '2030-01-01T00:00:00Z',
    }))
    tasks.push(makeTask({ key: 'reopened', status: 'in-progress', completed_at: dates.old, order_index: 50 }))
    const rows = buildTaskProgressRows(tasks, new Map(), emptyFilters())
    expect(projectBoardRows(rows, parseBoardFilters({ status: 'all', completed }), new Date('2026-11-10T12:00:00Z')).map(row => row.key)).toEqual([...expected, 'reopened'])
  })

  it('combines clauses against index summaries and full tickets, including negation and legacy restrictions', () => {
    const task = makeTask({ source_json: { template_key: 'engineering.delivery' }, ticket_summary: {
      total_count: 1, terminal_count: 0, in_progress_count: 0, blocked_count: 1,
      status_counts: { failed: 1 }, assignees: ['Reviewer'], search_text: 'Needle evidence',
    } })
    const state = parseBoardFilters({ search: 'needle', priority: 'p1', status: 'aborted', status_op: 'is-not',
      workflow: 'engineering.delivery', assignee: 'Reviewer', ticket_status: 'failed', focus: 'blocked' })
    const rows = buildTaskProgressRows([task], new Map(), emptyFilters())
    expect(projectBoardRows(rows, state)).toHaveLength(1)
    expect(projectBoardRows(rows, parseBoardFilters({ assignee: 'Reviewer', assignee_op: 'is-not' }))).toEqual([])
    expect(projectBoardRows(rows, parseBoardFilters({ workflow: 'engineering.delivery', workflow_op: 'is-not' }))).toEqual([])
    expect(projectBoardRows(rows, parseBoardFilters({ priority: 'p1', priority_op: 'is-not' }))).toEqual([])
    const ticket = makeTicket({ status: 'failed', assignee: 'Reviewer', title: 'Needle evidence' })
    const blocked = makeTicket({ id: 2, key: 'blocked', status: 'in-progress', blocker_reason: 'Waiting' })
    expect(projectBoardRows(buildTaskProgressRows([{ ...task, ticket_summary: null }], groupTicketsByTask([ticket, blocked]), emptyFilters()), state)).toHaveLength(1)
  })

  it('uses actual dependency titles in full snapshots without turning ready sibling work into a blocked task', () => {
    const task = makeTask({ key: 'delivery' })
    const waiting = makeTicket({ key: 'waiting', task_key: 'delivery', blocked_by: ['upstream'] })
    const upstream = makeTicket({ key: 'upstream', task_key: 'other-task', title: 'Actual upstream title' })
    const project = (tickets: TrackerTicket[]) => buildTaskProgressRows([task], groupTicketsByTask(tickets), emptyFilters())[0]!
    expect(project([waiting, upstream]).blockedByTitles).toEqual(['Actual upstream title'])
    expect(project([waiting, upstream, makeTicket({ key: 'ready', task_key: 'delivery' })]).blockedByTitles).toEqual([])
    expect(buildTaskProgressRows([{ ...task, status: 'complete' }], groupTicketsByTask([waiting, upstream]), emptyFilters())[0]!.blockedByTitles).toEqual([])
  })
  it('filters board task status independently from child ticket status', () => {
    const task = makeTask({ status: 'in-progress' })
    const ticket = makeTicket({ status: 'complete' })
    expect(buildTaskProgressRows([task], groupTicketsByTask([ticket]), {
      ...emptyFilters(), status: 'complete',
    })).toEqual([])
  })

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

  it('orders tasks by creation date with the newest first', () => {
    const olderBlocked = makeTask({
      id: 284,
      key: 'older-blocked',
      created_at: '2026-07-10T12:00:00Z',
      ticket_summary: {
        total_count: 5,
        terminal_count: 0,
        in_progress_count: 1,
        blocked_count: 4,
        status_counts: { 'in-progress': 1, 'not-started': 4 },
        assignees: [],
        search_text: '',
      },
    })
    const newer = makeTask({
      id: 329,
      key: 'newer',
      status: 'not-started',
      created_at: '2026-07-12T12:00:00Z',
    })
    const newestById = makeTask({ ...newer, id: 330, key: 'newest-by-id' })

    const rows = buildTaskProgressRows([olderBlocked, newer, newestById], new Map(), emptyFilters())

    expect(rows.map((row) => row.key)).toEqual(['newest-by-id', 'newer', 'older-blocked'])
  })

  it('applies workflow, assignee, status, and search controls without component state', () => {
    const task = makeTask({ source_json: { template_key: 'engineering.delivery' } })
    const ticket = makeTicket({
      status: 'in-progress',
      assignee: 'codex',
      outcome: 'Validated connection flow',
    })

    expect(
      buildTaskProgressRows([task], groupTicketsByTask([ticket]), {
        search: 'connection FLOW',
        status: 'in-progress',
        workflow: 'engineering.delivery',
        assignee: 'codex',
      }),
    ).toHaveLength(1)
    expect(
      buildTaskProgressRows([task], groupTicketsByTask([ticket]), {
        search: '',
        status: 'complete',
        workflow: 'engineering.delivery',
        assignee: 'codex',
      }),
    ).toHaveLength(0)
  })

  it('keeps task progress and filters truthful when only the task index is loaded', () => {
    const task = makeTask({
      ticket_summary: {
        total_count: 11,
        terminal_count: 8,
        in_progress_count: 1,
        blocked_count: 2,
        status_counts: {
          'not-started': 2,
          'in-progress': 1,
          complete: 8,
        },
        assignees: ['codex'],
        search_text: 'verification-ticket Verify release codex',
      },
    })

    const rows = buildTaskProgressRows([task], new Map(), {
      search: 'verification-ticket',
      status: 'all',
      ticketStatus: 'not-started',
      workflow: '',
      assignee: 'codex',
    })

    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({
      totalCount: 11,
      terminalCount: 8,
      inProgressCount: 1,
      blockedCount: 2,
      percent: 73,
      currentDetail: '2 blocked',
    })
  })

  it('prefers newly loaded ticket rows over an older index summary', () => {
    const task = makeTask({
      ticket_summary: {
        total_count: 9,
        terminal_count: 0,
        in_progress_count: 0,
        blocked_count: 0,
        status_counts: { 'not-started': 9 },
        assignees: [],
        search_text: '',
      },
    })
    const complete = makeTicket({ status: 'complete' })

    const rows = buildTaskProgressRows(
      [task],
      groupTicketsByTask([complete]),
      emptyFilters(),
    )
    const staleMatches = buildTaskProgressRows([task], groupTicketsByTask([complete]), {
      ...emptyFilters(),
      ticketStatus: 'not-started',
    })

    expect(rows[0]).toMatchObject({ totalCount: 1, terminalCount: 1, percent: 100 })
    expect(staleMatches).toEqual([])
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
    const task = makeTask({
      id: 1,
      key: 'delivery',
      ticket_summary: {
        total_count: 2,
        terminal_count: 0,
        in_progress_count: 0,
        blocked_count: 0,
        status_counts: {},
        assignees: [],
        search_text: '',
      },
    })
    const otherTask = makeTask({ id: 2, key: 'docs', title: 'Docs' })
    const visible = makeTicket({ id: 10, key: 'build', task_key: task.key })
    const hidden = makeTicket({ id: 11, key: 'ship', task_key: task.key })
    const otherTicket = makeTicket({ id: 12, key: 'write', task_key: otherTask.key })
    const base = makeSnapshot({ tasks: [task, otherTask], tickets: [visible, hidden, otherTicket] })
    const focused = makeSnapshot({
      tasks: [{ ...task, title: 'Updated delivery' }],
      tickets: [visible, hidden],
      dependencies: [
        {
          id: 1,
          ticket_key: visible.key,
          depends_on_ticket_key: hidden.key,
          dependency_type: 'hard',
        },
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
    expect(merged.tasks[1].ticket_summary?.total_count).toBe(2)
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
