<script setup lang="ts">
import { computed, provide, reactive, ref } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter, type LocationQuery, type LocationQueryRaw } from 'vue-router'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import { UiButton, UiCallout, UiEmptyState, UiLoadingState, UiPageShell, UiSegmentedControl } from '@/components/ui'
import { callOperation } from '@/lib/operations'
import { patchUsageSnapshot, patchUsageAfter, usageCheckpoint } from '@/lib/usageUpdates'
import type { UsageProjection } from '@/lib/usageUpdates'
import { resolveStatus } from '@/design/status'
import type { TrackerSnapshot, TrackerTask, TrackerTicket } from '@/lib/task-tracker/types'
import { isAutomaticDeliveryTask, taskDeliveryState } from '@/lib/task-tracker/delivery'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { usageFeedKey } from '@/composables/useUsageFeed'
import { useProjectScopedLoader } from '@/composables/useProjectScopedLoader'
import TaskTrackerCommandPanel from './task-tracker/TaskTrackerCommandPanel.vue'
import TrackerBoardPanel from './task-tracker/TrackerBoardPanel.vue'
import RunAbortDialog from '@/components/domain/RunAbortDialog.vue'
import { taskRunAbortTarget } from '@/composables/useRunAbort'
import TrackerGraphPanel from './task-tracker/TrackerGraphPanel.vue'
import TrackerTaskDetailDialog from './task-tracker/TrackerTaskDetailDialog.vue'
import TrackerTicketDetailPanel from './task-tracker/TrackerTicketDetailPanel.vue'
import TrackerWarningSummary from './task-tracker/TrackerWarningSummary.vue'
import { useTrackerExecutionContexts } from './task-tracker/useTrackerExecutionContexts'
import { useTrackerGraphSession } from './task-tracker/useTrackerGraphSession'
import { useTaskActivity } from './task-tracker/useTaskActivity'
import { useTaskResults } from './task-tracker/useTaskResults'
import { useStackOsResourcesStore } from '@/stores/stackosResources'
import type { LibrarySource } from '@/lib/librarySources'
import type { ViewMode } from './task-tracker/viewTypes'
import { buildAssigneeOptions, buildTaskProgressRows, buildWorkflowOptions, groupTicketsByTask, mergeFocusedTrackerSnapshot, projectBoardRows } from './task-tracker/viewModel'
import { boardFiltersFromEntry, clearBoardFilters, readBoardFilters, removeBoardFilter, saveBoardFilters, updateBoardDisplay, updateBoardFilter } from './task-tracker/boardFilters'
import type { BoardFilterClause, BoardFilterField, BoardFilterState } from './task-tracker/boardFilters'
import { useTaskBoardEdits } from './task-tracker/useTaskBoardEdits'

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
const boardFilters = ref(readBoardFilters(viewProjectId))
const viewOptions = [{ key: 'board', label: 'Board' }, { key: 'graph', label: 'Graph' }]
const detailTaskKey = computed(() => queryText(route.query.detail))
const detailTicketKey = computed(() => queryText(route.query.ticket))
const detailSnapshot = ref<TrackerSnapshot | null>(null)
const detailLoading = ref(false)
const detailError = ref<string | null>(null)
const taskDialog = ref<{ confirmLeave: () => boolean; syncTask: (task: TrackerTask | null) => void; prepareInspection: (ticketKey: string) => void } | null>(null)
const detailTask = computed(() => detailSnapshot.value?.tasks.find((task) => task.key === detailTaskKey.value) ?? null)
const detailTickets = computed(() => detailSnapshot.value?.tickets.filter((ticket) => ticket.task_key === detailTaskKey.value) ?? [])
const detailTicket = computed(() => detailTickets.value.find((ticket) => ticket.key === detailTicketKey.value) ?? null)
const inspectionTaskKey = computed(() => detailTask.value?.project_id === projectId.value ? detailTask.value.key : '')
const activity = reactive(useTaskActivity({ projectId, taskKey: inspectionTaskKey }))
const results = reactive(useTaskResults({ projectId, taskKey: inspectionTaskKey }))
const resultResources = useStackOsResourcesStore()
function inspectResult(source: LibrarySource): void {
  if (source.source_kind === 'resource_record') void resultResources.refreshSchemas(projectId.value)
}
const tasks = computed(() => snapshot.value?.tasks ?? [])
const ticketsByTask = computed(() => groupTicketsByTask(snapshot.value?.tickets ?? []))
const allTaskRows = computed(() => buildTaskProgressRows(tasks.value, ticketsByTask.value, { search: '', status: 'all', workflow: '', assignee: '' }))
const taskRows = computed(() => projectBoardRows(allTaskRows.value, boardFilters.value))
const boardEdits = useTaskBoardEdits({ rows: taskRows, projectId, currentScope, refresh: async (minRevision) => {
  if (!await load({ restartStream: false, quiet: true, minRevision })) throw new Error('Could not refresh the board.')
} })
const activeTaskRow = computed(() => allTaskRows.value.find((row) => row.key === mapTaskKey.value) ?? null)
const taskSelectOptions = computed(() => allTaskRows.value.map((row) => ({
  value: row.key, label: `#${row.id} ${row.task.title}`,
  rightLabel: resolveStatus('tracker', row.task.status).label,
  rightMeta: `${row.completedCount}/${row.totalCount} complete`,
  rightTone: resolveStatus('tracker', row.task.status).tone,
})))
const workflowOptions = computed(() => buildWorkflowOptions(tasks.value))
const assigneeOptions = computed(() => buildAssigneeOptions(tasks.value, snapshot.value?.tickets ?? []))
const liveTaskKey = computed(() => viewMode.value === 'graph' ? mapTaskKey.value : '')
const graph = useTrackerGraphSession({
  projectId, activeTaskKey: mapTaskKey, liveTaskKey, activeTaskRow, snapshot, routeError: error,
  reload: async (options) => { if (!await load(options)) throw new Error(error.value ?? 'Could not refresh work.') },
  refreshUsage,
})
provide(usageFeedKey, graph.feedState)
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
type LoadOptions = { refocus?: boolean; restartStream?: boolean; quiet?: boolean; minRevision?: number; taskKeys?: string[] }
interface LoadRequest { options: LoadOptions; obtainedRevision?: number; promise: Promise<boolean>; resolve: (result: boolean) => void }
let activeLoad: LoadRequest | null = null
let trailingLoad: LoadRequest | null = null
let loadController: AbortController | null = null
let requiredIndexRevision = 0
function refreshUsage(next?: UsageProjection): void {
  if (!currentScope() || !next) return
  patchUsageSnapshot(snapshot.value, next)
  patchUsageSnapshot(detailSnapshot.value, next)
  graph.patchUsage(next)
}

