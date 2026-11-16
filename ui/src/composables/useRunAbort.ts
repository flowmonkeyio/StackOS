import { computed, reactive, ref } from 'vue'
import { callOperation } from '@/lib/operations'
import { formatApiError } from '@/lib/client'
import { isAutomaticDeliveryTask } from '@/lib/task-tracker/delivery'
import type { TrackerTask } from '@/lib/task-tracker/types'

export interface RunAbortTarget {
  projectId: number
  runPlanId: number
  title: string
  status: string
  automated: boolean
}

export function taskRunAbortTarget(task?: TrackerTask | null): RunAbortTarget | null {
  const id = task?.source_json?.run_plan_id
  if (!task || task.source_kind !== 'workflow' || typeof id !== 'number' || !Number.isSafeInteger(id) || id <= 0) return null
  return { projectId: task.project_id, runPlanId: id, title: task.title, status: task.status, automated: isAutomaticDeliveryTask(task) }
}

const pendingRuns = reactive(new Set<string>())
const selected = ref<RunAbortTarget | null>(null)
const open = ref(false)
const error = ref<string | null>(null)
const outcome = ref<string | null>(null)
let refresh: () => void = () => {}
const key = computed(() => selected.value ? `${selected.value.projectId}:${selected.value.runPlanId}` : '')
const pending = computed(() => pendingRuns.has(key.value))
export function useRunAbort() {
  function show(target: RunAbortTarget, reload: () => void) {
    if (pending.value) return
    selected.value = { ...target }
    refresh = reload
    error.value = null
    outcome.value = null
    open.value = true
  }
  function close() { if (!pending.value) open.value = false }
  async function abort(): Promise<boolean> {
    const target = selected.value
    if (!target || pending.value) return false
    const requestKey = key.value
    pendingRuns.add(requestKey)
    error.value = null
    outcome.value = null
    try {
      const response = await callOperation<{ data: { cancellation: { status: string } } }>('runPlan.abort', {
        project_id: target.projectId, run_plan_id: target.runPlanId,
        reason: 'Aborted by operator from the local console.', actor: 'local-console',
      })
      outcome.value = response.data.cancellation.status === 'managed_stopped'
        ? 'Run aborted. Its StackOS-owned automated work stopped.'
        : 'Run aborted. No managed invocation was stopped. External agent sessions remain open.'
      return true
    } catch (err) {
      error.value = formatApiError(err, 'Abort failed. Retry to check this run again.')
      return false
    } finally {
      pendingRuns.delete(requestKey)
      // Canonical state may already be aborted after an unconfirmed stop.
      refresh()
    }
  }
  return { selected, open, pending, error, outcome, abort, show, close }
}
