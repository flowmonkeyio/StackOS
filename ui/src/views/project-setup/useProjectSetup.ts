import { computed, onBeforeUnmount, ref, type Ref } from 'vue'
import type { SchemaMainAgentPreferences, SchemaProjectSetupOut, SchemaSetupAttempt, SchemaWriteEnvelopeProjectSetupOut, SchemaWriteEnvelopeSetupStartOut } from '@/api'
import { usePolling } from '@/composables/usePolling'
import { ApiError, formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'

// Setup is a human decision surface. Keep machine routing hints and request
// metadata in the API/audit rather than appending them to the visible copy.
export function setupErrorMessage(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.detail : formatApiError(cause, fallback)
}

export function useProjectSetup(projectId: Ref<number>) {
  const state = ref<SchemaProjectSetupOut | null>(null)
  const error = ref<string | null>(null)
  const writing = ref(false)
  let revision = 0
  let disposed = false

  async function load(): Promise<void> {
    if (writing.value) return
    const current = revision
    const id = projectId.value
    try {
      const result = await callOperation<SchemaProjectSetupOut>('projectSetup.get', { project_id: id })
      if (disposed || current !== revision || id !== projectId.value) return
      state.value = result
      error.value = null
    } catch (cause) {
      if (!disposed && current === revision && id === projectId.value) error.value = setupErrorMessage(cause, 'Project setup could not be loaded.')
    }
  }
  const { refresh, running } = usePolling(load)
  onBeforeUnmount(() => { disposed = true; revision++ })

  async function dismiss(kind: 'setup' | 'updates'): Promise<void> {
    if (writing.value) return
    writing.value = true
    revision++
    try {
      const result = await callOperation<SchemaWriteEnvelopeProjectSetupOut>('projectSetup.setReminders', {
        project_id: projectId.value,
        ...(kind === 'setup' ? { dismiss_setup: true } : { dismiss_updates: true }),
      })
      if (!disposed) { state.value = result.data; error.value = null }
    } catch (cause) {
      if (!disposed) error.value = setupErrorMessage(cause, 'The reminder could not be dismissed.')
    } finally { if (!disposed) writing.value = false }
  }

  async function start(preferences: SchemaMainAgentPreferences): Promise<SchemaSetupAttempt | null> {
    if (writing.value || !state.value?.eligible || state.value.folder_status !== 'available') return null
    writing.value = true
    revision++
    try {
      const result = await callOperation<SchemaWriteEnvelopeSetupStartOut>('projectSetup.start', {
        project_id: projectId.value, entry_kind: 'managed_ui',
        mode: state.value.status === 'updates_available' ? 'update' : 'setup', preferences,
      })
      if (disposed) return null
      state.value = { ...state.value, current_attempt: result.data.attempt }
      error.value = null
      return result.data.attempt
    } catch (cause) {
      if (!disposed) error.value = setupErrorMessage(cause, 'Setup could not be started.')
      return null
    } finally { if (!disposed) writing.value = false }
  }

  const attempt = computed(() => state.value?.current_attempt)
  const actionLabel = computed(() => {
    const current = attempt.value
    if (state.value?.folder_status === 'unavailable') return 'View setup'
    if (current?.status === 'needs_input') return current.question?.response_channel === 'managed_ui' ? 'Answer question' : 'View setup'
    if (current && ['queued', 'running'].includes(current.status)) return 'View setup'
    if (current && ['partial', 'failed', 'interrupted', 'cancelled'].includes(current.status)) return 'Continue setup'
    if (state.value?.status === 'updates_available') return 'Update workflows'
    return state.value?.status === 'setup_needed' ? 'Set up project' : 'Manage setup'
  })
  const reminder = computed(() => {
    const value = state.value
    if (!value?.eligible || value.folder_status !== 'available'
      || (attempt.value && attempt.value.status !== 'success')) return null
    if (value.status === 'setup_needed' && !value.setup_reminder_dismissed_at) return 'setup'
    if (value.status === 'updates_available' && !value.update_reminder_dismissed_at) return 'updates'
    return null
  })
  const opensLaunch = computed(() => state.value?.folder_status === 'available'
    && (!attempt.value || attempt.value.status === 'success')
    && ['setup_needed', 'updates_available'].includes(state.value.status))

  return { state, attempt, error, writing, running, refresh, dismiss, start, actionLabel, reminder, opensLaunch }
}
