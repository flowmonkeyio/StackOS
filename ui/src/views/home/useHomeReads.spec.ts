import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, onMounted, ref } from 'vue'
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
  it.each([null, 1])('shows useful HTTP data before a delayed handshake without claiming Live (%s)', async project => {
    vi.mocked(callOperation).mockImplementation(async name => name.includes('agentStats')
      ? agentStatsFixture({ project_id: project }) : activeTasksFixture(project, []))
    let active!: ReturnType<typeof useActiveTasks>, stats!: ReturnType<typeof useAgentStats>
    const wrapper = mount(defineComponent({ setup() {
      active = useActiveTasks(project)
      stats = useAgentStats(project === null ? undefined : ref(project))
      onMounted(() => { void stats.load() })
      return () => null
    } }), { global: { plugins: [createPinia()] } })
    try {
      await flushPromises()
      expect(stats.summary.value).not.toBeNull()
      expect(active.asOf.value).not.toBeNull()
      expect(active.feed.value.status).toBe('connecting')
      const stream = vi.mocked(openTrackerStatusStream).mock.lastCall![0]
      stream.onFrame?.(Date.now())
      expect(active.feed.value.status).not.toBe('live')
      stream.onUsage?.({ tasks: [], tickets: [], replace: true })
      await flushPromises()
      expect(active.feed.value.status).toBe('live')
      expect(callOperation).toHaveBeenCalledTimes(4) // Required ordered replacement, no speculative Live.
    } finally { wrapper.unmount() }
  })

  it.each([null, 1])('retains only accepted same-scope content across route unmount (%s)', async project => {
    const visible = ref(true), scope = ref(project)
    let active!: ReturnType<typeof useActiveTasks>, stats!: ReturnType<typeof useAgentStats>
    const Reader = defineComponent({ setup() {
      active = useActiveTasks(scope.value)
      stats = useAgentStats(scope.value === null ? undefined : ref(scope.value))
      onMounted(() => { void stats.load() })
      return () => null
    } })
    vi.mocked(callOperation).mockImplementation(async (name, args) => name.includes('agentStats')
      ? agentStatsFixture({ project_id: args.project_id as number ?? null })
      : activeTasksFixture(args.project_id as number ?? null, []))
    const wrapper = mount(defineComponent({ setup: () => () => visible.value ? h(Reader, { key: scope.value ?? 'all' }) : null }), { global: { plugins: [createPinia()] } })
    try {
      const stream = vi.mocked(openTrackerStatusStream).mock.lastCall![0]
      stream.onFrame?.(Date.now()); stream.onUsage?.({ tasks: [], tickets: [], replace: true })
      await flushPromises()
      expect(callOperation).toHaveBeenCalledTimes(2) // A quiet mount joins baseline and HTTP work.
      visible.value = false; await flushPromises()
      const pending: (() => void)[] = []
      vi.mocked(callOperation).mockImplementation(async () => new Promise(resolve => { pending.push(() => resolve(null)) }))
      visible.value = true; await flushPromises()
      expect(stats.summary.value?.totals.reported_usage.total).toBe(100)
      expect(active.asOf.value).not.toBeNull()
      expect(active.loading.value).toBe(true)
      expect(active.feed.value.status).toBe('connecting') // Retained content is never retained freshness.
      scope.value = 2; await flushPromises()
      expect(stats.summary.value).toBeNull()
      expect(active.asOf.value).toBeNull()
      wrapper.unmount()
      pending.forEach(resolve => resolve())
      await flushPromises()
    } finally { if (wrapper.exists()) wrapper.unmount() }
  })
  it.each([null, 1])('coalesces quiet mount and preserves a real in-flight change in scope %s', async project => {
    const pending: (() => void)[] = []
    const trailing: (() => void)[] = []
    let slow = true
    let holdTrailing = true
    vi.mocked(callOperation).mockImplementation(async name => {
      if (slow) await new Promise<void>(done => pending.push(done))
      else if (holdTrailing) await new Promise<void>(done => trailing.push(done))
      return name.includes('agentStats') ? agentStatsFixture({ project_id: project }) : activeTasksFixture(project, [])
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
      // Useful reads start while the stream handshake is pending. They may
      // display data, but cannot establish Live without an ordered snapshot.
      expect(callOperation).toHaveBeenCalledTimes(2)
      expect(stats.loading.value).toBe(true)
      expect(active.feed.value.status).toBe('connecting')
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
      expect(stats.summary.value).not.toBeNull() // Crossed first content remains useful, but unconfirmed.
      expect(active.asOf.value).not.toBeNull()
      expect(active.feed.value.status).not.toBe('live')
      holdTrailing = false
      trailing.splice(0).forEach(done => done())
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
