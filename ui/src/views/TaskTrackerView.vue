<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'

import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import { UiButton, UiCallout, UiEmptyState, UiPageShell } from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import { callOperation } from '@/lib/operations'
import { resolveStatus, trackerStatus } from '@/design/status'
import { formatDateTime } from '@/lib/stackos/json'
import type { TrackerSnapshot, TrackerStatus, TrackerTicket } from '@/lib/task-tracker/types'

import TaskTrackerCommandPanel from './task-tracker/TaskTrackerCommandPanel.vue'
import TrackerGraphPanel from './task-tracker/TrackerGraphPanel.vue'
import TrackerStoriesPanel from './task-tracker/TrackerStoriesPanel.vue'
import TrackerTaskDetailDialog from './task-tracker/TrackerTaskDetailDialog.vue'
import TrackerTicketDetailPanel from './task-tracker/TrackerTicketDetailPanel.vue'
import TrackerTicketTable from './task-tracker/TrackerTicketTable.vue'
import TrackerWarningSummary from './task-tracker/TrackerWarningSummary.vue'
import { useTrackerExecutionContexts } from './task-tracker/useTrackerExecutionContexts'
import { useTrackerGraphSession } from './task-tracker/useTrackerGraphSession'
import type {
  SelectMetaTone,
  StatusFilter,
  TaskProgressRow,
  ViewMode,
} from './task-tracker/viewTypes'
import {
  buildAssigneeOptions,
  buildTaskProgressRows,
  buildWorkflowOptions,
  groupTicketsByTask,
  isOpenTicket,
  mergeFocusedTrackerSnapshot,
  ticketMatchesControls,
  type TrackerControlFilters,
} from './task-tracker/viewModel'

const route = useRoute()
const router = useRouter()
const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const routeFocus = computed(() => (typeof route.query.focus === 'string' ? route.query.focus : ''))

const snapshot = ref<TrackerSnapshot | null>(null)
const snapshotScope = ref<'active' | 'full'>('active')
const loading = ref(false)
const error = ref<string | null>(null)
const viewMode = ref<ViewMode>('graph')
const statusFilter = ref<StatusFilter>('all')
const workflowFilter = ref('')
const assigneeFilter = ref('')
const search = ref('')
const filtersExpanded = ref(false)
const activeTaskKey = ref(routeTaskKey())
const taskDetailOpen = ref(false)

const statusOptions: Array<{ key: StatusFilter; label: string }> = [
  { key: 'all', label: 'All' },
  ...(Object.entries(trackerStatus) as Array<[TrackerStatus, { label: string }]>).map(
    ([key, definition]) => ({
      key,
      label: definition.label,
    }),
  ),
]

function trackerStatusTone(status: TrackerStatus): SelectMetaTone {
  return resolveStatus('tracker', status).tone
}

const viewOptions: Array<{ key: ViewMode; label: string; icon: string }> = [
  { key: 'graph', label: 'Dependency map', icon: 'git-branch' },
  { key: 'stories', label: 'Stories', icon: 'list' },
  { key: 'tickets', label: 'Tickets', icon: 'list' },
]

const tasks = computed(() => snapshot.value?.tasks ?? [])
const tickets = computed(() => snapshot.value?.tickets ?? [])

const ticketsByTask = computed(() => groupTicketsByTask(tickets.value))
const workflowOptions = computed(() => buildWorkflowOptions(tasks.value))
const assigneeOptions = computed(() => buildAssigneeOptions(tickets.value))
const controlFilters = computed<TrackerControlFilters>(() => ({
  search: search.value,
  status: statusFilter.value,
  workflow: workflowFilter.value,
  assignee: assigneeFilter.value,
}))
const taskRows = computed<TaskProgressRow[]>(() =>
  buildTaskProgressRows(tasks.value, ticketsByTask.value, controlFilters.value),
)

const taskSelectOptions = computed(() =>
  taskRows.value.map((row) => ({
    value: row.key,
    label: `#${row.id} ${row.task.title}`,
    rightLabel: resolveStatus('tracker', row.task.status).label,
    rightMeta: `${row.terminalCount}/${row.totalCount} terminal`,
    rightTone: trackerStatusTone(row.task.status),
  })),
)

