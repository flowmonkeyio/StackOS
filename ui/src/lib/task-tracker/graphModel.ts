import dagre, { Graph } from '@dagrejs/dagre'
import { MarkerType, Position, type Edge, type Node } from '@vue-flow/core'

import type {
  TrackerGraphEdge,
  TrackerGraphNode,
  TrackerSelectedItem,
  TrackerSnapshot,
  TrackerTask,
  TrackerTicket,
} from './types'
import { trackerStatusSortOrder } from './status'

export interface TrackerVueNodeData {
  label: string
  status: string
  laneKey: string
  priorityKey: string
  itemKind?: 'task' | 'ticket'
  itemKey: string
  subtitle?: string
  blockedBy?: string[]
  assignee?: string | null
  runPlanId?: number | null
  raw: TrackerTask | TrackerTicket | TrackerGraphNode
}

export interface BuildTrackerFlowOptions {
  selected?: TrackerSelectedItem | null
  showContainment?: boolean
  highlightedNodeIds?: Set<string>
  highlightedEdgeIds?: Set<string>
  selectedNodeId?: string | null
  upstreamNodeIds?: Set<string>
  upstreamEdgeIds?: Set<string>
  downstreamNodeIds?: Set<string>
  downstreamEdgeIds?: Set<string>
  activeEdgeId?: string | null
  spotlight?: boolean
}

export interface TrackerFlowModel {
  nodes: Node<TrackerVueNodeData>[]
  edges: Edge[]
  warnings: string[]
}

type EdgeFocusTone = 'default' | 'muted' | 'upstream' | 'downstream' | 'active' | 'highlighted'

const TASK_WIDTH = 336
const TICKET_WIDTH = 236
const TICKET_HEIGHT = 88
const TASK_GAP_X = 386
const TASK_GAP_Y = 322
const CHILD_GAP_X = 22
const CHILD_GAP_Y = 14
const DEPENDENCY_LAYER_GAP_X = 308
const DEPENDENCY_ROW_GAP_Y = 116
const DEPENDENCY_GROUP_GAP_Y = 320
const LINK_GAP_X = 176
const EDGE_STROKE_WIDTH = 1.2
const EDGE_MARKER_SIZE = 7

