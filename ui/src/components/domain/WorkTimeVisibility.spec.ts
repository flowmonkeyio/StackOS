import { enableAutoUnmount, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick, ref } from 'vue'
import { WorkDurationOutCoverage } from '@/api'
import type { UsageFeedState } from '@/lib/usageUpdates'
import type { TrackerTask } from '@/lib/task-tracker/types'
import type { TrackerVueNodeData } from '@/lib/task-tracker/graphModel'
import { usageFeedKey } from '@/composables/useUsageFeed'
import * as time from '@/lib/stackos/time'
import TrackerBoardPanel from '@/views/task-tracker/TrackerBoardPanel.vue'
import TicketGraphNode from '@/views/task-tracker/TicketGraphNode.vue'
import { buildTaskProgressRows } from '@/views/task-tracker/viewModel'
import ActiveTaskRow from '@/views/home/ActiveTaskRow.vue'
import { activeTaskFixture } from '@/views/home/activeTaskFixtures'

enableAutoUnmount(afterEach)
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })
const execution = { live_usage: null, invocation_id: 'call-1', agent: 'sdlc_delivery', host_key: 'codex', model: 'fixture', started_at: '2026-11-08T01:08:00Z', ticket_key: 'child' }
const duration = { coverage: WorkDurationOutCoverage.complete, total_seconds: 840, observed_at: '2026-11-08T01:22:00Z', endpoint_at: '2026-11-08T01:22:00Z', recorded_intervals: 2, unavailable_intervals: 0, issues: [], live: [execution] }
function task(id: number): TrackerTask {
  return { id, project_id: 1, tracker_id: 1, key: `hidden-${id}`, title: `Hidden ${id}`, goal: '', description: '', status: 'complete',
    board_column: 'done', priority_key: 'p2', lane_key: 'done', owner: 'Codex', task_type: 'manual', order_index: id, source_kind: 'manual', source_json: null,
    definition_of_done_json: [], constraints_json: [], expected_outcomes_json: [], completion_evidence_json: null, context_json: null,
    metadata_json: null, created_by: 'codex', created_at: '2026-10-28T00:00:00Z', updated_at: '2026-10-28T00:00:00Z', started_at: null, completed_at: null, work_duration: null }
}
const rows = (tasks: TrackerTask[]) => buildTaskProgressRows(tasks, new Map(), { search: '', status: 'all', workflow: '', assignee: '' })
const liveFeed = () => ref<UsageFeedState>({ status: 'live', receivedAt: Date.parse(duration.observed_at) })

