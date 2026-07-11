import { computed, ref } from 'vue'

import type { TrackerSnapshot, TrackerTask, TrackerTicket } from '@/lib/task-tracker/types'
import { callOperation } from '@/lib/operations'
import type { Project } from '@/stores/projects'

interface TrackerCountSummary {
  total?: number
  active?: number
  done?: number
  in_progress?: number
  blocked?: number
}

interface TrackerStatusResponse {
  summary?: {
    tasks?: TrackerCountSummary
    tickets?: TrackerCountSummary
  }
  blocked_ticket_count?: number
  in_progress_ticket_count?: number
  task_counts?: Record<string, number>
  ticket_counts?: Record<string, number>
}

export interface PortfolioWorkItem {
  key: string
  title: string
  projectId: number
  projectName: string
  owner: string | null
  priority: string
  activeTicketCount: number
  blockerCount: number
  updatedAt: string | null
}

export interface ProjectPortfolioInsight {
  projectId: number
  projectName: string
  activeTaskCount: number
  inProgressTicketCount: number
  blockedTicketCount: number
  totalTaskCount: number
  doneTaskCount: number
  completionPercent: number
  workItems: PortfolioWorkItem[]
}

const MAX_CONCURRENT_PROJECTS = 4

function numeric(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function countTotal(counts: Record<string, number> | undefined): number {
  return Object.values(counts ?? {}).reduce((sum, value) => sum + numeric(value), 0)
}

function countDone(counts: Record<string, number> | undefined): number {
  return ['complete', 'deferred', 'aborted', 'failed', 'skipped'].reduce(
    (sum, key) => sum + numeric(counts?.[key]),
    0,
  )
}

function taskTickets(task: TrackerTask, tickets: TrackerTicket[]): TrackerTicket[] {
  return tickets.filter((ticket) => ticket.task_key === task.key)
}

function workItem(
  project: Project,
  task: TrackerTask,
  tickets: TrackerTicket[],
): PortfolioWorkItem {
  const children = taskTickets(task, tickets)
  return {
    key: task.key,
    title: task.title,
    projectId: project.id,
    projectName: project.name,
    owner: task.owner,
    priority: task.priority_key,
    activeTicketCount: children.length,
    blockerCount: children.filter((ticket) => ticket.blocked_by.length > 0).length,
    updatedAt: task.updated_at,
  }
}

async function readProjectInsight(project: Project): Promise<ProjectPortfolioInsight> {
  const status = await callOperation<TrackerStatusResponse>('tracker.status', {
    project_id: project.id,
    response_mode: 'raw',
  })
  const taskSummary = status.summary?.tasks
  const ticketSummary = status.summary?.tickets
  const totalTaskCount = numeric(taskSummary?.total) || countTotal(status.task_counts)
  const doneTaskCount = numeric(taskSummary?.done) || countDone(status.task_counts)
  const statusActiveTaskCount =
    numeric(taskSummary?.active) || numeric(status.task_counts?.['in-progress'])
  const inProgressTicketCount =
    numeric(status.in_progress_ticket_count) ||
    numeric(ticketSummary?.in_progress) ||
    numeric(status.ticket_counts?.['in-progress'])
  const blockedTicketCount = numeric(status.blocked_ticket_count) || numeric(ticketSummary?.blocked)
  const needsWorkDetails = statusActiveTaskCount > 0 || inProgressTicketCount > 0
  const snapshot = needsWorkDetails
    ? await callOperation<TrackerSnapshot>('tracker.get', {
        project_id: project.id,
        statuses: ['in-progress'],
        include_graph: false,
        response_mode: 'raw',
      })
    : { tasks: [], tickets: [] }
  const workItems = (snapshot.tasks ?? []).map((task) =>
    workItem(project, task, snapshot.tickets ?? []),
  )

  return {
    projectId: project.id,
    projectName: project.name,
    activeTaskCount:
      workItems.length ||
      statusActiveTaskCount,
    inProgressTicketCount,
    blockedTicketCount,
    totalTaskCount,
    doneTaskCount,
    completionPercent:
      totalTaskCount > 0 ? Math.round((doneTaskCount / totalTaskCount) * 100) : 0,
    workItems,
  }
}

async function mapWithConcurrency<T, R>(
  items: T[],
  concurrency: number,
  work: (item: T) => Promise<R>,
): Promise<PromiseSettledResult<R>[]> {
  const results: PromiseSettledResult<R>[] = new Array(items.length)
  let cursor = 0

  async function worker(): Promise<void> {
    while (cursor < items.length) {
      const index = cursor
      cursor += 1
      try {
        results[index] = { status: 'fulfilled', value: await work(items[index]) }
      } catch (reason) {
        results[index] = { status: 'rejected', reason }
      }
    }
  }

  await Promise.all(
    Array.from({ length: Math.min(concurrency, items.length) }, () => worker()),
  )
  return results
}

export function useHomePortfolioInsights() {
  const insights = ref<ProjectPortfolioInsight[]>([])
  const loading = ref(false)
  const failedProjectCount = ref(0)

  const insightByProjectId = computed<Record<number, ProjectPortfolioInsight>>(() =>
    Object.fromEntries(insights.value.map((insight) => [insight.projectId, insight])),
  )

  const activeWork = computed(() =>
    insights.value
      .flatMap((insight) => insight.workItems)
      .sort((a, b) => {
        const priority = a.priority.localeCompare(b.priority)
        if (priority !== 0) return priority
        return (b.updatedAt ?? '').localeCompare(a.updatedAt ?? '')
      }),
  )

  const totals = computed(() => ({
    activeProjects: insights.value.filter(
      (insight) => insight.activeTaskCount > 0 || insight.inProgressTicketCount > 0,
    ).length,
    activeTasks: insights.value.reduce((sum, insight) => sum + insight.activeTaskCount, 0),
    activeTickets: insights.value.reduce(
      (sum, insight) => sum + insight.inProgressTicketCount,
      0,
    ),
    blockers: insights.value.reduce((sum, insight) => sum + insight.blockedTicketCount, 0),
  }))

  async function load(projects: Project[]): Promise<void> {
    const activeProjects = projects.filter((project) => project.is_active)
    loading.value = true
    failedProjectCount.value = 0
    try {
      const results = await mapWithConcurrency(
        activeProjects,
        MAX_CONCURRENT_PROJECTS,
        readProjectInsight,
      )
      insights.value = results.flatMap((result) =>
        result.status === 'fulfilled' ? [result.value] : [],
      )
      failedProjectCount.value = results.filter((result) => result.status === 'rejected').length
    } finally {
      loading.value = false
    }
  }

  return {
    insights,
    insightByProjectId,
    activeWork,
    totals,
    loading,
    failedProjectCount,
    load,
  }
}