const activeTaskRow = computed<TaskProgressRow | null>(() => {
  if (!taskRows.value.length) return null
  return taskRows.value.find((row) => row.key === activeTaskKey.value) ?? taskRows.value[0]
})

const graphSession = useTrackerGraphSession({
  projectId,
  activeTaskKey,
  activeTaskRow,
  snapshot,
  routeError: error,
  reload: (options) => load(options),
})
const {
  statusFilters: graphStatusFilters,
  blockFilters: graphBlockFilters,
  viewport: graphViewport,
  refocusKey: graphRefocusKey,
  selected,
  detailPanelOpen,
  liveError,
  statusRows: graphStatusRows,
  blockRows: graphBlockRows,
  filtersActive: graphFiltersActive,
  selectedTicket,
  flowRenderKey,
  flow,
  fitOnInit: graphFitOnInit,
  focusNodeIds: graphFocusNodeIds,
  primaryFocusNodeId: primaryGraphFocusNodeId,
  ticketStatLabel: graphTicketStatLabel,
  edgeStatLabel: graphEdgeStatLabel,
  selectedEdge: selectedGraphEdge,
  selectionLabel: graphSelectionLabel,
  selectionVisible: graphSelectionVisible,
  selectionStats: graphSelectionStats,
  loadFocusedGraph,
  toggleStatus: toggleGraphStatus,
  toggleBlock: toggleGraphBlock,
  clearFilters: clearGraphFilters,
  restoreViewport: restoreGraphViewport,
  onViewportReady: onGraphViewportReady,
  onViewportChange: onGraphViewportChange,
  restartStatusStream: restartTrackerStatusStream,
  stopStatusStream: stopTrackerStatusStream,
  onNodeClick,
  onEdgeClick,
  onCanvasClick: onGraphCanvasClick,
  selectTicket,
  openSelectedDetail,
  clearFocus: clearGraphFocus,
} = graphSession

const executionContexts = useTrackerExecutionContexts({ projectId })
const {
  contexts: taskContexts,
  artifacts: taskContextArtifacts,
  pageInfo: taskContextPageInfo,
  artifactPageInfo: taskContextArtifactPageInfo,
  loading: taskContextLoading,
  error: taskContextError,
  load: loadTaskContexts,
} = executionContexts

const graphFlowId = computed(() => `tracker-flow-${projectId.value}`)

const visibleTickets = computed(() => {
  const row = activeTaskRow.value
  if (!row) return []
  return row.tickets.filter((ticket) =>
    ticketMatchesControls(ticket, row.task, controlFilters.value),
  )
})

const filteredTicketCount = computed(() =>
  taskRows.value.reduce(
    (sum, row) =>
      sum +
      row.tickets.filter((ticket) => ticketMatchesControls(ticket, row.task, controlFilters.value))
        .length,
    0,
  ),
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

let loadRequestSequence = 0

async function load(options: { refocus?: boolean; restartStream?: boolean } = {}): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  const requestSequence = ++loadRequestSequence
  const requestedProjectId = projectId.value
  loading.value = true
  error.value = null
  try {
    const needsFullSnapshot = viewMode.value !== 'graph'
    const nextSnapshot = await callOperation<TrackerSnapshot>('tracker.get', {
      project_id: requestedProjectId,
      ...(needsFullSnapshot ? {} : { statuses: ['in-progress'] }),
      include_graph: false,
    })
    if (requestSequence !== loadRequestSequence || requestedProjectId !== projectId.value) return
    snapshot.value = nextSnapshot
    snapshotScope.value = needsFullSnapshot ? 'full' : 'active'
    await nextTick()
    const requestedTaskKey = routeTaskKey()
    const nextTaskKey = requestedTaskKey || ensureActiveTask()
    const restoredViewport =
      options.refocus === false ? Boolean(graphViewport.value) : restoreGraphViewport()
    const shouldRefocus = options.refocus ?? !restoredViewport
    const focused = await loadFocusedGraph(nextTaskKey, { refocus: shouldRefocus })
    if (requestSequence !== loadRequestSequence || requestedProjectId !== projectId.value) return
    if (focused && snapshotScope.value === 'active') {
      snapshot.value = mergeFocusedTrackerSnapshot(snapshot.value, focused)
      await nextTick()
      ensureActiveTask()
    } else if (!focused && requestedTaskKey) {
      ensureActiveTask()
    }
    if (options.restartStream !== false) restartTrackerStatusStream()
  } catch (err) {
    if (requestSequence === loadRequestSequence) {
      error.value = err instanceof Error ? err.message : 'failed to load tracker'
    }
  } finally {
    if (requestSequence === loadRequestSequence) loading.value = false
  }
}

