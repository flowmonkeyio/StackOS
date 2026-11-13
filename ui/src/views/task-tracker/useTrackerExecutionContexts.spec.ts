// @vitest-environment node
import { effectScope, ref } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { TrackerTask } from '@/lib/task-tracker/types'
import { useTrackerExecutionContexts } from './useTrackerExecutionContexts'

const call = vi.hoisted(() => vi.fn())
vi.mock('@/lib/operations', () => ({ callOperation: call }))

describe('task context read disposal', () => {
  beforeEach(() => call.mockReset())

  it('keeps context facts readable while bounded secondary artifacts settle or fail', async () => {
    let finish!: (value: unknown) => void
    const secondary = new Promise(done => { finish = done })
    const contexts = Array.from({ length: 20 }, (_, i) => ({ context_ref: `context-${i}`, artifact_count: 1 }))
    let active = 0, peak = 0
    call.mockImplementation(async name => {
      if (name === 'executionContext.list') return { items: contexts, next_cursor: null, total_estimate: 20 }
      active++; peak = Math.max(peak, active)
      await secondary
      active--
      if (call.mock.calls.length === 21) throw new Error('one artifact unavailable')
      return { items: [], next_cursor: null, total_estimate: 0 }
    })
    const scope = effectScope(), reader = scope.run(() => useTrackerExecutionContexts({ projectId: ref(1) }))!
    const read = reader.load({ key: 'rich' } as TrackerTask)
    await flushPromises()
    expect(reader.contexts.value).toHaveLength(20)
    expect(reader.loading.value).toBe(false)
    expect(peak).toBeLessThanOrEqual(4)
    finish(null)
    await read
    expect(reader.contexts.value).toHaveLength(20)
    expect(reader.error.value).toContain('artifact')
    expect(call).toHaveBeenCalledTimes(21)
    scope.stop()
  })

  it.each(['close', 'project', 'dispose'])('ignores a late artifact response after %s', async (boundary) => {
    let resolveArtifacts!: (value: unknown) => void
    call.mockResolvedValueOnce({ items: [{ context_ref: 'alpha-context', artifact_count: 1 }], next_cursor: null, total_estimate: 1 })
      .mockReturnValueOnce(new Promise(done => { resolveArtifacts = done }))
    const projectId = ref(1), scope = effectScope()
    const reader = scope.run(() => useTrackerExecutionContexts({ projectId }))!
    const pending = reader.load({ key: 'alpha' } as TrackerTask)
    await flushPromises()
    expect(call).toHaveBeenLastCalledWith('executionContext.artifact.list', { project_id: 1, context_ref: 'alpha-context', limit: 5 })
    if (boundary === 'close') await reader.load(null)
    if (boundary === 'project') projectId.value = 2
    if (boundary === 'dispose') scope.stop()
    resolveArtifacts({ items: [{ id: 9 }], next_cursor: null, total_estimate: 1 })
    await pending
    expect(reader.contexts.value).toEqual([])
    expect(reader.artifacts.value).toEqual({})
    expect(reader.loading.value).toBe(false)
    expect(reader.error.value).toBeNull()
    scope.stop()
  })

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
