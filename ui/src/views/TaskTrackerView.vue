<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { Edge, EdgeMouseEvent, NodeMouseEvent } from '@vue-flow/core'

import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import { UiBadge, UiButton, UiCallout, UiEmptyState, UiPageShell } from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import { callOperation } from '@/lib/operations'
import { graphFocusFor, graphItemFromNodeId } from '@/lib/task-tracker/graphFocus'
import { buildTrackerFlowModel, type TrackerVueNodeData } from '@/lib/task-tracker/graphModel'
import { formatDateTime } from '@/lib/stackos/json'
import type {
  TrackerSelectedItem,
  TrackerSnapshot,
  TrackerStatus,
  TrackerTask,
  TrackerTicket,
} from '@/lib/task-tracker/types'

import TaskTrackerCommandPanel from './task-tracker/TaskTrackerCommandPanel.vue'
import TrackerGraphPanel from './task-tracker/TrackerGraphPanel.vue'
import TrackerTaskDetailDialog from './task-tracker/TrackerTaskDetailDialog.vue'
import TrackerTicketDetailPanel from './task-tracker/TrackerTicketDetailPanel.vue'
import TrackerTicketTable from './task-tracker/TrackerTicketTable.vue'
import type {
  GraphBlockFilter,
  SelectMetaTone,
  StatusFilter,
  TaskProgressRow,
  ViewMode,
} from './task-tracker/viewTypes'

const route = useRoute()
const router = useRouter()
const projectId = computed(() => Number.parseInt(route.params.id as string, 10))

const snapshot = ref<TrackerSnapshot | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const viewMode = ref<ViewMode>('graph')
const statusFilter = ref<StatusFilter>('all')
const workflowFilter = ref('')
const assigneeFilter = ref('')
const search = ref('')
const filtersExpanded = ref(false)
const activeTaskKey = ref(routeTaskKey())
const selected = ref<TrackerSelectedItem | null>(null)
const selectedEdgeId = ref<string | null>(null)
const selectedNodeFocusId = ref<string | null>(null)
const detailPanelOpen = ref(false)
const taskDetailOpen = ref(false)
const graphStatusFilters = ref<TrackerStatus[]>([])
const graphBlockFilters = ref<GraphBlockFilter[]>([])

const statusOptions: Array<{ key: StatusFilter; label: string }> = [
  { key: 'all', label: 'All' },
  { key: 'not-started', label: 'Not started' },
  { key: 'in-progress', label: 'In progress' },
  { key: 'complete', label: 'Complete' },
  { key: 'deferred', label: 'Deferred' },
]

function trackerStatusTone(status: TrackerStatus): SelectMetaTone {
  switch (status) {
    case 'complete':
      return 'success'
    case 'in-progress':
      return 'info'
    case 'deferred':
      return 'warning'
    default:
      return 'neutral'
  }
}

const viewOptions: Array<{ key: ViewMode; label: string; icon: string }> = [
  { key: 'graph', label: 'Graph', icon: 'git-branch' },
  { key: 'tickets', label: 'Tickets', icon: 'list' },
]

const tracker = computed(() => snapshot.value?.tracker ?? null)
const tasks = computed(() => snapshot.value?.tasks ?? [])
const tickets = computed(() => snapshot.value?.tickets ?? [])

const ticketsByTask = computed(() => {
  const groups = new Map<string, TrackerTicket[]>()
  for (const ticket of tickets.value) {
    const group = groups.get(ticket.task_key) ?? []
    group.push(ticket)
    groups.set(ticket.task_key, group)
  }
  for (const group of groups.values()) {
    group.sort((a, b) => a.order_index - b.order_index || a.id - b.id)
  }
  return groups
})

