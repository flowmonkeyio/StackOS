import { describe, expect, it } from 'vitest'

import { graphFocusFor, graphItemFromNodeId } from './graphFocus'
import type { TrackerGraph } from './types'

const graph: TrackerGraph = {
  nodes: [
    node('ticket:setup'),
    node('ticket:build'),
    node('ticket:verify'),
    node('ticket:ship'),
  ],
  edges: [
    dependency('dependency:setup:build', 'ticket:setup', 'ticket:build'),
    dependency('dependency:build:verify', 'ticket:build', 'ticket:verify'),
    dependency('dependency:verify:ship', 'ticket:verify', 'ticket:ship'),
  ],
  warnings: [],
  layout_hints: { direction: 'LR' },
}

describe('graphFocusFor', () => {
  it('marks all upstream dependencies and only the next downstream unblocker for a node', () => {
    const focus = graphFocusFor(graph, { nodeId: 'ticket:verify', edgeId: null })

    expect([...focus.upstreamNodeIds].sort()).toEqual(['ticket:build', 'ticket:setup'])
    expect([...focus.upstreamEdgeIds].sort()).toEqual([
      'dependency:build:verify',
      'dependency:setup:build',
    ])
    expect([...focus.downstreamNodeIds]).toEqual(['ticket:ship'])
    expect([...focus.downstreamEdgeIds]).toEqual(['dependency:verify:ship'])
    expect(focus.selectedNodeId).toBe('ticket:verify')
  })

  it('uses a clicked dependency edge as the active relation and selected target context', () => {
    const focus = graphFocusFor(graph, { nodeId: null, edgeId: 'dependency:build:verify' })

    expect(focus.activeEdgeId).toBe('dependency:build:verify')
    expect(focus.selectedNodeId).toBe('ticket:verify')
    expect(focus.edgeIds.has('dependency:build:verify')).toBe(true)
    expect(focus.upstreamNodeIds.has('ticket:setup')).toBe(true)
  })
})

describe('graphItemFromNodeId', () => {
  it('maps graph node ids to tracker items', () => {
    expect(graphItemFromNodeId('ticket:setup')).toEqual({ kind: 'ticket', key: 'setup' })
    expect(graphItemFromNodeId('task:semantic-file-decomposition')).toEqual({
      kind: 'task',
      key: 'semantic-file-decomposition',
    })
    expect(graphItemFromNodeId('link:artifact')).toBeNull()
  })
})

function node(id: string): TrackerGraph['nodes'][number] {
  return {
    id,
    type: 'ticket',
    parent_id: null,
    label: id,
    status: 'not-started',
    lane_key: 'implementation',
    priority_key: 'p1',
    data: {},
  }
}

function dependency(
  id: string,
  source: string,
  target: string,
): TrackerGraph['edges'][number] {
  return {
    id,
    type: 'dependency',
    source,
    target,
    label: 'blocks',
    data: {},
  }
}
