import { computed, ref, type ComputedRef, type Ref } from 'vue'
import type { Edge, EdgeMouseEvent, NodeMouseEvent } from '@vue-flow/core'

import { trackerStatus } from '@/design/status'
import { callOperation } from '@/lib/operations'
import { graphFocusFor, graphItemFromNodeId } from '@/lib/task-tracker/graphFocus'
import { buildTrackerFlowModel, type TrackerVueNodeData } from '@/lib/task-tracker/graphModel'
import { isTerminalTrackerStatus } from '@/lib/task-tracker/status'
import type {
  TrackerSelectedItem,
  TrackerSnapshot,
  TrackerStatus,
  TrackerTicket,
} from '@/lib/task-tracker/types'

import type { GraphBlockFilter, TaskProgressRow } from './viewTypes'
import { useTrackerGraphViewport } from './useTrackerGraphViewport'
import { useTrackerLiveUpdates } from './useTrackerLiveUpdates'
import {
  buildFilteredTrackerSnapshot,
  countStatuses,
  isGraphBlockedTicket,
  selectGraphVisibleTickets,
} from './viewModel'

interface TrackerReloadOptions {
  refocus?: boolean
  restartStream?: boolean
}

interface UseTrackerGraphSessionOptions {
  projectId: Readonly<Ref<number>>
  activeTaskKey: Ref<string>
  activeTaskRow: ComputedRef<TaskProgressRow | null>
  snapshot: Readonly<Ref<TrackerSnapshot | null>>
  routeError: Ref<string | null>
  reload: (options?: TrackerReloadOptions) => Promise<void>
}