const workflowOptions = computed(() => {
  const values = new Set<string>()
  for (const task of tasks.value) {
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
})

const assigneeOptions = computed(() => {
  const values = new Set<string>()
  for (const ticket of tickets.value) {
    if (ticket.assignee) values.add(ticket.assignee)
  }
  return [
    { value: '', label: 'All assignees' },
    ...Array.from(values)
      .sort()
      .map((value) => ({ value, label: value })),
  ]
})

const taskRows = computed<TaskProgressRow[]>(() =>
  tasks.value
    .map((task) => {
      const taskTickets = ticketsByTask.value.get(task.key) ?? []
      const completedCount = taskTickets.filter((ticket) => ticket.status === 'complete').length
      const deferredCount = taskTickets.filter((ticket) => ticket.status === 'deferred').length
      const doneCount = completedCount + deferredCount
      const inProgressCount = taskTickets.filter((ticket) => ticket.status === 'in-progress').length
      const blockedCount = taskTickets.filter(
        (ticket) => isOpenTicket(ticket) && (ticket.blocker_reason || ticket.blocked_by.length > 0),
      ).length
      const totalCount = taskTickets.length
      const percent = totalCount > 0 ? Math.round((doneCount / totalCount) * 100) : 0
      return {
        id: task.id,
        key: task.key,
        task,
        tickets: taskTickets,
        completedCount,
        deferredCount,
        doneCount,
        totalCount,
        inProgressCount,
        blockedCount,
        percent,
        workflowLabel: taskWorkflowLabel(task),
        currentDetail: taskCurrentDetail(task, taskTickets, inProgressCount, blockedCount),
      }
    })
    .filter(taskRowMatchesFilters)
    .sort((a, b) => {
      const createdDiff = Date.parse(b.task.created_at) - Date.parse(a.task.created_at)
      return createdDiff || b.id - a.id
    }),
)

const taskSelectOptions = computed(() =>
  taskRows.value.map((row) => ({
    value: row.key,
    label: row.task.title,
    rightLabel: row.task.status.replace(/-/g, ' '),
    rightMeta: `${row.doneCount}/${row.totalCount} tickets`,
    rightTone: trackerStatusTone(row.task.status),
  })),
)

const activeTaskRow = computed<TaskProgressRow | null>(() => {
  if (!taskRows.value.length) return null
  return taskRows.value.find((row) => row.key === activeTaskKey.value) ?? taskRows.value[0]
})

const graphTickets = computed(() => activeTaskRow.value?.tickets ?? [])

const graphStatusCounts = computed(() =>
  countStatuses(graphTickets.value.map((ticket) => ticket.status)),
)

const graphStatusRows = computed(() =>
  statusOptions
    .filter((option): option is { key: TrackerStatus; label: string } => option.key !== 'all')
    .map((option) => ({
      key: option.key,
      label: option.label.toLowerCase(),
      count: graphStatusCounts.value[option.key],
    })),
)

const graphBlockedCount = computed(
  () => graphTickets.value.filter((ticket) => isGraphBlockedTicket(ticket)).length,
)

const graphBlockRows = computed<Array<{ key: GraphBlockFilter; label: string; count: number }>>(
  () => [
    { key: 'blocked', label: 'blocked', count: graphBlockedCount.value },
    {
      key: 'open',
      label: 'open',
      count: Math.max(0, graphTickets.value.length - graphBlockedCount.value),
    },
  ],
)

const graphFiltersActive = computed(
  () => graphStatusFilters.value.length > 0 || graphBlockFilters.value.length > 0,
)

const graphTicketStatLabel = computed(() =>
  graphFiltersActive.value
    ? `${graphVisibleTickets.value.length}/${graphTickets.value.length} tickets visible`
    : `${graphTickets.value.length} tickets`,
)

const graphEdgeStatLabel = computed(() =>
  graphFiltersActive.value
    ? `${flow.value.edges.length} visible ${pluralize('relation', flow.value.edges.length)}`
    : `${flow.value.edges.length} ${pluralize('relation', flow.value.edges.length)}`,
)

const graphMatchedTickets = computed(() => graphTickets.value.filter(graphTicketMatchesFilters))

const graphVisibleTickets = computed(() => {
  if (!graphFiltersActive.value) return graphTickets.value
  const visibleKeys = new Set(graphMatchedTickets.value.map((ticket) => ticket.key))
  if (graphBlockFilters.value.includes('blocked')) {
    for (const ticket of graphMatchedTickets.value) {
      for (const blockerKey of ticket.blocked_by) {
        visibleKeys.add(blockerKey)
      }
    }
  }
  return graphTickets.value.filter((ticket) => visibleKeys.has(ticket.key))
})

const visibleTickets = computed(() => {
  const row = activeTaskRow.value
  if (!row) return []
  return row.tickets.filter((ticket) => ticketMatchesControls(ticket, row.task))
})

const filteredTicketCount = computed(() =>
  taskRows.value.reduce(
    (sum, row) =>
      sum + row.tickets.filter((ticket) => ticketMatchesControls(ticket, row.task)).length,
    0,
  ),
)

const selectedTicket = computed(() =>
  selected.value?.kind === 'ticket'
    ? (graphTickets.value.find((ticket) => ticket.key === selected.value?.key) ?? null)
    : null,
)

const activeTask = computed(() => activeTaskRow.value?.task ?? null)

const detailPanelTitle = computed(() => {
  if (selectedTicket.value) return selectedTicket.value.title
  return 'Work detail'
})

const detailPanelDescription = computed(() => {
  if (selectedTicket.value) return selectedTicket.value.key
  return undefined
})

const blockedCount = computed(
  () =>
    tickets.value.filter(
      (ticket) => isOpenTicket(ticket) && (ticket.blocked_by.length > 0 || ticket.blocker_reason),
    ).length,
)
const workflowCount = computed(
  () => new Set(tasks.value.map((task) => task.source_json?.run_plan_id).filter(Boolean)).size,
)

const commandFilterCount = computed(() => {
  let count = 0
  if (search.value.trim()) count += 1
  if (statusFilter.value !== 'all') count += 1
  if (workflowFilter.value) count += 1
  if (assigneeFilter.value) count += 1
  return count
})

const commandFiltersActive = computed(() => commandFilterCount.value > 0)
const commandFilterLabel = computed(() =>
  commandFilterCount.value ? `Filters (${commandFilterCount.value})` : 'Filters',
)

const filteredSnapshot = computed<TrackerSnapshot | null>(() => {
  if (!snapshot.value || !activeTaskRow.value) return null
  const activeTask = activeTaskRow.value.task
  const activeTickets = graphVisibleTickets.value
  const visibleTicketIds = new Set(activeTickets.map((ticket) => ticket.id))
  const visibleTaskIds = new Set([activeTask.id])
  const graphNodeIds = new Set(activeTickets.map((ticket) => `ticket:${ticket.key}`))
  const graph = snapshot.value.graph
    ? {
        ...snapshot.value.graph,
        nodes: snapshot.value.graph.nodes.filter((node) => graphNodeIds.has(node.id)),
        edges: snapshot.value.graph.edges.filter(
          (edge) =>
            edge.type === 'dependency' &&
            graphNodeIds.has(edge.source) &&
            graphNodeIds.has(edge.target),
        ),
      }
    : null
  return {
    ...snapshot.value,
    tasks: [activeTask],
    tickets: activeTickets,
    dependencies: snapshot.value.dependencies.filter(
      (dependency) =>
        activeTickets.some((ticket) => ticket.key === dependency.ticket_key) &&
        activeTickets.some((ticket) => ticket.key === dependency.depends_on_ticket_key),
    ),
    links: snapshot.value.links.filter(
      (link) =>
        (link.ticket_id !== null && visibleTicketIds.has(link.ticket_id)) ||
        (link.task_id !== null && visibleTaskIds.has(link.task_id)),
    ),
    graph,
  }
})

const relationFocus = computed(() =>
  graphFocusFor(filteredSnapshot.value?.graph ?? null, {
    edgeId: selectedEdgeId.value,
    nodeId: selectedNodeFocusId.value,
  }),
)

const relationFocusActive = computed(
  () => relationFocus.value.edgeIds.size > 0 || relationFocus.value.nodeIds.size > 1,
)

const relationFocusLabel = computed(() => {
  if (selectedEdgeId.value) return 'selected relation'
  if (selectedNodeFocusId.value) return 'related dependencies'
  return ''
})

const selectedGraphEdge = computed(
  () =>
    filteredSnapshot.value?.graph?.edges.find((edge) => edge.id === selectedEdgeId.value) ?? null,
)

const graphSelectionLabel = computed(() => {
  if (selectedEdgeId.value) return 'selected relation'
  if (selectedNodeFocusId.value) return 'selected ticket'
  if (selectedTicket.value) return 'selected ticket'
  return ''
})

const graphSelectionVisible = computed(
  () => Boolean(selectedTicket.value) || Boolean(relationFocusLabel.value),
)

const graphSelectionStats = computed(() => {
  const stats = []
  if (relationFocus.value.upstreamNodeIds.size) {
    stats.push(`${relationFocus.value.upstreamNodeIds.size} dependencies`)
  }
  if (relationFocus.value.downstreamNodeIds.size) {
    stats.push(`${relationFocus.value.downstreamNodeIds.size} unblocked next`)
  }
  return stats
})

const flowRenderKey = computed(() =>
  [
    activeTaskRow.value?.key ?? 'empty',
    graphStatusFilters.value.join(',') || 'all-status',
    graphBlockFilters.value.join(',') || 'all-block',
  ].join(':'),
)

const flow = computed(() =>
  filteredSnapshot.value
    ? buildTrackerFlowModel(filteredSnapshot.value, {
        selected: selected.value,
        highlightedNodeIds: relationFocus.value.nodeIds,
        highlightedEdgeIds: relationFocus.value.edgeIds,
        selectedNodeId: relationFocus.value.selectedNodeId,
        upstreamNodeIds: relationFocus.value.upstreamNodeIds,
        upstreamEdgeIds: relationFocus.value.upstreamEdgeIds,
        downstreamNodeIds: relationFocus.value.downstreamNodeIds,
        downstreamEdgeIds: relationFocus.value.downstreamEdgeIds,
        activeEdgeId: relationFocus.value.activeEdgeId,
        spotlight: relationFocusActive.value,
      })
    : { nodes: [], edges: [] as Edge[], warnings: [] },
)

const graphFitOnInit = computed(() => flow.value.nodes.length <= 40)

const ticketColumns: DataTableColumn<TrackerTicket>[] = [
  { key: 'key', label: 'Ticket' },
  { key: 'task_key', label: 'Task' },
  { key: 'status', label: 'Status', widthClass: 'w-32' },
  { key: 'priority_key', label: 'Priority', widthClass: 'w-24' },
  {
    key: 'assignee',
    label: 'Assignee',
    widthClass: 'w-32',
    format: (value) => String(value ?? '-'),
  },
  {
    key: 'updated_at',
    label: 'Updated',
    widthClass: 'w-40',
    format: (value) => formatDateTime(String(value)),
  },
]

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  loading.value = true
  error.value = null
  try {
    snapshot.value = await callOperation<TrackerSnapshot>('tracker.get', {
      project_id: projectId.value,
      include_graph: true,
    })
    await nextTick()
    ensureActiveTask()
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'failed to load tracker'
  } finally {
    loading.value = false
  }
}