export function buildTrackerFlowModel(
  snapshot: TrackerSnapshot,
  options: BuildTrackerFlowOptions = {},
): TrackerFlowModel {
  const graph = snapshot.graph
  if (!graph?.nodes.length) {
    return {
      nodes: [],
      edges: [],
      warnings: graph?.warnings ?? ['Tracker graph projection is not available.'],
    }
  }

  const showContainment = options.showContainment === true
  const warnings = new Set(graph.warnings ?? [])
  const graphNodeById = new Map(graph.nodes.map((node) => [node.id, node]))

  if (!showContainment) {
    return buildDependencyTreeFlow(snapshot, options, warnings, graphNodeById)
  }

  const childrenByParent = groupGraphChildren(graph.nodes)
  const childIndexByNode = childIndexes(childrenByParent)
  const childColumnsByParent = childColumns(childrenByParent)
  const positions = new Map<string, { x: number; y: number }>()
  const nodes: Node<TrackerVueNodeData>[] = []

  const taskNodes = graph.nodes.filter((node) => node.type === 'task')
  taskNodes.forEach((graphNode, index) => {
    const column = index % 2
    const row = Math.floor(index / 2)
    const childCount = showContainment ? (childrenByParent.get(graphNode.id)?.length ?? 0) : 0
    const childColumns = childColumnsByParent.get(graphNode.id) ?? 1
    const childRows = Math.ceil(childCount / childColumns)
    const position = { x: column * TASK_GAP_X, y: row * TASK_GAP_Y }
    positions.set(graphNode.id, position)
    nodes.push({
      id: graphNode.id,
      type: 'task-group',
      position,
      draggable: true,
      selectable: true,
      style: {
        width: `${taskWidth(childColumns, childCount)}px`,
        height: `${taskHeight(childRows, childCount)}px`,
      },
      data: vueNodeData(graphNode, snapshot),
      class: nodeClass(options, graphNode.id),
      zIndex: nodeZIndex(options, graphNode.id),
    })
  })

  graph.nodes
    .filter((graphNode) => graphNode.type !== 'task')
    .forEach((graphNode, index) => {
      const parentPosition = graphNode.parent_id ? positions.get(graphNode.parent_id) : undefined
      const isContained = showContainment && graphNode.parent_id && parentPosition
      const position = isContained
        ? containedPosition(graphNode, childIndexByNode, childColumnsByParent)
        : { x: 0, y: index * DEPENDENCY_ROW_GAP_Y }
      positions.set(
        graphNode.id,
        isContained ? absolutePosition(parentPosition, position) : position,
      )
      nodes.push({
        id: graphNode.id,
        type: 'tracker-ticket',
        position,
        parentNode: isContained ? (graphNode.parent_id ?? undefined) : undefined,
        extent: isContained ? 'parent' : undefined,
        draggable: false,
        selectable: !graphNode.id.startsWith('link:'),
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        data: vueNodeData(graphNode, snapshot),
        class: nodeClass(options, graphNode.id),
        zIndex: nodeZIndex(options, graphNode.id),
      })
    })

  const nodeIds = new Set(nodes.map((node) => node.id))
  const edges = graph.edges.flatMap((edge) => {
    if (showContainment && edge.type === 'contains') return []
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) {
      warnings.add(`Graph projection referenced missing edge endpoint ${edge.id}`)
      return []
    }
    return [vueEdge(edge, options)]
  })

  for (const nodeId of nodeIds) {
    if (!graphNodeById.has(nodeId))
      warnings.add(`Graph projection referenced missing node ${nodeId}`)
  }

  return { nodes, edges, warnings: Array.from(warnings) }
}