function currentScope(): boolean {
  return alive && projectId.value === viewProjectId && route.path === viewPath
}

function load(options: LoadOptions = {}): Promise<boolean> {
  if (!projectId.value || !currentScope()) return Promise.resolve(false)
  if (options.minRevision !== undefined) requiredIndexRevision = Math.max(requiredIndexRevision, options.minRevision)
  if (options.minRevision !== undefined && snapshot.value && snapshot.value.tracker.rev >= options.minRevision) {
    void refreshInspection(loadSequence, detailTaskKey.value, options.refocus, options.taskKeys, options.minRevision)
    return Promise.resolve(true)
  }
  // A failed read does not erase an accepted revision. Manual retry must
  // obtain it before dropping settled optimistic state.
  if (requiredIndexRevision > (snapshot.value?.tracker.rev ?? 0)) options = { ...options, minRevision: requiredIndexRevision }
  if (activeLoad) {
    // A known revision can reuse the obtained response, even if its read
    // began before the save/event. Only an older response needs another read.
    if (options.minRevision !== undefined) {
      activeLoad.options.minRevision = Math.max(activeLoad.options.minRevision ?? 0, options.minRevision)
      activeLoad.options.taskKeys = activeLoad.options.taskKeys && options.taskKeys
        ? [...new Set([...activeLoad.options.taskKeys, ...options.taskKeys])] : undefined
      if (!options.quiet) loading.value = true
      return activeLoad.promise
    }
    // Unversioned invalidations and explicit refresh still request latest data.
    if (!trailingLoad) trailingLoad = loadRequest(options)
    else trailingLoad.options = {
      refocus: trailingLoad.options.refocus || options.refocus,
      restartStream: trailingLoad.options.restartStream !== false || options.restartStream !== false,
      quiet: trailingLoad.options.quiet === true && options.quiet === true,
    }
    return trailingLoad.promise
  }
  const request = loadRequest(options)
  void runLoad(request)
  return request.promise
}

function loadRequest(options: LoadOptions): LoadRequest {
  let resolve!: (result: boolean) => void
  const promise = new Promise<boolean>(done => { resolve = done })
  return { options, promise, resolve }
}

