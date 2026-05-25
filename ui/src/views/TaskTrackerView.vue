<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import { VueFlow } from '@vue-flow/core'
import type { Edge, EdgeMouseEvent, NodeMouseEvent } from '@vue-flow/core'

import DataTable from '@/components/DataTable.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiCheckbox,
  UiEmptyState,
  UiFormField,
  UiInput,
  UiPageShell,
  UiPanel,
  UiProgressBar,
  UiSegmentedControl,
  UiSelect,
  UiSidePanel,
} from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import { callOperation } from '@/lib/operations'
import { buildTrackerFlowModel, type TrackerVueNodeData } from '@/lib/task-tracker/graphModel'
import { formatDateTime } from '@/lib/stackos/json'
import type {
  TrackerSelectedItem,
  TrackerSnapshot,
  TrackerStatus,
  TrackerTask,
  TrackerTicket,
} from '@/lib/task-tracker/types'

import TaskGraphNode from './task-tracker/TaskGraphNode.vue'
import TicketGraphNode from './task-tracker/TicketGraphNode.vue'
import TrackerStatusBadge from './task-tracker/TrackerStatusBadge.vue'

import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'

type ViewMode = 'graph' | 'tickets'
type StatusFilter = 'all' | TrackerStatus
type GraphBlockFilter = 'blocked' | 'open'
type TrackerGraphShape = NonNullable<TrackerSnapshot['graph']>

interface GraphFocus {
  nodeIds: Set<string>
  edgeIds: Set<string>
  activeEdgeId: string | null
}

interface TaskProgressRow {
  id: number
  key: string
  task: TrackerTask
  tickets: TrackerTicket[]
  completedCount: number
  deferredCount: number
  doneCount: number
  totalCount: number
  inProgressCount: number
  blockedCount: number
  percent: number
  workflowLabel: string
  currentDetail: string
}

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
const showContainment = ref(false)
const activeTaskKey = ref(routeTaskKey())
const selected = ref<TrackerSelectedItem | null>(null)
const selectedEdgeId = ref<string | null>(null)
const selectedNodeFocusId = ref<string | null>(null)
const detailPanelOpen = ref(false)
const graphStatusFilters = ref<TrackerStatus[]>([])
const graphBlockFilters = ref<GraphBlockFilter[]>([])

const statusOptions: Array<{ key: StatusFilter; label: string }> = [
  { key: 'all', label: 'All' },
  { key: 'not-started', label: 'Not started' },
  { key: 'in-progress', label: 'In progress' },
  { key: 'complete', label: 'Complete' },
  { key: 'deferred', label: 'Deferred' },
]

