import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DOMWrapper, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import type { TrackerSnapshot, TrackerTask, TrackerTicket } from '@/lib/task-tracker/types'
import { agentStatsFixture } from './home/agentStatsFixtures'
import TaskTrackerView from './TaskTrackerView.vue'

const mocks = vi.hoisted(() => ({ callOperation: vi.fn(), close: vi.fn(), stream: vi.fn() }))
vi.mock('@/lib/operations', () => ({ callOperation: mocks.callOperation }))
vi.mock('@/lib/task-tracker/liveEvents', () => ({ openTrackerStatusStream: mocks.stream }))

const taskA = trackerTask({ title: 'Alpha task' })
const taskB = trackerTask({ id: 2, key: 'task-b', title: 'Beta task', status: 'complete', completed_at: '2026-11-09T12:00:00Z' })
const ticketA = trackerTicket()
const ticketB = trackerTicket({ id: 12, key: 'ticket-b', title: 'Beta ticket', task_id: 2, task_key: 'task-b', status: 'complete' })

function dataMock(tasks = [taskA, taskB], tickets = [ticketA, ticketB], graphNodes = false) {
  mocks.callOperation.mockImplementation(async (operation: string, args: Record<string, unknown>) => {
    if (operation === 'executionContext.list') return { items: [], total_estimate: 0, next_cursor: null }
    if (operation !== 'tracker.get') throw new Error(`Unexpected operation ${operation}`)
    if (args.task_key) {
      const result = trackerSnapshot(tasks.filter(task => task.key === args.task_key), tickets.filter(ticket => ticket.task_key === args.task_key), args.include_graph === true)
      if (result.graph && graphNodes) result.graph.nodes = result.tickets.map(ticket => ({
        id: `ticket:${ticket.key}`, type: 'ticket', parent_id: null, label: ticket.title,
        status: ticket.status, lane_key: ticket.lane_key, priority_key: ticket.priority_key, data: {},
      }))
      return result
    }
    const index = tasks.map(task => {
      const children = tickets.filter(ticket => ticket.task_key === task.key)
      return { ...task, ticket_summary: {
        total_count: children.length, terminal_count: children.filter(ticket => ticket.status === 'complete').length,
        in_progress_count: children.filter(ticket => ticket.status === 'in-progress').length, blocked_count: 0,
        status_counts: Object.fromEntries(children.map(ticket => [ticket.status, 1])),
        assignees: [], search_text: children.map(ticket => ticket.title).join(' '),
      }}
    })
    return trackerSnapshot(index, [])
  })
}

async function render(url = '/projects/7/tasks', realDialogs = false, realControls = false) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/projects/:id/tasks', component: TaskTrackerView },
    { path: '/other', component: { template: '<div>Other page</div>' } },
  ] })
  await router.push(url)
  await router.isReady()
  const wrapper = mount({ template: '<RouterView :key="$route.params.id" />' }, {
    attachTo: realDialogs ? document.body : undefined,
    global: { plugins: [router, createPinia()], stubs: {
      ProjectPageHeader: { template: '<header><slot name="actions" /></header>' },
      TaskTrackerCommandPanel: realControls ? false : { name: 'TaskTrackerCommandPanel', props: ['viewMode','activeTaskKey','taskOptions','search','filtersActive','boardFilters','taskAgentStats','taskAutomated','taskDelivery','abortTarget'], template: '<nav />' },
      TrackerBoardPanel: { name: 'TrackerBoardPanel', props: ['rows','hideEmptyColumns'], template: '<section data-test="board" />' },
      TrackerGraphPanel: { name: 'TrackerGraphPanel', props: ['loading','activeTaskTitle','selectedTicket','selectionVisible','selectionLabel','flow'], template: '<section data-test="graph" />' },
      TrackerWarningSummary: true,
      TrackerTaskDetailDialog: realDialogs ? false : { name: 'TrackerTaskDetailDialog', props: ['modelValue','task','tickets','loading','error','contexts','ticket','ticketKey'], template: '<aside />' },
      TrackerTicketDetailPanel: realDialogs ? false : { name: 'TrackerTicketDetailPanel', props: ['modelValue','ticket','parentTask','error','presentation'], template: '<aside />' },
    }},
  })
  await flushPromises()
  return { wrapper, router, controls: () => wrapper.findComponent({ name: 'TaskTrackerCommandPanel' }), board: () => wrapper.findComponent({ name: 'TrackerBoardPanel' }), dialog: () => wrapper.findComponent({ name: 'TrackerTaskDetailDialog' }) }
}

const popup = () => new DOMWrapper(document.body)

