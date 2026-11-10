// @vitest-environment node
import { effectScope, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { useTaskActivity } from './useTaskActivity'

const call = vi.hoisted(() => vi.fn())
vi.mock('@/lib/operations', () => ({ callOperation: call }))
const item = (id: number) => ({ source_kind: 'project_event', source_id: id, summary: `Event ${id}` })
const page = (ids: number[], cursor: string | null = null, key = 'alpha', project = 1) => ({
  project_id: project, task_key: key, items: ids.map(item), next_cursor: cursor, coverage: 'Recorded sources', warnings: [],
})

describe('task history reader', () => {
  beforeEach(() => call.mockReset())
  it('pages actual source identities and preserves reached depth on refresh, including retry after failure', async () => {
    call.mockResolvedValueOnce(page([5, 4, 3], 'older'))
    const scope = effectScope()
    const reader = scope.run(() => useTaskActivity({ projectId: ref(1), taskKey: ref('alpha') }))!
    expect(call).not.toHaveBeenCalled()
    await reader.refresh()
    await flushPromises()
    expect(call).toHaveBeenLastCalledWith('tracker.taskActivity', { project_id: 1, task_key: 'alpha', limit: 3, cursor: null })
    call.mockRejectedValueOnce(new Error('Unavailable'))
    await reader.more()
    expect(reader.items.value.map(x => x.source_id)).toEqual([5, 4, 3])
    expect(reader.nextCursor.value).toBe('older')
    expect(reader.error.value).toContain('Unavailable')
    call.mockResolvedValueOnce(page([3, 2, 1]))
    await reader.more()
    expect(call).toHaveBeenLastCalledWith('tracker.taskActivity', expect.objectContaining({ cursor: 'older', limit: 50 }))
    expect(reader.items.value.map(x => x.source_id)).toEqual([5, 4, 3, 2, 1])
    call.mockResolvedValueOnce(page([6, 5, 4], 'next')).mockResolvedValueOnce(page([3, 2, 1]))
    await reader.refresh()
    expect(reader.items.value.map(x => x.source_id)).toEqual([6, 5, 4, 3, 2, 1])
    expect(reader.coverage.value).toBe('Recorded sources')
    scope.stop()
  })

  it('clears scope immediately and ignores old task, project and disposed responses', async () => {
    let resolveOld!: (value: unknown) => void
    let resolveDisposed!: (value: unknown) => void
    const old = new Promise(resolve => { resolveOld = resolve })
    const disposed = new Promise(resolve => { resolveDisposed = resolve })
    call.mockReturnValueOnce(old).mockResolvedValueOnce(page([2], null, 'beta')).mockReturnValueOnce(disposed)
    const scope = effectScope(), projectId = ref(1), taskKey = ref('alpha')
    const reader = scope.run(() => useTaskActivity({ projectId, taskKey }))!
    const first = reader.refresh()
    taskKey.value = 'beta'
    const second = reader.refresh()
    expect(reader.items.value).toEqual([])
    resolveOld(page([1]))
    await Promise.all([first, second])
    expect(reader.items.value.map(x => x.source_id)).toEqual([2])
    projectId.value = 2
    const third = reader.refresh()
    expect(reader.items.value).toEqual([])
    scope.stop()
    resolveDisposed(page([3], null, 'beta', 2))
    await third
    expect(reader.items.value).toEqual([])
    expect(reader.loading.value).toBe(false)
  })
})
