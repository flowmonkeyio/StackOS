<script setup lang="ts">
import { computed, ref } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter, type LocationQueryRaw } from 'vue-router'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import { UiBadge, UiButton, UiCallout, UiEmptyState, UiLoadingState, UiPageShell } from '@/components/ui'
import { callOperation } from '@/lib/operations'
import { resolveStatus, trackerStatus } from '@/design/status'
import type { TrackerSnapshot, TrackerStatus, TrackerTask, TrackerTicket } from '@/lib/task-tracker/types'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { useProjectScopedLoader } from '@/composables/useProjectScopedLoader'
import TaskTrackerCommandPanel from './task-tracker/TaskTrackerCommandPanel.vue'
import TrackerBoardPanel from './task-tracker/TrackerBoardPanel.vue'
import TrackerGraphPanel from './task-tracker/TrackerGraphPanel.vue'
import TrackerTaskDetailDialog from './task-tracker/TrackerTaskDetailDialog.vue'
import TrackerTicketDetailPanel from './task-tracker/TrackerTicketDetailPanel.vue'
import TrackerWarningSummary from './task-tracker/TrackerWarningSummary.vue'
import { useTrackerExecutionContexts } from './task-tracker/useTrackerExecutionContexts'
import { useTrackerGraphSession } from './task-tracker/useTrackerGraphSession'
import type { StatusFilter, ViewMode } from './task-tracker/viewTypes'
import { buildAssigneeOptions, buildTaskProgressRows, buildWorkflowOptions, groupTicketsByTask, mergeFocusedTrackerSnapshot } from './task-tracker/viewModel'

const route = useRoute()
const router = useRouter()
const { projectId } = useProjectRouteScope(route)
const viewProjectId = projectId.value
const viewPath = route.path
const snapshot = ref<TrackerSnapshot | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const mapTaskStorageKey = `stackos:work-map-task:${projectId.value}`
const mapTaskKey = ref(queryText(route.query.task) || rememberedMapTask())
const viewMode = computed<ViewMode>(() => route.query.view === 'graph' ? 'graph' : 'board')
const search = computed(() => queryText(route.query.search))
const statusFilter = computed(() => statusFromQuery(route.query.status))
const ticketStatusFilter = computed(() => statusFromQuery(route.query.ticket_status))
const workflowFilter = computed(() => queryText(route.query.workflow))
const assigneeFilter = computed(() => queryText(route.query.assignee))
const blockedFilter = computed(() => route.query.focus === 'blocked')
const detailTaskKey = computed(() => queryText(route.query.detail))
const detailTicketKey = computed(() => queryText(route.query.ticket))
const detailSnapshot = ref<TrackerSnapshot | null>(null)
const detailLoading = ref(false)
const detailError = ref<string | null>(null)
const taskDialog = ref<{ confirmLeave: () => boolean; syncTask: (task: TrackerTask | null) => void; prepareInspection: (ticketKey: string) => void } | null>(null)
const detailTask = computed(() => detailSnapshot.value?.tasks.find((task) => task.key === detailTaskKey.value) ?? null)
const detailTickets = computed(() => detailSnapshot.value?.tickets.filter((ticket) => ticket.task_key === detailTaskKey.value) ?? [])
const detailTicket = computed(() => detailTickets.value.find((ticket) => ticket.key === detailTicketKey.value) ?? null)
const tasks = computed(() => snapshot.value?.tasks ?? [])
const ticketsByTask = computed(() => groupTicketsByTask(snapshot.value?.tickets ?? []))
const allTaskRows = computed(() => buildTaskProgressRows(tasks.value, ticketsByTask.value, { search: '', status: 'all', workflow: '', assignee: '' }))
const taskRows = computed(() => buildTaskProgressRows(tasks.value, ticketsByTask.value, {
  search: search.value, status: statusFilter.value, workflow: workflowFilter.value, assignee: assigneeFilter.value,
  ticketStatus: ticketStatusFilter.value, blocked: blockedFilter.value,
}))
const activeTaskRow = computed(() => allTaskRows.value.find((row) => row.key === mapTaskKey.value) ?? null)
const taskSelectOptions = computed(() => allTaskRows.value.map((row) => ({
  value: row.key, label: `#${row.id} ${row.task.title}`,
  rightLabel: resolveStatus('tracker', row.task.status).label,
  rightMeta: `${row.completedCount}/${row.totalCount} complete`,
  rightTone: resolveStatus('tracker', row.task.status).tone,
})))
const workflowOptions = computed(() => buildWorkflowOptions(tasks.value))
const assigneeOptions = computed(() => buildAssigneeOptions(tasks.value, snapshot.value?.tickets ?? []))
const filtersActive = computed(() => Boolean(search.value || workflowFilter.value || assigneeFilter.value || statusFilter.value !== 'all' || ticketStatusFilter.value !== 'all' || blockedFilter.value))
const liveTaskKey = computed(() => viewMode.value === 'graph' ? mapTaskKey.value : '')
const graph = useTrackerGraphSession({
  projectId, activeTaskKey: mapTaskKey, liveTaskKey, activeTaskRow, snapshot, routeError: error,
  reload: (options) => load(options),
})
const {
  loading: graphLoading, graphDataAvailable, statusFilters: graphStatusFilters, blockFilters: graphBlockFilters,
  viewport: graphViewport, refocusKey: graphRefocusKey, liveError, statusRows: graphStatusRows, blockRows: graphBlockRows,
  filtersActive: graphFiltersActive, selectedTicket: graphSelectedTicket, flowRenderKey, flow, fitOnInit: graphFitOnInit,
  focusNodeIds: graphFocusNodeIds, primaryFocusNodeId: primaryGraphFocusNodeId, ticketStatLabel: graphTicketStatLabel,
  edgeStatLabel: graphEdgeStatLabel, selectedEdge: graphSelectedEdge, selectionLabel: graphSelectionLabel,
  selectionVisible: graphSelectionVisible, selectionStats: graphSelectionStats,
  detailPanelOpen: graphDetailPanelOpen,
} = graph
const graphPending = computed(() => graphLoading.value && !graphDataAvailable.value)
const contexts = useTrackerExecutionContexts({ projectId })
const {
  contexts: taskContexts, artifacts: taskContextArtifacts, pageInfo: taskContextPageInfo,
  artifactPageInfo: taskContextArtifactPageInfo, loading: taskContextLoading, error: taskContextError,
} = contexts
let loadSequence = 0
let detailSequence = 0
let alive = true

