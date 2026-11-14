import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h, nextTick, ref, type Ref } from 'vue'
import UsageLiveIndicator from '@/components/domain/UsageLiveIndicator.vue'
import { callOperation } from '@/lib/operations'
import { useAgentStats } from './useAgentStats'
import { localActivityFilters } from '@/lib/agentActivity'
import { agentStatsFixture as fixture } from './agentStatsFixtures'
import { subscribeUsageUpdates } from '@/lib/usageUpdates'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
vi.mock('@/lib/usageUpdates', () => ({ subscribeUsageUpdates: vi.fn(() => vi.fn()), usageSnapshotCheckpoint: vi.fn(() => ({})), markUsageSnapshot: vi.fn(() => true), subscribeUsageVisibility: vi.fn(() => vi.fn()) }))
const wrappers: ReturnType<typeof mount>[] = []
const agentStatsFixture = (overrides: Parameters<typeof fixture>[0] = {}) => fixture({ project_id: null, ...overrides })
beforeEach(() => { vi.mocked(callOperation).mockReset(); vi.mocked(subscribeUsageUpdates).mockClear() })
afterEach(() => wrappers.splice(0).forEach(wrapper => wrapper.unmount()))
function setup(projectId?: Ref<number>) {
  let stats!: ReturnType<typeof useAgentStats>
  wrappers.push(mount(defineComponent({ setup() { stats = useAgentStats(projectId); return () => null } })))
  return stats
}