async function runLoad(request: LoadRequest): Promise<void> {
  activeLoad = request
  let result = false
  try {
    result = await loadOnce(request)
    let previousRevision = -1
    while (result && request.options.minRevision !== undefined && (snapshot.value?.tracker.rev ?? -1) < request.options.minRevision) {
      if ((request.obtainedRevision ?? -1) <= previousRevision) {
        error.value = 'The board response is older than the saved change. Use Refresh to retry.'
        result = false
        break
      }
      previousRevision = request.obtainedRevision ?? -1
      result = await loadOnce(request)
    }
  } finally {
    activeLoad = null
    const next = trailingLoad
    trailingLoad = null
    request.resolve(result)
    // Resolve each batch independently so continuing live activity cannot
    // keep an acknowledged edit waiting for the stream to become quiet.
    if (next) {
      if (currentScope()) void runLoad(next)
      else next.resolve(false)
    }
  }
}

async function loadOnce(request: LoadRequest): Promise<boolean> {
  const options = request.options
  const sequence = ++loadSequence
  const usageVersion = usageCheckpoint()
  const controller = new AbortController()
  loadController = controller
  const timeout = setTimeout(() => controller.abort(new DOMException('Refresh timed out', 'TimeoutError')), 15000)
  if (!options.quiet || !snapshot.value) loading.value = true
  error.value = null
  try {
    const next = await callOperation<TrackerSnapshot>('tracker.get', {
      project_id: projectId.value, task_index_only: true, include_graph: false,
    }, { signal: controller.signal })
    if (controller.signal.aborted || !currentScope() || sequence !== loadSequence) return false
    request.obtainedRevision = next.tracker.rev
    // Do not publish a response known to predate the accepted mutation, or
    // refresh focused readers from it. runLoad obtains the required revision.
    if (options.minRevision !== undefined && next.tracker.rev < options.minRevision) return true
    patchUsageAfter(next, projectId.value, usageVersion)
    snapshot.value = next
    boardEdits.reconcile()
    if (viewMode.value === 'graph') mapTaskKey.value = resolveMapTaskKey()
    else if (!queryText(route.query.task)) mapTaskKey.value = validRememberedMapTask()
    // Start the stream while this accepted request still owns the current scope.
    // Awaited map/detail work must never restart a stream after scope disposal.
    if (options.restartStream !== false && !mapNeedsCanonicalRoute()) graph.restartStatusStream()
    const detailKey = detailTaskKey.value
    // Focused readers own their loading/error state. A slow inspector must
    // not hold the project refresh queue or a completed Board edit open.
    void refreshInspection(sequence, detailKey, options.refocus, options.taskKeys, options.minRevision)
    return currentScope() && sequence === loadSequence
  } catch (cause) {
    if (currentScope() && sequence === loadSequence) error.value = cause instanceof Error ? cause.message : 'Could not load work.'
    return false
  } finally {
    clearTimeout(timeout)
    if (loadController === controller) loadController = null
    if (currentScope() && sequence === loadSequence) loading.value = false
  }
}

async function refreshInspection(sequence: number, detailKey: string, refocus?: boolean, taskKeys?: string[], minRevision?: number): Promise<void> {
  try {
    if (viewMode.value === 'graph' && (!taskKeys?.length || taskKeys.includes(mapTaskKey.value))) await loadMap(refocus, minRevision)
    if (!currentScope() || sequence !== loadSequence) return
    if (detailKey && detailTaskKey.value === detailKey && (!taskKeys?.length || taskKeys.includes(detailKey))) await loadDetail(detailKey, minRevision)
  } catch (cause) {
    if (currentScope() && sequence === loadSequence) error.value = cause instanceof Error ? cause.message : 'Could not refresh task details.'
  }
}

async function loadMap(refocus?: boolean, minRevision?: number): Promise<void> {
  if (!currentScope() || !activeTaskRow.value || viewMode.value !== 'graph') return
  const taskKey = mapTaskKey.value
  try { window.sessionStorage.setItem(mapTaskStorageKey, mapTaskKey.value) } catch { /* Storage may be unavailable. */ }
  if (queryText(route.query.task) !== mapTaskKey.value) {
    await router.replace({ query: { ...route.query, task: mapTaskKey.value } })
    return
  }
  const restored = graph.restoreViewport()
  const focused = await graph.loadFocusedGraph(taskKey, { refocus: refocus ?? !restored, minRevision })
  if (!currentScope() || viewMode.value !== 'graph' || mapTaskKey.value !== taskKey || queryText(route.query.task) !== taskKey) return
  if (focused && snapshot.value) snapshot.value = mergeFocusedTrackerSnapshot(snapshot.value, focused)
}