function currentScope(): boolean {
  return alive && projectId.value === viewProjectId && route.path === viewPath
}

async function load(options: { refocus?: boolean; restartStream?: boolean } = {}): Promise<void> {
  if (!projectId.value || !currentScope()) return
  const sequence = ++loadSequence
  loading.value = true
  error.value = null
  try {
    const next = await callOperation<TrackerSnapshot>('tracker.get', {
      project_id: projectId.value, task_index_only: true, include_graph: false,
    })
    if (!currentScope() || sequence !== loadSequence) return
    snapshot.value = next
    if (viewMode.value === 'graph') mapTaskKey.value = resolveMapTaskKey()
    else if (!queryText(route.query.task)) mapTaskKey.value = validRememberedMapTask()
    // Start the stream while this accepted request still owns the current scope.
    // Awaited map/detail work must never restart a stream after scope disposal.
    if (options.restartStream !== false && !mapNeedsCanonicalRoute()) graph.restartStatusStream()
    const detailKey = detailTaskKey.value
    if (viewMode.value === 'graph') await loadMap(options.refocus)
    if (!currentScope() || sequence !== loadSequence) return
    if (detailKey && detailTaskKey.value === detailKey) await loadDetail(detailKey)
  } catch (cause) {
    if (currentScope() && sequence === loadSequence) error.value = cause instanceof Error ? cause.message : 'Could not load work.'
  } finally {
    if (currentScope() && sequence === loadSequence) loading.value = false
  }
}

async function loadMap(refocus?: boolean): Promise<void> {
  if (!currentScope() || !activeTaskRow.value || viewMode.value !== 'graph') return
  const taskKey = mapTaskKey.value
  try { window.sessionStorage.setItem(mapTaskStorageKey, mapTaskKey.value) } catch { /* Storage may be unavailable. */ }
  if (queryText(route.query.task) !== mapTaskKey.value) {
    await router.replace({ query: { ...route.query, task: mapTaskKey.value } })
    return
  }
  const restored = graph.restoreViewport()
  const focused = await graph.loadFocusedGraph(taskKey, { refocus: refocus ?? !restored })
  if (!currentScope() || viewMode.value !== 'graph' || mapTaskKey.value !== taskKey || queryText(route.query.task) !== taskKey) return
  if (focused && snapshot.value) snapshot.value = mergeFocusedTrackerSnapshot(snapshot.value, focused)
}