describe('Work board navigation', () => {
  it('passes the actual selected workflow link to the Graph header abort action and refreshes the existing projection', async () => {
    dataMock([{ ...taskA, source_kind: 'workflow', source_json: { run_plan_id: 321 } }], [ticketA])
    const { wrapper, controls } = await render('/projects/7/tasks?view=graph&task=task-a')
    expect(controls().props('abortTarget')).toMatchObject({ projectId: 7, runPlanId: 321, title: 'Alpha task', automated: false })
    const before = mocks.callOperation.mock.calls.length
    controls().vm.$emit('refresh')
    await flushPromises()
    expect(mocks.callOperation.mock.calls.length).toBeGreaterThan(before)
    wrapper.unmount()
  })
  it('patches usage projections across Board, Graph and open details without another tracker load', async () => {
    dataMock([taskA], [ticketA], true)
    const { wrapper, board, dialog } = await render('/projects/7/tasks?view=graph&task=task-a&detail=task-a')
    const graph = () => wrapper.findComponent({ name: 'TrackerGraphPanel' })
    const requests = mocks.callOperation.mock.calls.length
    const metrics = agentStatsFixture().totals
    mocks.stream.mock.calls.at(-1)![0].onUsage({ tasks: [{ key: taskA.key, agent_stats: metrics, active_execution: null }], tickets: [{ key: ticketA.key, agent_stats: metrics, active_execution: null }] })
    await flushPromises()
    expect(board().props('rows')[0].task.agent_stats).toEqual(metrics)
    expect(graph().props('flow').nodes[0].data.agentStats).toEqual(metrics)
    expect(dialog().props('task').agent_stats).toEqual(metrics)
    expect(dialog().props('tickets')[0].agent_stats).toEqual(metrics)
    expect(mocks.callOperation).toHaveBeenCalledTimes(requests)
    wrapper.unmount()
  })
  it('refreshes actual invocation identity through the existing stream with unchanged ticket status', async () => {
    dataMock([taskA], [{ ...ticketA, status: 'in-progress' }], true)
    const { wrapper, board, dialog } = await render('/projects/7/tasks?view=graph&task=task-a')
    const graph = () => wrapper.findComponent({ name: 'TrackerGraphPanel' })
    graph().vm.$emit('node-click', { event: { stopPropagation: vi.fn() }, node: graph().props('flow').nodes[0] })
    await flushPromises()
    for (const [index, agent, model] of [[1, 'Implementer', 'gpt-6.1-sol'], [2, 'Reviewer', 'gpt-6-astra'], [3, 'Implementer', 'gpt-6.1-sol'], [4, null, null]] as const) {
      const execution = agent ? { live_usage: null, invocation_id: `call-${index}`, agent, model, host_key: 'codex', started_at: `2026-11-08T01:0${index}:00Z`, ticket_key: ticketA.key } : null
      dataMock([{ ...taskA, active_execution: execution }], [{ ...ticketA, status: 'in-progress', active_execution: execution }], true)
      mocks.stream.mock.calls.at(-1)![0].onEvent({ id: 10000 + index, event_type: 'tracker.execution.changed', metadata_json: { task_key: taskA.key, ticket_key: ticketA.key } })
      await flushPromises()
      expect(board().props('rows')[0].task.active_execution).toEqual(execution)
      expect(graph().props('flow').nodes[0].data.activeExecution).toEqual(execution)
      expect(wrapper.findComponent({ name: 'TrackerTicketDetailPanel' }).props('ticket').active_execution).toEqual(execution)
      expect(graph().props('flow').nodes[0].data.active).toBe(Boolean(execution))
      expect(graph().props('flow').nodes[0].data.status).toBe('in-progress')
    }
    expect(dialog().props('task')?.status ?? taskA.status).toBe(taskA.status)
    wrapper.unmount()
  })
  it('refreshes stopped delivery in the header, graph and inspector without changing task lifecycle', async () => {
    const agent = { name: 'worker', instructions: 'Execute the assigned contract.' }
    const delivery = { status: 'stopped', ticket_key: ticketA.key, reason: 'Browser access denied.', job_ref: 'synthetic:never-execute', delivery_agent: agent, review_agent: agent }
    const automatic = { ...taskA, metadata_json: { delivery } }
    dataMock([automatic, taskB], [ticketA, ticketB], true)
    const { wrapper, controls, board } = await render('/projects/7/tasks?view=graph&task=task-a')
    const graph = () => wrapper.findComponent({ name: 'TrackerGraphPanel' })
    expect(controls().props('taskDelivery')).toEqual({ status: 'stopped', ticketKey: ticketA.key, reason: delivery.reason })
    expect(graph().props('flow').nodes[0].data).toMatchObject({ active: false, deliveryStopped: true })
    expect(board().props('rows').find((row: { key: string }) => row.key === taskA.key).task.status).toBe(taskA.status)
    graph().vm.$emit('node-click', { event: { stopPropagation: vi.fn() }, node: graph().props('flow').nodes[0] })
    await flushPromises()
    expect(wrapper.findComponent({ name: 'TrackerTicketDetailPanel' }).props('parentTask').metadata_json.delivery.status).toBe('stopped')
    const resumed = { ...automatic, metadata_json: { delivery: { ...delivery, status: 'running' } } }
    dataMock([resumed, taskB], [ticketA, ticketB], true)
    mocks.stream.mock.calls.at(-1)![0].onEvent({ id: 9902, event_type: 'tracker.task.updated', metadata_json: { task_key: 'task-a' } })
    await flushPromises()
    expect(controls().props('taskDelivery')).toEqual({ status: 'running', ticketKey: ticketA.key, reason: null })
    expect(graph().props('flow').nodes[0].data.deliveryStopped).toBe(false)
    controls().vm.$emit('taskSelect', 'task-b')
    await flushPromises()
    expect(controls().props('taskDelivery')).toBeNull()
    wrapper.unmount()
  })
  it('keeps board/header totals whole through graph filtering, task switching and live refresh', async () => {
    const alpha = agentStatsFixture().totals
    const beta = structuredClone(alpha)
    beta.reported_usage.total = 12
    const own = structuredClone(alpha)
    own.reported_usage.total = 7
    const agent = { name: 'worker', instructions: 'Execute the assigned contract.' }
    const automaticTask = { ...taskA, agent_stats: alpha, metadata_json: { delivery: { status: 'running', job_ref: 'synthetic:never-execute', delivery_agent: agent, review_agent: agent } } }
    dataMock([automaticTask, { ...taskB, agent_stats: beta }], [{ ...ticketA, agent_stats: own }, ticketB], true)
    const { wrapper, controls, board } = await render('/projects/7/tasks?view=graph&task=task-a')
    expect(controls().props('taskAgentStats')).toEqual(alpha)
    expect(controls().props('taskAutomated')).toBe(true)
    expect(board().props('rows').find((row: { key: string }) => row.key === 'task-a').task.agent_stats).toEqual(alpha)
    const graph = () => wrapper.findComponent({ name: 'TrackerGraphPanel' })
    expect(graph().props('flow').nodes[0].data.agentStats.reported_usage.total).toBe(7)
    expect(graph().props('flow').nodes[0].data.taskAutomated).toBe(true)
    graph().vm.$emit('node-click', { event: { stopPropagation: vi.fn() }, node: graph().props('flow').nodes[0] })
    await flushPromises()
    expect(wrapper.findComponent({ name: 'TrackerTicketDetailPanel' }).props('parentTask')).toMatchObject(automaticTask)
    graph().vm.$emit('toggle-status', 'complete')
    await flushPromises()
    expect(graph().props('flow').nodes).toHaveLength(0)
    expect(controls().props('taskAgentStats')).toEqual(alpha)
    controls().vm.$emit('taskSelect', 'task-b')
    await flushPromises()
    expect(controls().props('taskAgentStats')).toEqual(beta)
    expect(controls().props('taskAutomated')).toBe(false)
    const refreshed = structuredClone(beta)
    refreshed.reported_usage.total = 99
    dataMock([{ ...taskA, agent_stats: alpha }, { ...taskB, agent_stats: refreshed }])
    mocks.stream.mock.calls.at(-1)![0].onEvent({ id: 9901, event_type: 'tracker.ticket.updated', metadata_json: { task_key: 'task-b' } })
    await flushPromises()
    expect(controls().props('taskAgentStats')).toEqual(refreshed)
    expect(board().props('rows').find((row: { key: string }) => row.key === 'task-b').task.agent_stats).toEqual(refreshed)
    wrapper.unmount()
  })

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-11-10T12:00:00Z'))
    setActivePinia(createPinia())
    sessionStorage.clear()
    mocks.callOperation.mockReset()
    mocks.close.mockReset()
    mocks.stream.mockReset().mockReturnValue({ close: mocks.close })
    dataMock()
  })
  afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

  it('uses URL clauses for filters/display, history and reload without changing Graph picker order', async () => {
    dataMock([
      { ...taskA, priority_key: 'p0', order_index: 1, source_json: { template_key: 'engineering.delivery' }, owner: 'Operator' },
      { ...taskB, priority_key: 'p2', order_index: 0, created_at: '2026-11-01T12:00:00Z' },
      trackerTask({ id: 3, key: 'aborted', title: 'Aborted task', status: 'aborted', priority_key: 'p0' }),
    ], [ticketA, ticketB])
    const { wrapper, router, controls, board } = await render()
    const keys = () => board().props('rows').map((row: { key: string }) => row.key)
    expect(keys()).toEqual(['task-b', 'task-a'])
    expect(controls().props('boardFilters').completed).toBe('past-week')
    expect(controls().props('taskOptions').map((option: { value: string }) => option.value)).toEqual(['task-b', 'aborted', 'task-a'])
    controls().vm.$emit('update:filter', { field: 'priority', operator: 'is', values: ['p0'] })
    await flushPromises()
    expect(keys()).toEqual(['task-a'])
    controls().vm.$emit('update:filter', { field: 'workflow', operator: 'is', value: 'engineering.delivery' })
    await flushPromises()
    controls().vm.$emit('update:filter', { field: 'assignee', operator: 'is', value: 'Operator' })
    await flushPromises()
    controls().vm.$emit('update:filter', { field: 'text', value: 'Alpha' })
    await flushPromises()
    controls().vm.$emit('update:display', { hideEmptyColumns: true, completed: 'none' })
    await flushPromises()
    expect(board().props('hideEmptyColumns')).toBe(true)
    expect(router.currentRoute.value.query).toMatchObject({ search: 'Alpha', priority: 'p0', workflow: 'engineering.delivery', assignee: 'Operator', completed: 'none', hide_empty: '1' })
    controls().vm.$emit('clear')
    await flushPromises()
    expect(keys()).toEqual(['task-b', 'aborted', 'task-a'])
    expect(controls().props('boardFilters')).toMatchObject({ clauses: [], completed: 'all' })
    expect(router.currentRoute.value.query).toEqual({ status: 'all', completed: 'all', hide_empty: '1' })
    router.back()
    await flushPromises()
    expect(keys()).toEqual(['task-a'])
    expect(controls().props('boardFilters').completed).toBe('none')
    router.forward()
    await flushPromises()
    const url = router.currentRoute.value.fullPath
    wrapper.unmount()
    const reloaded = await render(url)
    expect(reloaded.controls().props('boardFilters')).toMatchObject({ clauses: [], completed: 'all' })
    expect(reloaded.board().props('rows')).toHaveLength(3)
    reloaded.wrapper.unmount()
  })

  it('removes legacy restrictions and reconciles Complete plus None in the route consumer', async () => {
    const { wrapper, router, controls } = await render('/projects/7/tasks?ticket_status=complete&focus=blocked&completed=none')
    controls().vm.$emit('removeFilter', 'ticket-status')
    await flushPromises()
    expect(router.currentRoute.value.query.ticket_status).toBeUndefined()
    expect(router.currentRoute.value.query.focus).toBe('blocked')
    controls().vm.$emit('removeFilter', 'blocked')
    await flushPromises()
    controls().vm.$emit('update:filter', { field: 'status', operator: 'is', values: ['complete'] })
    await flushPromises()
    expect(router.currentRoute.value.query.completed).toBe('all')
    expect(controls().props('boardFilters').completed).toBe('all')
    wrapper.unmount()
  })

  it('retains the open task and draft when a new filter excludes it', async () => {
    const { wrapper, router, dialog } = await render('/projects/7/tasks?detail=task-a', true)
    await popup().get('textarea').setValue('Keep my draft')
    await router.push({ query: { ...router.currentRoute.value.query, search: 'Beta' } })
    await flushPromises()
    expect(dialog().props('task')?.key).toBe('task-a')
    expect(popup().get('textarea').element.value).toBe('Keep my draft')
    wrapper.unmount()
  })

  it('opens the complete task index on Board without selecting a task or fetching all tickets', async () => {
    const { wrapper, router, controls, board } = await render()
    expect(controls().props('viewMode')).toBe('board')
    expect(board().props('rows')).toHaveLength(2)
    expect(router.currentRoute.value.query.task).toBeUndefined()
    expect(mocks.callOperation).toHaveBeenCalledWith('tracker.get', { project_id: 7, task_index_only: true, include_graph: false })
    expect(mocks.callOperation).toHaveBeenCalledTimes(1)
    expect(mocks.stream).toHaveBeenLastCalledWith(expect.objectContaining({ projectId: 7, taskKey: undefined }))
    wrapper.unmount()
    expect(mocks.close).toHaveBeenCalled()
  })

  it('keeps controls available through loading, error and retry, then shows an empty project', async () => {
    let fail!: (error: Error) => void
    mocks.callOperation.mockReturnValue(new Promise((_resolve, reject) => { fail = reject }))
    const { wrapper, board, controls } = await render()
    expect(wrapper.text()).toContain('Loading work')
    expect(controls().exists()).toBe(true)
    fail(new Error('Fixture index unavailable'))
    await flushPromises()
    expect(wrapper.text()).toContain('Fixture index unavailable')
    dataMock([], [])
    await wrapper.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).not.toContain('Fixture index unavailable')
    expect(wrapper.text()).toContain('No tasks yet.')
    expect(board().props('rows')).toEqual([])
    wrapper.unmount()
  })

  it.each([true, false])('announces an unavailable count until retry resolves (empty=%s)', async (empty) => {
    let fail!: (error: Error) => void
    mocks.callOperation.mockReturnValue(new Promise((_resolve, reject) => { fail = reject }))
    const { wrapper } = await render('/projects/7/tasks', false, true)
    const count = () => wrapper.get('.board-filter-bar__count')
    expect(count().text()).toBe('— / — tasks')
    expect(count().attributes('aria-label')).toBe('Task count unavailable')
    fail(new Error('Fixture count unavailable'))
    await flushPromises()
    expect(wrapper.text()).toContain('Fixture count unavailable')
    expect(count().text()).toBe('— / — tasks')
    dataMock(empty ? [] : [taskA, taskB], empty ? [] : [ticketA, ticketB])
    await wrapper.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click')
    await flushPromises()
    const knownCount = empty ? '0 / 0 tasks' : '2 / 2 tasks'
    expect(count().text()).toBe(knownCount)
    expect(count().attributes('aria-label')).toBeUndefined()
    // A later failed refresh retains the last accepted index, including a known empty result.
    mocks.callOperation.mockReturnValueOnce(new Promise((_resolve, reject) => { fail = reject }))
    await wrapper.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click')
    expect(count().text()).toBe(knownCount)
    fail(new Error('Fixture refresh unavailable'))
    await flushPromises()
    expect(wrapper.text()).toContain('Fixture refresh unavailable')
    expect(count().text()).toBe(knownCount)
    wrapper.unmount()
  })

  it('opens the clicked task independently of map selection and restores Board filters and mounted scroll region', async () => {
    const { wrapper, router, controls, board, dialog } = await render('/projects/7/tasks?view=graph&task=task-a')
    wrapper.findComponent({ name: 'UiSegmentedControl' }).vm.$emit('select', 'board')
    await flushPromises()
    const boardInstance = board().vm
    controls().vm.$emit('update:filter', { field: 'text', value: 'Beta' })
    await flushPromises()
    expect(board().props('rows').map((row: {key: string}) => row.key)).toEqual(['task-b'])
    board().vm.$emit('openTask', 'task-b')
    await flushPromises()
    expect(dialog().props('task')?.key).toBe('task-b')
    expect(dialog().props('tickets').map((ticket: TrackerTicket) => ticket.key)).toEqual(['ticket-b'])
    dialog().vm.$emit('viewDependencies', 'task-b')
    await flushPromises()
    expect(router.currentRoute.value.query).toMatchObject({ view: 'graph', task: 'task-b', search: 'Beta' })
    expect(dialog().props('modelValue')).toBe(false)
    expect(wrapper.findComponent({ name: 'TrackerGraphPanel' }).props('activeTaskTitle')).toBe('Beta task')
    wrapper.findComponent({ name: 'UiSegmentedControl' }).vm.$emit('select', 'board')
    await flushPromises()
    expect(board().vm).toBe(boardInstance)
    expect(controls().props('boardFilters').clauses).toContainEqual({ field: 'text', value: 'Beta' })
    expect(board().props('rows')).toHaveLength(1)
    wrapper.unmount()
  })

  it('refreshes the index on Graph return and preserves the mounted Board scroll and URL', async () => {
    dataMock()
    const { wrapper, router, controls, board } = await render('/projects/7/tasks?search=Alpha&completed=all')
    const boardElement = board().element as HTMLElement
    boardElement.scrollLeft = 320
    boardElement.scrollTop = 120
    await wrapper.get('[aria-label="Work view"] [role="tab"]:last-child').trigger('click')
    await flushPromises()
    dataMock([{ ...taskA, title: 'Alpha updated from another task', priority_key: 'p0' }, taskB])
    await wrapper.get('[aria-label="Work view"] [role="tab"]:first-child').trigger('click')
    await flushPromises()
    expect(board().element).toBe(boardElement)
    expect(boardElement.scrollLeft).toBe(320)
    expect(boardElement.scrollTop).toBe(120)
    expect(board().props('rows')[0].task.title).toBe('Alpha updated from another task')
    expect(router.currentRoute.value.query).toMatchObject({ search: 'Alpha', completed: 'all' })
    expect(controls().props('taskOptions').map((option: { value: string }) => option.value)).toEqual(['task-b', 'task-a'])
    wrapper.unmount()
  })

  it('ignores a late Board-return index after project navigation', async () => {
    const { wrapper, router, board } = await render('/projects/7/tasks?view=graph&task=task-a')
    let finish!: (snapshot: TrackerSnapshot) => void
    const late = new Promise<TrackerSnapshot>(resolve => { finish = resolve })
    const normal = mocks.callOperation.getMockImplementation()!
    mocks.callOperation.mockImplementation((name, args) => args.project_id === 7 && args.task_index_only ? late : normal(name, args))
    await wrapper.get('[aria-label="Work view"] [role="tab"]:first-child').trigger('click')
    await flushPromises()
    await router.push('/projects/8/tasks')
    await flushPromises()
    finish(trackerSnapshot([{ ...taskA, title: 'Old project response' }], []))
    await flushPromises()
    expect(board().props('rows').map((row: { task: TrackerTask }) => row.task.title)).not.toContain('Old project response')
    expect(mocks.stream).toHaveBeenLastCalledWith(expect.objectContaining({ projectId: 8 }))
    wrapper.unmount()
  })

  it('offers every task in the map picker even when Board filters exclude it', async () => {
    const { wrapper, controls, board } = await render('/projects/7/tasks?search=Alpha')
    wrapper.findComponent({ name: 'UiSegmentedControl' }).vm.$emit('select', 'graph')
    await flushPromises()
    expect(controls().props('taskOptions').map((option: {value: string}) => option.value)).toContain('task-b')
    controls().vm.$emit('taskSelect', 'task-b')
    await flushPromises()
    expect(controls().props('activeTaskKey')).toBe('task-b')
    expect(wrapper.findComponent({ name: 'TrackerGraphPanel' }).props('activeTaskTitle')).toBe('Beta task')
    wrapper.findComponent({ name: 'UiSegmentedControl' }).vm.$emit('select', 'board')
    await flushPromises()
    expect(board().props('rows').map((row: {key: string}) => row.key)).toEqual(['task-a'])
    wrapper.unmount()
  })

  it('supports task and ticket inspection, Back and Forward through explicit URL identities', async () => {
    const { wrapper, router, board, dialog } = await render()
    board().vm.$emit('openTask', 'task-b')
    await flushPromises()
    expect(router.currentRoute.value.query.detail).toBe('task-b')
    dialog().vm.$emit('openTicket', ticketB)
    await flushPromises()
    expect(dialog().props('modelValue')).toBe(true)
    expect(dialog().props('ticket')?.key).toBe('ticket-b')
    router.back()
    await vi.waitFor(() => expect(router.currentRoute.value.query.ticket).toBeUndefined())
    expect(dialog().props('modelValue')).toBe(true)
    router.back()
    await vi.waitFor(() => expect(dialog().props('modelValue')).toBe(false))
    router.forward()
    await vi.waitFor(() => expect(dialog().props('task')?.key).toBe('task-b'))
    wrapper.unmount()
  })

  it('defaults direct map entry to the first unfiltered picker task and replaces the route entry', async () => {
    const { wrapper, router, controls } = await render('/projects/7/tasks?view=graph')
    expect(controls().props('activeTaskKey')).toBe('task-b')
    expect(router.currentRoute.value.query.task).toBe('task-b')
    expect(mocks.callOperation).toHaveBeenCalledWith('tracker.get', expect.objectContaining({ task_key: 'task-b', include_graph: true }))
    wrapper.findComponent({ name: 'UiSegmentedControl' }).vm.$emit('select', 'board')
    await flushPromises()
    sessionStorage.clear()
    controls().vm.$emit('update:filter', { field: 'text', value: 'Alpha' })
    await flushPromises()
    wrapper.findComponent({ name: 'UiSegmentedControl' }).vm.$emit('select', 'graph')
    await flushPromises()
    expect(controls().props('activeTaskKey')).toBe('task-b')
    expect(router.currentRoute.value.query.task).toBe('task-b')
    router.back()
    await vi.waitFor(() => expect(controls().props('viewMode')).toBe('board'))
    expect(router.currentRoute.value.query.search).toBe('Alpha')
    wrapper.unmount()
  })

  it('restores the last valid map selection per project and keeps an explicit URL authoritative', async () => {
    const first = await render('/projects/7/tasks?view=graph&task=task-a')
    expect(sessionStorage.getItem('stackos:work-map-task:7')).toBe('task-a')
    first.wrapper.unmount()
    const restored = await render('/projects/7/tasks?view=graph')
    expect(restored.controls().props('activeTaskKey')).toBe('task-a')
    expect(restored.router.currentRoute.value.query.task).toBe('task-a')
    restored.wrapper.unmount()
    const explicitValid = await render('/projects/7/tasks?view=graph&task=task-b')
    expect(explicitValid.controls().props('activeTaskKey')).toBe('task-b')
    explicitValid.wrapper.unmount()
    const explicit = await render('/projects/7/tasks?view=graph&task=missing')
    expect(explicit.wrapper.text()).toContain('Task unavailable')
    expect(explicit.router.currentRoute.value.query.task).toBe('missing')
    expect(sessionStorage.getItem('stackos:work-map-task:7')).toBe('task-b')
    explicit.wrapper.unmount()
    expect(sessionStorage.getItem('stackos:work-map-task:8')).toBeNull()
  })

  it('replaces deleted remembered tasks with the first current picker task on map entry', async () => {
    sessionStorage.setItem('stackos:work-map-task:7', 'deleted-task')
    const { wrapper, router, controls } = await render()
    expect(sessionStorage.getItem('stackos:work-map-task:7')).toBeNull()
    wrapper.findComponent({ name: 'UiSegmentedControl' }).vm.$emit('select', 'graph')
    await flushPromises()
    expect(controls().props('activeTaskKey')).toBe('task-b')
    expect(router.currentRoute.value.query.task).toBe('task-b')
    expect(sessionStorage.getItem('stackos:work-map-task:7')).toBe('task-b')
    wrapper.unmount()
  })

  it('waits for a successful index and honors a newer URL selection while that request is pending', async () => {
    let resolveIndex!: (value: TrackerSnapshot) => void
    const pending = new Promise<TrackerSnapshot>(resolve => { resolveIndex = resolve })
    const read = mocks.callOperation.getMockImplementation()!
    mocks.callOperation.mockImplementation((name, args) => name === 'tracker.get' && args.task_index_only ? pending : read(name, args))
    const { wrapper, router, controls } = await render('/projects/7/tasks?view=graph')
    expect(router.currentRoute.value.query.task).toBeUndefined()
    expect(wrapper.text()).toContain('Loading work')
    await router.push('/projects/7/tasks?view=graph&task=task-a')
    resolveIndex(trackerSnapshot([taskA, taskB], []))
    await flushPromises()
    expect(controls().props('activeTaskKey')).toBe('task-a')
    expect(router.currentRoute.value.query.task).toBe('task-a')
    expect(mocks.callOperation).toHaveBeenCalledWith('tracker.get', expect.objectContaining({ task_key: 'task-a', include_graph: true }))
    expect(mocks.callOperation).not.toHaveBeenCalledWith('tracker.get', expect.objectContaining({ task_key: 'task-b' }))
    wrapper.unmount()
  })

  it('does not promote stale memory into an explicit URL when entering the map before the index arrives', async () => {
    sessionStorage.setItem('stackos:work-map-task:7', 'deleted-task')
    let resolveIndex!: (value: TrackerSnapshot) => void
    const pending = new Promise<TrackerSnapshot>(resolve => { resolveIndex = resolve })
    const read = mocks.callOperation.getMockImplementation()!
    mocks.callOperation.mockImplementation((name, args) => name === 'tracker.get' && args.task_index_only ? pending : read(name, args))
    const { wrapper, router, controls } = await render()
    wrapper.findComponent({ name: 'UiSegmentedControl' }).vm.$emit('select', 'graph')
    await flushPromises()
    expect(router.currentRoute.value.query.task).toBeUndefined()
    resolveIndex(trackerSnapshot([taskA, taskB], []))
    await flushPromises()
    expect(router.currentRoute.value.query.task).toBe('task-b')
    expect(controls().props('activeTaskKey')).toBe('task-b')
    expect(mocks.callOperation).not.toHaveBeenCalledWith('tracker.get', expect.objectContaining({ task_key: 'deleted-task' }))
    wrapper.unmount()
  })

  it('does not choose a task for an empty or failed index and defaults after a successful retry', async () => {
    dataMock([], [])
    const empty = await render('/projects/7/tasks?view=graph')
    expect(empty.controls().props('activeTaskKey')).toBe('')
    expect(empty.router.currentRoute.value.query.task).toBeUndefined()
    expect(empty.wrapper.text()).toContain('No tasks yet')
    empty.wrapper.unmount()
    mocks.callOperation.mockRejectedValue(new Error('Index unavailable'))
    const failed = await render('/projects/7/tasks?view=graph')
    expect(failed.router.currentRoute.value.query.task).toBeUndefined()
    expect(failed.wrapper.text()).toContain('Index unavailable')
    dataMock()
    await failed.wrapper.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click')
    await flushPromises()
    expect(failed.router.currentRoute.value.query.task).toBe('task-b')
    failed.wrapper.unmount()
  })

  it('keeps a valid selected task on refresh even when a newer task appears', async () => {
    const { wrapper, router, controls } = await render('/projects/7/tasks?view=graph&task=task-a')
    dataMock([trackerTask({ id: 9, key: 'task-new', created_at: '2026-10-29T01:00:00Z' }), taskA, taskB])
    await wrapper.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click')
    await flushPromises()
    expect(controls().props('taskOptions')[0].value).toBe('task-new')
    expect(controls().props('activeTaskKey')).toBe('task-a')
    expect(router.currentRoute.value.query.task).toBe('task-a')
    wrapper.unmount()
  })

  it('keeps node selection on the map and opens only explicit Details in the existing side inspector', async () => {
    const { wrapper, router, dialog } = await render('/projects/7/tasks?view=graph&task=task-a', true)
    const map = wrapper.findComponent({ name: 'TrackerGraphPanel' })
    const selectNode = () => map.vm.$emit('nodeClick', { event: new MouseEvent('click'), node: { id: 'ticket:ticket-a', data: { itemKind: 'ticket', itemKey: 'ticket-a' } } })
    selectNode()
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ view: 'graph', task: 'task-a' })
    expect(map.props('selectionVisible')).toBe(true)
    expect(map.props('selectedTicket')?.key).toBe('ticket-a')
    expect(dialog().props('modelValue')).toBe(false)
    map.vm.$emit('openSelectedDetail')
    await flushPromises()
    const inspector = () => wrapper.findComponent({ name: 'TrackerTicketDetailPanel' })
    expect(inspector().props('modelValue')).toBe(true)
    expect(inspector().findComponent({ name: 'InspectableDetailDrawer' }).exists()).toBe(true)
    expect(document.body.textContent).toContain('Ticket')
    expect(router.currentRoute.value.query.ticket).toBeUndefined()
    selectNode()
    await flushPromises()
    expect(inspector().props('modelValue')).toBe(false)
    map.vm.$emit('openSelectedDetail')
    await flushPromises()
    map.vm.$emit('edgeClick', { event: new MouseEvent('click'), edge: { id: 'edge-a', target: 'ticket:ticket-a' } })
    await flushPromises()
    expect(inspector().props('modelValue')).toBe(false)
    expect(map.props('selectionLabel')).toBe('Selected relation')
    map.vm.$emit('openSelectedDetail')
    await flushPromises()
    map.vm.$emit('clearGraphFocus')
    await flushPromises()
    expect(map.props('selectionVisible')).toBe(false)
    expect(inspector().props('modelValue')).toBe(false)
    selectNode()
    await flushPromises()
    map.vm.$emit('openSelectedDetail')
    await flushPromises()
    map.vm.$emit('paneClick', new MouseEvent('click'))
    await flushPromises()
    expect(inspector().props('modelValue')).toBe(false)
    expect(map.props('selectionVisible')).toBe(false)
    selectNode()
    await flushPromises()
    map.vm.$emit('openSelectedDetail')
    await flushPromises()
    await router.push('/projects/7/tasks?view=graph&task=task-a&detail=task-b')
    await flushPromises()
    expect(dialog().props('task')?.key).toBe('task-b')
    expect(dialog().props('modelValue')).toBe(true)
    expect(inspector().props('modelValue')).toBe(false)
    expect(router.currentRoute.value.query.task).toBe('task-a')
    wrapper.unmount()
  })

  it('keeps one persistent frame and restores task reading position, disclosures and row focus', async () => {
    dataMock([taskA, taskB], [ticketA, ...Array.from({ length: 6 }, (_, index) => trackerTicket({
      id: 20 + index, key: `extra-${index}`, title: `Extra ticket ${index}`, task_id: 2, task_key: 'task-b',
    })), ticketB])
    const { wrapper, router } = await render('/projects/7/tasks?detail=task-b', true)
    expect(popup().findAll('[role="dialog"]')).toHaveLength(1)
    const frame = popup().get('[role="dialog"]').element
    const body = popup().get('.ui-dialog__body').element as HTMLElement
    const more = popup().findAll('details').find(item => item.text().includes('More details'))!.element
    more.open = true
    const trace = popup().findAll('details').find(item => item.get('summary').text() === 'Trace')!.element
    trace.open = true
    const list = popup().get('[aria-label="Task tickets"]').element as HTMLElement
    list.scrollTop = 40
    await popup().get('[data-edit-content]').trigger('click')
    await popup().get('[data-description-editor]').setValue('Retained description draft')
    const editor = popup().get('[data-description-editor]').element
    body.scrollTop = 180
    const ticketButton = popup().findAll('button').find(button => button.text().includes('Beta ticket'))!
    ;(ticketButton.element as HTMLElement).focus()
    await ticketButton.trigger('click')
    await flushPromises()
    expect(popup().get('[role="dialog"]').element).toBe(frame)
    expect(body.scrollTop).toBe(0)
    expect(popup().findAll('[role="dialog"]')).toHaveLength(1)
    expect(popup().get('[role="dialog"]').text()).toContain('Beta ticket')
    expect(popup().get('[role="dialog"]').element.contains(document.activeElement)).toBe(true)
    const back = popup().findAll('button').find(button => button.text() === 'Back to task')!
    await back.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.detail).toBe('task-b')
    expect(router.currentRoute.value.query.ticket).toBeUndefined()
    expect(popup().findAll('[role="dialog"]')).toHaveLength(1)
    expect(popup().get('[role="dialog"]').text()).toContain('Beta task')
    expect(popup().get('[role="dialog"]').element).toBe(frame)
    expect(more.isConnected).toBe(true)
    expect(more.open).toBe(true)
    expect(trace.open).toBe(true)
    expect(popup().get('[data-description-editor]').element).toBe(editor)
    expect((editor as HTMLTextAreaElement).value).toBe('Retained description draft')
    expect(list.scrollTop).toBe(40)
    expect(body.scrollTop).toBe(180)
    expect(document.activeElement).toBe(ticketButton.element)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(popup().findAll('[role="dialog"]')).toHaveLength(0)
    wrapper.unmount()
  })

  it('resets reading state for a different task without replacing the detail frame', async () => {
    const { wrapper, router } = await render('/projects/7/tasks?detail=task-b', true)
    const frame = popup().get('[role="dialog"]').element
    const body = popup().get('.ui-dialog__body').element as HTMLElement
    popup().findAll('details').find(item => item.get('summary').text() === 'More details')!.element.open = true
    body.scrollTop = 150
    await router.push({ query: { detail: 'task-a' } })
    await flushPromises()
    expect(popup().get('[role="dialog"]').element).toBe(frame)
    expect(popup().get('h2').text()).toBe('Alpha task')
    expect(popup().findAll('details').every(item => !item.element.open)).toBe(true)
    expect(body.scrollTop).toBe(0)
    wrapper.unmount()
  })

  it('labels ticket count links explicitly while grouping by each task’s own status', async () => {
    const { wrapper, board, controls } = await render('/projects/7/tasks?ticket_status=complete')
    expect(controls().props('boardFilters').clauses).toContainEqual({ field: 'ticket-status', value: 'complete' })
    expect(board().props('rows').map((row: {key: string}) => row.key)).toEqual(['task-b'])
    wrapper.unmount()
  })

  it('never silently substitutes another task for an unavailable map or detail identity', async () => {
    const { wrapper, router, dialog } = await render('/projects/7/tasks?view=graph&task=missing')
    expect(wrapper.text()).toContain('Task unavailable')
    expect(router.currentRoute.value.query.task).toBe('missing')
    await router.push('/projects/7/tasks?detail=missing')
    await flushPromises()
    expect(dialog().props('task')).toBeNull()
    expect(dialog().props('error')).toContain('no longer available')
    expect(mocks.callOperation).not.toHaveBeenCalledWith('tracker.get', expect.objectContaining({ task_key: 'task-a' }))
    wrapper.unmount()
  })

  it('keeps unavailable ticket URLs explicit in the same bounded frame', async () => {
    const { wrapper, router } = await render('/projects/7/tasks?detail=task-b&ticket=missing', true)
    const frame = popup().get('[role="dialog"]').element
    expect(popup().text()).toContain('This ticket is no longer available')
    expect(popup().findAll('button').some(button => button.text() === 'Back to task')).toBe(true)
    await router.push('/projects/7/tasks?ticket=missing')
    await flushPromises()
    expect(popup().get('[role="dialog"]').element).toBe(frame)
    expect(popup().text()).toContain('This ticket is no longer available')
    expect(popup().findAll('button').some(button => button.text() === 'Back to task')).toBe(false)
    wrapper.unmount()
  })

  it('ignores stale detail responses after another task is opened', async () => {
    let resolveA!: (value: TrackerSnapshot) => void
    const pendingA = new Promise<TrackerSnapshot>(resolve => { resolveA = resolve })
    const defaultMock = mocks.callOperation.getMockImplementation()!
    mocks.callOperation.mockImplementation((name: string, args: Record<string, unknown>) => name === 'tracker.get' && args.task_key === 'task-a' ? pendingA : defaultMock(name, args))
    const { wrapper, router, dialog } = await render('/projects/7/tasks', true)
    await router.push('/projects/7/tasks?detail=task-a')
    await flushPromises()
    expect(dialog().props('loading')).toBe(true)
    await router.push('/projects/7/tasks?detail=task-b')
    await flushPromises()
    expect(dialog().props('task')?.key).toBe('task-b')
    await popup().get('textarea').setValue('Draft belongs to Beta')
    resolveA(trackerSnapshot([taskA], [ticketA]))
    await flushPromises()
    expect(dialog().props('task')?.key).toBe('task-b')
    expect(popup().get('textarea').element.value).toBe('Draft belongs to Beta')
    wrapper.unmount()
  })

  it('refreshes the board for project events without a selected task', async () => {
    const { wrapper, board } = await render()
    const onEvent = mocks.stream.mock.calls.at(-1)![0].onEvent
    dataMock([{ ...taskA, status: 'complete', completed_at: '2026-11-10T11:00:00Z' }, taskB], [ticketA, ticketB])
    onEvent({ id: 9301, event_type: 'tracker.task.status_changed', metadata_json: { task_key: 'task-a' } })
    await flushPromises()
    expect(board().props('rows').find((row: {key: string}) => row.key === 'task-a').task.status).toBe('complete')
    expect(mocks.stream).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it.each(['initial', 'navigation'])('does not reopen a stream after unmount with a pending %s map load', async (entry) => {
    let resolveGraph!: (value: TrackerSnapshot) => void
    const pending = new Promise<TrackerSnapshot>(resolve => { resolveGraph = resolve })
    const read = mocks.callOperation.getMockImplementation()!
    mocks.callOperation.mockImplementation((name, args) => name === 'tracker.get' && args.include_graph
      ? pending : read(name, args))
    const { wrapper, router } = await render(entry === 'initial'
      ? '/projects/7/tasks?view=graph&task=task-a&detail=task-b' : '/projects/7/tasks')
    if (entry === 'navigation') {
      await router.push('/projects/7/tasks?view=graph&task=task-a&detail=task-b')
      await flushPromises()
    }
    expect(mocks.callOperation).toHaveBeenCalledWith('tracker.get', expect.objectContaining({ task_key: 'task-a', include_graph: true }))
    wrapper.unmount()
    const streamCount = mocks.stream.mock.calls.length
    const requestCount = mocks.callOperation.mock.calls.length
    resolveGraph(trackerSnapshot([taskA], [ticketA], true))
    await flushPromises()
    expect(mocks.stream).toHaveBeenCalledTimes(streamCount)
    expect(mocks.callOperation).toHaveBeenCalledTimes(requestCount)
  })

  it('keeps only the new project stream when the previous project map response arrives late', async () => {
    let resolveGraph!: (value: TrackerSnapshot) => void
    const pending = new Promise<TrackerSnapshot>(resolve => { resolveGraph = resolve })
    const read = mocks.callOperation.getMockImplementation()!
    mocks.callOperation.mockImplementation((name, args) => name === 'tracker.get' && args.include_graph
      ? pending : read(name, args))
    const { wrapper, router, board } = await render('/projects/7/tasks')
    await router.push('/projects/7/tasks?view=graph&task=task-a')
    await flushPromises()
    await router.push('/projects/8/tasks')
    await flushPromises()
    expect(mocks.stream).toHaveBeenLastCalledWith(expect.objectContaining({ projectId: 8, taskKey: undefined }))
    const streamCount = mocks.stream.mock.calls.length
    const requestCount = mocks.callOperation.mock.calls.length
    resolveGraph(trackerSnapshot([{ ...taskA, title: 'Stale old-project task' }], [ticketA], true))
    await flushPromises()
    expect(mocks.stream).toHaveBeenCalledTimes(streamCount)
    expect(mocks.callOperation).toHaveBeenCalledTimes(requestCount)
    expect(board().props('rows').map((row: { task: TrackerTask }) => row.task.title)).not.toContain('Stale old-project task')
    wrapper.unmount()
  })

  it('saves the popup content through the narrow operation then refetches current details and board aggregates', async () => {
    const saved = { ...taskB, description: 'Updated description', updated_at: 'revision-2',
      context_json: { operator_inputs: [{ id: 'input-1', text: 'Please check the edge case.', created_at: '2026-10-28T12:00:00Z', actor: 'local-user:operator' }] },
    }
    const read = mocks.callOperation.getMockImplementation()!
    mocks.callOperation.mockImplementation(async (name, args) => {
      if (name !== 'tracker.updateTaskContent') return read(name, args)
      dataMock([taskA, saved])
      return { data: { task: { ...saved, description: 'Older receipt description', ticket_summary: null }, replayed: true } }
    })
    const { wrapper, board } = await render('/projects/7/tasks?detail=task-b', true)
    const button = (text: string) => popup().findAll('button').find(item => item.text() === text)!
    await button('Edit description').trigger('click')
    const fields = popup().findAll('textarea')
    await fields[0]!.setValue('Updated description')
    await fields[1]!.setValue('Please check the edge case.')
    await button('Save description and input').trigger('click')
    await flushPromises()
    expect(mocks.callOperation).toHaveBeenCalledWith('tracker.updateTaskContent', expect.objectContaining({
      task_key: 'task-b', project_id: 7, description: 'Updated description', additional_input: 'Please check the edge case.',
      expected_updated_at: taskB.updated_at, idempotency_key: expect.any(String),
    }))
    expect(popup().text()).toContain('Updated description')
    expect(popup().text()).not.toContain('Older receipt description')
    expect(popup().get('[aria-label="Additional input history"]').text()).toContain('Please check the edge case.')
    expect(popup().get('textarea').element.value).toBe('')
    expect(board().props('rows').find((row: { key: string }) => row.key === 'task-b').completedCount).toBe(1)
    expect(board().props('rows').find((row: { key: string }) => row.key === 'task-b').totalCount).toBe(1)
    expect(popup().text()).toContain('Changes saved.')
    wrapper.unmount()
  })

  it('protects the same draft on close, history, ticket inspection, dependencies and leaving Work', async () => {
    const { wrapper, router, board, dialog } = await render('/projects/7/tasks', true)
    board().vm.$emit('openTask', 'task-b')
    await flushPromises()
    await popup().get('textarea').setValue('Keep my unsaved input')
    const readsBeforeAbortedNavigation = mocks.callOperation.mock.calls.length
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    dialog().vm.$emit('update:modelValue', false)
    await flushPromises()
    expect(router.currentRoute.value.query.detail).toBe('task-b')
    router.back()
    await vi.waitFor(() => expect(confirm).toHaveBeenCalledTimes(2))
    await flushPromises()
    expect(router.currentRoute.value.query.detail).toBe('task-b')
    dialog().vm.$emit('openTicket', ticketB)
    await flushPromises()
    expect(router.currentRoute.value.query.ticket).toBe('ticket-b')
    expect(confirm).toHaveBeenCalledTimes(2)
    await router.push({ query: { detail: 'task-b' } })
    await flushPromises()
    expect(popup().get('textarea').element.value).toBe('Keep my unsaved input')
    dialog().vm.$emit('viewDependencies', 'task-b')
    await flushPromises()
    expect(router.currentRoute.value.query.view).toBeUndefined()
    await router.push('/other')
    expect(router.currentRoute.value.query.detail).toBe('task-b')
    expect(popup().get('textarea').element.value).toBe('Keep my unsaved input')
    expect(mocks.callOperation).toHaveBeenCalledTimes(readsBeforeAbortedNavigation)
    expect(popup().findAll('[role="dialog"]')).toHaveLength(1)
    confirm.mockReturnValue(true)
    dialog().vm.$emit('viewDependencies', 'task-b')
    await flushPromises()
    expect(router.currentRoute.value.query).toMatchObject({ view: 'graph', task: 'task-b' })
    expect(router.currentRoute.value.query.detail).toBeUndefined()
    wrapper.unmount()
  })

  it('blocks close and history during an in-flight content save and permits them once refreshed', async () => {
    let resolve!: (value: unknown) => void
    const read = mocks.callOperation.getMockImplementation()!
    mocks.callOperation.mockImplementation((name, args) => name === 'tracker.updateTaskContent'
      ? new Promise(done => { resolve = done }) : read(name, args))
    const { wrapper, router, board, dialog } = await render('/projects/7/tasks', true)
    board().vm.$emit('openTask', 'task-b')
    await flushPromises()
    await popup().get('textarea').setValue('Saving input')
    const save = popup().findAll('button').find(item => item.text() === 'Add input')!
    await save.trigger('click')
    await flushPromises()
    expect(popup().get('textarea').attributes('readonly')).toBeDefined()
    dialog().vm.$emit('update:modelValue', false)
    await flushPromises()
    expect(router.currentRoute.value.query.detail).toBe('task-b')
    router.back()
    await flushPromises()
    expect(router.currentRoute.value.query.detail).toBe('task-b')
    expect(popup().text()).toContain('Please wait for the current request')
    resolve({ data: { task: taskB, replayed: false } })
    await flushPromises()
    dialog().vm.$emit('update:modelValue', false)
    await flushPromises()
    expect(router.currentRoute.value.query.detail).toBeUndefined()
    wrapper.unmount()
  })
})

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