let detailRead: { taskKey: string; promise: Promise<void> } | null = null
async function loadDetail(taskKey: string, minRevision?: number): Promise<void> {
  const covered = () => detailSnapshot.value?.tasks[0]?.key === taskKey && detailSnapshot.value.tracker.rev >= minRevision!
  if (minRevision !== undefined) {
    if (covered()) return
    while (detailRead?.taskKey === taskKey) {
      await detailRead.promise
      if (!currentScope() || detailTaskKey.value !== taskKey || covered()) return
    }
  }
  const read = { taskKey, promise: readDetail(taskKey, minRevision) }
  detailRead = read
  try { await read.promise } finally { if (detailRead === read) detailRead = null }
}

async function readDetail(taskKey: string, minRevision?: number): Promise<void> {
  if (!currentScope() || detailTaskKey.value !== taskKey) return
  const sequence = ++detailSequence
  const usageVersion = usageCheckpoint()
  detailLoading.value = true
  detailError.value = null
  if (detailSnapshot.value?.tasks[0]?.key !== taskKey) {
    detailSnapshot.value = null
    taskDialog.value?.syncTask?.(null)
    results.reset()
    activity.reset()
    void contexts.load(null)
  }
  try {
    const next = await callOperation<TrackerSnapshot>('tracker.get', {
      project_id: projectId.value, task_key: taskKey, include_graph: false,
    })
    if (!currentScope() || sequence !== detailSequence || detailTaskKey.value !== taskKey) return
    if (minRevision !== undefined && next.tracker.rev < minRevision) throw new Error('Task details are older than the change. Use Refresh to retry.')
    patchUsageAfter(next, projectId.value, usageVersion)
    const task = next.tasks.find((item) => item.key === taskKey)
    if (!task) throw new Error('This task is no longer available in this project.')
    detailSnapshot.value = next
    taskDialog.value?.syncTask?.(task)
    if (snapshot.value) snapshot.value = mergeFocusedTrackerSnapshot(snapshot.value, next)
    void contexts.load(task)
    void results.refresh()
    void activity.refresh()
  } catch (cause) {
    if (currentScope() && sequence === detailSequence) {
      detailError.value = cause instanceof Error ? cause.message : 'Could not load task details.'
    }
  } finally {
    if (currentScope() && sequence === detailSequence) detailLoading.value = false
  }
}

