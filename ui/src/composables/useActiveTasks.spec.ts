import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type { SchemaActiveTaskOut, SchemaActiveTasksOut } from '@/api'
import { callOperation } from '@/lib/operations'
import { openTrackerStatusStream } from '@/lib/task-tracker/liveEvents'
import { useProjectsStore, type Project } from '@/stores/projects'
import { useActiveTasks } from './useActiveTasks'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
vi.mock('@/lib/task-tracker/liveEvents', () => ({ openTrackerStatusStream: vi.fn(() => ({ close: vi.fn() })) }))

const wrappers: VueWrapper[] = []
function task(projectId = 1, key = 'work', delivery: 'queued' | 'stopped' | 'running' = 'queued'): SchemaActiveTaskOut {
  return {
    id: projectId, project_id: projectId, key, title: key, goal: 'Recorded goal', status: 'in-progress',
    owner: 'Operator', priority_key: 'p1', created_at: '2026-11-01T00:00:00Z',
    updated_at: '2026-11-01T00:00:00Z', started_at: '2026-11-01T00:00:00Z', completed_at: null,
    project: { id: projectId, name: `Project ${projectId}`, slug: `p${projectId}`, domain: 'test', is_active: true, updated_at: '2026-11-01T00:00:00Z' },
    delivery: { status: delivery, ticketKey: 'child', reason: delivery === 'stopped' ? 'Review blocked' : null },
    agent_stats: null, active_execution: null, ticket_summary: null,
  } as SchemaActiveTaskOut
}
function page(items: SchemaActiveTaskOut[], projectId: number | null = null, cursor: number | null = null): SchemaActiveTasksOut {
  return { items, scope: projectId === null ? 'global' : 'project', project_id: projectId, next_cursor: cursor, as_of: '2026-11-11T00:00:00Z' } as SchemaActiveTasksOut
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (value: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function home(projectId: number | null = null) {
  let state!: ReturnType<typeof useActiveTasks>
  wrappers.push(mount(defineComponent({ setup() { state = useActiveTasks(projectId); return () => null } })))
  return state
}
function stream(index = 0) { return vi.mocked(openTrackerStatusStream).mock.calls[index]![0] }

beforeEach(() => {
  setActivePinia(createPinia())
  vi.mocked(callOperation).mockReset()
  vi.mocked(openTrackerStatusStream).mockClear()
  Object.defineProperty(document, 'hidden', { configurable: true, value: false })
})
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()) })

