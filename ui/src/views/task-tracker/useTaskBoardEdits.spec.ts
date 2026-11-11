import { computed, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/client'
import { reorderTask, saveTaskPriority } from '@/lib/stackos/taskBoard'
import type { TaskProgressRow } from './viewTypes'
import { useTaskBoardEdits } from './useTaskBoardEdits'

vi.mock('@/lib/stackos/taskBoard', () => ({ reorderTask: vi.fn(), saveTaskPriority: vi.fn() }))
function setup() {
  const source = ref(['a', 'b', 'c'].map(key => ({ key, task: {
    key, project_id: 1, status: 'in-progress', priority_key: 'p2', updated_at: '2026-11-10T00:00:00',
  } }) as TaskProgressRow))
  const projectId = ref(1)
  const refresh = vi.fn(async () => {})
  const edits = useTaskBoardEdits({ rows: computed(() => source.value), projectId, currentScope: () => projectId.value === 1, refresh })
  return { source, projectId, refresh, edits }
}
describe('Board save lifecycle', () => {
  beforeEach(() => { vi.resetAllMocks() })
  afterEach(() => { vi.useRealTimers() })
  it('does not save or refresh self, same-position, cross-column, cross-project or missing-anchor moves', async () => {
    const { edits, refresh, source } = setup()
    source.value.push({ key: 'other-status', task: { ...source.value[0]!.task, key: 'other-status', status: 'complete' } } as TaskProgressRow)
    source.value.push({ key: 'other-project', task: { ...source.value[0]!.task, key: 'other-project', project_id: 2 } } as TaskProgressRow)
    for (const move of [
      { taskKey: 'a', anchorKey: 'a', placement: 'before' },
      { taskKey: 'a', anchorKey: 'b', placement: 'before' },
      { taskKey: 'b', anchorKey: 'a', placement: 'after' },
      { taskKey: 'a', anchorKey: 'other-status', placement: 'after' },
      { taskKey: 'a', anchorKey: 'other-project', placement: 'after' },
      { taskKey: 'a', anchorKey: 'missing', placement: 'after' },
    ] as const) await edits.move(move)
    expect(reorderTask).not.toHaveBeenCalled()
    expect(refresh).not.toHaveBeenCalled()
    expect(edits.pending.value).toBe(false)
  })
  it('optimistically moves, submits only the bounded snapshot/anchor intent, then refreshes canonical ranks', async () => {
    const { edits, refresh } = setup()
    let finish!: (value: never) => void
    vi.mocked(reorderTask).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const saving = edits.move({ taskKey: 'a', anchorKey: 'c', placement: 'after' })
    expect(edits.rows.value.map(row => row.key)).toEqual(['b', 'c', 'a'])
    expect(edits.pending.value).toBe(true)
    expect(reorderTask).toHaveBeenLastCalledWith({ project_id: 1, task_key: 'a', anchor_task_key: 'c', placement: 'after', expected_status: 'in-progress', expected_updated_at: '2026-11-10T00:00:00', idempotency_key: expect.any(String) }, expect.any(AbortSignal))
    finish({} as never)
    await saving
    expect(refresh).toHaveBeenCalledOnce()
    expect(edits.pending.value).toBe(false)
  })
  it('rolls back failed priority/order saves with a concise error', async () => {
    const { edits } = setup()
    vi.mocked(reorderTask).mockRejectedValueOnce(new ApiError('conflict', 409, { detail: 'Task changed' }))
    await edits.move({ taskKey: 'a', anchorKey: 'c', placement: 'after' })
    expect(edits.rows.value.map(row => row.key)).toEqual(['a', 'b', 'c'])
    expect(edits.error.value).toContain('Could not save task')
    vi.mocked(saveTaskPriority).mockRejectedValueOnce(new ApiError('rejected', 422, { detail: 'Invalid priority' }))
    await edits.priority('a', 'p0')
    expect(edits.rows.value[0]!.task.priority_key).toBe('p2')
    expect(edits.pending.value).toBe(false)
  })
  it('edits only priority and ignores a response after project scope exits', async () => {
    const { edits, projectId, refresh } = setup()
    let finish!: (value: never) => void
    vi.mocked(saveTaskPriority).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const saving = edits.priority('a', 'p1')
    expect(edits.rows.value[0]!.task.priority_key).toBe('p1')
    expect(saveTaskPriority).toHaveBeenLastCalledWith({ project_id: 1, task_key: 'a', priority_key: 'p1', expected_status: 'in-progress', expected_updated_at: '2026-11-10T00:00:00', idempotency_key: expect.any(String) }, expect.any(AbortSignal))
    projectId.value = 2
    finish({} as never)
    await saving
    expect(refresh).not.toHaveBeenCalled()
    expect(edits.pending.value).toBe(false)
    expect(edits.rows.value[0]!.task.priority_key).toBe('p2')
  })
  it('retains acknowledged order on a failed read and clears pending without repeating the save', async () => {
    const { edits, refresh } = setup()
    refresh.mockRejectedValueOnce(new Error('offline'))
    await edits.move({ taskKey: 'a', anchorKey: 'c', placement: 'after' })
    expect(reorderTask).toHaveBeenCalledOnce()
    expect(refresh).toHaveBeenCalledOnce()
    expect(edits.pending.value).toBe(false)
    expect(edits.rows.value.map(row => row.key)).toEqual(['b', 'c', 'a'])
    expect(edits.error.value).toContain('Task saved')
  })
  it('evaluates a subsequent explicit move against retained optimistic order rather than the stale read', async () => {
    const { edits, refresh } = setup()
    refresh.mockRejectedValueOnce(new Error('offline'))
    await edits.move({ taskKey: 'a', anchorKey: 'c', placement: 'after' })
    expect(edits.rows.value.map(row => row.key)).toEqual(['b', 'c', 'a'])
    await edits.move({ taskKey: 'a', anchorKey: 'b', placement: 'before' })
    expect(reorderTask).toHaveBeenCalledTimes(2)
    expect(edits.rows.value.map(row => row.key)).toEqual(['a', 'b', 'c'])
  })
  it('reconciles an uncertain transport outcome by reading once, with no mutation retry', async () => {
    const { edits, refresh, source } = setup()
    vi.mocked(reorderTask).mockRejectedValueOnce(new TypeError('Failed to fetch'))
    refresh.mockImplementationOnce(async () => { source.value.reverse() })
    await edits.move({ taskKey: 'a', anchorKey: 'c', placement: 'after' })
    expect(reorderTask).toHaveBeenCalledOnce()
    expect(refresh).toHaveBeenCalledOnce()
    expect(edits.rows.value.map(row => row.key)).toEqual(['c', 'b', 'a'])
    expect(edits.pending.value).toBe(false)
    expect(edits.error.value).toContain('could not confirm')
  })
  it('allows a different column to save while an ordering edit is pending', async () => {
    const { edits, source } = setup()
    source.value.push({ key: 'd', task: { ...source.value[0]!.task, key: 'd', status: 'complete' } } as TaskProgressRow)
    let finish!: (value: never) => void
    vi.mocked(reorderTask).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const saving = edits.move({ taskKey: 'a', anchorKey: 'c', placement: 'after' })
    await edits.priority('d', 'p0')
    expect(saveTaskPriority).toHaveBeenCalledOnce()
    await edits.priority('b', 'p0')
    expect(saveTaskPriority).toHaveBeenCalledOnce()
    finish({} as never)
    await saving
    expect(edits.pending.value).toBe(false)
  })
  it('settles a timed-out mutation, checks truth once and accepts a later explicit edit', async () => {
    vi.useFakeTimers()
    const { edits, refresh } = setup()
    let signal!: AbortSignal
    vi.mocked(reorderTask).mockImplementationOnce((_intent, requestSignal) => new Promise((_resolve, reject) => {
      signal = requestSignal!
      signal.addEventListener('abort', () => reject(signal.reason), { once: true })
    }))
    const saving = edits.move({ taskKey: 'a', anchorKey: 'c', placement: 'after' })
    await vi.advanceTimersByTimeAsync(15000)
    await saving
    expect(signal.aborted).toBe(true)
    expect(reorderTask).toHaveBeenCalledOnce()
    expect(refresh).toHaveBeenCalledOnce()
    expect(edits.pending.value).toBe(false)
    expect(edits.error.value).toContain('could not confirm')
    await edits.priority('a', 'p0')
    expect(saveTaskPriority).toHaveBeenCalledOnce()
    expect(edits.pending.value).toBe(false)
  })
  it('rolls back a confirmed rejection before a delayed reconciliation and contains a failed read', async () => {
    const { edits, refresh } = setup()
    vi.mocked(reorderTask).mockRejectedValueOnce(new ApiError('conflict', 409, { detail: 'Task changed' }))
    let fail!: (cause: Error) => void
    refresh.mockImplementationOnce(() => new Promise((_resolve, reject) => { fail = reject }))
    const saving = edits.move({ taskKey: 'a', anchorKey: 'c', placement: 'after' })
    await Promise.resolve()
    expect(edits.rows.value.map(row => row.key)).toEqual(['a', 'b', 'c'])
    fail(new Error('offline'))
    await saving
    expect(edits.pending.value).toBe(false)
    expect(edits.error.value).toContain('Could not save task or refresh')
  })
  it('aborts and clears a disposed edit immediately, ignoring its late outcome', async () => {
    const { edits, refresh } = setup()
    let finish!: (value: never) => void
    vi.mocked(reorderTask).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const saving = edits.move({ taskKey: 'a', anchorKey: 'c', placement: 'after' })
    const signal = vi.mocked(reorderTask).mock.calls[0]![1]!
    edits.stop()
    expect(signal.aborted).toBe(true)
    expect(edits.pending.value).toBe(false)
    expect(edits.rows.value.map(row => row.key)).toEqual(['a', 'b', 'c'])
    finish({} as never)
    await saving
    expect(refresh).not.toHaveBeenCalled()
    expect(edits.error.value).toBeNull()
  })
})
