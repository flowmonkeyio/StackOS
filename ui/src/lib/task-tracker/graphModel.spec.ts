import { describe, expect, it } from 'vitest'

import {
  buildTrackerFlowModel,
  TRACKER_TICKET_NODE_HEIGHT,
  TRACKER_TICKET_NODE_WIDTH,
} from './graphModel'
import type { TrackerSnapshot } from './types'

const snapshot: TrackerSnapshot = {
  tracker: {
    id: 1,
    project_id: 1,
    key: 'default',
    name: 'Tracker',
    description: '',
    rev: 3,
    created_at: '2026-05-24T00:00:00',
    updated_at: '2026-05-24T00:00:00',
  },
  lanes: [],
  priorities: [],
  tasks: [
    {
      id: 10,
      project_id: 1,
      tracker_id: 1,
      key: 'workflow-1',
      title: 'Workflow one',
      goal: 'Deliver the workflow',
      description: '',
      status: 'in-progress',
      priority_key: 'p1',
      lane_key: 'implementation',
      owner: 'codex',
      task_type: 'workflow',
      order_index: 0,
      source_kind: 'workflow',
      source_json: { run_plan_id: 1, template_key: 'demo' },
      definition_of_done_json: [],
      constraints_json: [],
      expected_outcomes_json: [],
      completion_evidence_json: null,
      context_json: null,
      metadata_json: null,
      created_by: 'codex',
      created_at: '2026-05-24T00:00:00',
      updated_at: '2026-05-24T00:00:00',
      started_at: null,
      completed_at: null,
    },
  ],
  tickets: [
    {
      id: 20,
      project_id: 1,
      tracker_id: 1,
      task_id: 10,
      task_key: 'workflow-1',
      parent_ticket_id: null,
      parent_ticket_key: null,
      run_plan_id: 1,
      run_plan_step_id: 1,
      run_id: 4,
      agent_request_id: null,
      key: 'prepare',
      title: 'Prepare',
      goal: '',
      status: 'complete',
      kind: 'ticket',
      assignee: 'codex',
      priority_key: 'p1',
      lane_key: 'done',
      order_index: 0,
      blocker_reason: null,
      outcome: 'done',
      effort: null,
      source_kind: 'workflow',
      source_json: null,
      definition_of_done_json: [],
      constraints_json: [],
      expected_changes_json: [],
      allowed_paths_json: [],
      completion_evidence_json: null,
      context_json: null,
      metadata_json: null,
      created_by: 'codex',
      claimed_at: null,
      created_at: '2026-05-24T00:00:00',
      updated_at: '2026-05-24T00:00:00',
      started_at: null,
      completed_at: null,
      dependency_keys: [],
      blocked_by: [],
      reference_count: 0,
      link_count: 1,
    },
    {
      id: 21,
      project_id: 1,
      tracker_id: 1,
      task_id: 10,
      task_key: 'workflow-1',
      parent_ticket_id: null,
      parent_ticket_key: null,
      run_plan_id: 1,
      run_plan_step_id: 2,
      run_id: 4,
      agent_request_id: null,
      key: 'deliver',
      title: 'Deliver',
      goal: '',
      status: 'not-started',
      kind: 'ticket',
      assignee: null,
      priority_key: 'p1',
      lane_key: 'implementation',
      order_index: 1,
      blocker_reason: null,
      outcome: null,
      effort: null,
      source_kind: 'workflow',
      source_json: null,
      definition_of_done_json: [],
      constraints_json: [],
      expected_changes_json: [],
      allowed_paths_json: [],
      completion_evidence_json: null,
      context_json: null,
      metadata_json: null,
      created_by: 'codex',
      claimed_at: null,
      created_at: '2026-05-24T00:00:00',
      updated_at: '2026-05-24T00:00:00',
      started_at: null,
      completed_at: null,
      dependency_keys: ['prepare'],
      blocked_by: [],
      reference_count: 0,
      link_count: 1,
    },
  ],
  dependencies: [
    {
      id: 100,
      ticket_key: 'deliver',
      depends_on_ticket_key: 'prepare',
      dependency_type: 'blocks',
      metadata_json: null,
    },
  ],
  links: [],
  graph: {
    nodes: [
      {
        id: 'task:workflow-1',
        type: 'task',
        parent_id: null,
        label: 'Workflow one',
        status: 'in-progress',
        lane_key: 'implementation',
        priority_key: 'p1',
        data: {},
      },
      {
        id: 'ticket:prepare',
        type: 'ticket',
        parent_id: 'task:workflow-1',
        label: 'Prepare',
        status: 'complete',
        lane_key: 'done',
        priority_key: 'p1',
        data: {},
      },
      {
        id: 'ticket:deliver',
        type: 'ticket',
        parent_id: 'task:workflow-1',
        label: 'Deliver',
        status: 'not-started',
        lane_key: 'implementation',
        priority_key: 'p1',
        data: {},
      },
    ],
    edges: [
      {
        id: 'contains:workflow-1:prepare',
        type: 'contains',
        source: 'task:workflow-1',
        target: 'ticket:prepare',
        label: null,
        data: {},
      },
      {
        id: 'contains:workflow-1:deliver',
        type: 'contains',
        source: 'task:workflow-1',
        target: 'ticket:deliver',
        label: null,
        data: {},
      },
      {
        id: 'dependency:prepare:deliver',
        type: 'dependency',
        source: 'ticket:prepare',
        target: 'ticket:deliver',
        label: 'blocks',
        data: { dependency_id: 100 },
      },
    ],
    warnings: [],
    layout_hints: { direction: 'LR' },
  },
}