describe('canonical duration visibility and demand', () => {
  it.each([5, 50])('does zero heartbeat duration work for %i inactive Board cards', async count => {
    const feed = liveFeed(), format = vi.spyOn(time, 'formatWorkTime')
    const wrapper = mount(TrackerBoardPanel, { props: { rows: rows(Array.from({ length: count }, (_, index) => task(index))), metricsActive: false, metricDemandEnabled: true },
      global: { provide: { [usageFeedKey as symbol]: feed } } })
    await nextTick(); format.mockClear()
    feed.value = { status: 'live', receivedAt: feed.value.receivedAt! + 60_000 }
    await nextTick()
    expect(format).not.toHaveBeenCalled()
    expect(wrapper.findAll('.active-execution-line__elapsed')).toHaveLength(count)
    expect(wrapper.findAll('canvas')).toHaveLength(0)
  })

  it('keeps hidden live Board work dormant, then reveals current partial values and resumes the shared heartbeat', async () => {
    vi.useFakeTimers({ toFake: ['Date'] }).setSystemTime(new Date(duration.observed_at))
    const feed = liveFeed(), format = vi.spyOn(time, 'formatWorkTime')
    const record = { ...task(1), status: 'in-progress' as const, active_execution: execution, work_duration: duration }
    const wrapper = mount(TrackerBoardPanel, { props: { rows: rows([record]), metricsActive: false, metricDemandEnabled: true },
      global: { provide: { [usageFeedKey as symbol]: feed } } })
    format.mockClear()
    const current = { ...record, work_duration: { ...duration, total_seconds: 900, coverage: WorkDurationOutCoverage.partial, observed_at: '2026-11-08T01:23:00Z' } }
    await wrapper.setProps({ rows: rows([current]), metricsActive: true }) // active presentation, still undemanded
    vi.setSystemTime(new Date('2026-11-08T01:25:00Z'))
    feed.value = { status: 'live', receivedAt: Date.now() }; await nextTick()
    expect(format).not.toHaveBeenCalled()
    await wrapper.setProps({ metricDemandEnabled: false }) // admit the retained card through its existing demand control
    expect(wrapper.get('.active-execution-line__elapsed').attributes('aria-label')).toBe('Work time: 17m · Partial coverage')
    format.mockClear()
    vi.setSystemTime(new Date('2026-11-08T01:26:00Z'))
    feed.value = { status: 'live', receivedAt: Date.now() }; await nextTick()
    expect(format).toHaveBeenCalledTimes(1)
    expect(wrapper.get('.active-execution-line__elapsed').text()).toBe('18mPartial')
  })

  it('suspends undemanded Graph duration and reveals the latest final receipt without changing the footer', async () => {
    const feed = liveFeed(), format = vi.spyOn(time, 'formatWorkTime')
    const data: TrackerVueNodeData = { raw: { id: 900 } as TrackerVueNodeData['raw'], itemKind: 'ticket', itemKey: 'hidden', label: 'Hidden ticket', status: 'complete', laneKey: 'done', priorityKey: 'p2', owner: 'Codex', taskAutomated: false,
      lifecycle: { status: 'complete', started_at: null, completed_at: null, work_duration: null } }
    const wrapper = mount(TicketGraphNode, { props: { metricsVisible: false, data }, global: { provide: { [usageFeedKey as symbol]: feed }, stubs: { Handle: true } } })
    const footer = wrapper.get('.ticket-graph-node__footer').element
    format.mockClear()
    await wrapper.setProps({ data: { ...data, lifecycle: { ...data.lifecycle!, work_duration: { ...duration, total_seconds: 960, live: [] } } } })
    feed.value = { status: 'live', receivedAt: feed.value.receivedAt! + 60_000 }; await nextTick()
    expect(format).not.toHaveBeenCalled()
    await wrapper.setProps({ metricsVisible: true })
    expect(wrapper.get('.active-execution-line__elapsed').attributes('aria-label')).toBe('Work time: 16m')
    expect(wrapper.get('.ticket-graph-node__footer').element).toBe(footer)
    format.mockClear()
    feed.value = { status: 'live', receivedAt: feed.value.receivedAt! + 60_000 }; await nextTick()
    expect(format).not.toHaveBeenCalled() // terminal values do not subscribe to a live clock
  })

  it('uses the shared viewport lifetime for Active-task rows and refreshes owned live time on reveal', async () => {
    vi.useFakeTimers({ toFake: ['Date'] }).setSystemTime(new Date(duration.observed_at))
    let observe!: (entries: { target: Element; isIntersecting: boolean }[]) => void
    vi.stubGlobal('IntersectionObserver', class {
      constructor(callback: typeof observe) { observe = callback }
      observe() {}; unobserve() {}; disconnect() {}
    })
    const feed = liveFeed(), format = vi.spyOn(time, 'formatWorkTime')
    const record = activeTaskFixture({ active_execution: execution, work_duration: duration })
    const wrapper = mount(ActiveTaskRow, { props: { task: record }, global: { provide: { [usageFeedKey as symbol]: feed }, stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    const elapsed = wrapper.get('.active-execution-line__elapsed').element
    format.mockClear()
    vi.setSystemTime(new Date('2026-11-08T01:25:00Z'))
    feed.value = { status: 'live', receivedAt: Date.now() }; await nextTick()
    expect(format).not.toHaveBeenCalled()
    observe([{ target: elapsed, isIntersecting: true }]); await nextTick()
    expect(wrapper.get('.active-execution-line__elapsed').attributes('title')).toBe('Work time: 17m')
    observe([{ target: elapsed, isIntersecting: false }]); await nextTick(); format.mockClear()
    await wrapper.setProps({ task: { ...record, work_duration: { ...duration, total_seconds: 960, observed_at: '2026-11-08T01:24:00Z' } } })
    vi.setSystemTime(new Date('2026-11-08T01:27:00Z'))
    feed.value = { status: 'live', receivedAt: Date.now() }; await nextTick()
    expect(format).not.toHaveBeenCalled()
    observe([{ target: elapsed, isIntersecting: true }]); await nextTick()
    expect(wrapper.get('.active-execution-line__elapsed').attributes('title')).toBe('Work time: 19m')
  })
})