function ticketMatchesControls(ticket: TrackerTicket, task: TrackerTask): boolean {
  const q = search.value.trim().toLowerCase()
  if (statusFilter.value !== 'all' && ticket.status !== statusFilter.value) return false
  if (assigneeFilter.value && ticket.assignee !== assigneeFilter.value) return false
  if (workflowFilter.value) {
    const source = task.source_json ?? {}
    if (
      source.template_key !== workflowFilter.value &&
      source.run_plan_key !== workflowFilter.value
    ) {
      return false
    }
  }
  if (!q) return true
  return [ticket.key, ticket.title, ticket.goal, ticket.outcome ?? '', ticket.assignee ?? '']
    .join(' ')
    .toLowerCase()
    .includes(q)
}

function taskRowMatchesFilters(row: TaskProgressRow): boolean {
  const task = row.task
  const taskTickets = row.tickets
  const q = search.value.trim().toLowerCase()
  if (statusFilter.value !== 'all') {
    const taskMatches = task.status === statusFilter.value
    const ticketMatches = taskTickets.some((ticket) => ticket.status === statusFilter.value)
    if (!taskMatches && !ticketMatches) return false
  }
  if (
    assigneeFilter.value &&
    !taskTickets.some((ticket) => ticket.assignee === assigneeFilter.value)
  ) {
    return false
  }
  if (workflowFilter.value) {
    const source = task.source_json ?? {}
    if (
      source.template_key !== workflowFilter.value &&
      source.run_plan_key !== workflowFilter.value
    ) {
      return false
    }
  }
  if (!q) return true
  return [
    task.key,
    task.title,
    task.goal,
    task.description,
    task.owner ?? '',
    row.workflowLabel,
    ...taskTickets.flatMap((ticket) => [
      ticket.key,
      ticket.title,
      ticket.goal,
      ticket.outcome ?? '',
      ticket.assignee ?? '',
    ]),
  ]
    .join(' ')
    .toLowerCase()
    .includes(q)
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
  taskTickets: TrackerTicket[],
  inProgressCount: number,
  blockedCount: number,
): string {
  const completedCount = taskTickets.filter((ticket) => ticket.status === 'complete').length
  const deferredCount = taskTickets.filter((ticket) => ticket.status === 'deferred').length
  if (blockedCount > 0) return `${blockedCount} blocked`
  if (inProgressCount > 0) return `${inProgressCount} in progress`
  if (deferredCount > 0) return `${completedCount} complete, ${deferredCount} deferred`
  if (task.status === 'complete') return 'complete'
  if (!taskTickets.length) return task.status
  return `${taskTickets.length} tickets`
}

