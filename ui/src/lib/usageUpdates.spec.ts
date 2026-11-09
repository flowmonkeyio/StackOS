import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { subscribeUsageUpdates, usageCheckpoint, patchUsageAfter } from './usageUpdates'
import type { TrackerSnapshot } from '@/lib/task-tracker/types'
import { agentStatsFixture } from '@/views/home/agentStatsFixtures'
import { openTrackerStatusStream } from '@/lib/task-tracker/liveEvents'
vi.mock('@/lib/task-tracker/liveEvents', () => ({ openTrackerStatusStream: vi.fn(() => ({ close: vi.fn() })) }))
beforeEach(() => {
  vi.mocked(openTrackerStatusStream).mockClear()
  Object.defineProperty(document, 'hidden', { configurable: true, value: false })
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-11-09T08:00:00Z'))
})
afterEach(() => vi.useRealTimers())
describe('one shared usage transport', () => {
  it('preserves later usage across pending page reads, isolates scopes and replaces reconnect state', () => {
    const stop = subscribeUsageUpdates(7, { usage: vi.fn() })
    const stream = vi.mocked(openTrackerStatusStream).mock.calls[0]![0]
    const start = usageCheckpoint()
    const measured = agentStatsFixture().totals
    measured.last_execution = { invocation_id: 'finished-call', agent: 'Actual reviewer', host_key: 'codex', model: 'recorded-model', started_at: '2026-11-08T01:00:00Z', ticket_key: 'child' }
    measured.samples = Array.from({ length: 120 }, (_, i) => ({ at: `2026-11-08T01:${String(Math.floor(i / 60)).padStart(2, '0')}:${String(i % 60).padStart(2, '0')}Z`, input: i, cache_read: 0, remaining_input: i, output: i, total: i * 2, cache_write: null, reasoning: null }))
    stream.onUsage?.({ tasks: [{ key: 'task', agent_stats: measured, active_execution: null }], tickets: [], replace: true })
    const stale = { tasks: [{ key: 'task', agent_stats: null }], tickets: [] } as unknown as TrackerSnapshot
    patchUsageAfter(stale, 8, start)
    expect(stale.tasks[0]!.agent_stats).toBeNull()
    patchUsageAfter(stale, 7, start)
    expect(stale.tasks[0]!.agent_stats).toEqual(measured)
    const freshRequest = usageCheckpoint()
    stale.tasks[0]!.agent_stats = null
    patchUsageAfter(stale, 7, freshRequest)
    expect(stale.tasks[0]!.agent_stats).toBeNull()
    stream.onUsage?.({ tasks: [{ key: 'task', agent_stats: measured, active_execution: null }], tickets: [], replace: true })
    patchUsageAfter(stale, 7, freshRequest)
    expect(stale.tasks[0]!.active_execution).toBeNull()
    stop()
  })
  it('shares scoped subscriptions, releases hidden/unmounted consumers and keeps global scope separate', () => {
    const first = vi.fn(), second = vi.fn(), other = vi.fn(), status = vi.fn()
    const one = subscribeUsageUpdates(1, { usage: first, status })
    const two = subscribeUsageUpdates(1, { usage: second })
    const global = subscribeUsageUpdates(null, { usage: other })
    expect(openTrackerStatusStream).toHaveBeenCalledTimes(2)
    const projectStream = vi.mocked(openTrackerStatusStream).mock.calls[0]![0]
    projectStream.onUsage?.()
    expect(first).toHaveBeenCalledTimes(1); expect(second).toHaveBeenCalledTimes(1); expect(other).not.toHaveBeenCalled()
    projectStream.onEvent({ id: 1, event_type: 'tracker.execution.changed' } as never)
    expect(status).toHaveBeenCalledTimes(1)
    projectStream.onEvent({ id: 2, event_type: 'tracker.usage.changed' } as never)
    expect(status).toHaveBeenCalledTimes(1)
    // The server emits one usage refresh per cycle, including final receipt
    // events. Its durable status companion must not reload analytics again.
    expect(first).toHaveBeenCalledTimes(1); expect(second).toHaveBeenCalledTimes(1)
    const close = vi.mocked(openTrackerStatusStream).mock.results[0]!.value.close
    one(); expect(close).not.toHaveBeenCalled()
    Object.defineProperty(document, 'hidden', { configurable: true, value: true })
    document.dispatchEvent(new Event('visibilitychange'))
    expect(close).toHaveBeenCalledTimes(1)
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    document.dispatchEvent(new Event('visibilitychange'))
    expect(openTrackerStatusStream).toHaveBeenCalledTimes(4)
    expect(first).toHaveBeenCalledTimes(1); expect(second).toHaveBeenCalledTimes(2)
    two(); global()
    document.dispatchEvent(new Event('visibilitychange'))
    expect(openTrackerStatusStream).toHaveBeenCalledTimes(4)
  })

  it('stays live through heartbeats without new token measurements and shares one freshness deadline', () => {
    const connection = vi.fn(), other = vi.fn(), usage = vi.fn()
    const stop = subscribeUsageUpdates(3, { usage, connection })
    const stream = vi.mocked(openTrackerStatusStream).mock.calls[0]![0]
    expect(connection).toHaveBeenLastCalledWith({ status: 'connecting', receivedAt: null })
    stream.onFrame?.(Date.now())
    expect(connection.mock.lastCall?.[0].status).toBe('connecting')
    stream.onUsage?.({ tasks: [], tickets: [], replace: true })
    expect(connection).toHaveBeenLastCalledWith({ status: 'live', receivedAt: Date.now() })
    const stopOther = subscribeUsageUpdates(3, { usage: vi.fn(), connection: other })
    expect(other).toHaveBeenLastCalledWith({ status: 'live', receivedAt: Date.now() })
    expect(openTrackerStatusStream).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(1)
    for (let i = 0; i < 5; i += 1) {
      vi.advanceTimersByTime(15_000)
      stream.onFrame?.(Date.now())
      expect(connection).toHaveBeenLastCalledWith({ status: 'live', receivedAt: Date.now() })
      expect(vi.getTimerCount()).toBe(1)
    }
    expect(usage).toHaveBeenCalledTimes(1)
    stop(); expect(vi.getTimerCount()).toBe(1)
    stopOther(); expect(vi.getTimerCount()).toBe(0)
  })

  it('reconnects a silent stream and requires a fresh replacement snapshot before becoming live again', () => {
    const connection = vi.fn(), error = vi.fn()
    const stop = subscribeUsageUpdates(4, { usage: vi.fn(), connection, error })
    const original = vi.mocked(openTrackerStatusStream).mock.calls[0]![0]
    original.onUsage?.({ tasks: [], tickets: [], replace: true })
    const oldClose = vi.mocked(openTrackerStatusStream).mock.results[0]!.value.close
    vi.advanceTimersByTime(45_000)
    expect(oldClose).toHaveBeenCalledTimes(1)
    expect(openTrackerStatusStream).toHaveBeenCalledTimes(2)
    expect(connection.mock.lastCall?.[0].status).toBe('reconnecting')
    const current = vi.mocked(openTrackerStatusStream).mock.calls[1]![0]
    original.onUsage?.({ tasks: [], tickets: [], replace: true })
    current.onFrame?.(Date.now())
    current.onUsage?.({ tasks: [], tickets: [] })
    expect(connection.mock.lastCall?.[0].status).toBe('reconnecting')
    current.onUsage?.({ tasks: [], tickets: [], replace: true })
    expect(connection.mock.lastCall?.[0].status).toBe('live')
    current.onError?.(new Error('offline'))
    expect(error).toHaveBeenCalledTimes(1)
    expect(connection.mock.lastCall?.[0].status).toBe('reconnecting')
    current.onFrame?.(Date.now())
    expect(connection.mock.lastCall?.[0].status).toBe('reconnecting')
    current.onUsage?.({ tasks: [], tickets: [], replace: true })
    expect(connection.mock.lastCall?.[0].status).toBe('live')
    current.onDisconnect?.()
    expect(connection.mock.lastCall?.[0].status).toBe('reconnecting')
    stop()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('pauses hidden feeds and ignores retired callbacks after resume or unmount', () => {
    const connection = vi.fn(), usage = vi.fn(), resume = vi.fn()
    const stop = subscribeUsageUpdates(5, { usage, connection, resume })
    const original = vi.mocked(openTrackerStatusStream).mock.calls[0]![0]
    original.onUsage?.({ tasks: [], tickets: [], replace: true })
    Object.defineProperty(document, 'hidden', { configurable: true, value: true })
    document.dispatchEvent(new Event('visibilitychange'))
    expect(connection.mock.lastCall?.[0].status).toBe('paused')
    expect(vi.getTimerCount()).toBe(0)
    original.onFrame?.(Date.now())
    original.onUsage?.({ tasks: [], tickets: [], replace: true })
    expect(usage).toHaveBeenCalledTimes(1)
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    document.dispatchEvent(new Event('visibilitychange'))
    expect(connection.mock.lastCall?.[0].status).toBe('reconnecting')
    expect(resume).toHaveBeenCalledTimes(1)
    const current = vi.mocked(openTrackerStatusStream).mock.calls[1]![0]
    stop()
    const calls = connection.mock.calls.length
    current.onFrame?.(Date.now())
    current.onUsage?.({ tasks: [], tickets: [], replace: true })
    expect(connection).toHaveBeenCalledTimes(calls)
    expect(vi.getTimerCount()).toBe(0)
  })
})