function buildDependencyTreeFlow(
  snapshot: TrackerSnapshot,
  options: BuildTrackerFlowOptions,
  warnings: Set<string>,
  graphNodeById: Map<string, TrackerGraphNode>,
): TrackerFlowModel {
  const graph = snapshot.graph
  if (!graph) return { nodes: [], edges: [], warnings: Array.from(warnings) }

  const childrenByParent = groupGraphChildren(graph.nodes)
  const nodes: Node<TrackerVueNodeData>[] = []
  const positions = new Map<string, { x: number; y: number }>()
  const taskNodes = graph.nodes
    .filter((node) => node.type === 'task')
    .sort((a, b) => graphNodeOrder(a) - graphNodeOrder(b) || a.id.localeCompare(b.id))

  let nextGroupY = 0
  for (const taskNode of taskNodes) {
    const taskChildren = (childrenByParent.get(taskNode.id) ?? [])
      .filter((node) => !node.id.startsWith('link:'))
      .sort((a, b) => graphNodeOrder(a) - graphNodeOrder(b) || a.id.localeCompare(b.id))
    const layout = dependencyLayerLayout(taskChildren, graph.edges, nextGroupY, warnings)
    const bounds = layoutBounds(layout.positions)

    for (const graphNode of orderedLayoutNodes(taskChildren, layout.layers)) {
      const position =
        layout.positions.get(graphNode.id) ?? fallbackDependencyPosition(graphNode, nextGroupY)
      positions.set(graphNode.id, position)
      nodes.push({
        id: graphNode.id,
        type: 'tracker-ticket',
        position,
        draggable: false,
        selectable: true,
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        data: vueNodeData(graphNode, snapshot),
        class: nodeClass(options, graphNode.id),
        zIndex: nodeZIndex(options, graphNode.id),
      })
    }

    nextGroupY = Math.max(bounds.bottom, nextGroupY) + DEPENDENCY_GROUP_GAP_Y
  }

  const placedNodeIds = new Set(nodes.map((node) => node.id))
  const orphanNodes = graph.nodes
    .filter(
      (node) => node.type !== 'task' && !node.id.startsWith('link:') && !placedNodeIds.has(node.id),
    )
    .sort((a, b) => graphNodeOrder(a) - graphNodeOrder(b) || a.id.localeCompare(b.id))
  const orphanLayout = dependencyLayerLayout(orphanNodes, graph.edges, nextGroupY, warnings)
  for (const graphNode of orderedLayoutNodes(orphanNodes, orphanLayout.layers)) {
    const position =
      orphanLayout.positions.get(graphNode.id) ?? fallbackDependencyPosition(graphNode, nextGroupY)
    positions.set(graphNode.id, position)
    nodes.push({
      id: graphNode.id,
      type: 'tracker-ticket',
      position,
      draggable: false,
      selectable: true,
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      data: vueNodeData(graphNode, snapshot),
      class: nodeClass(options, graphNode.id),
      zIndex: nodeZIndex(options, graphNode.id),
    })
  }

  for (const graphNode of graph.nodes.filter((node) => node.id.startsWith('link:'))) {
    const targetEdge = graph.edges.find((edge) => edge.source === graphNode.id)
    const targetPosition = targetEdge ? positions.get(targetEdge.target) : undefined
    const position = targetPosition
      ? { x: targetPosition.x - LINK_GAP_X, y: targetPosition.y + 16 }
      : fallbackDependencyPosition(graphNode, nextGroupY)
    positions.set(graphNode.id, position)
    nodes.push({
      id: graphNode.id,
      type: 'tracker-ticket',
      position,
      draggable: false,
      selectable: false,
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      data: vueNodeData(graphNode, snapshot),
      class: nodeClass(options, graphNode.id),
      zIndex: nodeZIndex(options, graphNode.id),
    })
  }

  const nodeIds = new Set(nodes.map((node) => node.id))
  const edges = graph.edges.flatMap((edge) => {
    if (edge.type === 'contains') return []
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) {
      warnings.add(`Graph projection referenced missing edge endpoint ${edge.id}`)
      return []
    }
    return [vueEdge(edge, options)]
  })

  for (const nodeId of nodeIds) {
    if (!graphNodeById.has(nodeId))
      warnings.add(`Graph projection referenced missing node ${nodeId}`)
  }

  return { nodes, edges, warnings: Array.from(warnings) }
}

export function nodeId(kind: 'task' | 'ticket', key: string): string {
  return `${kind}:${key}`
}

function groupGraphChildren(nodes: TrackerGraphNode[]): Map<string, TrackerGraphNode[]> {
  const groups = new Map<string, TrackerGraphNode[]>()
  for (const node of nodes) {
    if (!node.parent_id) continue
    const children = groups.get(node.parent_id) ?? []
    children.push(node)
    groups.set(node.parent_id, children)
  }
  for (const children of groups.values()) {
    children.sort((a, b) => graphNodeOrder(a) - graphNodeOrder(b) || a.id.localeCompare(b.id))
  }
  return groups
}

function childIndexes(childrenByParent: Map<string, TrackerGraphNode[]>): Map<string, number> {
  const indexes = new Map<string, number>()
  for (const children of childrenByParent.values()) {
    children.forEach((node, index) => indexes.set(node.id, index))
  }
  return indexes
}

function childColumns(childrenByParent: Map<string, TrackerGraphNode[]>): Map<string, number> {
  const columns = new Map<string, number>()
  for (const [parentId, children] of childrenByParent.entries()) {
    columns.set(parentId, childColumnCount(children.length))
  }
  return columns
}

function childColumnCount(count: number): number {
  if (count <= 2) return 1
  if (count <= 8) return 2
  if (count <= 21) return 3
  if (count <= 48) return 4
  return 5
}

