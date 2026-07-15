import { isTerminalTrackerStatus } from '@/lib/task-tracker/status'
import type {
  TrackerSnapshot,
  TrackerStatus,
  TrackerTask,
  TrackerTicket,
} from '@/lib/task-tracker/types'

import type { GraphBlockFilter, StatusFilter, TaskProgressRow } from './viewTypes'

export interface TrackerControlFilters {
  search: string
  status: StatusFilter
  workflow: string
  assignee: string
}

export interface TrackerGraphFilters {
  statuses: TrackerStatus[]
  blocks: GraphBlockFilter[]
}

export function groupTicketsByTask(tickets: TrackerTicket[]): Map<string, TrackerTicket[]> {
  const groups = new Map<string, TrackerTicket[]>()
  for (const ticket of tickets) {
    const group = groups.get(ticket.task_key) ?? []
    group.push(ticket)
    groups.set(ticket.task_key, group)
  }
  for (const group of groups.values()) {
    group.sort((a, b) => a.order_index - b.order_index || a.id - b.id)
  }
  return groups
}

export function buildWorkflowOptions(
  tasks: TrackerTask[],
): Array<{ value: string; label: string }> {
  const values = new Set<string>()
  for (const task of tasks) {
    const source = task.source_json ?? {}
    for (const key of ['template_key', 'run_plan_key']) {
      const value = source[key]
      if (typeof value === 'string' && value) values.add(value)
    }
  }
  return [
    { value: '', label: 'All workflows' },
    ...Array.from(values)
      .sort()
      .map((value) => ({ value, label: value })),
  ]
}

export function buildAssigneeOptions(
  tasks: TrackerTask[],
  tickets: TrackerTicket[],
): Array<{ value: string; label: string }> {
  const values = new Set([
    ...tickets.flatMap((ticket) => (ticket.assignee ? [ticket.assignee] : [])),
    ...tasks.flatMap((task) => task.ticket_summary?.assignees ?? []),
  ])
  return [
    { value: '', label: 'All assignees' },
    ...Array.from(values)
      .sort()
      .map((value) => ({ value, label: value })),
  ]
}

export function buildTaskProgressRows(
  tasks: TrackerTask[],
  ticketsByTask: Map<string, TrackerTicket[]>,
  filters: TrackerControlFilters,
): TaskProgressRow[] {
  return tasks
    .map((task) => {
      const taskTickets = ticketsByTask.get(task.key) ?? []
      const summary = taskTickets.length > 0 ? null : task.ticket_summary
      const completedCount = summaryCount(summary, taskTickets, 'complete')
      const deferredCount = summaryCount(summary, taskTickets, 'deferred')
      const abortedCount = summaryCount(summary, taskTickets, 'aborted')
      const failedCount = summaryCount(summary, taskTickets, 'failed')
      const skippedCount = summaryCount(summary, taskTickets, 'skipped')
      const terminalCount =
        summary?.terminal_count ??
        completedCount + deferredCount + abortedCount + failedCount + skippedCount
      const inProgressCount = summaryCount(summary, taskTickets, 'in-progress')
      const blockedCount = summary?.blocked_count ?? taskTickets.filter(isGraphBlockedTicket).length
      const totalCount = summary?.total_count ?? taskTickets.length
      return {
        id: task.id,
        key: task.key,
        task,
        tickets: taskTickets,
        completedCount,
        deferredCount,
        abortedCount,
        failedCount,
        skippedCount,
        terminalCount,
        totalCount,
        inProgressCount,
        blockedCount,
        percent: totalCount > 0 ? Math.round((terminalCount / totalCount) * 100) : 0,
        workflowLabel: taskWorkflowLabel(task),
        currentDetail: taskCurrentDetail(task, totalCount, {
          completedCount,
          deferredCount,
          abortedCount,
          failedCount,
          skippedCount,
          inProgressCount,
          blockedCount,
        }),
      }
    })
    .filter((row) => taskRowMatchesControls(row, filters))
    .sort((a, b) => {
      const createdDiff = Date.parse(b.task.created_at) - Date.parse(a.task.created_at)
      return createdDiff || b.id - a.id
    })
}