function setViewMode(value: ViewMode): void {
  viewMode.value = value
  if (value !== 'graph' && snapshotScope.value !== 'full') {
    void load({ refocus: false, restartStream: false })
  }
}

function onTaskRow(row: TaskProgressRow): void {
  clearGraphFocus()
  activeTaskKey.value = row.key
  syncActiveTaskToUrl(row.key)
  const restoredViewport = restoreGraphViewport()
  void loadFocusedGraph(row.key, { refocus: !restoredViewport })
  restartTrackerStatusStream()
  if (taskDetailOpen.value) void loadTaskContexts(row.task)
}

function onTaskSelect(value: string | number | null): void {
  const taskKey = typeof value === 'string' ? value : String(value ?? '')
  const row = taskRows.value.find((candidate) => candidate.key === taskKey)
  if (row) onTaskRow(row)
}

function onPaneClick(event: MouseEvent): void {
  onGraphCanvasClick(event)
}

function onTicketRow(row: TrackerTicket): void {
  activeTaskKey.value = row.task_key
  syncActiveTaskToUrl(row.task_key)
  selectTicket(row)
  restoreGraphViewport()
  void loadFocusedGraph(row.task_key, { refocus: false })
  restartTrackerStatusStream()
  if (taskDetailOpen.value) void loadTaskContexts(activeTask.value)
}

function openTaskDetail(): void {
  taskDetailOpen.value = true
  void loadTaskContexts(activeTask.value)
}

function ensureActiveTask(): string {
  if (!taskRows.value.length) {
    activeTaskKey.value = ''
    syncActiveTaskToUrl('')
    clearGraphFocus()
    return ''
  }
  const current = taskRows.value.find((row) => row.key === activeTaskKey.value)
  const nextRow = current ?? taskRows.value[0]
  activeTaskKey.value = nextRow.key
  syncActiveTaskToUrl(nextRow.key)
  if (
    selected.value?.kind === 'ticket' &&
    !nextRow.tickets.some((ticket) => ticket.key === selected.value?.key)
  ) {
    clearGraphFocus()
    return nextRow.key
  }
  return nextRow.key
}

async function reconcileActiveTask(): Promise<void> {
  const previousTaskKey = activeTaskKey.value
  const nextTaskKey = ensureActiveTask()
  if (snapshot.value && nextTaskKey && nextTaskKey !== previousTaskKey) {
    const restoredViewport = restoreGraphViewport()
    await loadFocusedGraph(nextTaskKey, { refocus: !restoredViewport })
    restartTrackerStatusStream()
  }
  if (taskDetailOpen.value) void loadTaskContexts(activeTask.value)
}

function setSearch(value: string): void {
  search.value = value
  void reconcileActiveTask()
}

function setStatusFilter(value: StatusFilter): void {
  statusFilter.value = value
  void reconcileActiveTask()
}

function setWorkflowFilter(value: string): void {
  workflowFilter.value = value
  void reconcileActiveTask()
}

function setAssigneeFilter(value: string): void {
  assigneeFilter.value = value
  void reconcileActiveTask()
}

function clearFilters(): void {
  statusFilter.value = 'all'
  workflowFilter.value = ''
  assigneeFilter.value = ''
  search.value = ''
  clearGraphFilters()
  void reconcileActiveTask()
}

function taskKeyFromQueryValue(raw: unknown): string {
  if (Array.isArray(raw)) return typeof raw[0] === 'string' ? raw[0] : ''
  return typeof raw === 'string' ? raw : ''
}