function taskWidth(childColumns: number, childCount: number): number {
  if (childCount === 0) return TASK_WIDTH
  return Math.max(TASK_WIDTH, 44 + childColumns * TICKET_WIDTH + (childColumns - 1) * CHILD_GAP_X)
}

function taskHeight(childRows: number, childCount: number): number {
  if (childCount === 0) return 180
  return Math.max(180, 102 + childRows * TICKET_HEIGHT + (childRows - 1) * CHILD_GAP_Y)
}

function containedPosition(
  graphNode: TrackerGraphNode,
  childIndexByNode: Map<string, number>,
  childColumnsByParent: Map<string, number>,
): { x: number; y: number } {
  const index = childIndexByNode.get(graphNode.id) ?? 0
  const columns = graphNode.parent_id ? (childColumnsByParent.get(graphNode.parent_id) ?? 1) : 1
  const column = index % columns
  const row = Math.floor(index / columns)
  return {
    x: 22 + column * (TICKET_WIDTH + CHILD_GAP_X),
    y: 78 + row * (TICKET_HEIGHT + CHILD_GAP_Y),
  }
}

function absolutePosition(
  parent: { x: number; y: number },
  child: { x: number; y: number },
): { x: number; y: number } {
  return { x: parent.x + child.x, y: parent.y + child.y }
}

function graphNodeOrder(node: TrackerGraphNode): number {
  const order = node.data.order_index
  return typeof order === 'number' ? order : Number.MAX_SAFE_INTEGER
}

function dependencyLayerLayout(
  graphNodes: TrackerGraphNode[],
  edges: TrackerGraphEdge[],
  startY: number,
  warnings: Set<string>,
): { positions: Map<string, { x: number; y: number }>; layers: TrackerGraphNode[][] } {
  const dagreLayout = dagreDependencyLayout(graphNodes, edges, startY, warnings)
  if (dagreLayout) return dagreLayout

  const nodeIds = new Set(graphNodes.map((node) => node.id))
  const rankById = dependencyRanks(graphNodes, edges, warnings)
  const layersByRank = new Map<number, TrackerGraphNode[]>()
  for (const graphNode of graphNodes) {
    const rank = rankById.get(graphNode.id) ?? 0
    const layer = layersByRank.get(rank) ?? []
    layer.push(graphNode)
    layersByRank.set(rank, layer)
  }

  const ranks = Array.from(layersByRank.keys()).sort((a, b) => a - b)
  const predecessorOrder = new Map<string, number>()
  const dependencyEdges = edges.filter(
    (edge) => edge.type === 'dependency' && nodeIds.has(edge.source) && nodeIds.has(edge.target),
  )
  const layers = ranks.map((rank) => {
    const layer = layersByRank.get(rank) ?? []
    layer.sort((a, b) => {
      const aPredecessorOrder = averagePredecessorOrder(a.id, dependencyEdges, predecessorOrder)
      const bPredecessorOrder = averagePredecessorOrder(b.id, dependencyEdges, predecessorOrder)
      return (
        aPredecessorOrder - bPredecessorOrder ||
        statusSort(a.status) - statusSort(b.status) ||
        graphNodeOrder(a) - graphNodeOrder(b) ||
        a.id.localeCompare(b.id)
      )
    })
    layer.forEach((node, index) => predecessorOrder.set(node.id, index))
    return layer
  })

  const positions = new Map<string, { x: number; y: number }>()
  for (let column = 0; column < layers.length; column += 1) {
    const layer = layers[column]
    for (let row = 0; row < layer.length; row += 1) {
      positions.set(layer[row].id, {
        x: column * DEPENDENCY_LAYER_GAP_X,
        y: startY + row * DEPENDENCY_ROW_GAP_Y,
      })
    }
  }
  return { positions, layers }
}