function isOpenTicket(ticket: TrackerTicket): boolean {
  return ticket.status !== 'complete' && ticket.status !== 'deferred'
}

function isGraphBlockedTicket(ticket: TrackerTicket): boolean {
  return ticket.blocked_by.length > 0 || (isOpenTicket(ticket) && Boolean(ticket.blocker_reason))
}

function graphTicketMatchesFilters(ticket: TrackerTicket): boolean {
  const statusOk =
    graphStatusFilters.value.length === 0 || graphStatusFilters.value.includes(ticket.status)
  const blockKind: GraphBlockFilter = isGraphBlockedTicket(ticket) ? 'blocked' : 'open'
  const blockOk =
    graphBlockFilters.value.length === 0 || graphBlockFilters.value.includes(blockKind)
  return statusOk && blockOk
}

function toggleGraphStatus(status: TrackerStatus): void {
  graphStatusFilters.value = graphStatusFilters.value.includes(status)
    ? graphStatusFilters.value.filter((item) => item !== status)
    : [...graphStatusFilters.value, status]
  clearGraphFocus()
}

function toggleGraphBlock(block: GraphBlockFilter): void {
  graphBlockFilters.value = graphBlockFilters.value.includes(block)
    ? graphBlockFilters.value.filter((item) => item !== block)
    : [...graphBlockFilters.value, block]
  clearGraphFocus()
}