function routeTaskKey(): string {
  return taskKeyFromQueryValue(route.query.task)
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

onMounted(() => {
  void load()
})

onBeforeRouteUpdate((to) => {
  const nextProjectId = Number.parseInt(String(to.params.id), 10)
  if (nextProjectId !== projectId.value) {
    loadRequestSequence += 1
    stopTrackerStatusStream()
    snapshot.value = null
    activeTaskKey.value = taskKeyFromQueryValue(to.query.task)
    clearGraphFocus()
    setTimeout(() => void load(), 0)
    return
  }
  const nextTaskKey = taskKeyFromQueryValue(to.query.task)
  if (nextTaskKey === activeTaskKey.value) return
  activeTaskKey.value = nextTaskKey
  clearGraphFocus()
  void reconcileActiveTask()
})
</script>

<template>
  <UiPageShell class="tracker-page-shell">
    <ProjectPageHeader
      :project-id="projectId"
      title="Work"
      description="What agents are building, what’s blocked, and how work gets verified."
      :breadcrumbs="[{ label: 'Work' }]"
    >
      <template #actions>
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="refresh"
          :loading="loading"
          @click="load()"
        >
          Refresh
        </UiButton>
      </template>
    </ProjectPageHeader>

    <UiCallout v-if="error" tone="danger">
      {{ error }}
    </UiCallout>

    <UiCallout v-if="liveError" tone="warning">
      {{ liveError }}
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
      :active-terminal-count="activeTaskRow?.terminalCount ?? null"
      :active-total-count="activeTaskRow?.totalCount ?? null"
      :blocked-count="blockedCount"
      :workflow-count="workflowCount"
      @task-select="onTaskSelect"
      @update:view-mode="setViewMode"
      @update:filters-expanded="filtersExpanded = $event"
      @update:search="setSearch"
      @update:status-filter="setStatusFilter"
      @update:workflow-filter="setWorkflowFilter"
      @update:assignee-filter="setAssigneeFilter"
      @clear="clearFilters"
    />

    <TrackerStoriesPanel
      v-if="viewMode === 'stories' && !loading"
      :key="routeFocus"
      :project-id="projectId"
      :rows="taskRows"
      :active-task-key="activeTaskRow?.key ?? ''"
      :focus="routeFocus"
      @select-task="onTaskRow"
      @open-task-detail="openTaskDetail"
    />

    <UiCallout v-else-if="viewMode === 'stories'" tone="neutral"> Loading work stories… </UiCallout>

    <TrackerWarningSummary v-if="viewMode === 'graph'" :warnings="flow.warnings" />

    <UiCallout v-if="viewMode !== 'stories' && loading" tone="neutral">
      Loading tracker work…
    </UiCallout>

    <div v-else-if="viewMode !== 'stories' && taskRows.length === 0" class="min-h-[360px]">
      <UiEmptyState
        title="No tracker work"
        description="Agents can create tasks and tickets through tracker operations."
      />
    </div>

    <div v-else-if="viewMode !== 'stories'" class="tracker-workspace">
      <div class="tracker-focus">
        <div class="tracker-main">
          <TrackerGraphPanel
            v-if="viewMode === 'graph'"
            :flow="flow"
            :flow-id="graphFlowId"
            :flow-render-key="flowRenderKey"
            :graph-fit-on-init="graphFitOnInit"
            :focus-node-ids="graphFocusNodeIds"
            :primary-focus-node-id="primaryGraphFocusNodeId"
            :refocus-key="graphRefocusKey"
            :initial-viewport="graphViewport"
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
            @open-task-detail="openTaskDetail"
            @node-click="onNodeClick"
            @edge-click="onEdgeClick"
            @pane-click="onPaneClick"
            @graph-canvas-click="onGraphCanvasClick"
            @open-selected-detail="openSelectedDetail"
            @clear-graph-focus="clearGraphFocus"
            @viewport-change-end="onGraphViewportChange"
            @viewport-ready="onGraphViewportReady"
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

    <TrackerTaskDetailDialog
      v-model="taskDetailOpen"
      :task="activeTask"
      :contexts="taskContexts"
      :context-artifacts="taskContextArtifacts"
      :context-page-info="taskContextPageInfo"
      :context-artifact-page-info="taskContextArtifactPageInfo"
      :context-loading="taskContextLoading"
      :context-error="taskContextError"
    />
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
</style>