const viewOptions: Array<{ key: ViewMode; label: string }> = [
  { key: 'graph', label: 'Graph' },
  { key: 'tickets', label: 'Tickets' },
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
    label: `${row.task.title} · ${row.doneCount}/${row.totalCount} · ${row.task.status}`,
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

const selectedTask = computed(() =>
  selected.value?.kind === 'task' || selectedTicket.value
    ? (activeTaskRow.value?.task ?? null)
    : null,
)

const detailPanelTitle = computed(() => {
  if (selectedTicket.value) return selectedTicket.value.title
  if (selectedTask.value) return selectedTask.value.title
  return 'Work detail'
})

const detailPanelDescription = computed(() => {
  if (selectedTicket.value) return selectedTicket.value.key
  if (selectedTask.value) return selectedTask.value.key
  return undefined
})

const ticketCounts = computed(() => countStatuses(tickets.value.map((ticket) => ticket.status)))
const blockedCount = computed(
  () =>
    tickets.value.filter(
      (ticket) => isOpenTicket(ticket) && (ticket.blocked_by.length > 0 || ticket.blocker_reason),
    ).length,
)
const workflowCount = computed(
  () => new Set(tasks.value.map((task) => task.source_json?.run_plan_id).filter(Boolean)).size,
)

const filteredSnapshot = computed<TrackerSnapshot | null>(() => {
  if (!snapshot.value || !activeTaskRow.value) return null
  const activeTask = activeTaskRow.value.task
  const activeTickets = graphVisibleTickets.value
  const visibleTicketIds = new Set(activeTickets.map((ticket) => ticket.id))
  const visibleTaskIds = new Set([activeTask.id])
  const visibleGraphNodeIds = new Set([
    `task:${activeTask.key}`,
    ...activeTickets.map((ticket) => `ticket:${ticket.key}`),
  ])
  const visibleLinkNodeIds = new Set(
    snapshot.value.graph?.nodes
      .filter((node) => node.id.startsWith('link:'))
      .filter((node) =>
        snapshot.value?.graph?.edges.some(
          (edge) => edge.source === node.id && visibleGraphNodeIds.has(edge.target),
        ),
      )
      .map((node) => node.id) ?? [],
  )
  const graphNodeIds = new Set([...visibleGraphNodeIds, ...visibleLinkNodeIds])
  const graph = snapshot.value.graph
    ? {
        ...snapshot.value.graph,
        nodes: snapshot.value.graph.nodes.filter((node) => graphNodeIds.has(node.id)),
        edges: snapshot.value.graph.edges.filter(
          (edge) => graphNodeIds.has(edge.source) && graphNodeIds.has(edge.target),
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

const flowSnapshot = computed<TrackerSnapshot | null>(() => {
  const base = filteredSnapshot.value
  const activeRow = activeTaskRow.value
  if (!base || !activeRow || !relationFocusActive.value) return base
  return focusedSnapshotFor(base, relationFocus.value, activeRow)
})

const relationFocusLabel = computed(() => {
  if (selectedEdgeId.value) return 'selected relation'
  if (selectedNodeFocusId.value) return 'related dependencies'
  return ''
})

const flowRenderKey = computed(() =>
  [
    activeTaskRow.value?.key ?? 'empty',
    showContainment.value ? 'nested' : 'deps',
    graphStatusFilters.value.join(',') || 'all-status',
    graphBlockFilters.value.join(',') || 'all-block',
    selectedNodeFocusId.value ?? 'no-node',
    selectedEdgeId.value ?? 'no-edge',
  ].join(':'),
)

const flow = computed(() =>
  flowSnapshot.value
    ? buildTrackerFlowModel(flowSnapshot.value, {
        selected: selected.value,
        showContainment: showContainment.value,
        highlightedNodeIds: relationFocus.value.nodeIds,
        highlightedEdgeIds: relationFocus.value.edgeIds,
        activeEdgeId: relationFocus.value.activeEdgeId,
        spotlight: relationFocusActive.value,
      })
    : { nodes: [], edges: [] as Edge[], warnings: [] },
)

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
  selectedEdgeId.value = null
  selectedNodeFocusId.value = null
  graphStatusFilters.value = graphStatusFilters.value.includes(status)
    ? graphStatusFilters.value.filter((item) => item !== status)
    : [...graphStatusFilters.value, status]
}

function toggleGraphBlock(block: GraphBlockFilter): void {
  selectedEdgeId.value = null
  selectedNodeFocusId.value = null
  graphBlockFilters.value = graphBlockFilters.value.includes(block)
    ? graphBlockFilters.value.filter((item) => item !== block)
    : [...graphBlockFilters.value, block]
}

function clearGraphFilters(): void {
  selectedEdgeId.value = null
  selectedNodeFocusId.value = null
  graphStatusFilters.value = []
  graphBlockFilters.value = []
}

function onTaskRow(row: TaskProgressRow): void {
  selectedEdgeId.value = null
  selectedNodeFocusId.value = null
  detailPanelOpen.value = false
  activeTaskKey.value = row.key
  syncActiveTaskToUrl(row.key)
  selected.value = row.tickets[0]
    ? { kind: 'ticket', key: row.tickets[0].key }
    : { kind: 'task', key: row.task.key }
}

function onTaskSelect(value: string | number | null): void {
  const taskKey = typeof value === 'string' ? value : String(value ?? '')
  const row = taskRows.value.find((candidate) => candidate.key === taskKey)
  if (row) onTaskRow(row)
}

function onNodeClick(event: NodeMouseEvent): void {
  selectedEdgeId.value = null
  const data = event.node.data as TrackerVueNodeData
  if (!data?.itemKind || data.itemKey.startsWith('link:')) return
  selectedNodeFocusId.value = event.node.id
  selected.value = { kind: data.itemKind, key: data.itemKey }
  detailPanelOpen.value = true
}

function onEdgeClick(event: EdgeMouseEvent): void {
  selectedEdgeId.value = event.edge.id
  selectedNodeFocusId.value = null
  const target = graphItemFromNodeId(event.edge.target)
  if (target?.kind === 'ticket') {
    selected.value = { kind: 'ticket', key: target.key }
    detailPanelOpen.value = true
  }
}

function onTicketRow(row: TrackerTicket): void {
  selectedEdgeId.value = null
  selectedNodeFocusId.value = null
  activeTaskKey.value = row.task_key
  syncActiveTaskToUrl(row.task_key)
  selected.value = { kind: 'ticket', key: row.key }
  detailPanelOpen.value = true
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
    selected.value = nextRow.tickets[0]
      ? { kind: 'ticket', key: nextRow.tickets[0].key }
      : { kind: 'task', key: nextRow.task.key }
    return
  }
  if (selected.value?.kind === 'task' && selected.value.key !== nextRow.key) {
    selectedEdgeId.value = null
    selectedNodeFocusId.value = null
    selected.value = { kind: 'task', key: nextRow.key }
    return
  }
  if (!selected.value) {
    selected.value = nextRow.tickets[0]
      ? { kind: 'ticket', key: nextRow.tickets[0].key }
      : { kind: 'task', key: nextRow.task.key }
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

function graphFocusFor(
  graph: TrackerSnapshot['graph'],
  target: { edgeId: string | null; nodeId: string | null },
): GraphFocus {
  if (!graph) return emptyGraphFocus()
  if (target.edgeId) return edgeFocusFor(graph, target.edgeId)
  if (target.nodeId) return nodeFocusFor(graph, target.nodeId)
  return emptyGraphFocus()
}

function emptyGraphFocus(activeEdgeId: string | null = null): GraphFocus {
  return { nodeIds: new Set(), edgeIds: new Set(), activeEdgeId }
}

function edgeFocusFor(graph: TrackerGraphShape, edgeId: string): GraphFocus {
  const focus = emptyGraphFocus(edgeId)

  const selectedEdge = graph.edges.find((edge) => edge.id === edgeId)
  if (!selectedEdge) return emptyGraphFocus()

  if (selectedEdge.type === 'dependency') {
    addDependencyEdgeChain(graph, focus, selectedEdge)
  } else {
    addGraphEdge(focus, selectedEdge)
  }
  return focus
}

function nodeFocusFor(graph: TrackerGraphShape, nodeId: string): GraphFocus {
  const focus = emptyGraphFocus()
  const graphNode = graph.nodes.find((node) => node.id === nodeId)
  if (!graphNode || nodeId.startsWith('link:')) return focus

  focus.nodeIds.add(nodeId)

  if (graphNode.type === 'task') {
    const childNodeIds = new Set(
      graph.nodes.filter((node) => node.parent_id === nodeId).map((node) => node.id),
    )
    for (const edge of graph.edges) {
      const insideTask =
        edge.source === nodeId ||
        edge.target === nodeId ||
        (childNodeIds.has(edge.source) && childNodeIds.has(edge.target))
      if (insideTask) addGraphEdge(focus, edge)
    }
    return focus
  }

  const { incoming, outgoing } = dependencyMaps(graph)

  function collectUpstream(currentNodeId: string): void {
    for (const edge of incoming.get(currentNodeId) ?? []) {
      if (focus.edgeIds.has(edge.id)) continue
      addGraphEdge(focus, edge)
      collectUpstream(edge.source)
    }
  }

  function collectDownstream(currentNodeId: string): void {
    for (const edge of outgoing.get(currentNodeId) ?? []) {
      if (focus.edgeIds.has(edge.id)) continue
      addGraphEdge(focus, edge)
      collectDownstream(edge.target)
    }
  }

  collectUpstream(nodeId)
  collectDownstream(nodeId)

  for (const edge of graph.edges) {
    if (edge.type !== 'dependency' && (edge.source === nodeId || edge.target === nodeId)) {
      addGraphEdge(focus, edge)
    }
  }
  return focus
}

function addDependencyEdgeChain(
  graph: TrackerGraphShape,
  focus: GraphFocus,
  selectedEdge: TrackerGraphShape['edges'][number],
): void {
  const { incoming, outgoing } = dependencyMaps(graph)

  function collectUpstream(nodeId: string): void {
    for (const edge of incoming.get(nodeId) ?? []) {
      if (focus.edgeIds.has(edge.id)) continue
      addGraphEdge(focus, edge)
      collectUpstream(edge.source)
    }
  }

  function collectDownstream(nodeId: string): void {
    for (const edge of outgoing.get(nodeId) ?? []) {
      if (focus.edgeIds.has(edge.id)) continue
      addGraphEdge(focus, edge)
      collectDownstream(edge.target)
    }
  }

  addGraphEdge(focus, selectedEdge)
  collectUpstream(selectedEdge.source)
  collectDownstream(selectedEdge.target)
}

function dependencyMaps(graph: TrackerGraphShape): {
  incoming: Map<string, TrackerGraphShape['edges']>
  outgoing: Map<string, TrackerGraphShape['edges']>
} {
  const incoming = new Map<string, TrackerGraphShape['edges']>()
  const outgoing = new Map<string, TrackerGraphShape['edges']>()
  for (const edge of graph.edges) {
    if (edge.type !== 'dependency') continue
    const incomingEdges = incoming.get(edge.target) ?? []
    incomingEdges.push(edge)
    incoming.set(edge.target, incomingEdges)
    const outgoingEdges = outgoing.get(edge.source) ?? []
    outgoingEdges.push(edge)
    outgoing.set(edge.source, outgoingEdges)
  }
  return { incoming, outgoing }
}

function addGraphEdge(focus: GraphFocus, edge: TrackerGraphShape['edges'][number]): void {
  focus.edgeIds.add(edge.id)
  focus.nodeIds.add(edge.source)
  focus.nodeIds.add(edge.target)
}

function focusedSnapshotFor(
  snapshot: TrackerSnapshot,
  focus: GraphFocus,
  activeRow: TaskProgressRow,
): TrackerSnapshot {
  if (!snapshot.graph) return snapshot

  const nodeIds = new Set(focus.nodeIds)
  const activeTaskNodeId = `task:${activeRow.key}`
  nodeIds.add(activeTaskNodeId)

  const ticketKeys = new Set(
    Array.from(nodeIds).flatMap((nodeId) => {
      const item = graphItemFromNodeId(nodeId)
      return item?.kind === 'ticket' ? [item.key] : []
    }),
  )
  const edgeIds = new Set(focus.edgeIds)
  const graph = {
    ...snapshot.graph,
    nodes: snapshot.graph.nodes.filter((node) => nodeIds.has(node.id)),
    edges: snapshot.graph.edges.filter(
      (edge) => edgeIds.has(edge.id) && nodeIds.has(edge.source) && nodeIds.has(edge.target),
    ),
  }

  return {
    ...snapshot,
    tasks: [activeRow.task],
    tickets: snapshot.tickets.filter((ticket) => ticketKeys.has(ticket.key)),
    dependencies: snapshot.dependencies.filter(
      (dependency) =>
        ticketKeys.has(dependency.ticket_key) &&
        ticketKeys.has(dependency.depends_on_ticket_key),
    ),
    links: [],
    graph,
  }
}

function graphItemFromNodeId(nodeId: string): TrackerSelectedItem | null {
  const separatorIndex = nodeId.indexOf(':')
  if (separatorIndex < 0) return null
  const kind = nodeId.slice(0, separatorIndex)
  const key = nodeId.slice(separatorIndex + 1)
  return kind === 'task' || kind === 'ticket' ? { kind, key } : null
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
  <UiPageShell>
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

    <UiPanel class="tracker-command-panel">
      <div class="tracker-command-panel__controls">
        <UiFormField class="tracker-command-panel__search" label="Search">
          <UiInput v-model="search" placeholder="Ticket, task, owner, outcome" />
        </UiFormField>

        <div class="tracker-command-panel__segment tracker-command-panel__status">
          <span>Status</span>
          <div class="tracker-top-filters" role="group" aria-label="Ticket status">
            <button
              v-for="item in statusOptions"
              :key="item.key"
              type="button"
              class="tracker-top-filter"
              :class="{ 'tracker-top-filter--active': statusFilter === item.key }"
              @click="statusFilter = item.key"
            >
              {{ item.label }}
            </button>
          </div>
        </div>

        <UiFormField class="tracker-command-panel__task" label="Task">
          <UiSelect
            :model-value="activeTaskRow?.key ?? ''"
            :options="taskSelectOptions"
            placeholder="Select active task"
            @change="onTaskSelect"
          />
        </UiFormField>

        <UiFormField class="tracker-command-panel__workflow" label="Workflow">
          <UiSelect v-model="workflowFilter" :options="workflowOptions" />
        </UiFormField>

        <UiFormField class="tracker-command-panel__assignee" label="Assignee">
          <UiSelect v-model="assigneeFilter" :options="assigneeOptions" />
        </UiFormField>

        <div class="tracker-command-panel__segment tracker-command-panel__view">
          <span>View</span>
          <UiSegmentedControl
            v-model="viewMode"
            label="Task tracker view"
            :options="viewOptions"
            @select="(value) => (viewMode = String(value) as ViewMode)"
          />
        </div>

        <UiButton class="tracker-command-panel__clear" variant="ghost" @click="clearFilters">
          Clear
        </UiButton>
      </div>

      <div class="tracker-command-panel__meta">
        <span>{{ tasks.length }} tasks</span>
        <span>{{ tickets.length }} tickets</span>
        <span>{{ taskRows.length }} matching tasks</span>
        <span>{{ filteredTicketCount }} matching tickets</span>
        <span>{{ ticketCounts.complete }} complete</span>
        <span>{{ blockedCount }} blocked</span>
        <span>{{ workflowCount }} workflow-linked tasks</span>
        <span v-if="activeTaskRow">Focused: {{ activeTaskRow.key }}</span>
        <UiCheckbox v-model="showContainment" label="Nested task box" />
      </div>
    </UiPanel>

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
        <UiPanel v-if="activeTaskRow" class="tracker-task-summary">
          <div class="tracker-task-summary__main">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <p class="tracker-task-summary__eyebrow">Focused task</p>
                <TrackerStatusBadge :status="activeTaskRow.task.status" />
              </div>
              <h2 class="tracker-task-summary__title">{{ activeTaskRow.task.title }}</h2>
              <p class="tracker-task-summary__subtitle">
                {{ activeTaskRow.task.goal || activeTaskRow.task.description || activeTaskRow.key }}
              </p>
            </div>
            <div class="tracker-task-summary__progress">
              <span>{{ activeTaskRow.doneCount }}/{{ activeTaskRow.totalCount }}</span>
              <span>{{ activeTaskRow.percent }}%</span>
            </div>
          </div>
          <UiProgressBar
            :value="activeTaskRow.percent"
            :max="100"
            :tone="
              activeTaskRow.percent === 100
                ? 'success'
                : activeTaskRow.blockedCount
                  ? 'warning'
                  : 'accent'
            "
            size="sm"
            :aria-label="`${activeTaskRow.task.title} completion`"
          />
          <div class="tracker-task-summary__meta">
            <span>{{ activeTaskRow.workflowLabel }}</span>
            <span>owner {{ activeTaskRow.task.owner ?? '-' }}</span>
            <span>{{ activeTaskRow.currentDetail }}</span>
          </div>
        </UiPanel>

        <div class="tracker-main">
          <UiPanel v-if="viewMode === 'graph'" :padded="false" class="tracker-flow-shell">
            <div class="tracker-flow-shell__bar">
              <div>
                <p class="tracker-flow-shell__eyebrow">
                  {{ showContainment ? 'Containment view' : 'Dependency tree' }}
                </p>
                <p class="tracker-flow-shell__title">
                  {{ activeTaskRow?.task.title ?? 'Task graph' }}
                </p>
              </div>
              <div class="tracker-flow-shell__stats">
                <span>{{ graphTicketStatLabel }}</span>
                <span>{{ graphEdgeStatLabel }}</span>
              </div>
            </div>
            <div class="tracker-graph-controls">
              <div class="tracker-graph-filter-group">
                <span class="tracker-graph-filter-group__label">Status</span>
                <button
                  v-for="item in graphStatusRows"
                  :key="item.key"
                  type="button"
                  class="tracker-graph-filter"
                  :class="{
                    'tracker-graph-filter--active': graphStatusFilters.includes(item.key),
                  }"
                  @click="toggleGraphStatus(item.key)"
                >
                  <span>{{ item.label }}</span>
                  <strong>{{ item.count }}</strong>
                </button>
              </div>
              <div class="tracker-graph-filter-group">
                <span class="tracker-graph-filter-group__label">Block</span>
                <button
                  v-for="item in graphBlockRows"
                  :key="item.key"
                  type="button"
                  class="tracker-graph-filter"
                  :class="[
                    `tracker-graph-filter--${item.key}`,
                    { 'tracker-graph-filter--active': graphBlockFilters.includes(item.key) },
                  ]"
                  @click="toggleGraphBlock(item.key)"
                >
                  <span>{{ item.label }}</span>
                  <strong>{{ item.count }}</strong>
                </button>
              </div>
              <div class="tracker-graph-controls__tail">
                <span v-if="relationFocusLabel" class="tracker-graph-focus-note">
                  {{ relationFocusLabel }}
                </span>
                <button
                  v-if="graphFiltersActive || selectedEdgeId || selectedNodeFocusId"
                  type="button"
                  class="tracker-graph-clear"
                  @click="clearGraphFilters"
                >
                  Clear graph
                </button>
              </div>
            </div>
            <VueFlow
              :key="flowRenderKey"
              class="tracker-flow"
              :nodes="flow.nodes"
              :edges="flow.edges"
              :default-viewport="{ x: 32, y: 32, zoom: 0.72 }"
              fit-view-on-init
              :min-zoom="0.12"
              :max-zoom="1.5"
              pan-on-scroll
              @node-click="onNodeClick"
              @edge-click="onEdgeClick"
            >
              <template #node-task-group="props">
                <TaskGraphNode v-bind="props" />
              </template>
              <template #node-tracker-ticket="props">
                <TicketGraphNode v-bind="props" />
              </template>
              <Background pattern-color="var(--color-border-subtle)" />
              <MiniMap pannable zoomable />
              <Controls />
            </VueFlow>
          </UiPanel>

          <UiPanel v-else :padded="false" class="overflow-hidden">
            <DataTable
              :items="visibleTickets"
              :columns="ticketColumns"
              :loading="loading"
              interactive
              :selected-id="selectedTicket?.id"
              empty-message="No tickets match the current filters."
              @row-click="onTicketRow"
            >
              <template #cell:status="{ row }">
                <TrackerStatusBadge :status="(row as TrackerTicket).status" />
              </template>
            </DataTable>
          </UiPanel>

        </div>
      </div>
    </div>

    <UiSidePanel
      v-model="detailPanelOpen"
      :title="detailPanelTitle"
      :description="detailPanelDescription"
      size="lg"
    >
      <div v-if="selectedTicket" class="tracker-detail__body tracker-detail__body--drawer">
        <div class="tracker-detail__drawer-kicker">
          <p class="tracker-detail__eyebrow">Ticket</p>
          <TrackerStatusBadge :status="selectedTicket.status" />
        </div>
        <p v-if="selectedTicket.goal" class="tracker-detail__description">
          {{ selectedTicket.goal }}
        </p>
        <div class="tracker-detail__facts">
          <div class="tracker-detail-fact">
            <span>Task</span>
            <strong>{{ selectedTicket.task_key }}</strong>
          </div>
          <div class="tracker-detail-fact">
            <span>Assignee</span>
            <strong>{{ selectedTicket.assignee ?? '-' }}</strong>
          </div>
          <div class="tracker-detail-fact">
            <span>Priority</span>
            <strong>{{ selectedTicket.priority_key }}</strong>
          </div>
          <div class="tracker-detail-fact">
            <span>Lane</span>
            <strong>{{ selectedTicket.lane_key }}</strong>
          </div>
          <div class="tracker-detail-fact">
            <span>Run plan</span>
            <strong>{{ selectedTicket.run_plan_id ?? '-' }}</strong>
          </div>
        </div>
        <UiCallout
          v-if="selectedTicket.blocker_reason || selectedTicket.blocked_by.length"
          tone="warning"
        >
          {{
            selectedTicket.blocker_reason || `Blocked by ${selectedTicket.blocked_by.join(', ')}`
          }}
        </UiCallout>
        <div v-if="selectedTicket.definition_of_done_json.length" class="tracker-detail-section">
          <p class="tracker-detail-section__title">Definition of done</p>
          <ul class="tracker-detail-list">
            <li v-for="item in selectedTicket.definition_of_done_json" :key="item">
              {{ item }}
            </li>
          </ul>
        </div>
        <p v-if="selectedTicket.outcome" class="tracker-detail__outcome">
          {{ selectedTicket.outcome }}
        </p>
      </div>

      <div v-else-if="selectedTask" class="tracker-detail__body tracker-detail__body--drawer">
        <div class="tracker-detail__drawer-kicker">
          <p class="tracker-detail__eyebrow">Task</p>
          <TrackerStatusBadge :status="selectedTask.status" />
        </div>
        <p class="tracker-detail__description">
          {{ selectedTask.goal || selectedTask.description }}
        </p>
        <div class="tracker-detail__facts">
          <div class="tracker-detail-fact">
            <span>Type</span>
            <strong>{{ selectedTask.task_type }}</strong>
          </div>
          <div class="tracker-detail-fact">
            <span>Owner</span>
            <strong>{{ selectedTask.owner ?? '-' }}</strong>
          </div>
          <div class="tracker-detail-fact">
            <span>Source</span>
            <strong>{{ selectedTask.source_kind }}</strong>
          </div>
        </div>
      </div>

      <UiEmptyState
        v-else
        title="Select work"
        description="Pick a task or ticket from the graph or table."
      />
    </UiSidePanel>
  </UiPageShell>
</template>

<style scoped>
.tracker-command-panel {
  display: grid;
  gap: 12px;
  padding: 14px;
}

.tracker-command-panel__controls {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: 12px;
  align-items: end;
}

.tracker-command-panel__segment {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.tracker-command-panel__segment > span {
  color: var(--color-text-muted);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}

.tracker-top-filters {
  display: flex;
  flex-wrap: nowrap;
  gap: 4px;
  overflow-x: auto;
}

.tracker-top-filter {
  min-height: 32px;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: var(--color-text-muted);
  padding: 5px 8px;
  font-size: 12px;
  font-weight: 650;
  white-space: nowrap;
}

.tracker-top-filter:hover {
  color: var(--color-text);
  background: var(--color-bg-surface-alt);
}

.tracker-top-filter--active {
  border-color: var(--color-border-subtle);
  background: var(--color-bg-surface);
  color: var(--color-text);
  box-shadow: var(--shadow-xs);
}

.tracker-command-panel__search {
  grid-column: span 3;
  min-width: 0;
}

.tracker-command-panel__status {
  grid-column: span 5;
}

.tracker-command-panel__task {
  grid-column: span 4;
}

.tracker-command-panel__workflow,
.tracker-command-panel__assignee,
.tracker-command-panel__view {
  grid-column: span 3;
}

.tracker-command-panel__clear {
  grid-column: span 1;
  justify-self: end;
}

.tracker-command-panel__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  border-top: 1px solid var(--color-border-subtle);
  padding-top: 10px;
  color: var(--color-text-muted);
  font-size: 12px;
}

.tracker-workspace {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  align-items: stretch;
}

.tracker-task-summary__eyebrow {
  color: var(--color-text-muted);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
}

.tracker-focus {
  display: grid;
  gap: 16px;
  min-width: 0;
}

.tracker-task-summary {
  display: grid;
  gap: 12px;
}

.tracker-task-summary__main {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.tracker-task-summary__title {
  margin-top: 4px;
  color: var(--color-text);
  font-size: 20px;
  font-weight: 700;
  line-height: 1.25;
}

.tracker-task-summary__subtitle {
  margin-top: 4px;
  color: var(--color-text-muted);
  font-size: 14px;
}

.tracker-task-summary__progress {
  display: grid;
  min-width: 72px;
  flex: none;
  justify-items: end;
  color: var(--color-text);
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 700;
}

.tracker-task-summary__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  color: var(--color-text-muted);
  font-size: 12px;
}

.tracker-main {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.tracker-flow-shell {
  overflow: hidden;
}

.tracker-flow-shell__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid var(--color-border-subtle);
  background: var(--color-bg-surface);
  padding: 10px 14px;
}

.tracker-flow-shell__eyebrow {
  color: var(--color-text-muted);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
}

.tracker-flow-shell__title {
  margin-top: 2px;
  color: var(--color-text);
  font-size: 14px;
  font-weight: 700;
}

.tracker-flow-shell__stats {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
  color: var(--color-text-muted);
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 600;
}

.tracker-graph-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 14px;
  border-bottom: 1px solid var(--color-border-subtle);
  background: var(--color-bg-surface);
  padding: 8px 14px;
}

.tracker-graph-filter-group {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.tracker-graph-filter-group__label {
  color: var(--color-text-muted);
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
}

.tracker-graph-filter {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 24px;
  border: 1px solid var(--color-border-subtle);
  border-radius: 4px;
  background: var(--color-bg-surface-alt);
  color: var(--color-text-muted);
  padding: 3px 7px;
  font-size: 11px;
  font-weight: 650;
}

.tracker-graph-filter:hover {
  border-color: var(--color-border-strong);
  color: var(--color-text);
}

.tracker-graph-filter strong {
  color: var(--color-text);
  font-family: var(--font-mono);
  font-size: 11px;
}

.tracker-graph-filter--active {
  border-color: var(--color-accent-default);
  background: color-mix(in srgb, var(--color-accent-default) 10%, var(--color-bg-surface));
  color: var(--color-text);
}

.tracker-graph-filter--blocked {
  color: var(--color-danger-default);
}

.tracker-graph-filter--open {
  color: var(--color-success-default);
}

.tracker-graph-controls__tail {
  display: flex;
  min-height: 24px;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.tracker-graph-focus-note {
  border-radius: 999px;
  background: color-mix(in srgb, var(--color-info-default) 10%, var(--color-bg-surface));
  color: var(--color-info-default);
  font-size: 11px;
  font-weight: 700;
  padding: 3px 8px;
}

.tracker-graph-clear {
  border: 0;
  background: transparent;
  color: var(--color-text-muted);
  font-size: 12px;
  font-weight: 700;
}

.tracker-graph-clear:hover {
  color: var(--color-text);
}

.tracker-flow {
  width: 100%;
  height: 640px;
  background: var(--color-bg-surface-alt);
}

.tracker-detail__body {
  display: grid;
  gap: 18px;
}

.tracker-detail__body--drawer {
  padding: 0;
}

.tracker-detail__drawer-kicker {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.tracker-detail__eyebrow {
  color: var(--color-text-muted);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
}

.tracker-detail__description {
  max-width: 82ch;
  color: var(--color-text-muted);
  font-size: 14px;
  line-height: 1.55;
}

.tracker-detail__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
}

.tracker-detail-fact {
  display: grid;
  gap: 5px;
  min-width: 0;
  border: 1px solid var(--color-border-subtle);
  border-radius: 6px;
  background: var(--color-bg-surface-alt);
  padding: 10px 12px;
}

.tracker-detail-fact span {
  color: var(--color-text-muted);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}

.tracker-detail-fact strong {
  overflow: hidden;
  color: var(--color-text);
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tracker-detail-section {
  display: grid;
  gap: 8px;
  border-top: 1px solid var(--color-border-subtle);
  padding-top: 16px;
}

.tracker-detail-section__title {
  color: var(--color-text-muted);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}

.tracker-detail-list {
  display: grid;
  gap: 6px;
  color: var(--color-text-muted);
  font-size: 13px;
  line-height: 1.45;
}

.tracker-detail__outcome {
  border: 1px solid var(--color-border-subtle);
  border-radius: 6px;
  background: var(--color-bg-surface-alt);
  color: var(--color-text-muted);
  font-size: 13px;
  line-height: 1.5;
  padding: 12px 14px;
}

:deep(.tracker-node-selected .task-graph-node),
:deep(.tracker-node-selected .ticket-graph-node) {
  border-color: var(--color-accent-default);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-accent-default) 18%, transparent);
}

:deep(.tracker-node-highlighted .ticket-graph-node),
:deep(.tracker-node-highlighted .task-graph-node) {
  border-color: var(--color-info-default);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-info-default) 20%, transparent);
}

:deep(.tracker-node-muted) {
  opacity: 0.24;
}

:deep(.tracker-edge-dependency .vue-flow__edge-path) {
  stroke: color-mix(in srgb, var(--color-border-strong) 70%, var(--color-bg-surface));
  stroke-width: 1.25;
}

:deep(.tracker-edge-highlighted .vue-flow__edge-path) {
  stroke: var(--color-info-default);
  stroke-width: 3;
}

:deep(.tracker-edge-muted .vue-flow__edge-path) {
  opacity: 0.12;
}

:deep(.tracker-edge-active .vue-flow__edge-path),
:deep(.tracker-edge-active.selected .vue-flow__edge-path) {
  stroke: var(--color-success-default);
  stroke-width: 4;
  filter: drop-shadow(0 0 4px color-mix(in srgb, var(--color-success-default) 35%, transparent));
}

:deep(.tracker-edge-link .vue-flow__edge-path) {
  stroke: var(--color-info-default);
  stroke-dasharray: 5 5;
}

:deep(.tracker-edge-contains .vue-flow__edge-path) {
  stroke: var(--color-border-strong);
}

@media (max-width: 1180px) {
  .tracker-command-panel__controls {
    grid-template-columns: repeat(6, minmax(0, 1fr));
  }

  .tracker-command-panel__search {
    grid-column: span 3;
  }

  .tracker-command-panel__status {
    grid-column: 1 / -1;
  }

  .tracker-command-panel__task {
    grid-column: span 3;
  }

  .tracker-command-panel__workflow,
  .tracker-command-panel__assignee,
  .tracker-command-panel__view {
    grid-column: span 2;
  }
}

@media (max-width: 720px) {
  .tracker-command-panel__controls {
    grid-template-columns: 1fr;
  }

  .tracker-command-panel__search,
  .tracker-command-panel__status,
  .tracker-command-panel__task,
  .tracker-command-panel__workflow,
  .tracker-command-panel__assignee,
  .tracker-command-panel__view,
  .tracker-command-panel__clear {
    grid-column: 1 / -1;
  }

  .tracker-command-panel__clear {
    justify-self: start;
  }

  .tracker-flow-shell {
    min-height: 520px;
  }

  .tracker-flow-shell__bar {
    display: grid;
  }

  .tracker-flow-shell__stats {
    justify-content: start;
  }

  .tracker-flow {
    height: 520px;
  }

  .tracker-task-summary__main {
    display: grid;
  }

  .tracker-task-summary__progress {
    justify-items: start;
  }
}
</style>
