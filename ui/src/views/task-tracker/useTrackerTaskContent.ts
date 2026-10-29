import { computed, onBeforeUnmount, ref, type Ref } from 'vue'
import { ApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import type { TrackerTask } from '@/lib/task-tracker/types'

interface ContentIntent {
  project_id: number
  task_key: string
  expected_updated_at: string
  idempotency_key: string
  description?: string
  additional_input?: string
}

export function useTrackerTaskContent(options: {
  task: Readonly<Ref<TrackerTask | null>>
  refresh: (taskKey: string) => Promise<TrackerTask>
}) {
  const editing = ref(false)
  const description = ref('')
  const additionalInput = ref('')
  const saving = ref(false)
  const refreshing = ref(false)
  const error = ref<string | null>(null)
  const notice = ref<string | null>(null)
  const conflict = ref(false)
  const latestDescription = ref<string | null>(null)
  const pendingIntent = ref<ContentIntent | null>(null)
  const baseDescription = ref('')
  const expectedUpdatedAt = ref('')
  let acceptedIdentity = ''
  const descriptionChanged = computed(() => editing.value && description.value !== baseDescription.value)
  const dirty = computed(() => descriptionChanged.value || additionalInput.value.length > 0)
  const busy = computed(() => saving.value || refreshing.value)
  const locked = computed(() => busy.value || Boolean(pendingIntent.value))
  const canSave = computed(() => Boolean(options.task.value) && !busy.value && !conflict.value && (
    Boolean(pendingIntent.value) || descriptionChanged.value || Boolean(additionalInput.value.trim())
  ))

  function reset(task = options.task.value): void {
    acceptedIdentity = task ? `${task.project_id}:${task.key}` : ''
    editing.value = false
    description.value = task?.description ?? ''
    baseDescription.value = task?.description ?? ''
    expectedUpdatedAt.value = task?.updated_at ?? ''
    additionalInput.value = ''
    error.value = null
    notice.value = null
    conflict.value = false
    latestDescription.value = null
    pendingIntent.value = null
  }

  function syncTask(task: TrackerTask | null): void {
    const identity = task ? `${task.project_id}:${task.key}` : ''
    if (identity !== acceptedIdentity) reset(task)
    else if (!dirty.value && !editing.value && !locked.value && !conflict.value) reset(task)
  }
  reset()

  function beginEdit(): void {
    if (locked.value || !options.task.value) return
    editing.value = true
    description.value = options.task.value.description ?? ''
    baseDescription.value = description.value
    // An existing input draft keeps the revision at which that draft started.
    if (!additionalInput.value) expectedUpdatedAt.value = options.task.value.updated_at
    notice.value = null
  }

  function cancelEdit(): void {
    if (locked.value) return
    if (descriptionChanged.value && !window.confirm('Discard your description changes?')) return
    editing.value = false
    description.value = baseDescription.value
    if (!dirty.value) reset()
  }

  async function save(): Promise<void> {
    const task = options.task.value
    if (!task || !canSave.value) return
    if ((descriptionChanged.value && description.value.length > 20_000) || additionalInput.value.length > 20_000) {
      error.value = 'Keep the description and additional input under 20,000 characters each.'
      return
    }
    pendingIntent.value ??= {
      project_id: task.project_id,
      task_key: task.key,
      expected_updated_at: expectedUpdatedAt.value,
      idempotency_key: crypto.randomUUID(),
      ...(descriptionChanged.value ? { description: description.value } : {}),
      ...(additionalInput.value.trim() ? { additional_input: additionalInput.value } : {}),
    }
    const intent = { ...pendingIntent.value }
    saving.value = true
    error.value = null
    notice.value = null
    let saved = false
    try {
      await callOperation('tracker.updateTaskContent', { ...intent, response_mode: 'raw' })
      saved = true
      // Mutation receipts (including replays) are snapshots, not the current task index.
      const current = await options.refresh(intent.task_key)
      reset(current)
      notice.value = 'Changes saved.'
    } catch (cause) {
      if (!saved && cause instanceof ApiError && cause.status === 409) {
        pendingIntent.value = null
        conflict.value = true
        error.value = 'This task changed since you started. Load the latest content, review your draft, then save again.'
      } else if (!saved && cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        pendingIntent.value = null
        error.value = cause.status === 401 || cause.status === 403
          ? 'Your session cannot save this task. Reconnect to StackOS and try again. Your draft is still here.'
          : cause.status === 404
            ? 'This task is no longer available. Your draft is still here so you can copy it.'
            : 'The changes could not be saved. Check the text and try again. Your draft is still here.'
      } else {
        error.value = saved
          ? 'Changes were saved, but the latest details could not be loaded. Retry to refresh without adding the input twice.'
          : 'The save could not be confirmed. Retry the same submission; it will not be added twice.'
      }
    } finally {
      saving.value = false
    }
  }

  async function refreshLatest(): Promise<void> {
    const task = options.task.value
    if (!task || busy.value || pendingIntent.value) return
    refreshing.value = true
    try {
      const latest = await options.refresh(task.key)
      expectedUpdatedAt.value = latest.updated_at
      baseDescription.value = latest.description ?? ''
      latestDescription.value = editing.value ? baseDescription.value : null
      conflict.value = false
      error.value = null
      notice.value = 'Latest content loaded. Your draft is unchanged. Review it before saving.'
    } catch {
      error.value = 'The latest task could not be loaded. Try again; your draft is still here.'
    } finally {
      refreshing.value = false
    }
  }

  function confirmLeave(): boolean {
    if (busy.value) {
      error.value = 'Please wait for the current request before leaving this task.'
      return false
    }
    if (!dirty.value && !pendingIntent.value) return true
    const message = pendingIntent.value
      ? 'The save result is unconfirmed. Leave this task and discard the local draft? Changes may already have been saved.'
      : 'Discard unsaved task changes?'
    if (!window.confirm(message)) return false
    reset()
    return true
  }

  function beforeUnload(event: BeforeUnloadEvent): void {
    if (!dirty.value && !busy.value && !pendingIntent.value) return
    event.preventDefault()
    event.returnValue = ''
  }
  window.addEventListener('beforeunload', beforeUnload)
  onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))

  return {
    editing, description, additionalInput, saving, refreshing, error, notice, conflict,
    latestDescription, pendingIntent, descriptionChanged, dirty, locked, canSave, beginEdit, cancelEdit,
    save, refreshLatest, confirmLeave, syncTask,
  }
}