function dagreDependencyLayout(
  graphNodes: TrackerGraphNode[],
  edges: TrackerGraphEdge[],
  startY: number,
  warnings: Set<string>,
): { positions: Map<string, { x: number; y: number }>; layers: TrackerGraphNode[][] } | null {
  if (!graphNodes.length) return { positions: new Map(), layers: [] }

  try {
    const nodeIds = new Set(graphNodes.map((node) => node.id))
    const graph = new Graph({ multigraph: true })
    graph.setGraph({
      rankdir: 'LR',
      nodesep: 30,
      ranksep: 90,
      edgesep: 12,
      marginx: 24,
      marginy: 24,
    })
    graph.setDefaultEdgeLabel(() => ({}))

    for (const graphNode of graphNodes) {
      graph.setNode(graphNode.id, {
        width: TICKET_WIDTH,
        height: TICKET_HEIGHT,
      })
    }

    edges
      .filter(
        (edge) =>
          edge.type === 'dependency' && nodeIds.has(edge.source) && nodeIds.has(edge.target),
      )
      .forEach((edge, index) => {
        graph.setEdge(edge.source, edge.target, { weight: 1 }, `dependency:${index}`)
      })

    dagre.layout(graph)

    const rawPositions = graphNodes.map((graphNode) => {
      const position = graph.node(graphNode.id)
      return {
        id: graphNode.id,
        x: Number.isFinite(position?.x) ? Number(position.x) - TICKET_WIDTH / 2 : 0,
        y: Number.isFinite(position?.y) ? Number(position.y) - TICKET_HEIGHT / 2 : 0,
      }
    })
    const minX = Math.min(...rawPositions.map((position) => position.x))
    const minY = Math.min(...rawPositions.map((position) => position.y))
    const positions = new Map<string, { x: number; y: number }>()
    for (const position of rawPositions) {
      positions.set(position.id, {
        x: position.x - minX,
        y: startY + position.y - minY,
      })
    }

    return { positions, layers: layeredNodesFromPositions(graphNodes, positions) }
  } catch {
    warnings.add('Dagre dependency layout failed; using deterministic fallback layout.')
    return null
  }
}

function layeredNodesFromPositions(
  graphNodes: TrackerGraphNode[],
  positions: Map<string, { x: number; y: number }>,
): TrackerGraphNode[][] {
  const layersByX = new Map<number, TrackerGraphNode[]>()
  for (const graphNode of graphNodes) {
    const position = positions.get(graphNode.id)
    const rank = Math.round((position?.x ?? 0) / DEPENDENCY_LAYER_GAP_X)
    const layer = layersByX.get(rank) ?? []
    layer.push(graphNode)
    layersByX.set(rank, layer)
  }

  return Array.from(layersByX.entries())
    .sort(([a], [b]) => a - b)
    .map(([, layer]) =>
      layer.sort((a, b) => {
        const aPosition = positions.get(a.id)
        const bPosition = positions.get(b.id)
        return (
          (aPosition?.y ?? 0) - (bPosition?.y ?? 0) ||
          graphNodeOrder(a) - graphNodeOrder(b) ||
          a.id.localeCompare(b.id)
        )
      }),
    )
}

function orderedLayoutNodes(
  graphNodes: TrackerGraphNode[],
  layers: TrackerGraphNode[][],
): TrackerGraphNode[] {
  const ordered = layers.flat()
  const orderedIds = new Set(ordered.map((node) => node.id))
  return [...ordered, ...graphNodes.filter((node) => !orderedIds.has(node.id))]
}