export function ticketMatchesControls(
  ticket: TrackerTicket,
  task: TrackerTask,
  filters: TrackerControlFilters,
): boolean {
  const query = normalizedSearch(filters.search)
  if (filters.status !== 'all' && ticket.status !== filters.status) return false
  if (filters.assignee && ticket.assignee !== filters.assignee) return false
  if (!taskMatchesWorkflow(task, filters.workflow)) return false
  if (!query) return true
  return includesSearch(
    [ticket.key, ticket.title, ticket.goal, ticket.outcome ?? '', ticket.assignee ?? ''],
    query,
  )
}

export function taskRowMatchesControls(
  row: TaskProgressRow,
  filters: TrackerControlFilters,
): boolean {
  const { task, tickets } = row
  const summary = tickets.length > 0 ? null : task.ticket_summary
  const query = normalizedSearch(filters.search)
  if (
    filters.status !== 'all' &&
    task.status !== filters.status &&
    !tickets.some((ticket) => ticket.status === filters.status) &&
    !summary?.status_counts[filters.status]
  ) {
    return false
  }
  if (
    filters.assignee &&
    !tickets.some((ticket) => ticket.assignee === filters.assignee) &&
    !summary?.assignees.includes(filters.assignee)
  ) {
    return false
  }
  if (!taskMatchesWorkflow(task, filters.workflow)) return false
  if (!query) return true
  return includesSearch(
    [
      task.key,
      task.title,
      task.goal,
      task.description,
      task.owner ?? '',
      row.workflowLabel,
      summary?.search_text ?? '',
      ...tickets.flatMap((ticket) => [
        ticket.key,
        ticket.title,
        ticket.goal,
        ticket.outcome ?? '',
        ticket.assignee ?? '',
      ]),
    ],
    query,
  )
}

export function selectGraphVisibleTickets(
  tickets: TrackerTicket[],
  filters: TrackerGraphFilters,
): TrackerTicket[] {
  if (!filters.statuses.length && !filters.blocks.length) return tickets
  const matchedTickets = tickets.filter((ticket) => graphTicketMatchesFilters(ticket, filters))
  const visibleKeys = new Set(matchedTickets.map((ticket) => ticket.key))
  if (filters.blocks.includes('blocked')) {
    for (const ticket of matchedTickets) {
      for (const blockerKey of ticket.blocked_by) visibleKeys.add(blockerKey)
    }
  }
  return tickets.filter((ticket) => visibleKeys.has(ticket.key))
}

export function graphTicketMatchesFilters(
  ticket: TrackerTicket,
  filters: TrackerGraphFilters,
): boolean {
  const statusMatches = !filters.statuses.length || filters.statuses.includes(ticket.status)
  const blockKind: GraphBlockFilter = isGraphBlockedTicket(ticket) ? 'blocked' : 'open'
  const blockMatches = !filters.blocks.length || filters.blocks.includes(blockKind)
  return statusMatches && blockMatches
}

export function buildFilteredTrackerSnapshot(
  snapshot: TrackerSnapshot | null,
  activeTask: TrackerTask | null,
  activeTickets: TrackerTicket[],
  focusedGraph: TrackerSnapshot | null,
): TrackerSnapshot | null {
  if (!snapshot || !activeTask) return null
  const visibleTicketIds = new Set(activeTickets.map((ticket) => ticket.id))
  const graphNodeIds = new Set(activeTickets.map((ticket) => `ticket:${ticket.key}`))
  const graph = focusedGraph?.graph
    ? {
        ...focusedGraph.graph,
        nodes: focusedGraph.graph.nodes.filter((node) => graphNodeIds.has(node.id)),
        edges: focusedGraph.graph.edges.filter(
          (edge) =>
            edge.type === 'dependency' &&
            graphNodeIds.has(edge.source) &&
            graphNodeIds.has(edge.target),
        ),
      }
    : null
  const dependencies = focusedGraph?.dependencies ?? snapshot.dependencies
  const links = focusedGraph?.links ?? snapshot.links
  const visibleTicketKeys = new Set(activeTickets.map((ticket) => ticket.key))
  return {
    ...snapshot,
    tasks: [activeTask],
    tickets: activeTickets,
    dependencies: dependencies.filter(
      (dependency) =>
        visibleTicketKeys.has(dependency.ticket_key) &&
        visibleTicketKeys.has(dependency.depends_on_ticket_key),
    ),
    links: links.filter(
      (link) =>
        (link.ticket_id !== null && visibleTicketIds.has(link.ticket_id)) ||
        (link.task_id !== null && link.task_id === activeTask.id),
    ),
    graph,
  }
}