describe('shared Home active-task reads', () => {
  it('loads one bulk scope, invalidates global frames with no metrics, retains stopped work and removes completed work', async () => {
    vi.mocked(callOperation).mockResolvedValue(page([task(1), task(2)]))
    const state = home()
    await flushPromises()
    expect(state.items.value).toHaveLength(2)
    expect(callOperation).toHaveBeenCalledTimes(1)
    expect(callOperation).toHaveBeenCalledWith('tracker.activeTasksAll', { limit: 200 })
    expect(state.feed.value.status).not.toBe('live')
    stream().onFrame?.(Date.now())
    stream().onUsage?.() // Actual global transport cannot supply project replacements.
    await flushPromises()
    expect(state.feed.value.status).toBe('live')
    const started = task(1, 'work', 'running')
    started.active_execution = { invocation_id: 'actual', agent: 'reviewer', host_key: 'codex', model: 'exact', started_at: '2026-11-11T00:00:00Z', ticket_key: 'child', live_usage: null }
    vi.mocked(callOperation).mockResolvedValue(page([started, task(2)]))
    stream().onUsage?.()
    stream().onEvent({ event_type: 'tracker.execution.changed' } as never)
    await flushPromises()
    expect(state.items.value[0]!.active_execution?.invocation_id).toBe('actual')
    expect(callOperation).toHaveBeenCalledTimes(3) // Companions coalesce, no per-row reads.
    vi.mocked(callOperation).mockResolvedValue(page([task(1, 'work', 'stopped'), task(2)]))
    stream().onUsage?.()
    await flushPromises()
    expect(state.items.value[0]!.delivery?.reason).toBe('Review blocked')
    expect(state.items.value[0]!.active_execution).toBeNull()
    vi.mocked(callOperation).mockResolvedValue(page([task(2)]))
    stream().onEvent({ event_type: 'tracker.task.status_changed' } as never)
    await flushPromises()
    expect(state.items.value.map(row => row.project_id)).toEqual([2])
  })

  it('keeps prior facts on failed reads and requires a current replacement after reconnect', async () => {
    vi.mocked(callOperation).mockResolvedValue(page([task()]))
    const state = home()
    await flushPromises()
    stream().onFrame?.(Date.now()); stream().onUsage?.()
    await flushPromises()
    expect(state.feed.value.status).toBe('live')
    stream().onDisconnect?.()
    vi.mocked(callOperation).mockRejectedValue(new Error('Offline'))
    stream().onFrame?.(Date.now()); stream().onUsage?.()
    await flushPromises()
    expect(state.error.value).toContain('Offline')
    expect(state.items.value).toHaveLength(1)
    expect(state.feed.value.status).not.toBe('live')
    stream().onFrame?.(Date.now())
    expect(state.feed.value.status).not.toBe('live')
    vi.mocked(callOperation).mockResolvedValue(page([]))
    stream().onUsage?.()
    await flushPromises()
    expect(state.error.value).toBeNull()
    expect(state.items.value).toEqual([])
    expect(state.feed.value.status).toBe('live')
  })

  it('discards reads crossed by invalidation and ignores late completion after unmount', async () => {
    const old = deferred<SchemaActiveTasksOut>()
    vi.mocked(callOperation).mockReturnValueOnce(old.promise).mockResolvedValue(page([task(2)]))
    const state = home()
    await flushPromises()
    stream().onFrame?.(Date.now()); stream().onUsage?.()
    old.resolve(page([task(1)]))
    await flushPromises()
    expect(state.items.value.map(row => row.project_id)).toEqual([2])
    expect(state.feed.value.status).toBe('live')
    const late = deferred<SchemaActiveTasksOut>()
    vi.mocked(callOperation).mockReturnValueOnce(late.promise)
    void state.refresh()
    await flushPromises()
    wrappers.pop()!.unmount()
    late.resolve(page([]))
    await flushPromises()
    expect(state.items.value).toHaveLength(1)
    expect(vi.mocked(openTrackerStatusStream).mock.results[0]!.value.close).toHaveBeenCalledOnce()
  })

  it('reads paged composite identities atomically and never publishes a failed partial page', async () => {
    vi.mocked(callOperation).mockResolvedValueOnce(page([task(1)], null, 1))
      .mockResolvedValueOnce(page([task(2)]))
    const state = home()
    await flushPromises()
    expect(state.items.value.map(row => `${row.project_id}:${row.key}`)).toEqual(['1:work', '2:work'])
    expect(callOperation).toHaveBeenLastCalledWith('tracker.activeTasksAll', { limit: 200, after_id: 1 })
    vi.mocked(callOperation).mockResolvedValueOnce(page([task(3)], null, 3)).mockRejectedValueOnce(new Error('Page failed'))
    await state.refresh()
    expect(state.items.value.map(row => row.project_id)).toEqual([1, 2])
    expect(state.error.value).toContain('Page failed')
  })

  it('uses canonical project identity and deletion fences across delayed snapshots', async () => {
    const projects = useProjectsStore()
    projects.items = [{ ...task().project, cleanup_json: null, updated_at: '2026-11-11T01:00:00Z', name: 'Renamed' } as Project]
    vi.mocked(callOperation).mockResolvedValue(page([task()]))
    const state = home()
    await flushPromises()
    expect(state.items.value[0]!.project.name).toBe('Renamed')
    expect(projects.items[0]!.name).toBe('Renamed')
    vi.spyOn(projects, 'isProjectDeleted').mockReturnValue(true)
    await state.refresh()
    expect(state.items.value).toEqual([])
    expect(projects.items).toHaveLength(1)
  })

  it('isolates project reads/streams and rejects a foreign response without empty success', async () => {
    vi.mocked(callOperation).mockResolvedValue(page([task(7)], 7))
    const state = home(7)
    await flushPromises()
    expect(callOperation).toHaveBeenCalledTimes(1)
    expect(callOperation).toHaveBeenCalledWith('tracker.activeTasks', { project_id: 7, limit: 200 })
    expect(stream().projectId).toBe(7)
    expect(stream().taskKey).toBeUndefined()
    vi.mocked(callOperation).mockResolvedValue(page([task(8)], 7))
    await state.refresh()
    expect(state.items.value.map(row => row.project_id)).toEqual([7])
    expect(state.error.value).toContain('scope')
  })
})
