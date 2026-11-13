import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, onMounted, ref } from 'vue'
import { createPinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { callOperation } from '@/lib/operations'
import { openTrackerStatusStream } from '@/lib/task-tracker/liveEvents'
import { useActiveTasks } from '@/composables/useActiveTasks'
import { useAgentStats } from './useAgentStats'
import { agentStatsFixture } from './agentStatsFixtures'
import { activeTasksFixture } from './activeTaskFixtures'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
vi.mock('@/lib/task-tracker/liveEvents', async original => ({ ...await original<object>(), openTrackerStatusStream: vi.fn(() => ({ close: vi.fn() })) }))
afterEach(() => { vi.clearAllMocks() })
describe('both Home read boundaries on the shared real feed owner', () => {
  it.each([null, 1])('coalesces quiet mount and preserves a real in-flight change in scope %s', async project => {
    const pending: (() => void)[] = []
    let slow = true
    vi.mocked(callOperation).mockImplementation(async name => {
      if (slow) await new Promise<void>(done => pending.push(done))
      return name.includes('agentStats') ? agentStatsFixture() : activeTasksFixture(project, [])
    })
    let active!: ReturnType<typeof useActiveTasks>, stats!: ReturnType<typeof useAgentStats>
    const wrapper = mount(defineComponent({ setup() {
      active = useActiveTasks(project)
      stats = useAgentStats(project === null ? undefined : ref(project))
      onMounted(() => { void stats.load() })
      return () => null
    } }), { global: { plugins: [createPinia()] } })
    try {
      await flushPromises()
      expect(callOperation).not.toHaveBeenCalled()
      expect(openTrackerStatusStream).toHaveBeenCalledOnce()
      const stream = vi.mocked(openTrackerStatusStream).mock.lastCall![0]
      expect(stream.uiDemand).toEqual({ projects: {} })
      const frame = () => {
        stream.onFrame?.(Date.now())
        stream.onUsage?.({ projection: 'ui', scope_complete: true, project_id: project, scope: project === null ? 'global' : 'project', projects: [], tasks: [], tickets: [], replace: true })
      }
      frame()
      await flushPromises()
      expect(callOperation).toHaveBeenCalledTimes(2)
      void stats.load(); void active.refresh() // Manual callers join the same work.
      frame(); frame() // A real update after the initial boundary needs a fresh read.
      slow = false
      pending.splice(0).forEach(done => done())
      await flushPromises()
      expect(callOperation).toHaveBeenCalledTimes(4)
      expect(stats.summary.value).not.toBeNull()
      expect(active.feed.value.status).toBe('live')
      stream.onDisconnect?.()
      frame()
      await flushPromises()
      expect(callOperation).toHaveBeenCalledTimes(6)
      expect(active.feed.value.status).toBe('live')
      await stats.setFilters({ group_by: 'agent' as never })
      expect(callOperation).toHaveBeenCalledTimes(7)
    } finally { wrapper.unmount() }
    expect(vi.mocked(openTrackerStatusStream).mock.results[0]!.value.close).toHaveBeenCalledOnce()
  })
})