export function mergeFocusedTrackerSnapshot(
  base: TrackerSnapshot,
  focused: TrackerSnapshot,
): TrackerSnapshot {
  const focusedTask = focused.tasks[0]
  if (!focusedTask) return base
  const existingTask = base.tasks.find((task) => task.key === focusedTask.key)
  const mergedTask = {
    ...focusedTask,
    ticket_summary: focusedTask.ticket_summary ?? existingTask?.ticket_summary,
  }
  return {
    ...base,
    tasks: [...base.tasks.filter((task) => task.key !== focusedTask.key), mergedTask],
    tickets: [
      ...base.tickets.filter((ticket) => ticket.task_key !== focusedTask.key),
      ...focused.tickets,
    ],
  }
}

export function isOpenTicket(ticket: TrackerTicket): boolean {
  return !isTerminalTrackerStatus(ticket.status)
}

export function isGraphBlockedTicket(ticket: TrackerTicket): boolean {
  return isOpenTicket(ticket) && (ticket.blocked_by.length > 0 || Boolean(ticket.blocker_reason))
}

export function countStatuses(statuses: TrackerStatus[]): Record<TrackerStatus, number> {
  return statuses.reduce((counts, status) => {
    counts[status] += 1
    return counts
  }, emptyStatusCounts())
}

function taskWorkflowLabel(task: TrackerTask): string {
  const source = task.source_json ?? {}
  if (typeof source.run_plan_key === 'string' && source.run_plan_key) return source.run_plan_key
  if (typeof source.template_key === 'string' && source.template_key) return source.template_key
  if (typeof source.run_plan_id === 'number') return `run #${source.run_plan_id}`
  return task.source_kind
}

function taskCurrentDetail(
  task: TrackerTask,
  totalCount: number,
  counts: {
    completedCount: number
    deferredCount: number
    abortedCount: number
    failedCount: number
    skippedCount: number
    inProgressCount: number
    blockedCount: number
  },
): string {
  if (counts.blockedCount > 0) return `${counts.blockedCount} blocked`
  if (counts.inProgressCount > 0) return `${counts.inProgressCount} in progress`
  if (counts.failedCount > 0)
    return `${counts.completedCount} complete, ${counts.failedCount} failed`
  if (counts.abortedCount > 0)
    return `${counts.completedCount} complete, ${counts.abortedCount} aborted`
  if (counts.deferredCount > 0) {
    return `${counts.completedCount} complete, ${counts.deferredCount} deferred`
  }
  if (counts.skippedCount > 0)
    return `${counts.completedCount} complete, ${counts.skippedCount} skipped`
  if (task.status === 'complete') return 'complete'
  if (!totalCount) return task.status
  return `${totalCount} tickets`
}

function taskMatchesWorkflow(task: TrackerTask, workflow: string): boolean {
  if (!workflow) return true
  const source = task.source_json ?? {}
  return source.template_key === workflow || source.run_plan_key === workflow
}

function countTicketStatus(tickets: TrackerTicket[], status: TrackerStatus): number {
  return tickets.filter((ticket) => ticket.status === status).length
}

function summaryCount(
  summary: TrackerTask['ticket_summary'],
  tickets: TrackerTicket[],
  status: TrackerStatus,
): number {
  return summary?.status_counts[status] ?? countTicketStatus(tickets, status)
}

function normalizedSearch(search: string): string {
  return search.trim().toLowerCase()
}

function includesSearch(values: string[], search: string): boolean {
  return values.join(' ').toLowerCase().includes(search)
}

function emptyStatusCounts(): Record<TrackerStatus, number> {
  return {
    'not-started': 0,
    'in-progress': 0,
    complete: 0,
    deferred: 0,
    aborted: 0,
    failed: 0,
    skipped: 0,
  }
}