function dependencyRanks(
  graphNodes: TrackerGraphNode[],
  edges: TrackerGraphEdge[],
  warnings: Set<string>,
): Map<string, number> {
  const nodeIds = new Set(graphNodes.map((node) => node.id))
  const outgoing = new Map<string, string[]>()
  const incomingCount = new Map<string, number>()
  const ranks = new Map<string, number>()
  for (const node of graphNodes) {
    outgoing.set(node.id, [])
    incomingCount.set(node.id, 0)
    ranks.set(node.id, 0)
  }

  for (const edge of edges) {
    if (edge.type !== 'dependency' || !nodeIds.has(edge.source) || !nodeIds.has(edge.target))
      continue
    outgoing.get(edge.source)?.push(edge.target)
    incomingCount.set(edge.target, (incomingCount.get(edge.target) ?? 0) + 1)
  }

  const queue = graphNodes
    .filter((node) => (incomingCount.get(node.id) ?? 0) === 0)
    .sort((a, b) => graphNodeOrder(a) - graphNodeOrder(b) || a.id.localeCompare(b.id))
  let visited = 0

  while (queue.length) {
    const current = queue.shift()
    if (!current) continue
    visited += 1
    for (const targetId of outgoing.get(current.id) ?? []) {
      ranks.set(targetId, Math.max(ranks.get(targetId) ?? 0, (ranks.get(current.id) ?? 0) + 1))
      incomingCount.set(targetId, (incomingCount.get(targetId) ?? 1) - 1)
      if ((incomingCount.get(targetId) ?? 0) === 0) {
        const targetNode = graphNodes.find((node) => node.id === targetId)
        if (targetNode) queue.push(targetNode)
      }
    }
    queue.sort((a, b) => graphNodeOrder(a) - graphNodeOrder(b) || a.id.localeCompare(b.id))
  }

  if (visited < graphNodes.length) {
    warnings.add(
      'Dependency graph contains a cycle or unresolved dependency; cyclic nodes were placed by ticket order.',
    )
    graphNodes
      .filter((node) => (incomingCount.get(node.id) ?? 0) > 0)
      .forEach((node, index) => {
        ranks.set(node.id, Math.max(ranks.get(node.id) ?? 0, index))
      })
  }

  return ranks
}

function averagePredecessorOrder(
  nodeId: string,
  dependencyEdges: TrackerGraphEdge[],
  predecessorOrder: Map<string, number>,
): number {
  const orders = dependencyEdges
    .filter((edge) => edge.target === nodeId)
    .map((edge) => predecessorOrder.get(edge.source))
    .filter((value): value is number => typeof value === 'number')
  if (!orders.length) return Number.MAX_SAFE_INTEGER
  return orders.reduce((sum, value) => sum + value, 0) / orders.length
}

function layoutBounds(positions: Map<string, { x: number; y: number }>): {
  width: number
  bottom: number
} {
  if (!positions.size) return { width: 0, bottom: 0 }
  let maxX = 0
  let maxY = 0
  for (const position of positions.values()) {
    maxX = Math.max(maxX, position.x + TICKET_WIDTH)
    maxY = Math.max(maxY, position.y + TICKET_HEIGHT)
  }
  return { width: maxX, bottom: maxY }
}

function fallbackDependencyPosition(
  graphNode: TrackerGraphNode,
  startY: number,
): { x: number; y: number } {
  return {
    x: 0,
    y: startY + graphNodeOrder(graphNode) * DEPENDENCY_ROW_GAP_Y,
  }
}

function statusSort(status: string): number {
  return trackerStatusSortOrder(status)
}

function vueNodeData(graphNode: TrackerGraphNode, snapshot: TrackerSnapshot): TrackerVueNodeData {
  const itemKind = itemKindForNodeId(graphNode.id)
  const itemKey = itemKeyForNodeId(graphNode.id) ?? graphNode.id
  const raw = rawForGraphNode(graphNode, snapshot) ?? graphNode
  const ticket = isTrackerTicket(raw) ? raw : null
  const task = isTrackerTask(raw) ? raw : null
  return {
    label: graphNode.label,
    status: graphNode.status,
    laneKey: graphNode.lane_key,
    priorityKey: graphNode.priority_key,
    itemKind,
    itemKey,
    subtitle:
      task?.goal ||
      task?.task_type ||
      ticket?.goal ||
      ticket?.source_kind ||
      linkSubtitle(graphNode),
    blockedBy: ticket?.blocked_by,
    assignee: ticket?.assignee,
    runPlanId: ticket?.run_plan_id,
    raw,
  }
}

