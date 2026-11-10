// @vitest-environment node
import { effectScope, ref } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import type { TrackerTask } from '@/lib/task-tracker/types'
import { useTrackerExecutionContexts } from './useTrackerExecutionContexts'

const call = vi.hoisted(() => vi.fn())
vi.mock('@/lib/operations', () => ({ callOperation: call }))

describe('task context read disposal', () => {
  it('does not hydrate artifacts from a late context response after project switch or unmount', async () => {
    let resolve!: (value: unknown) => void
    call.mockReturnValueOnce(new Promise(done => { resolve = done }))
    const projectId = ref(1), scope = effectScope()
    const reader = scope.run(() => useTrackerExecutionContexts({ projectId }))!
    const first = reader.load({ key: 'alpha' } as TrackerTask)
    projectId.value = 2
    resolve({ items: [{ context_ref: 'old', artifact_count: 1 }], next_cursor: null, total_estimate: 1 })
    await first
    await flushPromises()
    expect(reader.contexts.value).toEqual([])
    expect(call).toHaveBeenCalledTimes(1)
    call.mockReturnValueOnce(new Promise(done => { resolve = done }))
    const second = reader.load({ key: 'beta' } as TrackerTask)
    scope.stop()
    resolve({ items: [{ context_ref: 'disposed', artifact_count: 1 }], next_cursor: null, total_estimate: 1 })
    await second
    expect(reader.contexts.value).toEqual([])
    expect(reader.artifacts.value).toEqual({})
    expect(reader.loading.value).toBe(false)
    expect(call).toHaveBeenCalledTimes(2)
  })
})