function clearGraphFilters(): void {
  graphStatusFilters.value = []
  graphBlockFilters.value = []
  clearGraphFocus()
}

function onTaskRow(row: TaskProgressRow): void {
  selectedEdgeId.value = null
  selectedNodeFocusId.value = null
  detailPanelOpen.value = false
  activeTaskKey.value = row.key
  syncActiveTaskToUrl(row.key)
  selected.value = null
}

function onTaskSelect(value: string | number | null): void {
  const taskKey = typeof value === 'string' ? value : String(value ?? '')
  const row = taskRows.value.find((candidate) => candidate.key === taskKey)
  if (row) onTaskRow(row)
}

function onNodeClick(event: NodeMouseEvent): void {
  event.event.stopPropagation()
  selectedEdgeId.value = null
  detailPanelOpen.value = false
  const data = event.node.data as TrackerVueNodeData
  if (!data?.itemKind || data.itemKey.startsWith('link:')) return
  selectedNodeFocusId.value = event.node.id
  selected.value = { kind: data.itemKind, key: data.itemKey }
}

function onEdgeClick(event: EdgeMouseEvent): void {
  event.event.stopPropagation()
  selectedEdgeId.value = event.edge.id
  selectedNodeFocusId.value = null
  detailPanelOpen.value = false
  selected.value = null
  const target = graphItemFromNodeId(event.edge.target)
  if (target?.kind === 'ticket') {
    selected.value = { kind: 'ticket', key: target.key }
  }
}