async function loadDetail(taskKey: string): Promise<void> {
  if (!currentScope() || detailTaskKey.value !== taskKey) return
  const sequence = ++detailSequence
  detailLoading.value = true
  detailError.value = null
  if (detailSnapshot.value?.tasks[0]?.key !== taskKey) {
    detailSnapshot.value = null
    taskDialog.value?.syncTask?.(null)
    void contexts.load(null)
  }
  try {
    const next = await callOperation<TrackerSnapshot>('tracker.get', {
      project_id: projectId.value, task_key: taskKey, include_graph: false,
    })
    if (!currentScope() || sequence !== detailSequence || detailTaskKey.value !== taskKey) return
    const task = next.tasks.find((item) => item.key === taskKey)
    if (!task) throw new Error('This task is no longer available in this project.')
    detailSnapshot.value = next
    taskDialog.value?.syncTask?.(task)
    if (snapshot.value) snapshot.value = mergeFocusedTrackerSnapshot(snapshot.value, next)
    void contexts.load(task)
  } catch (cause) {
    if (currentScope() && sequence === detailSequence) {
      detailError.value = cause instanceof Error ? cause.message : 'Could not load task details.'
    }
  } finally {
    if (currentScope() && sequence === detailSequence) detailLoading.value = false
  }
}

async function refreshTaskContent(taskKey: string): Promise<TrackerTask> {
  const [index, focused] = await Promise.all([
    callOperation<TrackerSnapshot>('tracker.get', {
      project_id: projectId.value, task_index_only: true, include_graph: false,
    }),
    callOperation<TrackerSnapshot>('tracker.get', {
      project_id: projectId.value, task_key: taskKey, include_graph: false,
    }),
  ])
  const task = focused.tasks.find((item) => item.key === taskKey)
  if (!task) throw new Error('This task is no longer available in this project.')
  if (currentScope() && detailTaskKey.value === taskKey) {
    loadSequence += 1
    detailSequence += 1
    loading.value = false
    detailLoading.value = false
    error.value = null
    detailError.value = null
    snapshot.value = mergeFocusedTrackerSnapshot(index, focused)
    detailSnapshot.value = focused
    taskDialog.value?.syncTask?.(task)
  }
  return task
}

onBeforeRouteUpdate((to, from) => {
  const sameTask = to.path === from.path && Boolean(from.query.detail)
    && ['detail', 'view', 'task'].every((key) => to.query[key] === from.query[key])
  if (sameTask && to.query.ticket !== from.query.ticket) {
    taskDialog.value?.prepareInspection?.(queryText(to.query.ticket))
    return true
  }
  if (to.path !== from.path || ['detail', 'ticket', 'view', 'task'].some((key) => to.query[key] !== from.query[key])) {
    return taskDialog.value?.confirmLeave?.() ?? true
  }
  return true
})
onBeforeRouteLeave(() => taskDialog.value?.confirmLeave?.() ?? true)

function navigate(changes: LocationQueryRaw, replace = false): void {
  const query: LocationQueryRaw = { ...route.query, ...changes }
  for (const [key, value] of Object.entries(query)) if (value === '' || value == null) delete query[key]
  void router[replace ? 'replace' : 'push']({ query })
}
function setViewMode(mode: ViewMode): void {
  if (mode === viewMode.value) return
  navigate({ view: mode === 'graph' ? 'graph' : undefined, task: mode === 'graph' ? queryText(route.query.task) || undefined : undefined, detail: undefined, ticket: undefined })
}
function selectMapTask(value: string | number | null): void {
  navigate({ task: String(value ?? ''), detail: undefined, ticket: undefined })
}
function openTaskDetail(taskKey: string): void { navigate({ detail: taskKey, ticket: undefined }) }
function openTicket(ticket: TrackerTicket): void { navigate({ detail: ticket.task_key, ticket: ticket.key }) }
function viewDependencies(taskKey: string): void {
  navigate({ view: 'graph', task: taskKey, detail: undefined, ticket: undefined })
}
function clearFilters(): void {
  navigate({ search: undefined, status: undefined, ticket_status: undefined, workflow: undefined, assignee: undefined, focus: undefined })
}
function queryText(value: unknown): string { return typeof value === 'string' ? value : '' }
function rememberedMapTask(): string {
  try { return window.sessionStorage.getItem(mapTaskStorageKey) ?? '' } catch { return '' }
}
function validRememberedMapTask(): string {
  const remembered = rememberedMapTask()
  if (!remembered || allTaskRows.value.some((row) => row.key === remembered)) return remembered
  try { window.sessionStorage.removeItem(mapTaskStorageKey) } catch { /* Storage may be unavailable. */ }
  return ''
}
function resolveMapTaskKey(): string {
  // Only called after the project index is accepted; the URL always stays authoritative.
  return queryText(route.query.task) || validRememberedMapTask() || allTaskRows.value[0]?.key || ''
}
function mapNeedsCanonicalRoute(): boolean {
  return viewMode.value === 'graph' && Boolean(activeTaskRow.value) && queryText(route.query.task) !== mapTaskKey.value
}
function statusFromQuery(value: unknown): StatusFilter {
  return typeof value === 'string' && Object.hasOwn(trackerStatus, value) ? value as TrackerStatus : 'all'
}

