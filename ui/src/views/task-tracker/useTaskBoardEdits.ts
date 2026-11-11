import { computed, ref, type Ref } from 'vue'
import { ApiError } from '@/lib/client'
import { reorderTask, saveTaskPriority, type TaskPriorityIntent, type TaskReorderIntent } from '@/lib/stackos/taskBoard'
import type { TaskProgressRow } from './viewTypes'
import type { TaskPriority } from './taskPriority'
import { changesOrder } from '@/components/ui/verticalReorder'

export interface BoardMove { taskKey: string; anchorKey: string; placement: 'before' | 'after' }
type BoardEdit = BoardMove | { taskKey: string; priority: TaskPriority }
interface EditState {
  edit: BoardEdit | null
  pending: boolean
  controller: AbortController
}

// Owns only the pending Board edit. The index and existing stream remain the
// canonical read owner; refresh resolves hidden ranks and concurrent changes.
export function useTaskBoardEdits(options: {
  rows: Ref<TaskProgressRow[]>
  projectId: Ref<number>
  currentScope: () => boolean
  refresh: () => Promise<void>
}) {
  const states = ref(new Map<string, EditState>())
  const pendingColumns = computed(() => [...states.value].filter(([, state]) => state.pending).map(([status]) => status))
  const pending = computed(() => pendingColumns.value.length > 0)
  const error = ref<string | null>(null)
  let generation = 0
  const rows = computed(() => {
    const result = [...options.rows.value]
    for (const { edit } of states.value.values()) {
      if (!edit) continue
      const index = result.findIndex(row => row.key === edit.taskKey)
      if (index < 0) continue
      if ('priority' in edit) {
        result[index] = { ...result[index]!, task: { ...result[index]!.task, priority_key: edit.priority } }
      } else {
        const [row] = result.splice(index, 1)
        const anchor = result.findIndex(item => item.key === edit.anchorKey && item.task.status === row!.task.status)
        if (anchor < 0) { result.splice(index, 0, row!); continue }
        result.splice(anchor + (edit.placement === 'after' ? 1 : 0), 0, row!)
      }
    }
    return result
  })

  function stop(): void {
    generation += 1
    for (const state of states.value.values()) state.controller.abort()
    states.value.clear()
    error.value = null
  }

  // A later successful manual/live read reconciles any settled optimistic
  // state left by an unavailable post-save read. Never clear an active edit.
  function reconcile(): void {
    for (const [status, state] of states.value) if (!state.pending) states.value.delete(status)
    if (!pending.value) error.value = null
  }

  async function save(edit: BoardEdit): Promise<void> {
    if (!options.currentScope()) return
    const row = options.rows.value.find(row => row.key === edit.taskKey)
    if (!row || row.task.project_id !== options.projectId.value) return
    if (states.value.get(row.task.status)?.pending) return
    if ('anchorKey' in edit && !changesOrder(rows.value.filter(anchor => anchor.task.project_id === options.projectId.value && anchor.task.status === row.task.status).map(anchor => anchor.key), { itemKey: edit.taskKey, anchorKey: edit.anchorKey, placement: edit.placement })) return
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
    const scopeGeneration = generation
    const state: EditState = { edit, pending: true, controller: new AbortController() }
    const current = () => generation === scopeGeneration && options.currentScope() && options.projectId.value === projectId
    states.value.set(row.task.status, state)
    error.value = null
    let outcome: 'saved' | 'rejected' | 'uncertain' = 'uncertain'
    const timeout = setTimeout(() => state.controller.abort(new DOMException('Save timed out', 'TimeoutError')), 15000)
    try {
      if ('priority_key' in intent) await saveTaskPriority(intent, state.controller.signal)
      else await reorderTask(intent, state.controller.signal)
      outcome = 'saved'
    } catch (cause) {
      // An explicit client rejection confirms no save. A timeout, lost
      // response or server error may follow a committed write: read, never
      // blindly resubmit the mutation with a fresh identity.
      outcome = cause instanceof ApiError && cause.status >= 400 && cause.status < 500 && cause.status !== 408
        ? 'rejected' : 'uncertain'
    } finally {
      clearTimeout(timeout)
    }
    try {
      if (!current()) return
      if (outcome === 'rejected') states.value.get(row.task.status)!.edit = null
      if (outcome !== 'saved') error.value = outcome === 'rejected'
        ? 'Could not save task. Refreshing the current board before you try again.'
        : 'We could not confirm the save. Checking the current board; the save has not been repeated.'
      try {
        await options.refresh()
        if (current()) states.value.delete(row.task.status)
      } catch {
        if (current()) error.value = outcome === 'saved'
          ? 'Task saved, but the board could not refresh. Use Refresh to load the current order.'
          : outcome === 'rejected'
            ? 'Could not save task or refresh the board. Use Refresh before trying again.'
            : 'We could not confirm the save or refresh the board. Use Refresh to check the current order before trying again.'
      }
    } finally {
      if (current()) {
        const retained = states.value.get(row.task.status)
        if (retained) retained.pending = false
      } else if (generation === scopeGeneration) {
        stop()
      }
    }
  }

  return { rows, pending, pendingColumns, error, stop, reconcile, move: (edit: BoardMove) => save(edit), priority: (taskKey: string, priority: TaskPriority) => save({ taskKey, priority }) }
}
