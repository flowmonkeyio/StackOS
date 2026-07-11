import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { TrackerSnapshot, TrackerTask, TrackerTicket } from '@/lib/task-tracker/types'

import TaskTrackerView from './TaskTrackerView.vue'

const operationMocks = vi.hoisted(() => ({
  callOperation: vi.fn(),
}))
const streamMocks = vi.hoisted(() => ({
  close: vi.fn(),
  openTrackerStatusStream: vi.fn(),
}))

vi.mock('@/lib/operations', () => ({ callOperation: operationMocks.callOperation }))
vi.mock('@/lib/task-tracker/liveEvents', () => ({
  openTrackerStatusStream: streamMocks.openTrackerStatusStream,
}))

describe('TaskTrackerView route integration', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    sessionStorage.clear()
    operationMocks.callOperation.mockReset()
    streamMocks.close.mockReset()
    streamMocks.openTrackerStatusStream.mockReset()
    streamMocks.openTrackerStatusStream.mockReturnValue({ close: streamMocks.close })
  })

  afterEach(() => {
    sessionStorage.clear()
    vi.restoreAllMocks()
  })

  it('preserves query, graph session, stream, and execution-context wiring', async () => {
    const taskA = trackerTask({ id: 1, key: 'task-a', title: 'Prepare delivery' })
    const taskB = trackerTask({ id: 2, key: 'task-b', title: 'Ship delivery' })
    const ticketA = trackerTicket({ id: 11, task_id: 1, task_key: 'task-a', key: 'ticket-a' })
    const ticketB = trackerTicket({ id: 12, task_id: 2, task_key: 'task-b', key: 'ticket-b' })
    const base = trackerSnapshot([taskA, taskB], [ticketA, ticketB])

    operationMocks.callOperation.mockImplementation(
      async (operation: string, args: Record<string, unknown>) => {
        if (operation === 'tracker.get' && args.include_graph === false) return base
        if (operation === 'tracker.get' && args.task_key === 'task-a') {
          return trackerSnapshot([taskA], [ticketA], true)
        }
        if (operation === 'tracker.get' && args.task_key === 'task-b') {
          return trackerSnapshot([taskB], [ticketB], true)
        }
        if (operation === 'executionContext.list') {
          return {
            items: [
              {
                id: 21,
                context_ref: 'execution-context:ship',
                name: 'Shipping context',
                status: 'active',
                artifact_count: 1,
              },
            ],
            next_cursor: null,
            total_estimate: 1,
          }
        }
        if (operation === 'executionContext.artifact.list') {
          return {
            items: [
              {
                id: 31,
                context_ref: 'execution-context:ship',
                artifact_id: 41,
                action_call_id: null,
                semantic_name: 'release-note',
                action_ref: null,
                metadata_json: {},
                created_at: '2026-07-10T00:00:00Z',
                artifact: {},
              },
            ],
            next_cursor: null,
            total_estimate: 1,
          }
        }
        throw new Error(`Unexpected operation: ${operation}`)
      },
    )

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/tasks', component: TaskTrackerView }],
    })
    await router.push('/projects/7/tasks?task=task-b')
    await router.isReady()

    const wrapper = mount(
      { template: '<RouterView />' },
      {
        global: {
          plugins: [router, createPinia()],
          stubs: trackerRouteStubs(),
        },
      },
    )

    await vi.waitFor(() => {
      expect(operationMocks.callOperation).toHaveBeenCalledWith(
        'tracker.get',
        expect.objectContaining({ project_id: 7, task_key: 'task-b', include_graph: true }),
      )
      expect(streamMocks.openTrackerStatusStream).toHaveBeenCalledWith(
        expect.objectContaining({ projectId: 7, taskKey: 'task-b' }),
      )
    })

    await wrapper.get('[data-test="persist-viewport"]').trigger('click')
    expect(sessionStorage.getItem('stackos:tracker-graph:7:task-b:all-status:all-block')).toBe(
      JSON.stringify({ x: 12, y: 24, zoom: 1.25 }),
    )

    await wrapper.get('[data-test="open-task-detail"]').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Shipping context'))
    expect(operationMocks.callOperation).toHaveBeenCalledWith('executionContext.list', {
      project_id: 7,
      task_key: 'task-b',
      limit: 20,
    })
    expect(operationMocks.callOperation).toHaveBeenCalledWith('executionContext.artifact.list', {
      project_id: 7,
      context_ref: 'execution-context:ship',
      limit: 5,
    })

    await wrapper.get('[data-test="select-task-a"]').trigger('click')
    await vi.waitFor(() => expect(router.currentRoute.value.query.task).toBe('task-a'))
    expect(operationMocks.callOperation).toHaveBeenCalledWith(
      'tracker.get',
      expect.objectContaining({ project_id: 7, task_key: 'task-a', include_graph: true }),
    )
    expect(streamMocks.close).toHaveBeenCalled()
    expect(streamMocks.openTrackerStatusStream).toHaveBeenLastCalledWith(
      expect.objectContaining({ projectId: 7, taskKey: 'task-a' }),
    )

    wrapper.unmount()
    expect(streamMocks.close).toHaveBeenCalled()
  })

  it('reloads reused routes and keeps live cursors scoped to the project', async () => {
    const task7 = trackerTask({ project_id: 7, key: 'shared-task', title: 'Project seven' })
    const task8 = trackerTask({ project_id: 8, key: 'shared-task', title: 'Project eight' })

    operationMocks.callOperation.mockImplementation(
      async (operation: string, args: Record<string, unknown>) => {
        if (operation !== 'tracker.get') throw new Error(`Unexpected operation: ${operation}`)
        const task = args.project_id === 8 ? task8 : task7
        return trackerSnapshot([task], [], args.include_graph === true)
      },
    )

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/tasks', component: TaskTrackerView }],
    })
    await router.push('/projects/7/tasks?task=shared-task')
    await router.isReady()

    const wrapper = mount(
      { template: '<RouterView />' },
      {
        global: {
          plugins: [router, createPinia()],
          stubs: trackerRouteStubs(),
        },
      },
    )

    await vi.waitFor(() =>
      expect(streamMocks.openTrackerStatusStream).toHaveBeenLastCalledWith(
        expect.objectContaining({ projectId: 7, taskKey: 'shared-task', afterId: null }),
      ),
    )
    const project7Stream = streamMocks.openTrackerStatusStream.mock.calls.at(-1)?.[0]
    project7Stream.onEvent({ id: 91, metadata_json: { task_key: 'shared-task' } })

    await router.push('/projects/8/tasks?task=shared-task')
    await vi.waitFor(() =>
      expect(streamMocks.openTrackerStatusStream).toHaveBeenLastCalledWith(
        expect.objectContaining({ projectId: 8, taskKey: 'shared-task', afterId: null }),
      ),
    )

    expect(operationMocks.callOperation).toHaveBeenCalledWith(
      'tracker.get',
      expect.objectContaining({ project_id: 8, task_key: 'shared-task', include_graph: true }),
    )
    expect(streamMocks.close).toHaveBeenCalled()
    wrapper.unmount()
  })
})

