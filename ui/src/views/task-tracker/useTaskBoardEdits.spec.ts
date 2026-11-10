import { computed, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
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
  it('optimistically moves, submits only the bounded snapshot/anchor intent, then refreshes canonical ranks', async () => {
    const { edits, refresh } = setup()
    let finish!: (value: never) => void
    vi.mocked(reorderTask).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const saving = edits.move({ taskKey: 'a', anchorKey: 'c', placement: 'after' })
    expect(edits.rows.value.map(row => row.key)).toEqual(['b', 'c', 'a'])
    expect(edits.pending.value).toBe(true)
    expect(reorderTask).toHaveBeenLastCalledWith({ project_id: 1, task_key: 'a', anchor_task_key: 'c', placement: 'after', expected_status: 'in-progress', expected_updated_at: '2026-11-10T00:00:00', idempotency_key: expect.any(String) })
    finish({} as never)
    await saving
    expect(refresh).toHaveBeenCalledOnce()
    expect(edits.pending.value).toBe(false)
  })
  it('rolls back failed priority/order saves with a concise error', async () => {
    const { edits } = setup()
    vi.mocked(reorderTask).mockRejectedValueOnce(new Error('conflict'))
    await edits.move({ taskKey: 'a', anchorKey: 'c', placement: 'after' })
    expect(edits.rows.value.map(row => row.key)).toEqual(['a', 'b', 'c'])
    expect(edits.error.value).toContain('Could not save task')
    vi.mocked(saveTaskPriority).mockRejectedValueOnce(new Error('network'))
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
    expect(saveTaskPriority).toHaveBeenLastCalledWith({ project_id: 1, task_key: 'a', priority_key: 'p1', expected_status: 'in-progress', expected_updated_at: '2026-11-10T00:00:00', idempotency_key: expect.any(String) })
    projectId.value = 2
    finish({} as never)
    await saving
    expect(refresh).not.toHaveBeenCalled()
  })
})