function vueEdge(edge: TrackerGraphEdge, options: BuildTrackerFlowOptions): Edge {
  const edgeTone = edgeFocusTone(edge, options)
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    sourceHandle: 'out',
    targetHandle: 'in',
    type: 'default',
    animated: false,
    label: edge.type === 'link' ? (edge.label ?? undefined) : undefined,
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: edgeMarkerColor(edgeTone),
      width: EDGE_MARKER_SIZE,
      height: EDGE_MARKER_SIZE,
    },
    class: edgeClass(edge, options),
    style: edgeStyle(edgeTone),
    interactionWidth: edgeTone === 'muted' ? 8 : 18,
    zIndex: edgeZIndex(edgeTone),
    data: { kind: edge.type, tone: edgeTone },
  }
}

function rawForGraphNode(
  graphNode: TrackerGraphNode,
  snapshot: TrackerSnapshot,
): TrackerTask | TrackerTicket | null {
  const key = itemKeyForNodeId(graphNode.id)
  if (!key) return null
  if (graphNode.id.startsWith('task:')) {
    return snapshot.tasks.find((task) => task.key === key) ?? null
  }
  if (graphNode.id.startsWith('ticket:')) {
    return snapshot.tickets.find((ticket) => ticket.key === key) ?? null
  }
  return null
}

function itemKindForNodeId(id: string): 'task' | 'ticket' | undefined {
  if (id.startsWith('task:')) return 'task'
  if (id.startsWith('ticket:')) return 'ticket'
  return undefined
}

function itemKeyForNodeId(id: string): string | null {
  const index = id.indexOf(':')
  return index >= 0 ? id.slice(index + 1) : null
}

function nodeClass(options: BuildTrackerFlowOptions, graphNodeId: string): string {
  const kind = itemKindForNodeId(graphNodeId)
  const key = itemKeyForNodeId(graphNodeId)
  const classes = []
  const selectedByItem = Boolean(
    kind && key && options.selected?.kind === kind && options.selected.key === key,
  )
  const selectedByGraph = options.selectedNodeId === graphNodeId
  const selected = selectedByItem || selectedByGraph
  const upstream = options.upstreamNodeIds?.has(graphNodeId) === true
  const downstream = options.downstreamNodeIds?.has(graphNodeId) === true
  const highlighted =
    selected || upstream || downstream || options.highlightedNodeIds?.has(graphNodeId) === true
  if (options.spotlight) {
    classes.push(highlighted ? 'tracker-node-highlighted' : 'tracker-node-muted')
  }
  if (upstream) classes.push('tracker-node-upstream')
  if (downstream) classes.push('tracker-node-downstream')
  if (selected) classes.push('tracker-node-selected')
  return classes.join(' ')
}

function nodeZIndex(options: BuildTrackerFlowOptions, graphNodeId: string): number {
  const kind = itemKindForNodeId(graphNodeId)
  const key = itemKeyForNodeId(graphNodeId)
  const selectedByItem = Boolean(
    kind && key && options.selected?.kind === kind && options.selected.key === key,
  )
  const selected = selectedByItem || options.selectedNodeId === graphNodeId
  if (selected) return 30
  if (
    options.upstreamNodeIds?.has(graphNodeId) === true ||
    options.downstreamNodeIds?.has(graphNodeId) === true ||
    options.highlightedNodeIds?.has(graphNodeId) === true
  ) {
    return 24
  }
  return 10
}