function onPaneClick(event: MouseEvent): void {
  clearGraphFocusFromCanvasClick(event)
}

function onGraphCanvasClick(event: MouseEvent): void {
  clearGraphFocusFromCanvasClick(event)
}

function clearGraphFocusFromCanvasClick(event: MouseEvent): void {
  const target = event.target instanceof Element ? event.target : null
  if (
    target?.closest(
      [
        '.vue-flow__node',
        '.vue-flow__edge',
        '.vue-flow__controls',
        '.vue-flow__minimap',
        '.tracker-graph-selection__actions',
      ].join(', '),
    )
  ) {
    return
  }
  clearGraphFocus()
}

function onTicketRow(row: TrackerTicket): void {
  selectedEdgeId.value = null
  selectedNodeFocusId.value = null
  activeTaskKey.value = row.task_key
  syncActiveTaskToUrl(row.task_key)
  selected.value = { kind: 'ticket', key: row.key }
  detailPanelOpen.value = true
}

function openSelectedDetail(): void {
  if (!selectedTicket.value) return
  detailPanelOpen.value = true
}

function clearGraphFocus(): void {
  selectedEdgeId.value = null
  selectedNodeFocusId.value = null
  selected.value = null
  detailPanelOpen.value = false
}

function ensureActiveTask(): void {
  if (!taskRows.value.length) {
    activeTaskKey.value = ''
    syncActiveTaskToUrl('')
    selected.value = null
    selectedEdgeId.value = null
    selectedNodeFocusId.value = null
    return
  }
  const current = taskRows.value.find((row) => row.key === activeTaskKey.value)
  const nextRow = current ?? taskRows.value[0]
  activeTaskKey.value = nextRow.key
  syncActiveTaskToUrl(nextRow.key)
  if (
    selected.value?.kind === 'ticket' &&
    !nextRow.tickets.some((ticket) => ticket.key === selected.value?.key)
  ) {
    selectedEdgeId.value = null
    selectedNodeFocusId.value = null
    selected.value = null
    return
  }
}

function clearFilters(): void {
  statusFilter.value = 'all'
  workflowFilter.value = ''
  assigneeFilter.value = ''
  search.value = ''
  clearGraphFilters()
}

function routeTaskKey(): string {
  const raw = route.query.task
  if (Array.isArray(raw)) return typeof raw[0] === 'string' ? raw[0] : ''
  return typeof raw === 'string' ? raw : ''
}