const removeRouteListener = router.afterEach(async (to, from, failure) => {
  if (failure || to.path !== from.path || !currentScope() || !snapshot.value) return
  const mapChanged = to.query.view !== from.query.view || to.query.task !== from.query.task
  if (viewMode.value === 'graph' && mapChanged) {
    mapTaskKey.value = resolveMapTaskKey()
  }
  if (mapChanged) graph.clearFocus()
  if (to.query.detail && to.query.detail !== from.query.detail) graphDetailPanelOpen.value = false
  if (mapChanged && !mapNeedsCanonicalRoute()) graph.restartStatusStream()
  if (viewMode.value === 'graph' && mapChanged) {
    await loadMap()
    if (!currentScope() || router.currentRoute.value !== to) return
  }
  if (to.query.detail !== from.query.detail) {
    if (detailTaskKey.value) await loadDetail(detailTaskKey.value)
    else {
      detailSequence += 1
      detailSnapshot.value = null
      taskDialog.value?.syncTask?.(null)
      detailError.value = null
      detailLoading.value = false
      void contexts.load(null)
    }
  }
})
useProjectScopedLoader({
  projectId, load: () => load(),
  onScopeExit: () => {
    alive = false
    loadSequence += 1
    detailSequence += 1
    graph.stopStatusStream()
    removeRouteListener()
  },
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
          @click="load({ refocus: false })"
        >
          Refresh
        </UiButton>
      </template>
    </ProjectPageHeader>
    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>
    <UiCallout
      v-if="liveError"
      tone="warning"
    >
      {{ liveError }}
    </UiCallout>
    <TaskTrackerCommandPanel
      :active-task-key="mapTaskKey"
      :task-options="taskSelectOptions"
      :view-mode="viewMode"
      :filters-active="filtersActive"
      :search="search"
      :workflow-filter="workflowFilter"
      :workflow-options="workflowOptions"
      :assignee-filter="assigneeFilter"
      :assignee-options="assigneeOptions"
      :task-rows-count="taskRows.length"
      :tasks-count="tasks.length"
      :task-available="Boolean(activeTaskRow)"
      :graph-summary="`${graphTicketStatLabel} · ${graphEdgeStatLabel}`"
      @task-select="selectMapTask"
      @update:view-mode="setViewMode"
      @update:search="navigate({ search: $event }, true)"
      @update:workflow-filter="navigate({ workflow: $event })"
      @update:assignee-filter="navigate({ assignee: $event })"
      @clear="clearFilters"
      @open-task-detail="openTaskDetail(mapTaskKey)"
    />
    <div
      v-if="viewMode === 'board' && (ticketStatusFilter !== 'all' || statusFilter !== 'all' || blockedFilter)"
      class="flex flex-wrap items-center gap-2"
    >
      <UiBadge v-if="ticketStatusFilter !== 'all'">
        Contains tickets: {{ trackerStatus[ticketStatusFilter].label }}
      </UiBadge>
      <UiBadge v-if="statusFilter !== 'all'">
        Task status: {{ trackerStatus[statusFilter].label }}
      </UiBadge>
      <UiBadge
        v-if="blockedFilter"
        tone="warning"
      >
        Has blocked tickets
      </UiBadge>
    </div>
    <UiLoadingState
      v-if="loading && !snapshot"
      label="Loading work…"
    />
    <div
      v-show="viewMode === 'board' && Boolean(snapshot)"
      class="tracker-board-workspace"
    >
      <p
        v-if="tasks.length === 0"
        class="text-sm text-fg-muted"
      >
        No tasks yet. Work tracked by agents will appear here.
      </p>
      <p
        v-else-if="taskRows.length === 0"
        class="text-sm text-fg-muted"
      >
        No tasks match these filters.
      </p>
      <TrackerBoardPanel
        :rows="taskRows"
        :selected-task-key="detailTaskKey"
        @open-task="openTaskDetail"
      />
    </div>
    <template v-if="viewMode === 'graph' && snapshot">
      <UiEmptyState
        v-if="!activeTaskRow"
        :title="mapTaskKey ? 'Task unavailable' : tasks.length ? 'Choose a task' : 'No tasks yet'"
        :description="mapTaskKey ? 'The selected task is no longer available in this project. Choose another task.' : 'Select a task to see its tickets and dependencies.'"
      />
      <template v-else>
        <TrackerWarningSummary :warnings="flow.warnings" />
        <TrackerGraphPanel
          :flow="flow"
          :loading="graphPending"
          :flow-id="`tracker-flow-${projectId}`"
          :flow-render-key="flowRenderKey"
          :graph-fit-on-init="graphFitOnInit"
          :focus-node-ids="graphFocusNodeIds"
          :primary-focus-node-id="primaryGraphFocusNodeId"
          :refocus-key="graphRefocusKey"
          :initial-viewport="graphViewport"
          :active-task-title="activeTaskRow.task.title"
          :active-task-available="true"
          :ticket-stat-label="graphTicketStatLabel"
          :edge-stat-label="graphEdgeStatLabel"
          :status-rows="graphStatusRows"
          :status-filters="graphStatusFilters"
          :block-rows="graphBlockRows"
          :block-filters="graphBlockFilters"
          :filters-active="graphFiltersActive"
          :selection-visible="graphSelectionVisible"
          :selection-label="graphSelectionLabel"
          :selected-ticket="graphSelectedTicket"
          :selected-edge-label="graphSelectedEdge?.label ?? null"
          :selection-stats="graphSelectionStats"
          @toggle-status="graph.toggleStatus"
          @toggle-block="graph.toggleBlock"
          @clear-filters="graph.clearFilters"
          @open-task-detail="openTaskDetail(mapTaskKey)"
          @node-click="graph.onNodeClick"
          @edge-click="graph.onEdgeClick"
          @pane-click="graph.onCanvasClick"
          @graph-canvas-click="graph.onCanvasClick"
          @open-selected-detail="graph.openSelectedDetail"
          @clear-graph-focus="graph.clearFocus"
          @viewport-change-end="graph.onViewportChange"
          @viewport-ready="graph.onViewportReady"
        />
      </template>
    </template>
    <TrackerTaskDetailDialog
      ref="taskDialog"
      :model-value="Boolean(detailTaskKey || detailTicketKey)"
      :task="detailTask"
      :tickets="detailTickets"
      :loading="detailLoading"
      :error="detailError"
      :ticket-key="detailTicketKey"
      :ticket="detailTicket"
      :can-go-back="Boolean(detailTaskKey)"
      :ticket-error="!detailLoading && !detailTicket ? detailError ?? 'This ticket is no longer available in the selected task.' : null"
      :refresh-content="refreshTaskContent"
      :contexts="taskContexts"
      :context-artifacts="taskContextArtifacts"
      :context-page-info="taskContextPageInfo"
      :context-artifact-page-info="taskContextArtifactPageInfo"
      :context-loading="taskContextLoading"
      :context-error="taskContextError"
      @update:model-value="!$event && navigate({ detail: undefined, ticket: undefined })"
      @view-dependencies="viewDependencies"
      @open-ticket="openTicket"
      @back-to-task="navigate({ ticket: undefined })"
      @retry="loadDetail(detailTaskKey)"
    />
    <TrackerTicketDetailPanel
      :model-value="viewMode === 'graph' && graphDetailPanelOpen && !detailTaskKey && !detailTicketKey"
      :ticket="graphSelectedTicket"
      :title="graphSelectedTicket?.title ?? 'Ticket details'"
      :description="graphSelectedTicket?.key"
      @update:model-value="graphDetailPanelOpen = $event"
    />
  </UiPageShell>
</template>

<style scoped>
.tracker-page-shell { display: flex; height: calc(100dvh - 48px); min-height: 540px; flex-direction: column; }
.tracker-board-workspace { display: flex; flex: 1 1 auto; min-height: 0; flex-direction: column; }
.tracker-page-shell > :deep(.tracker-flow-shell) { min-height: 360px; }
</style>
