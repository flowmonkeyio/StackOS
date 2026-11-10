import { computed, ref, type Ref } from 'vue'
import { reorderTask, saveTaskPriority, type TaskPriorityIntent, type TaskReorderIntent } from '@/lib/stackos/taskBoard'
import type { TaskProgressRow } from './viewTypes'
import type { TaskPriority } from './taskPriority'

export interface BoardMove { taskKey: string; anchorKey: string; placement: 'before' | 'after' }

// Owns only the pending Board edit. The index and existing stream remain the
// canonical read owner; refresh resolves hidden ranks and concurrent changes.
export function useTaskBoardEdits(options: {
  rows: Ref<TaskProgressRow[]>
  projectId: Ref<number>
  currentScope: () => boolean
  refresh: () => Promise<void>
}) {
  const pending = ref(false)
  const error = ref<string | null>(null)
  const optimistic = ref<BoardMove | { taskKey: string; priority: TaskPriority } | null>(null)
  const rows = computed(() => {
    const edit = optimistic.value
    const result = [...options.rows.value]
    if (!edit) return result
    const index = result.findIndex(row => row.key === edit.taskKey)
    if (index < 0) return result
    if ('priority' in edit) {
      result[index] = { ...result[index]!, task: { ...result[index]!.task, priority_key: edit.priority } }
    } else {
      const [row] = result.splice(index, 1)
      const anchor = result.findIndex(item => item.key === edit.anchorKey && item.task.status === row!.task.status)
      if (anchor < 0) return options.rows.value
      result.splice(anchor + (edit.placement === 'after' ? 1 : 0), 0, row!)
    }
    return result
  })

  async function save(edit: NonNullable<typeof optimistic.value>): Promise<void> {
    if (pending.value || !options.currentScope()) return
    const row = options.rows.value.find(row => row.key === edit.taskKey)
    if (!row || row.task.project_id !== options.projectId.value) return
    if ('anchorKey' in edit && !options.rows.value.some(anchor => anchor.key === edit.anchorKey && anchor.task.status === row.task.status)) return
    if ('priority' in edit && row.task.priority_key === edit.priority) return
    const projectId = options.projectId.value
    const base = {
      project_id: projectId, task_key: row.key, expected_status: row.task.status,
      expected_updated_at: row.task.updated_at, idempotency_key: crypto.randomUUID(),
    }
    // Each explicit edit has one snapshot and idempotency key. A failed save
    // refreshes the Board before the user makes another edit.
    const intent: TaskPriorityIntent | TaskReorderIntent = 'priority' in edit
      ? { ...base, priority_key: edit.priority }
      : { ...base, anchor_task_key: edit.anchorKey, placement: edit.placement }
    const current = () => options.currentScope() && options.projectId.value === projectId
    pending.value = true
    error.value = null
    optimistic.value = edit
    try {
      if ('priority_key' in intent) await saveTaskPriority(intent)
      else await reorderTask(intent)
      if (current()) await options.refresh()
    } catch {
      if (current()) {
        optimistic.value = null
        error.value = 'Could not save task. Refresh and try again.'
        await options.refresh()
      }
    } finally {
      if (current()) {
        optimistic.value = null
        pending.value = false
      }
    }
  }

  return { rows, pending, error, move: (edit: BoardMove) => save(edit), priority: (taskKey: string, priority: TaskPriority) => save({ taskKey, priority }) }
}