function syncActiveTaskToUrl(taskKey: string): void {
  if (routeTaskKey() === taskKey) return
  const nextQuery = { ...route.query }
  if (taskKey) {
    nextQuery.task = taskKey
  } else {
    delete nextQuery.task
  }
  void router.replace({ query: nextQuery })
}

function pluralize(label: string, count: number): string {
  return count === 1 ? label : `${label}s`
}

function countStatuses(statuses: TrackerStatus[]): Record<TrackerStatus, number> {
  return statuses.reduce(
    (acc, status) => {
      acc[status] += 1
      return acc
    },
    { 'not-started': 0, 'in-progress': 0, complete: 0, deferred: 0 },
  )
}

onMounted(load)
watch(projectId, load)
watch(
  () => route.query.task,
  () => {
    activeTaskKey.value = routeTaskKey()
    selectedEdgeId.value = null
    selectedNodeFocusId.value = null
    ensureActiveTask()
  },
)
watch([statusFilter, workflowFilter, assigneeFilter, search], () => ensureActiveTask())
</script>

<template>
  <UiPageShell class="tracker-page-shell">
    <ProjectPageHeader
      :project-id="projectId"
      title="Tasks"
      description="Project work tracker for workflow runs, manual agent tasks, dependencies, and verification state."
      :breadcrumbs="[{ label: 'Tasks' }]"
    >
      <template #actions>
        <UiButton variant="secondary" :disabled="loading" @click="load"> Refresh </UiButton>
      </template>
      <template #titleMeta>
        <UiBadge v-if="tracker" tone="neutral" variant="outline"> rev {{ tracker.rev }} </UiBadge>
      </template>
    </ProjectPageHeader>

    <UiCallout v-if="error" tone="danger">
      {{ error }}
    </UiCallout>

    <TaskTrackerCommandPanel
      :active-task-key="activeTaskRow?.key ?? ''"
      :task-options="taskSelectOptions"
      :view-mode="viewMode"
      :view-options="viewOptions"
      :filters-expanded="filtersExpanded"
      :filter-label="commandFilterLabel"
      :filters-active="commandFiltersActive"
      :search="search"
      :status-filter="statusFilter"
      :status-options="statusOptions"
      :workflow-filter="workflowFilter"
      :workflow-options="workflowOptions"
      :assignee-filter="assigneeFilter"
      :assignee-options="assigneeOptions"
      :task-rows-count="taskRows.length"
      :tasks-count="tasks.length"
      :filtered-ticket-count="filteredTicketCount"
      :tickets-count="tickets.length"
      :active-done-count="activeTaskRow?.doneCount ?? null"
      :active-total-count="activeTaskRow?.totalCount ?? null"
      :blocked-count="blockedCount"
      :workflow-count="workflowCount"
      @task-select="onTaskSelect"
      @update:view-mode="viewMode = $event"
      @update:filters-expanded="filtersExpanded = $event"
      @update:search="search = $event"
      @update:status-filter="statusFilter = $event"
      @update:workflow-filter="workflowFilter = $event"
      @update:assignee-filter="assigneeFilter = $event"
      @clear="clearFilters"
    />

    <UiCallout v-for="warning in flow.warnings" :key="warning" tone="warning">
      {{ warning }}
    </UiCallout>

    <div v-if="!loading && taskRows.length === 0" class="min-h-[360px]">
      <UiEmptyState
        title="No tracker work"
        description="Agents can create tasks and tickets through tracker operations."
      />
    </div>

    <div v-else class="tracker-workspace">
      <div class="tracker-focus">
        <div class="tracker-main">
          <TrackerGraphPanel
            v-if="viewMode === 'graph'"
            :flow="flow"
            :flow-render-key="flowRenderKey"
            :graph-fit-on-init="graphFitOnInit"
            :active-task-title="activeTaskRow?.task.title ?? 'Task graph'"
            :active-task-available="Boolean(activeTask)"
            :ticket-stat-label="graphTicketStatLabel"
            :edge-stat-label="graphEdgeStatLabel"
            :status-rows="graphStatusRows"
            :status-filters="graphStatusFilters"
            :block-rows="graphBlockRows"
            :block-filters="graphBlockFilters"
            :filters-active="graphFiltersActive"
            :selection-visible="graphSelectionVisible"
            :selection-label="graphSelectionLabel"
            :selected-ticket="selectedTicket"
            :selected-edge-label="selectedGraphEdge?.label ?? null"
            :selection-stats="graphSelectionStats"
            @toggle-status="toggleGraphStatus"
            @toggle-block="toggleGraphBlock"
            @clear-filters="clearGraphFilters"
            @open-task-detail="taskDetailOpen = true"
            @node-click="onNodeClick"
            @edge-click="onEdgeClick"
            @pane-click="onPaneClick"
            @graph-canvas-click="onGraphCanvasClick"
            @open-selected-detail="openSelectedDetail"
            @clear-graph-focus="clearGraphFocus"
          />

          <TrackerTicketTable
            v-else
            :tickets="visibleTickets"
            :columns="ticketColumns"
            :loading="loading"
            :selected-ticket-id="selectedTicket?.id ?? null"
            @row-click="onTicketRow"
          />
        </div>
      </div>
    </div>

    <TrackerTicketDetailPanel
      v-model="detailPanelOpen"
      :ticket="selectedTicket"
      :title="detailPanelTitle"
      :description="detailPanelDescription"
    />

    <TrackerTaskDetailDialog v-model="taskDetailOpen" :task="activeTask" />
  </UiPageShell>