function trackerRouteStubs() {
  return {
    ProjectPageHeader: { template: '<header><slot name="actions" /></header>' },
    TaskTrackerCommandPanel: {
      emits: ['task-select'],
      template:
        '<nav><button data-test="select-task-a" @click="$emit(\'task-select\', \'task-a\')">Task A</button></nav>',
    },
    TrackerGraphPanel: {
      emits: ['open-task-detail', 'viewport-change-end'],
      template: `<section>
        <button data-test="open-task-detail" @click="$emit('open-task-detail')">Detail</button>
        <button data-test="persist-viewport" @click="$emit('viewport-change-end', { x: 12, y: 24, zoom: 1.25 })">Viewport</button>
      </section>`,
    },
    TrackerWarningSummary: true,
    TrackerTicketTable: true,
    TrackerTicketDetailPanel: true,
    TrackerStoriesPanel: true,
    TrackerTaskDetailDialog: {
      props: ['contexts'],
      template: '<aside>{{ contexts?.[0]?.name }}</aside>',
    },
  }
}

function trackerTask(overrides: Partial<TrackerTask> = {}): TrackerTask {
  return {
    id: 1,
    project_id: 7,
    tracker_id: 1,
    key: 'task-a',
    title: 'Task',
    goal: '',
    description: '',
    status: 'in-progress',
    priority_key: 'normal',
    lane_key: 'in-progress',
    owner: null,
    task_type: 'delivery',
    order_index: 0,
    source_kind: 'manual',
    source_json: null,
    definition_of_done_json: [],
    constraints_json: [],
    expected_outcomes_json: [],
    completion_evidence_json: null,
    context_json: null,
    metadata_json: null,
    created_by: 'codex',
    created_at: '2026-07-10T00:00:00Z',
    updated_at: '2026-07-10T00:00:00Z',
    started_at: '2026-07-10T00:00:00Z',
    completed_at: null,
    ...overrides,
  }
}

function trackerTicket(overrides: Partial<TrackerTicket> = {}): TrackerTicket {
  return {
    id: 11,
    project_id: 7,
    tracker_id: 1,
    task_id: 1,
    task_key: 'task-a',
    parent_ticket_id: null,
    parent_ticket_key: null,
    run_plan_id: null,
    run_plan_step_id: null,
    run_id: null,
    agent_request_id: null,
    key: 'ticket-a',
    title: 'Ticket',
    goal: '',
    status: 'in-progress',
    kind: 'ticket',
    assignee: null,
    priority_key: 'normal',
    lane_key: 'in-progress',
    order_index: 0,
    blocker_reason: null,
    outcome: null,
    effort: null,
    source_kind: 'manual',
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
    created_at: '2026-07-10T00:00:00Z',
    updated_at: '2026-07-10T00:00:00Z',
    started_at: '2026-07-10T00:00:00Z',
    completed_at: null,
    dependency_keys: [],
    blocked_by: [],
    reference_count: 0,
    link_count: 0,
    ...overrides,
  }
}

function trackerSnapshot(
  tasks: TrackerTask[],
  tickets: TrackerTicket[],
  includeGraph = false,
): TrackerSnapshot {
  return {
    tracker: {
      id: 1,
      project_id: 7,
      key: 'project',
      name: 'Project tracker',
      description: '',
      rev: 1,
      created_at: '2026-07-10T00:00:00Z',
      updated_at: '2026-07-10T00:00:00Z',
    },
    lanes: [],
    priorities: [],
    tasks,
    tickets,
    dependencies: [],
    links: [],
    graph: includeGraph ? { nodes: [], edges: [], warnings: [], layout_hints: {} } : null,
  }
}