export function useTrackerGraphSession(options: UseTrackerGraphSessionOptions) {
  const { projectId, activeTaskKey, activeTaskRow, snapshot, routeError, reload } = options
  const graphSnapshot = ref<TrackerSnapshot | null>(null)
  const loading = ref(false)
  const statusFilters = ref<TrackerStatus[]>([])
  const blockFilters = ref<GraphBlockFilter[]>([])
  const selected = ref<TrackerSelectedItem | null>(null)
  const selectedEdgeId = ref<string | null>(null)
  const selectedNodeFocusId = ref<string | null>(null)
  const detailPanelOpen = ref(false)
  let requestSeq = 0
  const {
    liveError,
    recentNodeIds,
    restart: restartStatusStream,
    stop: stopStatusStream,
  } = useTrackerLiveUpdates({
    projectId,
    activeTaskKey,
    reload: () => reload({ refocus: false, restartStream: false }),
  })

  const focusedGraph = computed(() => {
    const activeKey = activeTaskRow.value?.key
    if (
      activeKey &&
      graphSnapshot.value?.tasks.length === 1 &&
      graphSnapshot.value.tasks[0].key === activeKey
    ) {
      return graphSnapshot.value
    }
    return null
  })

  const tickets = computed(() => {
    if (focusedGraph.value) return focusedGraph.value.tickets
    return activeTaskRow.value?.tickets ?? []
  })

  const graphDataAvailable = computed(() => {
    const row = activeTaskRow.value
    if (!row) return false
    return Boolean(focusedGraph.value) || row.tickets.length > 0 || row.totalCount === 0
  })
  const statusCounts = computed(() => {
    if (graphDataAvailable.value) {
      return countStatuses(tickets.value.map((ticket) => ticket.status))
    }
    const counts = countStatuses([])
    const summary = activeTaskRow.value?.task.ticket_summary
    for (const status of Object.keys(trackerStatus) as TrackerStatus[]) {
      counts[status] = summary?.status_counts[status] ?? 0
    }
    return counts
  })
  const statusRows = computed(() =>
    (Object.entries(trackerStatus) as Array<[TrackerStatus, { label: string }]>).map(
      ([key, definition]) => ({ key, label: definition.label, count: statusCounts.value[key] }),
    ),
  )
  const blockedCount = computed(() =>
    graphDataAvailable.value
      ? tickets.value.filter((ticket) => isGraphBlockedTicket(ticket)).length
      : (activeTaskRow.value?.blockedCount ?? 0),
  )
  const graphTicketCount = computed(() =>
    graphDataAvailable.value ? tickets.value.length : (activeTaskRow.value?.totalCount ?? 0),
  )
  const blockRows = computed<Array<{ key: GraphBlockFilter; label: string; count: number }>>(() => [
    { key: 'blocked', label: 'Blocked', count: blockedCount.value },
    { key: 'open', label: 'Open', count: Math.max(0, graphTicketCount.value - blockedCount.value) },
  ])
  const filtersActive = computed(
    () => statusFilters.value.length > 0 || blockFilters.value.length > 0,
  )
  const visibleTickets = computed(() =>
    selectGraphVisibleTickets(tickets.value, {
      statuses: statusFilters.value,
      blocks: blockFilters.value,
    }),
  )
  const filteredSnapshot = computed<TrackerSnapshot | null>(() => {
    const activeTask = activeTaskRow.value?.task ?? null
    return buildFilteredTrackerSnapshot(
      snapshot.value,
      activeTask,
      visibleTickets.value,
      focusedGraph.value,
    )
  })
  const selectedTicket = computed(() =>
    selected.value?.kind === 'ticket'
      ? (tickets.value.find((ticket) => ticket.key === selected.value?.key) ?? null)
      : null,
  )
  const relationFocus = computed(() =>
    graphFocusFor(filteredSnapshot.value?.graph ?? null, {
      edgeId: selectedEdgeId.value,
      nodeId: selectedNodeFocusId.value,
    }),
  )
  const relationFocusActive = computed(
    () => relationFocus.value.edgeIds.size > 0 || relationFocus.value.nodeIds.size > 1,
  )
  const activeNodeIds = computed(() => {
    const nodeIds = new Set<string>()
    if (selectedNodeFocusId.value) nodeIds.add(selectedNodeFocusId.value)
    if (selectedTicket.value) nodeIds.add(`ticket:${selectedTicket.value.key}`)
    for (const ticket of tickets.value) {
      if (ticket.status === 'in-progress') nodeIds.add(`ticket:${ticket.key}`)
    }
    if (!nodeIds.size) {
      const firstOpenTicket = tickets.value.find(
        (ticket) => !isTerminalTrackerStatus(ticket.status) && !isGraphBlockedTicket(ticket),
      )
      if (firstOpenTicket) nodeIds.add(`ticket:${firstOpenTicket.key}`)
    }
    return nodeIds
  })
  const primaryFocusNodeId = computed(() => Array.from(activeNodeIds.value)[0] ?? null)
  const focusNodeIds = computed(() => {
    const graph = filteredSnapshot.value?.graph ?? null
    if (!graph) return []
    if (relationFocus.value.nodeIds.size) return Array.from(relationFocus.value.nodeIds)
    const primaryNodeId = primaryFocusNodeId.value
    if (!primaryNodeId) return []
    const focus = graphFocusFor(graph, { nodeId: primaryNodeId, edgeId: null })
    return Array.from(new Set([primaryNodeId, ...focus.nodeIds]))
  })
  const flowRenderKey = computed(() =>
    [
      activeTaskRow.value?.key ?? 'empty',
      statusFilters.value.join(',') || 'all-status',
      blockFilters.value.join(',') || 'all-block',
    ].join(':'),
  )
  const viewportStorageKey = computed(() =>
    [
      'stackos',
      'tracker-graph',
      projectId.value,
      activeTaskRow.value?.key ?? 'empty',
      statusFilters.value.join(',') || 'all-status',
      blockFilters.value.join(',') || 'all-block',
    ].join(':'),
  )
  const {
    viewport,
    refocusKey,
    requestRefocus,
    restore: restoreViewport,
    onReady: onViewportReady,
    onChange: onViewportChange,
  } = useTrackerGraphViewport({
    storageKey: viewportStorageKey,
    refocusScopeKey: flowRenderKey,
  })
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
          activeNodeIds: activeNodeIds.value,
          recentNodeIds: recentNodeIds.value,
          spotlight: relationFocusActive.value,
        })
      : { nodes: [], edges: [] as Edge[], warnings: [] },
  )
  const fitOnInit = computed(() => flow.value.nodes.length > 0 && flow.value.nodes.length <= 12)
  const ticketStatLabel = computed(() => {
    if (!graphDataAvailable.value) {
      const count = activeTaskRow.value?.totalCount ?? 0
      return `${count} indexed ${pluralize('ticket', count)}`
    }
    return filtersActive.value
      ? `${visibleTickets.value.length}/${tickets.value.length} tickets visible`
      : `${tickets.value.length} ${pluralize('ticket', tickets.value.length)}`
  })
  const edgeStatLabel = computed(() => {
    if (!graphDataAvailable.value) return 'relations pending'
    return filtersActive.value
      ? `${flow.value.edges.length} visible ${pluralize('relation', flow.value.edges.length)}`
      : `${flow.value.edges.length} ${pluralize('relation', flow.value.edges.length)}`
  })
  const selectedEdge = computed(
    () =>
      filteredSnapshot.value?.graph?.edges.find((edge) => edge.id === selectedEdgeId.value) ?? null,
  )
  const selectionLabel = computed(() => {
    if (selectedEdgeId.value) return 'Selected relation'
    if (selectedNodeFocusId.value || selectedTicket.value) return 'Selected ticket'
    return ''
  })
  const selectionVisible = computed(
    () =>
      Boolean(selectedTicket.value) || Boolean(selectedEdgeId.value || selectedNodeFocusId.value),
  )
  const selectionStats = computed(() => {
    const stats = []
    if (relationFocus.value.upstreamNodeIds.size) {
      stats.push(`${relationFocus.value.upstreamNodeIds.size} dependencies`)
    }
    if (relationFocus.value.downstreamNodeIds.size) {
      stats.push(`${relationFocus.value.downstreamNodeIds.size} unblocked next`)
    }
    return stats
  })

  async function loadFocusedGraph(
    taskKey: string,
    loadOptions: { refocus?: boolean } = {},
  ): Promise<TrackerSnapshot | null> {
    if (!validProjectId(projectId.value) || !taskKey) {
      graphSnapshot.value = null
      return null
    }
    const currentRequest = ++requestSeq
    loading.value = true
    try {
      const nextGraph = await callOperation<TrackerSnapshot>('tracker.get', {
        project_id: projectId.value,
        task_key: taskKey,
        include_graph: true,
      })
      if (currentRequest !== requestSeq) return null
      graphSnapshot.value = nextGraph
      if (loadOptions.refocus) requestRefocus()
      return nextGraph
    } catch (cause) {
      if (currentRequest === requestSeq) {
        graphSnapshot.value = null
        routeError.value = cause instanceof Error ? cause.message : 'failed to load task graph'
      }
      return null
    } finally {
      if (currentRequest === requestSeq) loading.value = false
    }
  }

  function toggleStatus(status: TrackerStatus): void {
    statusFilters.value = statusFilters.value.includes(status)
      ? statusFilters.value.filter((item) => item !== status)
      : [...statusFilters.value, status]
    refocusAfterFilterChange()
  }

  function toggleBlock(block: GraphBlockFilter): void {
    blockFilters.value = blockFilters.value.includes(block)
      ? blockFilters.value.filter((item) => item !== block)
      : [...blockFilters.value, block]
    refocusAfterFilterChange()
  }

  function clearFilters(): void {
    statusFilters.value = []
    blockFilters.value = []
    refocusAfterFilterChange()
  }

  function refocusAfterFilterChange(): void {
    if (!restoreViewport()) requestRefocus()
    clearFocus()
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
    if (target?.kind === 'ticket') selected.value = target
  }

  function onCanvasClick(event: MouseEvent): void {
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
    clearFocus()
  }

  function selectTicket(ticket: TrackerTicket, openDetail = true): void {
    selectedEdgeId.value = null
    selectedNodeFocusId.value = `ticket:${ticket.key}`
    selected.value = { kind: 'ticket', key: ticket.key }
    detailPanelOpen.value = openDetail
  }

  function openSelectedDetail(): void {
    if (selectedTicket.value) detailPanelOpen.value = true
  }

  function clearFocus(): void {
    selectedEdgeId.value = null
    selectedNodeFocusId.value = null
    selected.value = null
    detailPanelOpen.value = false
  }

  return {
    graphSnapshot,
    loading,
    graphDataAvailable,
    statusFilters,
    blockFilters,
    viewport,
    refocusKey,
    selected,
    selectedEdgeId,
    selectedNodeFocusId,
    detailPanelOpen,
    liveError,
    tickets,
    statusRows,
    blockRows,
    filtersActive,
    selectedTicket,
    flowRenderKey,
    flow,
    fitOnInit,
    focusNodeIds,
    primaryFocusNodeId,
    ticketStatLabel,
    edgeStatLabel,
    selectedEdge,
    selectionLabel,
    selectionVisible,
    selectionStats,
    loadFocusedGraph,
    toggleStatus,
    toggleBlock,
    clearFilters,
    restoreViewport,
    onViewportReady,
    onViewportChange,
    restartStatusStream,
    stopStatusStream,
    onNodeClick,
    onEdgeClick,
    onCanvasClick,
    selectTicket,
    openSelectedDetail,
    clearFocus,
  }
}

function validProjectId(projectId: number): boolean {
  return Boolean(projectId) && !Number.isNaN(projectId)
}

function pluralize(label: string, count: number): string {
  return count === 1 ? label : `${label}s`
}
