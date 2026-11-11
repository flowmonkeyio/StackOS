import { beforeEach, describe, expect, it, vi } from 'vitest'
import { callOperation } from '@/lib/operations'
import { reorderTask, saveTaskPriority } from './taskBoard'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))

describe('bounded Board operation client', () => {
  beforeEach(() => { vi.mocked(callOperation).mockReset() })

  it('preserves immutable priority and neighbor intents, including retry identity', async () => {
    const base = { project_id: 2, task_key: 'task', expected_status: 'not-started' as const,
      expected_updated_at: '2026-11-10T00:00:00', idempotency_key: 'once' }
    vi.mocked(callOperation).mockResolvedValue({ data: { replayed: false } })
    const priority = { ...base, priority_key: 'p1' as const }
    await saveTaskPriority(priority)
    await saveTaskPriority(priority)
    const move = { ...base, anchor_task_key: 'hidden-neighbor', placement: 'before' as const }
    await reorderTask(move)
    expect(vi.mocked(callOperation).mock.calls).toEqual([
      ['tracker.updateTaskPriority', priority, undefined], ['tracker.updateTaskPriority', priority, undefined],
      ['tracker.reorderTask', move, undefined],
    ])
  })

  it('returns conflicts to the caller without retrying or changing intent', async () => {
    const conflict = new Error('task changed')
    vi.mocked(callOperation).mockRejectedValue(conflict)
    await expect(reorderTask({ project_id: 2, task_key: 'task', expected_status: 'complete' as const,
      expected_updated_at: '2026-11-10T00:00:00', idempotency_key: 'move',
      anchor_task_key: 'anchor', placement: 'after' as const })).rejects.toBe(conflict)
    expect(callOperation).toHaveBeenCalledTimes(1)
  })

  it('carries the edit lifetime signal to the existing operation client', async () => {
    const controller = new AbortController()
    const intent = { project_id: 2, task_key: 'task', expected_status: 'complete' as const,
      expected_updated_at: 'revision', idempotency_key: 'once', anchor_task_key: 'anchor', placement: 'after' as const }
    await reorderTask(intent, controller.signal)
    expect(callOperation).toHaveBeenCalledWith('tracker.reorderTask', intent, { signal: controller.signal })
  })
})