describe('Agent activity query', () => {
  it('drops a queued prior selection without clearing the current invalid-date error', async () => {
    let finish!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const stats = setup()
    const initial = stats.load()
    await flushPromises()
    vi.mocked(subscribeUsageUpdates).mock.lastCall![1].usage()
    await stats.setFilters({ end_date: 'invalid' })
    expect(stats.error.value).toContain('ordered date range')
    finish(agentStatsFixture()); await initial
    expect(stats.error.value).toContain('ordered date range')
    expect(callOperation).toHaveBeenCalledOnce()
  })
  it.each([false, true])('keeps explicit 7/90/custom dates through refresh (project=%s)', async project => {
    vi.mocked(callOperation).mockResolvedValue(agentStatsFixture({ project_id: project ? 1 : null }))
    const stats = setup(project ? ref(1) : undefined)
    const end = '2026-01-02'
    for (const start of ['2025-12-27', '2025-10-05', '2025-12-10']) {
      await stats.setFilters({ start_date: start, end_date: end })
      await stats.load(true)
      expect(stats.filters.value).toMatchObject({ start_date: start, end_date: end })
      expect(callOperation).toHaveBeenLastCalledWith(project ? 'tracker.agentStats' : 'tracker.agentStatsAll', expect.objectContaining({ start_date: start, end_date: end }))
    }
  })
  it('provides freshness from its existing subscription and ignores retired-scope connection callbacks', async () => {
    const id = ref(1)
    let stats!: ReturnType<typeof useAgentStats>
    const wrapper = mount(defineComponent({ setup() { stats = useAgentStats(id); return () => h(UsageLiveIndicator, { active: true }) } }))
    wrappers.push(wrapper)
    const first = vi.mocked(subscribeUsageUpdates).mock.calls[0]![1]
    vi.mocked(callOperation).mockResolvedValue(agentStatsFixture({ project_id: 1 }))
    await stats.load()
    first.connection?.({ status: 'live', receivedAt: Date.now() }); await nextTick()
    expect(wrapper.text()).toBe('Live')
    vi.mocked(callOperation).mockResolvedValue(agentStatsFixture({ project_id: 2 }))
    id.value = 2; await stats.load(); await nextTick()
    expect(wrapper.text()).toBe('Connecting')
    first.connection?.({ status: 'live', receivedAt: Date.now() }); await nextTick()
    expect(wrapper.text()).toBe('Connecting')
    const current = vi.mocked(subscribeUsageUpdates).mock.calls[1]![1]
    current.connection?.({ status: 'reconnecting', receivedAt: Date.now() }); await nextTick()
    expect(wrapper.text()).toBe('Reconnecting')
    expect(subscribeUsageUpdates).toHaveBeenCalledTimes(2) // one per explicit scope
  })
  it('replaces the project subscription on explicit scope loads and releases it on route unmount', async () => {
    vi.mocked(callOperation).mockImplementation(async (_name, args) => agentStatsFixture({ project_id: args.project_id as number ?? null }) as never)
    const id = ref(1)
    const stats = setup(id)
    const first = vi.mocked(subscribeUsageUpdates).mock.calls[0]![1]
    const releaseFirst = vi.mocked(subscribeUsageUpdates).mock.results[0]!.value
    await stats.load()
    await stats.load()
    expect(subscribeUsageUpdates).toHaveBeenCalledTimes(1)
    id.value = 2
    await stats.load()
    expect(releaseFirst).toHaveBeenCalledOnce()
    expect(subscribeUsageUpdates).toHaveBeenLastCalledWith(2, expect.any(Object))
    const calls = vi.mocked(callOperation).mock.calls.length
    first.usage()
    await flushPromises()
    expect(callOperation).toHaveBeenCalledTimes(calls)
    const current = vi.mocked(subscribeUsageUpdates).mock.calls[1]![1]
    current.usage()
    await flushPromises()
    expect(callOperation).toHaveBeenCalledTimes(calls + 1)
    expect(callOperation).toHaveBeenLastCalledWith('tracker.agentStats', { project_id: 2, ...stats.filters.value })
    const releaseCurrent = vi.mocked(subscribeUsageUpdates).mock.results[1]!.value
    wrappers.pop()!.unmount()
    expect(releaseCurrent).toHaveBeenCalledOnce()
    current.usage()
    await stats.load()
    await flushPromises()
    expect(callOperation).toHaveBeenCalledTimes(calls + 1)
    expect(subscribeUsageUpdates).toHaveBeenCalledTimes(2)
  })

  it('drops a queued usage refresh when its global route unmounts during an in-flight load', async () => {
    let resolve!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(done => { resolve = done }))
    const stats = setup()
    expect(subscribeUsageUpdates).toHaveBeenLastCalledWith(null, expect.any(Object))
    const listener = vi.mocked(subscribeUsageUpdates).mock.calls[0]![1]
    listener.usage()
    listener.usage()
    await flushPromises()
    wrappers.pop()!.unmount()
    resolve(agentStatsFixture())
    await flushPromises()
    expect(callOperation).toHaveBeenCalledOnce()
    expect(stats.summary.value).toBeNull()
  })

  it('uses accepted scoped operations and the exact existing visibility values', async () => {
    vi.mocked(callOperation).mockResolvedValue(agentStatsFixture())
    const project = setup(ref(7))
    await project.load()
    expect(callOperation).toHaveBeenLastCalledWith('tracker.agentStats', { project_id: 7, ...localActivityFilters() })
    const global = setup()
    await global.load()
    expect(callOperation).toHaveBeenLastCalledWith('tracker.agentStatsAll', { is_active: true, ...localActivityFilters() })
    await global.setVisibility(false)
    expect(callOperation).toHaveBeenLastCalledWith('tracker.agentStatsAll', { is_active: false, ...localActivityFilters() })
    await global.setVisibility(null)
    expect(callOperation).toHaveBeenLastCalledWith('tracker.agentStatsAll', { is_active: null, ...localActivityFilters() })
  })

  it('discards late responses and hides old project values immediately without a watcher', async () => {
    let resolveOld!: (value: unknown) => void
    vi.mocked(callOperation).mockResolvedValueOnce(agentStatsFixture({ project_id: 1 }))
    const id = ref(1)
    const stats = setup(id)
    await stats.load()
    expect(stats.summary.value?.totals.launch_attempts).toBe(4)
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
    const old = stats.load()
    await flushPromises()
    id.value = 2
    expect(stats.summary.value).toBeNull()
    vi.mocked(callOperation).mockResolvedValueOnce(agentStatsFixture({ project_id: 2 }))
    await stats.load()
    resolveOld(agentStatsFixture())
    await old
    expect(stats.summary.value?.project_id).toBe(2)
  })

  it('keeps refresh failure visible with old same-scope values but never carries them across visibility', async () => {
    const stats = setup()
    vi.mocked(callOperation).mockRejectedValueOnce(new Error('initial failure'))
    await stats.load()
    expect(stats.summary.value).toBeNull()
    expect(stats.error.value).toContain('initial failure')
    vi.mocked(callOperation).mockResolvedValueOnce(agentStatsFixture())
    await stats.load()
    vi.mocked(callOperation).mockRejectedValueOnce(new Error('refresh failed'))
    await stats.load()
    expect(stats.summary.value).not.toBeNull()
    expect(stats.error.value).toContain('refresh failed')
    vi.mocked(callOperation).mockRejectedValueOnce(new Error('archived failed'))
    await stats.setVisibility(false)
    expect(stats.summary.value).toBeNull()
    expect(stats.error.value).toContain('archived failed')
    expect(stats.loading.value).toBe(false)
  })

  it('ignores pending results after the route owner unmounts', async () => {
    let resolve!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(done => { resolve = done }))
    const stats = setup()
    const load = stats.load()
    await flushPromises()
    wrappers.pop()!.unmount()
    resolve(agentStatsFixture())
    await load
    await flushPromises()
    expect(stats.summary.value).toBeNull()
  })

  it('retains the dated chart during regrouping and preserves honest failures and date invalidation', async () => {
    const stats = setup()
    vi.mocked(callOperation).mockResolvedValueOnce(agentStatsFixture())
    await stats.load()
    const daily = stats.summary.value!.daily
    let resolveGroup!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(resolve => { resolveGroup = resolve }))
    const pending = stats.setFilters({ group_by: 'agent' as never })
    await flushPromises()
    expect(stats.loading.value).toBe(true)
    expect(stats.summary.value?.group_by).toBe('model')
    expect(stats.summary.value?.daily).toEqual(daily)
    resolveGroup(agentStatsFixture({ group_by: 'agent' as never }))
    await pending
    expect(stats.summary.value?.group_by).toBe('agent')
    vi.mocked(callOperation).mockRejectedValueOnce(new Error('grouping failed'))
    await stats.setFilters({ group_by: 'agent_model' as never })
    expect(stats.summary.value?.group_by).toBe('agent')
    expect(stats.error.value).toContain('grouping failed')
    await stats.setFilters({ end_date: 'invalid' })
    expect(stats.summary.value).toBeNull()
    expect(stats.error.value).toContain('ordered date range')
  })

  it('uses one date/group selection for every response and invalidates pending prior dates', async () => {
    const stats = setup()
    let resolveOld!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
    const old = stats.load()
    await flushPromises()
    vi.mocked(callOperation).mockResolvedValueOnce(agentStatsFixture({ start_date: '2026-11-01' }))
    await stats.setFilters({ start_date: '2026-11-01', end_date: '2026-11-03', group_by: 'agent' as never })
    resolveOld(agentStatsFixture({ start_date: '2025-01-01' }))
    await old
    expect(stats.summary.value?.start_date).toBe('2026-11-01')
    expect(callOperation).toHaveBeenLastCalledWith('tracker.agentStatsAll', { is_active: true, ...stats.filters.value })
    await stats.setFilters({ end_date: '2026-10-31' })
    expect(stats.summary.value).toBeNull()
    expect(stats.error.value).toContain('ordered date range')
    expect(callOperation).toHaveBeenCalledTimes(2)
  })
})
