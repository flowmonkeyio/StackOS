import type { TrackerGraph, TrackerGraphEdge, TrackerSelectedItem, TrackerSnapshot } from './types'

export type TrackerGraphShape = NonNullable<TrackerSnapshot['graph']>

export interface GraphFocus {
  nodeIds: Set<string>
  edgeIds: Set<string>
  selectedNodeId: string | null
  upstreamNodeIds: Set<string>
  upstreamEdgeIds: Set<string>
  downstreamNodeIds: Set<string>
  downstreamEdgeIds: Set<string>
  activeEdgeId: string | null
}

export function graphFocusFor(
  graph: TrackerGraph | null,
  target: { edgeId: string | null; nodeId: string | null },
): GraphFocus {
  if (!graph) return emptyGraphFocus()
  if (target.edgeId) return edgeFocusFor(graph, target.edgeId)
  if (target.nodeId) return nodeFocusFor(graph, target.nodeId)
  return emptyGraphFocus()
}

export function graphItemFromNodeId(nodeId: string): TrackerSelectedItem | null {
  const separatorIndex = nodeId.indexOf(':')
  if (separatorIndex < 0) return null
  const kind = nodeId.slice(0, separatorIndex)
  const key = nodeId.slice(separatorIndex + 1)
  return kind === 'task' || kind === 'ticket' ? { kind, key } : null
}

function emptyGraphFocus(
  activeEdgeId: string | null = null,
  selectedNodeId: string | null = null,
): GraphFocus {
  const focus: GraphFocus = {
    nodeIds: new Set(),
    edgeIds: new Set(),
    selectedNodeId,
    upstreamNodeIds: new Set(),
    upstreamEdgeIds: new Set(),
    downstreamNodeIds: new Set(),
    downstreamEdgeIds: new Set(),
    activeEdgeId,
  }
  if (selectedNodeId) focus.nodeIds.add(selectedNodeId)
  return focus
}

function edgeFocusFor(graph: TrackerGraphShape, edgeId: string): GraphFocus {
  const selectedEdge = graph.edges.find((edge) => edge.id === edgeId)
  if (!selectedEdge) return emptyGraphFocus()

  const selectedNodeId = selectedEdge.type === 'dependency' ? selectedEdge.target : null
  const focus = emptyGraphFocus(edgeId, selectedNodeId)
  if (selectedEdge.type === 'dependency') {
    addDependencyContext(graph, focus, selectedNodeId ?? selectedEdge.target)
  } else {
    addGraphEdge(focus, selectedEdge)
  }
  return focus
}

function nodeFocusFor(graph: TrackerGraphShape, nodeId: string): GraphFocus {
  const focus = emptyGraphFocus(null, nodeId)
  const graphNode = graph.nodes.find((node) => node.id === nodeId)
  if (!graphNode || nodeId.startsWith('link:')) return focus

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

  addDependencyContext(graph, focus, nodeId)

  for (const edge of graph.edges) {
    if (edge.type !== 'dependency' && (edge.source === nodeId || edge.target === nodeId)) {
      addGraphEdge(focus, edge)
    }
  }
  return focus
}

function addDependencyContext(graph: TrackerGraphShape, focus: GraphFocus, nodeId: string): void {
  const { incoming, outgoing } = dependencyMaps(graph)
  const maxDepth = 2

  function collectUpstream(nodeId: string, depth: number, visited: Set<string>): void {
    if (depth >= maxDepth) return
    for (const edge of incoming.get(nodeId) ?? []) {
      if (focus.edgeIds.has(edge.id)) continue
      addUpstreamGraphEdge(focus, edge)
      if (visited.has(edge.source)) continue
      visited.add(edge.source)
      collectUpstream(edge.source, depth + 1, visited)
    }
  }

  function collectDownstream(nodeId: string, depth: number, visited: Set<string>): void {
    if (depth >= maxDepth) return
    for (const edge of outgoing.get(nodeId) ?? []) {
      if (focus.edgeIds.has(edge.id)) continue
      addDownstreamGraphEdge(focus, edge)
      if (visited.has(edge.target)) continue
      visited.add(edge.target)
      collectDownstream(edge.target, depth + 1, visited)
    }
  }

  collectUpstream(nodeId, 0, new Set([nodeId]))
  collectDownstream(nodeId, 0, new Set([nodeId]))
}

function dependencyMaps(graph: TrackerGraphShape): {
  incoming: Map<string, TrackerGraphEdge[]>
  outgoing: Map<string, TrackerGraphEdge[]>
} {
  const incoming = new Map<string, TrackerGraphEdge[]>()
  const outgoing = new Map<string, TrackerGraphEdge[]>()
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

function addGraphEdge(focus: GraphFocus, edge: TrackerGraphEdge): void {
  focus.edgeIds.add(edge.id)
  focus.nodeIds.add(edge.source)
  focus.nodeIds.add(edge.target)
}

function addUpstreamGraphEdge(focus: GraphFocus, edge: TrackerGraphEdge): void {
  addGraphEdge(focus, edge)
  focus.upstreamEdgeIds.add(edge.id)
  if (edge.source !== focus.selectedNodeId) focus.upstreamNodeIds.add(edge.source)
  if (edge.target !== focus.selectedNodeId) focus.upstreamNodeIds.add(edge.target)
}

function addDownstreamGraphEdge(focus: GraphFocus, edge: TrackerGraphEdge): void {
  addGraphEdge(focus, edge)
  focus.downstreamEdgeIds.add(edge.id)
  if (edge.source !== focus.selectedNodeId) focus.downstreamNodeIds.add(edge.source)
  if (edge.target !== focus.selectedNodeId) focus.downstreamNodeIds.add(edge.target)
}
