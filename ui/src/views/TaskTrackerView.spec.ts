import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { TrackerSnapshot, TrackerTask, TrackerTicket } from '@/lib/task-tracker/types'
import { formatDateTime } from '@/lib/stackos/json'

import TaskTrackerView from './TaskTrackerView.vue'
import TrackerTicketDetailPanel from './task-tracker/TrackerTicketDetailPanel.vue'

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
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  it('opens and updates the existing ticket view from overview status links', async () => {
    const task = trackerTask()
    const secondTask = trackerTask({ id: 2, key: 'task-b', title: 'Second task' })
    const open = trackerTicket({ id: 11, key: 'open-ticket', status: 'in-progress' })
    const done = trackerTicket({ id: 12, key: 'done-ticket', status: 'complete' })
    const group = trackerTicket({
      id: 13,
      key: 'group-row',
      task_id: 2,
      task_key: 'task-b',
      status: 'complete',
      kind: 'group',
    })
    const mirror = trackerTicket({
      id: 14,
      key: 'mirror-row',
      task_id: 2,
      task_key: 'task-b',
      status: 'complete',
      source_kind: 'run-plan',
    })
    operationMocks.callOperation.mockResolvedValue(
      trackerSnapshot([task, secondTask], [open, done, group, mirror]),
    )
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/tasks', component: TaskTrackerView }],
    })
    await router.push('/projects/7/tasks?view=tickets&status=complete')
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
      expect(wrapper.findComponent({ name: 'TaskTrackerCommandPanel' }).props('viewMode')).toBe(
        'tickets',
      ),
    )
    expect(wrapper.findComponent({ name: 'TaskTrackerCommandPanel' }).props('statusFilter')).toBe(
      'complete',
    )
    expect(
      wrapper
        .findComponent({ name: 'TrackerTicketTable' })
        .props('tickets')
        .map((ticket: TrackerTicket) => ticket.key)
        .sort(),
    ).toEqual(['done-ticket', 'group-row', 'mirror-row'])
    expect(router.currentRoute.value.query.task).toBeUndefined()
    wrapper.findComponent({ name: 'TrackerTicketTable' }).vm.$emit('row-click', group)
    await wrapper.vm.$nextTick()
    expect(wrapper.findComponent({ name: 'TrackerTicketDetailPanel' }).props('ticket')?.key).toBe(
      'group-row',
    )
    expect(router.currentRoute.value.query.task).toBeUndefined()
    expect(wrapper.findComponent({ name: 'TrackerTicketTable' }).props('tickets')).toHaveLength(3)
    await router.push('/projects/7/tasks?view=tickets&status=in-progress')
    await vi.waitFor(() =>
      expect(
        wrapper
          .findComponent({ name: 'TrackerTicketTable' })
          .props('tickets')
          .map((ticket: TrackerTicket) => ticket.key),
      ).toEqual(['open-ticket']),
    )
    await router.push('/projects/7/tasks?view=tickets&status=invalid')
    await vi.waitFor(() =>
      expect(wrapper.findComponent({ name: 'TaskTrackerCommandPanel' }).props('statusFilter')).toBe(
        'all',
      ),
    )
    await router.push('/projects/7/tasks?view=tickets&status=complete&task=task-b')
    await vi.waitFor(() =>
      expect(
        wrapper
          .findComponent({ name: 'TrackerTicketTable' })
          .props('tickets')
          .map((ticket: TrackerTicket) => ticket.key),
      ).toEqual(['group-row', 'mirror-row']),
    )
    wrapper.unmount()
  })

  it('selects and loads a matching task when leaving all-task tickets for the dependency map', async () => {
    const taskA = trackerTask({ key: 'task-a', status: 'complete' })
    const taskB = trackerTask({ id: 2, key: 'task-b' })
    const ticketA = trackerTicket({ key: 'done-ticket', status: 'complete' })
    const ticketB = trackerTicket({ id: 12, task_id: 2, task_key: 'task-b' })
    operationMocks.callOperation.mockImplementation(
      async (operation: string, args: Record<string, unknown>) => {
        if (operation !== 'tracker.get') throw new Error(`Unexpected operation: ${operation}`)
        return args.include_graph === true
          ? trackerSnapshot([taskA], [ticketA], true)
          : trackerSnapshot([taskA, taskB], [ticketA, ticketB])
      },
    )
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/tasks', component: TaskTrackerView }],
    })
    await router.push('/projects/7/tasks?view=tickets&status=complete')
    await router.isReady()
    const wrapper = mount(
      { template: '<RouterView />' },
      { global: { plugins: [router, createPinia()], stubs: trackerRouteStubs() } },
    )
    await vi.waitFor(() =>
      expect(wrapper.findComponent({ name: 'TrackerTicketTable' }).props('tickets')).toHaveLength(
        1,
      ),
    )
    const controls = wrapper.findComponent({ name: 'TaskTrackerCommandPanel' })
    controls.vm.$emit('update:view-mode', 'graph')
    await flushPromises()
    expect(operationMocks.callOperation).toHaveBeenCalledWith('tracker.get', {
      project_id: 7,
      task_key: 'task-a',
      include_graph: true,
    })
    expect(controls.props('activeTaskKey')).toBe('task-a')
    expect(router.currentRoute.value.query).toEqual({ status: 'complete', task: 'task-a' })
    expect(streamMocks.openTrackerStatusStream).toHaveBeenLastCalledWith(
      expect.objectContaining({ projectId: 7, taskKey: 'task-a' }),
    )
    wrapper.unmount()
  })

  it('keeps the requested status when filtering replaces the selected task', async () => {
    const taskA = trackerTask({ key: 'task-a' })
    const taskB = trackerTask({ id: 2, key: 'task-b', status: 'complete' })
    const ticketA = trackerTicket()
    const ticketB = trackerTicket({
      id: 12,
      key: 'done-ticket',
      task_id: 2,
      task_key: 'task-b',
      status: 'complete',
    })
    operationMocks.callOperation.mockResolvedValue(
      trackerSnapshot([taskA, taskB], [ticketA, ticketB]),
    )
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/tasks', component: TaskTrackerView }],
    })
    await router.push('/projects/7/tasks?view=tickets&status=in-progress&task=task-a')
    await router.isReady()
    const wrapper = mount(
      { template: '<RouterView />' },
      { global: { plugins: [router, createPinia()], stubs: trackerRouteStubs() } },
    )
    await vi.waitFor(() =>
      expect(wrapper.findComponent({ name: 'TrackerTicketTable' }).props('tickets')).toHaveLength(
        1,
      ),
    )
    const controls = wrapper.findComponent({ name: 'TaskTrackerCommandPanel' })
    controls.vm.$emit('update:status-filter', 'complete')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({
      view: 'tickets',
      status: 'complete',
      task: 'task-b',
    })
    expect(controls.props('statusFilter')).toBe('complete')
    expect(controls.props('activeTaskKey')).toBe('task-b')
    expect(
      wrapper
        .findComponent({ name: 'TrackerTicketTable' })
        .props('tickets')
        .map((ticket: TrackerTicket) => ticket.key),
    ).toEqual(['done-ticket'])
    wrapper.unmount()
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
      { template: '<RouterView :key="$route.params.id" />' },
      {
        global: {
          plugins: [router, createPinia()],
          stubs: trackerRouteStubs(),
        },
      },
    )

    await vi.waitFor(() => {
      expect(operationMocks.callOperation).toHaveBeenCalledWith('tracker.get', {
        project_id: 7,
        task_index_only: true,
        include_graph: false,
      })
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

  it('remounts project scope and keeps live cursors scoped to the project', async () => {
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
      { template: '<RouterView :key="$route.params.id" />' },
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

  it('shows an honest pending state while the selected graph loads', async () => {
    const task = trackerTask({
      key: 'slow-task',
      ticket_summary: {
        total_count: 1,
        terminal_count: 0,
        in_progress_count: 1,
        blocked_count: 0,
        status_counts: {
          'not-started': 0,
          'in-progress': 1,
          complete: 0,
          deferred: 0,
          aborted: 0,
          failed: 0,
          skipped: 0,
        },
        assignees: [],
        search_text: 'slow-ticket Slow ticket',
      },
    })
    const ticket = trackerTicket({ task_key: 'slow-task', key: 'slow-ticket' })
    let resolveFocused!: (snapshot: TrackerSnapshot) => void
    const focused = new Promise<TrackerSnapshot>((resolve) => {
      resolveFocused = resolve
    })
    operationMocks.callOperation.mockImplementation(
      async (operation: string, args: Record<string, unknown>) => {
        if (operation !== 'tracker.get') throw new Error(`Unexpected operation: ${operation}`)
        if (args.include_graph === false) return trackerSnapshot([task], [])
        return focused
      },
    )

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/tasks', component: TaskTrackerView }],
    })
    await router.push('/projects/7/tasks?task=slow-task')
    await router.isReady()

    const wrapper = mount(
      { template: '<RouterView :key="$route.params.id" />' },
      {
        global: {
          plugins: [router, createPinia()],
          stubs: trackerRouteStubs(),
        },
      },
    )

    await vi.waitFor(() => expect(wrapper.get('[data-test="graph-state"]').text()).toBe('pending'))
    resolveFocused(trackerSnapshot([task], [ticket], true))
    await vi.waitFor(() => expect(wrapper.get('[data-test="graph-state"]').text()).toBe('ready'))
    wrapper.unmount()
  })

  it('discards an owner status response after the selected task changes', async () => {
    const taskA = trackerTask({ id: 1, key: 'task-a', title: 'Task A' })
    const taskB = trackerTask({ id: 2, key: 'task-b', title: 'Task B' })
    const base = trackerSnapshot([taskA, taskB], [])
    const pending: Array<(value: { status: Record<string, unknown> }) => void> = []
    operationMocks.callOperation.mockImplementation((operation: string) => {
      if (operation === 'tracker.get') return Promise.resolve(base)
      if (operation === 'run.ownerGet') {
        return new Promise((resolve) => pending.push(resolve))
      }
      return Promise.resolve({ items: [], next_cursor: null, total_estimate: 0 })
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/tasks', component: TaskTrackerView }],
    })
    await router.push('/projects/7/tasks?task=task-a')
    await router.isReady()
    const wrapper = mount(
      { template: '<RouterView :key="$route.params.id" />' },
      { global: { plugins: [router, createPinia()], stubs: trackerRouteStubs() } },
    )
    await vi.waitFor(() =>
      expect(operationMocks.callOperation).toHaveBeenCalledWith('tracker.get', expect.anything()),
    )
    await wrapper.get('[data-test="open-task-detail"]').trigger('click')
    await vi.waitFor(() => expect(pending.length).toBeGreaterThan(0))
    const priorRequests = pending.length
    await wrapper.get('[data-test="select-task-b"]').trigger('click')
    await vi.waitFor(() => expect(pending.length).toBeGreaterThan(priorRequests))
    pending.slice(0, priorRequests).forEach((resolve) => resolve({ status: { state: 'running' } }))
    await flushPromises()
    expect(
      wrapper.findComponent({ name: 'TrackerTaskDetailDialog' }).props('ownerStatus'),
    ).toBeNull()
    pending[pending.length - 1]({ status: { state: 'production_succeeded' } })
    await vi.waitFor(() =>
      expect(
        wrapper.findComponent({ name: 'TrackerTaskDetailDialog' }).props('ownerStatus'),
      ).toEqual({
        state: 'production_succeeded',
      }),
    )
    wrapper.unmount()
  })

  it('discards an owner status response after a project switch with the same task id', async () => {
    const taskInProject7 = trackerTask({
      id: 1,
      project_id: 7,
      key: 'task-a',
      title: 'Project 7 task',
    })
    const taskInProject8 = trackerTask({
      id: 1,
      project_id: 8,
      key: 'task-a',
      title: 'Project 8 task',
    })
    const snapshot7 = trackerSnapshot([taskInProject7], [], true)
    const snapshot8 = trackerSnapshot([taskInProject8], [], true)
    snapshot8.tracker.project_id = 8
    const pending: Array<{
      projectId: number
      canonicalItemId: string
      resolve: (value: { status: Record<string, unknown> }) => void
    }> = []
    operationMocks.callOperation.mockImplementation(
      (operation: string, args: Record<string, unknown>) => {
        if (operation === 'tracker.get') {
          return Promise.resolve(args.project_id === 8 ? snapshot8 : snapshot7)
        }
        if (operation === 'run.ownerGet') {
          return new Promise((resolve) =>
            pending.push({
              projectId: Number(args.project_id),
              canonicalItemId: String(args.canonical_item_id),
              resolve,
            }),
          )
        }
        return Promise.resolve({ items: [], next_cursor: null, total_estimate: 0 })
      },
    )
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/tasks', component: TaskTrackerView }],
    })
    await router.push('/projects/7/tasks?task=task-a')
    await router.isReady()
    const wrapper = mount(
      { template: '<RouterView :key="$route.params.id" />' },
      { global: { plugins: [router, createPinia()], stubs: trackerRouteStubs() } },
    )

    await vi.waitFor(() =>
      expect(operationMocks.callOperation).toHaveBeenCalledWith('tracker.get', expect.anything()),
    )
    await wrapper.get('[data-test="open-task-detail"]').trigger('click')
    await vi.waitFor(() => expect(pending.some((request) => request.projectId === 7)).toBe(true))
    const staleRequest = pending.find((request) => request.projectId === 7)
    expect(staleRequest?.canonicalItemId).toBe('task:1')

    await router.push('/projects/8/tasks?task=task-a')
    await vi.waitFor(() =>
      expect(operationMocks.callOperation).toHaveBeenCalledWith(
        'tracker.get',
        expect.objectContaining({ project_id: 8 }),
      ),
    )
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="open-task-detail"]').exists()).toBe(true),
    )
    await wrapper.get('[data-test="open-task-detail"]').trigger('click')
    await vi.waitFor(() =>
      expect(
        pending.some((request) => request.projectId === 8 && request.canonicalItemId === 'task:1'),
      ).toBe(true),
    )

    staleRequest?.resolve({ status: { state: 'running', captured_project: 7 } })
    await flushPromises()
    expect(
      wrapper.findComponent({ name: 'TrackerTaskDetailDialog' }).props('ownerStatus'),
    ).toBeNull()

    const currentRequest = pending.filter((request) => request.projectId === 8).at(-1)
    currentRequest?.resolve({ status: { state: 'production_succeeded', captured_project: 8 } })
    await vi.waitFor(() =>
      expect(
        wrapper.findComponent({ name: 'TrackerTaskDetailDialog' }).props('ownerStatus'),
      ).toEqual({ state: 'production_succeeded', captured_project: 8 }),
    )
    wrapper.unmount()
  })

  it('refreshes open task status and fences responses across close and reopen', async () => {
    const task = trackerTask()
    const pending: Array<(value: { status: Record<string, unknown> }) => void> = []
    operationMocks.callOperation.mockImplementation((operation: string) => {
      if (operation === 'tracker.get') return Promise.resolve(trackerSnapshot([task], [], true))
      if (operation === 'run.ownerGet') return new Promise((resolve) => pending.push(resolve))
      return Promise.resolve({ items: [], next_cursor: null, total_estimate: 0 })
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/tasks', component: TaskTrackerView }],
    })
    await router.push('/projects/7/tasks?task=task-a')
    await router.isReady()
    const wrapper = mount(
      { template: '<RouterView />' },
      { global: { plugins: [router, createPinia()], stubs: trackerRouteStubs() } },
    )
    await vi.waitFor(() => expect(wrapper.get('[data-test="graph-state"]').text()).toBe('ready'))
    await wrapper.get('[data-test="open-task-detail"]').trigger('click')
    await vi.waitFor(() => expect(pending).toHaveLength(1))
    pending[0]({ status: { state: 'running' } })
    const dialog = wrapper.findComponent({ name: 'TrackerTaskDetailDialog' })
    await vi.waitFor(() => expect(dialog.props('ownerStatus')).toEqual({ state: 'running' }))

    const refresh = wrapper.findAll('button').find((button) => button.text() === 'Refresh')
    expect(refresh).toBeDefined()
    await refresh!.trigger('click')
    await vi.waitFor(() => expect(pending).toHaveLength(2))
    expect(dialog.props('ownerStatus')).toBeNull()
    dialog.vm.$emit('update:modelValue', false)
    await wrapper.vm.$nextTick()
    expect(dialog.props('modelValue')).toBe(false)
    await wrapper.get('[data-test="open-task-detail"]').trigger('click')
    await vi.waitFor(() => expect(pending).toHaveLength(3))
    pending[1]({ status: { state: 'stale' } })
    await flushPromises()
    expect(dialog.props('ownerStatus')).toBeNull()
    pending[2]({ status: { state: 'production_succeeded' } })
    await vi.waitFor(() =>
      expect(dialog.props('ownerStatus')).toEqual({ state: 'production_succeeded' }),
    )
    wrapper.unmount()
  })

  it('refreshes ticket status on selection and fences responses across close and reopen', async () => {
    const task = trackerTask()
    const ticket = trackerTicket()
    const pending: Array<(value: { status: Record<string, unknown> }) => void> = []
    operationMocks.callOperation.mockImplementation((operation: string) => {
      if (operation === 'tracker.get') {
        return Promise.resolve(trackerSnapshot([task], [ticket], true))
      }
      if (operation === 'run.ownerGet') return new Promise((resolve) => pending.push(resolve))
      return Promise.resolve({ items: [], next_cursor: null, total_estimate: 0 })
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/tasks', component: TaskTrackerView }],
    })
    await router.push('/projects/7/tasks?view=tickets')
    await router.isReady()
    const wrapper = mount(
      { template: '<RouterView />' },
      { global: { plugins: [router, createPinia()], stubs: trackerRouteStubs() } },
    )
    await vi.waitFor(() =>
      expect(wrapper.findComponent({ name: 'TrackerTicketTable' }).exists()).toBe(true),
    )
    const table = wrapper.findComponent({ name: 'TrackerTicketTable' })
    table.vm.$emit('row-click', ticket)
    await vi.waitFor(() => expect(pending).toHaveLength(1))
    expect(operationMocks.callOperation).toHaveBeenCalledWith('run.ownerGet', {
      project_id: 7,
      canonical_item_id: 'ticket:11',
    })
    const panel = wrapper.findComponent({ name: 'TrackerTicketDetailPanel' })
    panel.vm.$emit('update:modelValue', false)
    await wrapper.vm.$nextTick()
    expect(panel.props('modelValue')).toBe(false)
    table.vm.$emit('row-click', ticket)
    await vi.waitFor(() => expect(pending).toHaveLength(2))
    pending[0]({ status: { state: 'stale' } })
    await flushPromises()
    expect(panel.props('ownerStatus')).toBeNull()
    pending[1]({ status: { state: 'running' } })
    await vi.waitFor(() => expect(panel.props('ownerStatus')).toEqual({ state: 'running' }))
    wrapper.unmount()
  })

  it('keeps task and ticket detail bindings mutually exclusive', async () => {
    const task = trackerTask()
    const ticket = trackerTicket({ id: 11, task_id: task.id, task_key: task.key })
    operationMocks.callOperation.mockResolvedValue(trackerSnapshot([task], [ticket], true))
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/tasks', component: TaskTrackerView }],
    })
    await router.push('/projects/7/tasks?view=stories')
    await router.isReady()
    const wrapper = mount(
      { template: '<RouterView />' },
      { global: { plugins: [router, createPinia()], stubs: trackerRouteStubs() } },
    )
    await vi.waitFor(() =>
      expect(operationMocks.callOperation).toHaveBeenCalledWith('tracker.get', expect.anything()),
    )
    await vi.waitFor(() =>
      expect(wrapper.findComponent({ name: 'TrackerStoriesPanel' }).exists()).toBe(true),
    )
    wrapper.findComponent({ name: 'TrackerStoriesPanel' }).vm.$emit('open-task-detail')
    await vi.waitFor(() =>
      expect(wrapper.findComponent({ name: 'TrackerTaskDetailDialog' }).props('modelValue')).toBe(
        true,
      ),
    )

    wrapper
      .findComponent({ name: 'TaskTrackerCommandPanel' })
      .vm.$emit('update:view-mode', 'tickets')
    await vi.waitFor(() =>
      expect(wrapper.findComponent({ name: 'TrackerTicketTable' }).exists()).toBe(true),
    )
    wrapper.findComponent({ name: 'TrackerTicketTable' }).vm.$emit('row-click', ticket)
    await wrapper.vm.$nextTick()
    expect(wrapper.findComponent({ name: 'TrackerTaskDetailDialog' }).props('modelValue')).toBe(
      false,
    )
    expect(wrapper.findComponent({ name: 'TrackerTicketDetailPanel' }).props('modelValue')).toBe(
      true,
    )
    wrapper.unmount()
  })

  it.each([
    ['running', 'agent', 'inspect_attempt'],
    ['production_succeeded', 'main_agent', 'review_acceptance'],
    ['mixed', 'main_agent', 'reconcile_provenance'],
    ['rejected', 'operator', 'repair_authority'],
  ])(
    'renders ticket owner status %s as read-only context',
    async (state, nextActor, nextAction) => {
      const wrapper = mount(TrackerTicketDetailPanel, {
        props: {
          modelValue: true,
          ticket: trackerTicket(),
          title: 'Ticket detail',
          ownerStatus: {
            state,
            next_actor: nextActor,
            next_action: nextAction,
            acceptance_state: 'not_evaluated',
            captured_at: '2026-10-17T18:00:00Z',
            freshness: 'fresh',
            owner: { owner_ref: 'owner:current', epoch: 8, revision: 12 },
            original_binding: {
              owner_ref: 'owner:original',
              epoch: 7,
              reservation_ref: 'intent:original',
            },
            needs: ['attempt_running', 'host_unavailable'],
          },
        },
        attachTo: document.body,
      })
      await nextTick()
      const text = (document.body.textContent ?? '').replace(/\s+/g, ' ')
      expect(text).toContain(state)
      expect(text).toContain('acceptance not_evaluated')
      expect(text).toContain(`Captured ${formatDateTime('2026-10-17T18:00:00Z')} · freshness fresh`)
      expect(text).toContain('Current owner:current · epoch 8 · revision 12')
      expect(text).toContain('Original owner:original · epoch 7 · reservation intent:original')
      expect(text).toContain('Needs: attempt_running, host_unavailable')
      wrapper.unmount()
    },
  )
})