async function refreshTaskContent(taskKey: string): Promise<TrackerTask> {
  const usageVersion = usageCheckpoint()
  const [index, focused] = await Promise.all([
    callOperation<TrackerSnapshot>('tracker.get', {
      project_id: projectId.value, task_index_only: true, include_graph: false,
    }),
    callOperation<TrackerSnapshot>('tracker.get', {
      project_id: projectId.value, task_key: taskKey, include_graph: false,
    }),
  ])
  patchUsageAfter(index, projectId.value, usageVersion)
  patchUsageAfter(focused, projectId.value, usageVersion)
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
  setBoardFilters(clearBoardFilters(boardFilters.value))
}
function updateFilter(clause: BoardFilterClause): void {
  setBoardFilters(updateBoardFilter(boardFilters.value, clause))
}
function removeFilter(field: BoardFilterField): void {
  setBoardFilters(removeBoardFilter(boardFilters.value, field))
}
function updateDisplay(changes: Partial<Pick<BoardFilterState, 'completed' | 'hideEmptyColumns'>>): void {
  setBoardFilters(updateBoardDisplay(boardFilters.value, changes))
}
function setBoardFilters(state: BoardFilterState): void {
  if (!currentScope()) return
  boardFilters.value = state
  saveBoardFilters(viewProjectId, state)
}
function consumeBoardEntry(): void {
  if (!currentScope() || !('work_entry' in route.query)) return
  const state = boardFiltersFromEntry(route.query.work_entry)
  if (state) setBoardFilters(state)
  navigate({ work_entry: undefined }, true)
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
function isCurrentNavigation(query: LocationQuery): boolean {
  // Consuming the entry intent replaces the route without changing its inspection target.
  return currentScope() && ['view', 'task', 'detail', 'ticket'].every(key => queryText(route.query[key]) === queryText(query[key]))
}
const removeRouteListener = router.afterEach(async (to, from, failure) => {
  if (failure || to.path !== from.path || !currentScope()) return
  consumeBoardEntry()
  if (!snapshot.value) return
  const mapChanged = to.query.view !== from.query.view || to.query.task !== from.query.task
  if (viewMode.value === 'graph' && mapChanged) {
    mapTaskKey.value = resolveMapTaskKey()
  }
  if (mapChanged) graph.clearFocus()
  if (to.query.detail && to.query.detail !== from.query.detail) graphDetailPanelOpen.value = false
  if (mapChanged && !mapNeedsCanonicalRoute()) graph.restartStatusStream()
  if (viewMode.value === 'graph' && mapChanged) {
    await loadMap()
    if (!isCurrentNavigation(to.query)) return
  }
  if (viewMode.value === 'board' && from.query.view === 'graph') {
    await load({ restartStream: false })
    if (!isCurrentNavigation(to.query)) return
  }
  if (to.query.detail !== from.query.detail) {
    if (detailTaskKey.value) await loadDetail(detailTaskKey.value)
    else {
      detailSequence += 1
      detailSnapshot.value = null
      taskDialog.value?.syncTask?.(null)
      detailError.value = null
      detailLoading.value = false
      results.reset()
      activity.reset()
      void contexts.load(null)
    }
  }
})
consumeBoardEntry()
useProjectScopedLoader({
  projectId, load: () => load(),
  onScopeExit: () => {
    alive = false
    loadController?.abort()
    trailingLoad?.resolve(false)
    trailingLoad = null
    loading.value = false
    boardEdits.stop()
    loadSequence += 1
    detailSequence += 1
    results.reset()
    activity.reset()
    void contexts.load(null)
    graph.stopStatusStream()
    removeRouteListener()
  },
})
</script>

<template>
  <UiPageShell
    class="tracker-page-shell"
    :inert="detailTaskKey || detailTicketKey ? true : undefined"
    :aria-hidden="detailTaskKey || detailTicketKey ? true : undefined"
  >
    <RunAbortDialog />
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
        <UiSegmentedControl
          :model-value="viewMode"
          label="Work view"
          :options="viewOptions"
          @select="setViewMode(String($event) as ViewMode)"
        />
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
      :board-filters="boardFilters"
      :workflow-options="workflowOptions"
      :assignee-options="assigneeOptions"
      :task-rows-count="taskRows.length"
      :tasks-count="tasks.length"
      :index-available="Boolean(snapshot)"
      :task-available="Boolean(activeTaskRow)"
      :task-id="activeTaskRow?.id"
      :task-status="activeTaskRow?.task.status"
      :completed-count="activeTaskRow?.completedCount"
      :total-count="activeTaskRow?.totalCount"
      :active-execution="activeTaskRow?.task.active_execution"
      :task-agent-stats="activeTaskRow?.task.agent_stats"
      :task-automated="isAutomaticDeliveryTask(activeTaskRow?.task)"
      :task-delivery="taskDeliveryState(activeTaskRow?.task)"
      :abort-target="taskRunAbortTarget(activeTaskRow?.task)"
      @refresh="load()"
      @task-select="selectMapTask"
      @update:filter="updateFilter($event)"
      @remove-filter="removeFilter"
      @update:display="updateDisplay"
      @clear="clearFilters"
      @open-task-detail="openTaskDetail(mapTaskKey)"
    />
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
        :rows="boardEdits.rows.value"
        :edit-pending="boardEdits.pending.value"
        :edit-pending-columns="boardEdits.pendingColumns.value"
        :edit-error="boardEdits.error.value"
        :interaction-key="`${route.path}:${JSON.stringify(boardFilters)}:${viewMode}`"
        :hide-empty-columns="boardFilters.hideEmptyColumns"
        :selected-task-key="detailTaskKey"
        @open-task="openTaskDetail"
        @refresh="load()"
        @move="boardEdits.move"
        @priority="boardEdits.priority($event.taskKey, $event.priority)"
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
      :activity="activity"
      :results="results"
      :result-resources="resultResources.resources"
      :result-schema-loading="resultResources.schemaLoading"
      :result-schema-error="resultResources.schemaError"
      @update:model-value="!$event && navigate({ detail: undefined, ticket: undefined })"
      @view-dependencies="viewDependencies"
      @open-ticket="openTicket"
      @back-to-task="navigate({ ticket: undefined })"
      @retry="loadDetail(detailTaskKey)"
      @more-results="results.more"
      @retry-results="results.retry"
      @more-history="activity.more"
      @retry-history="activity.retry"
      @inspect-result="inspectResult"
    />
    <TrackerTicketDetailPanel
      :model-value="viewMode === 'graph' && graphDetailPanelOpen && !detailTaskKey && !detailTicketKey"
      :ticket="graphSelectedTicket"
      :parent-task="tasks.find(task => task.key === graphSelectedTicket?.task_key)"
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
