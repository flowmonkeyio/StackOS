import { defineComponent, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { subscribeUsageUpdates } from '@/lib/usageUpdates'
import { useTrackerLiveUpdates } from './useTrackerLiveUpdates'

vi.mock('@/lib/usageUpdates', () => ({ subscribeUsageUpdates: vi.fn() }))

function setup(reload = vi.fn(async () => {})) {
  const unsubscribe = vi.fn()
  let listener!: Parameters<typeof subscribeUsageUpdates>[1]
  vi.mocked(subscribeUsageUpdates).mockImplementation((_projectId, next) => {
    listener = next
    return unsubscribe
  })
  let live!: ReturnType<typeof useTrackerLiveUpdates>
  const wrapper = mount(defineComponent({ setup() {
    live = useTrackerLiveUpdates({ projectId: ref(1), activeTaskKey: ref('chosen'), reload })
    return () => null
  } }))
  live.restart()
  return { wrapper, live, listener, reload, unsubscribe }
}

describe('Work live refresh lifetime', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.resetAllMocks() })
  afterEach(() => { vi.useRealTimers() })

  it('retains task filtering and cannot recreate recent-node timers after scope disposal', async () => {
    let finish!: () => void
    const reload = vi.fn(() => new Promise<void>(resolve => { finish = resolve }))
    const { wrapper, live, listener, unsubscribe } = setup(reload)
    listener.status?.({ id: 1, metadata_json: { task_key: 'other' } } as never)
    expect(reload).not.toHaveBeenCalled()
    listener.status?.({ id: 2, metadata_json: { task_key: 'chosen' } } as never)
    expect(live.recentNodeIds.value.has('task:chosen')).toBe(true)
    wrapper.unmount()
    expect(unsubscribe).toHaveBeenCalledOnce()
    expect(live.recentNodeIds.value.size).toBe(0)
    finish()
    await flushPromises()
    expect(live.recentNodeIds.value.size).toBe(0)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('contains resume refresh errors and ignores a late error from a stopped generation', async () => {
    const reload = vi.fn(async () => { throw new Error('Read unavailable') })
    const { wrapper, live, listener } = setup(reload)
    listener.resume?.()
    await flushPromises()
    expect(live.liveError.value).toBe('Read unavailable')
    let fail!: (cause: Error) => void
    reload.mockImplementationOnce(() => new Promise((_resolve, reject) => { fail = reject }))
    listener.resume?.()
    live.stop()
    fail(new Error('Stale error'))
    await flushPromises()
    expect(live.liveError.value).toBe('Read unavailable')
    wrapper.unmount()
  })
})