function trackerRouteStubs() {
  return {
    ProjectPageHeader: { template: '<header><slot name="actions" /></header>' },
    TaskTrackerCommandPanel: {
      name: 'TaskTrackerCommandPanel',
      props: ['viewMode', 'statusFilter', 'activeTaskKey'],
      emits: ['task-select', 'update:view-mode', 'update:status-filter'],
      template:
        '<nav><button data-test="select-task-a" @click="$emit(\'task-select\', \'task-a\')">Task A</button><button data-test="select-task-b" @click="$emit(\'task-select\', \'task-b\')">Task B</button></nav>',
    },
    TrackerGraphPanel: {
      props: ['loading'],
      emits: ['open-task-detail', 'viewport-change-end'],
      template: `<section>
        <span data-test="graph-state">{{ loading ? 'pending' : 'ready' }}</span>
        <button data-test="open-task-detail" @click="$emit('open-task-detail')">Detail</button>
        <button data-test="persist-viewport" @click="$emit('viewport-change-end', { x: 12, y: 24, zoom: 1.25 })">Viewport</button>
      </section>`,
    },
    TrackerWarningSummary: true,
    TrackerTicketTable: true,
    TrackerTicketDetailPanel: true,
    TrackerStoriesPanel: true,
    TrackerTaskDetailDialog: {
      name: 'TrackerTaskDetailDialog',
      props: ['contexts', 'ownerStatus', 'modelValue'],
      emits: ['update:modelValue'],
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
