import { describe, expect, it } from 'vitest'

import { graphFocusFor, graphItemFromNodeId } from './graphFocus'
import type { TrackerGraph } from './types'

const graph: TrackerGraph = {
  nodes: [
    node('ticket:setup'),
    node('ticket:build'),
    node('ticket:verify'),
    node('ticket:ship'),
    node('ticket:archive'),
    node('ticket:cleanup'),
  ],
  edges: [
    dependency('dependency:setup:build', 'ticket:setup', 'ticket:build'),
    dependency('dependency:build:verify', 'ticket:build', 'ticket:verify'),
    dependency('dependency:verify:ship', 'ticket:verify', 'ticket:ship'),
    dependency('dependency:ship:archive', 'ticket:ship', 'ticket:archive'),
    dependency('dependency:archive:cleanup', 'ticket:archive', 'ticket:cleanup'),
  ],
  warnings: [],
  layout_hints: { direction: 'LR' },
}

describe('graphFocusFor', () => {
  it('marks the two-hop dependency neighborhood for a node', () => {
    const focus = graphFocusFor(graph, { nodeId: 'ticket:verify', edgeId: null })

    expect([...focus.upstreamNodeIds].sort()).toEqual(['ticket:build', 'ticket:setup'])
    expect([...focus.upstreamEdgeIds].sort()).toEqual([
      'dependency:build:verify',
      'dependency:setup:build',
    ])
    expect([...focus.downstreamNodeIds].sort()).toEqual(['ticket:archive', 'ticket:ship'])
    expect([...focus.downstreamEdgeIds].sort()).toEqual([
      'dependency:ship:archive',
      'dependency:verify:ship',
    ])
    expect(focus.downstreamNodeIds.has('ticket:cleanup')).toBe(false)
    expect(focus.downstreamEdgeIds.has('dependency:archive:cleanup')).toBe(false)
    expect(focus.selectedNodeId).toBe('ticket:verify')
  })

  it('uses a clicked dependency edge as the active relation and selected target context', () => {
    const focus = graphFocusFor(graph, { nodeId: null, edgeId: 'dependency:build:verify' })

    expect(focus.activeEdgeId).toBe('dependency:build:verify')
    expect(focus.selectedNodeId).toBe('ticket:verify')
    expect(focus.edgeIds.has('dependency:build:verify')).toBe(true)
    expect(focus.upstreamNodeIds.has('ticket:setup')).toBe(true)
  })

  it('stops at the depth bound even when dependencies cycle', () => {
    const focus = graphFocusFor(
      {
        nodes: [node('ticket:a'), node('ticket:b'), node('ticket:c')],
        edges: [
          dependency('dependency:a:b', 'ticket:a', 'ticket:b'),
          dependency('dependency:b:c', 'ticket:b', 'ticket:c'),
          dependency('dependency:c:a', 'ticket:c', 'ticket:a'),
        ],
        warnings: [],
        layout_hints: {},
      },
      { nodeId: 'ticket:a', edgeId: null },
    )

    expect(focus.selectedNodeId).toBe('ticket:a')
    expect(focus.edgeIds.size).toBeLessThanOrEqual(3)
    expect(focus.nodeIds.has('ticket:a')).toBe(true)
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