function edgeClass(edge: TrackerGraphEdge, options: BuildTrackerFlowOptions): string {
  const classes = ['tracker-edge', `tracker-edge-${edge.type}`]
  const upstream = options.upstreamEdgeIds?.has(edge.id) === true
  const downstream = options.downstreamEdgeIds?.has(edge.id) === true
  const highlighted = upstream || downstream || options.highlightedEdgeIds?.has(edge.id) === true
  if (options.spotlight) {
    classes.push(highlighted ? 'tracker-edge-highlighted' : 'tracker-edge-muted')
  }
  if (upstream) classes.push('tracker-edge-upstream')
  if (downstream) classes.push('tracker-edge-downstream')
  if (options.activeEdgeId === edge.id) classes.push('tracker-edge-active')
  return classes.join(' ')
}

function edgeFocusTone(edge: TrackerGraphEdge, options: BuildTrackerFlowOptions): EdgeFocusTone {
  if (options.activeEdgeId === edge.id) return 'active'
  if (options.upstreamEdgeIds?.has(edge.id)) return 'upstream'
  if (options.downstreamEdgeIds?.has(edge.id)) return 'downstream'
  if (!options.spotlight) return 'default'
  if (options.highlightedEdgeIds?.has(edge.id)) return 'highlighted'
  return 'muted'
}

function edgeMarkerColor(tone: EdgeFocusTone): string {
  switch (tone) {
    case 'active':
      return 'var(--color-accent-primary)'
    case 'upstream':
      return 'var(--color-warning-default)'
    case 'downstream':
      return 'var(--color-success-default)'
    case 'muted':
      return 'color-mix(in srgb, var(--color-border-strong) 42%, var(--color-bg-surface))'
    default:
      return 'var(--color-border-strong)'
  }
}

function edgeStyle(tone: EdgeFocusTone): Record<string, string | number> | undefined {
  switch (tone) {
    case 'active':
      return {
        stroke: 'var(--color-accent-primary)',
        strokeWidth: EDGE_STROKE_WIDTH,
        strokeLinecap: 'round',
        strokeLinejoin: 'round',
        opacity: 1,
      }
    case 'upstream':
      return {
        stroke: 'var(--color-warning-default)',
        strokeWidth: EDGE_STROKE_WIDTH,
        strokeLinecap: 'round',
        strokeLinejoin: 'round',
        opacity: 1,
      }
    case 'downstream':
      return {
        stroke: 'var(--color-success-default)',
        strokeWidth: EDGE_STROKE_WIDTH,
        strokeLinecap: 'round',
        strokeLinejoin: 'round',
        opacity: 1,
      }
    case 'highlighted':
      return {
        stroke: 'var(--color-border-strong)',
        strokeWidth: EDGE_STROKE_WIDTH,
        strokeLinecap: 'round',
        strokeLinejoin: 'round',
        opacity: 1,
      }
    case 'muted':
      return {
        stroke: 'color-mix(in srgb, var(--color-border-strong) 42%, var(--color-bg-surface))',
        strokeWidth: EDGE_STROKE_WIDTH,
        opacity: 0.24,
      }
    default:
      return {
        stroke: 'var(--color-border-strong)',
        strokeWidth: EDGE_STROKE_WIDTH,
        strokeLinecap: 'round',
        strokeLinejoin: 'round',
        opacity: 0.86,
      }
  }
}

function edgeZIndex(tone: EdgeFocusTone): number {
  switch (tone) {
    case 'active':
      return 6
    case 'upstream':
    case 'downstream':
      return 5
    case 'highlighted':
      return 4
    case 'muted':
      return 0
    default:
      return 1
  }
}

function linkSubtitle(graphNode: TrackerGraphNode): string | undefined {
  const linkKind = graphNode.data.link_kind
  if (typeof linkKind === 'string' && linkKind) return linkKind
  const ref = graphNode.data.ref
  return typeof ref === 'string' && ref ? ref : undefined
}

function isTrackerTask(
  value: TrackerTask | TrackerTicket | TrackerGraphNode,
): value is TrackerTask {
  return 'task_type' in value
}

function isTrackerTicket(
  value: TrackerTask | TrackerTicket | TrackerGraphNode,
): value is TrackerTicket {
  return 'task_key' in value && 'blocked_by' in value
}