describe('buildTrackerFlowModel', () => {
  it('composes ticket nodes as a dependency tree by default', () => {
    const model = buildTrackerFlowModel(snapshot)

    expect(model.nodes.map((node) => node.id)).toEqual(['ticket:prepare', 'ticket:deliver'])
    expect(model.nodes.find((node) => node.id === 'ticket:prepare')?.parentNode).toBeUndefined()
    expect(model.nodes.find((node) => node.id === 'ticket:deliver')?.position.x).toBeGreaterThan(
      model.nodes.find((node) => node.id === 'ticket:prepare')?.position.x ?? 0,
    )
    expect(model.nodes.find((node) => node.id === 'ticket:prepare')?.sourcePosition).toBe('right')
    expect(model.nodes.find((node) => node.id === 'ticket:prepare')?.targetPosition).toBe('left')
    expect(model.nodes.find((node) => node.id === 'ticket:prepare')?.data).toMatchObject({
      owner: 'codex',
      agent: 'codex',
      sourceKind: 'workflow',
    })
    expect(TRACKER_TICKET_NODE_WIDTH).toBe(296)
    expect(TRACKER_TICKET_NODE_HEIGHT).toBe(176)
    expect(model.edges).toEqual([
      expect.objectContaining({
        id: 'dependency:prepare:deliver',
        source: 'ticket:prepare',
        target: 'ticket:deliver',
        sourceHandle: 'out',
        targetHandle: 'in',
        type: 'default',
        markerEnd: expect.objectContaining({
          height: 7,
          width: 7,
        }),
        style: expect.objectContaining({
          strokeWidth: 1.2,
        }),
      }),
    ])
  })

  it('can compose tickets inside their task box for compact containment views', () => {
    const model = buildTrackerFlowModel(snapshot, { showContainment: true })

    expect(model.nodes.find((node) => node.id === 'ticket:prepare')?.parentNode).toBe(
      'task:workflow-1',
    )
    expect(model.edges.map((edge) => edge.id)).toEqual(['dependency:prepare:deliver'])
  })

  it('marks the focused dependency relation for the graph renderer', () => {
    const model = buildTrackerFlowModel(snapshot, {
      activeEdgeId: 'dependency:prepare:deliver',
      selectedNodeId: 'ticket:deliver',
      upstreamEdgeIds: new Set(['dependency:prepare:deliver']),
      upstreamNodeIds: new Set(['ticket:prepare']),
      highlightedEdgeIds: new Set(['dependency:prepare:deliver']),
      highlightedNodeIds: new Set(['ticket:prepare', 'ticket:deliver']),
      spotlight: true,
    })

    expect(model.edges[0]?.class).toContain('tracker-edge-active')
    expect(model.edges[0]?.class).toContain('tracker-edge-highlighted')
    expect(model.edges[0]?.class).toContain('tracker-edge-upstream')
    expect(model.edges[0]?.style).toMatchObject({ stroke: 'var(--color-accent-primary)' })
    expect(model.edges[0]?.style).toMatchObject({ strokeWidth: 1.2 })
    expect(model.edges[0]?.markerEnd).toMatchObject({ height: 7, width: 7 })
    expect(model.nodes.find((node) => node.id === 'ticket:prepare')?.class).toContain(
      'tracker-node-upstream',
    )
    expect(model.nodes.find((node) => node.id === 'ticket:deliver')?.class).toContain(
      'tracker-node-selected',
    )
    expect(model.nodes.find((node) => node.id === 'ticket:deliver')?.zIndex).toBeGreaterThan(
      model.edges[0]?.zIndex ?? 0,
    )
  })

  it('marks immediate downstream dependency context separately', () => {
    const model = buildTrackerFlowModel(snapshot, {
      selectedNodeId: 'ticket:prepare',
      downstreamEdgeIds: new Set(['dependency:prepare:deliver']),
      downstreamNodeIds: new Set(['ticket:deliver']),
      highlightedEdgeIds: new Set(['dependency:prepare:deliver']),
      highlightedNodeIds: new Set(['ticket:prepare', 'ticket:deliver']),
      spotlight: true,
    })

    expect(model.edges[0]?.class).toContain('tracker-edge-downstream')
    expect(model.edges[0]?.style).toMatchObject({
      opacity: 1,
      stroke: 'var(--color-success-default)',
      strokeWidth: 1.2,
    })
    expect(model.edges[0]?.markerEnd).toMatchObject({ height: 7, width: 7 })
    expect(model.edges[0]?.zIndex).toBeGreaterThan(1)
    expect(model.nodes.find((node) => node.id === 'ticket:prepare')?.class).toContain(
      'tracker-node-selected',
    )
    expect(model.nodes.find((node) => node.id === 'ticket:deliver')?.class).toContain(
      'tracker-node-downstream',
    )
  })

  it('marks active and recently updated graph nodes for visual emphasis', () => {
    const model = buildTrackerFlowModel(snapshot, {
      activeNodeIds: new Set(['ticket:deliver']),
      recentNodeIds: new Set(['ticket:prepare']),
    })

    const activeNode = model.nodes.find((node) => node.id === 'ticket:deliver')
    const recentNode = model.nodes.find((node) => node.id === 'ticket:prepare')
    expect(activeNode?.class).toContain('tracker-node-active')
    expect(activeNode?.data?.active).toBe(true)
    expect(activeNode?.zIndex).toBe(30)
    expect(recentNode?.class).toContain('tracker-node-recent')
    expect(recentNode?.data?.recentlyUpdated).toBe(true)
  })
})