</template>

<style scoped>
.tracker-page-shell {
  display: flex;
  min-height: calc(100vh - 40px);
  flex-direction: column;
}

.tracker-workspace {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
}

.tracker-focus {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.tracker-main {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
}

:deep(.tracker-detail__body) {
  display: grid;
  gap: 18px;
}

:deep(.tracker-detail__body--drawer) {
  padding: 0;
}

:deep(.tracker-detail__drawer-kicker) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

:deep(.tracker-detail__eyebrow) {
  color: var(--color-fg-muted);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
}

:deep(.tracker-detail__description) {
  max-width: 82ch;
  color: var(--color-fg-muted);
  font-size: 14px;
  line-height: 1.55;
}

:deep(.tracker-detail__facts) {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
}

:deep(.tracker-detail-fact) {
  display: grid;
  gap: 5px;
  min-width: 0;
  border: 1px solid var(--color-border-subtle);
  border-radius: 6px;
  background: var(--color-bg-surface-alt);
  padding: 10px 12px;
}

:deep(.tracker-detail-fact span) {
  color: var(--color-fg-muted);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}

:deep(.tracker-detail-fact strong) {
  overflow: hidden;
  color: var(--color-fg-default);
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.tracker-detail-section) {
  display: grid;
  gap: 8px;
  border-top: 1px solid var(--color-border-subtle);
  padding-top: 16px;
}

:deep(.tracker-detail-section__title) {
  color: var(--color-fg-muted);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}

:deep(.tracker-detail-list) {
  display: grid;
  gap: 6px;
  color: var(--color-fg-muted);
  font-size: 13px;
  line-height: 1.45;
}

:deep(.tracker-detail-list--mono) {
  font-family: var(--font-mono);
  font-size: 12px;
}

:deep(.tracker-detail__outcome) {
  border: 1px solid var(--color-border-subtle);
  border-radius: 6px;
  background: var(--color-bg-surface-alt);
  color: var(--color-fg-muted);
  font-size: 13px;
  line-height: 1.5;
  padding: 12px 14px;
}

:deep(.tracker-detail-json) {
  max-height: 260px;
  overflow: auto;
  border: 1px solid var(--color-border-subtle);
  border-radius: 6px;
  background: var(--color-bg-sunken);
  color: var(--color-fg-default);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.55;
  padding: 12px;
  white-space: pre-wrap;
}
</style>
