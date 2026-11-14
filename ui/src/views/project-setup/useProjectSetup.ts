import { computed, onBeforeUnmount, ref, type Ref } from 'vue'
import type { SchemaMainAgentPreferences, SchemaProjectSetupAnswerInput, SchemaProjectSetupCancelInput, SchemaProjectSetupContinueInput, SchemaProjectSetupOut, SchemaSetupAttempt, SchemaWriteEnvelopeProjectSetupOut, SchemaWriteEnvelopeSetupAttempt, SchemaWriteEnvelopeSetupStartOut } from '@/api'
import { usePolling } from '@/composables/usePolling'
import { ApiError, formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { setupWorkflowReady } from './viewModel'

// Setup is a human decision surface. Keep machine routing hints and request
// metadata in the API/audit rather than appending them to the visible copy.
export function setupErrorMessage(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.detail : formatApiError(cause, fallback)
}

export function readProjectSetup(projectId: number, resolveLaunchPreferences = false): Promise<SchemaProjectSetupOut> {
  return callOperation<SchemaProjectSetupOut>('projectSetup.get', {
    project_id: projectId, ...(resolveLaunchPreferences ? { resolve_launch_preferences: true } : {}),
  })
}

export function useProjectSetup(projectId: Ref<number>) {
  const state = ref<SchemaProjectSetupOut | null>(null)
  const readError = ref<string | null>(null)
  const actionError = ref<string | null>(null)
  const error = computed(() => actionError.value || readError.value)
  const writing = ref(false)
  let revision = 0
  let disposed = false

  async function load(): Promise<void> {
    if (writing.value) return
    const current = revision
    const id = projectId.value
    try {
      const result = await readProjectSetup(id)
      if (disposed || current !== revision || id !== projectId.value) return
      state.value = result
      readError.value = null
    } catch (cause) {
      if (!disposed && current === revision && id === projectId.value) readError.value = setupErrorMessage(cause, 'Project setup could not be loaded.')
    }
  }
  const { refresh, running } = usePolling(load)
  onBeforeUnmount(() => { disposed = true; revision++ })

  async function dismiss(kind: 'setup' | 'updates'): Promise<void> {
    if (writing.value) return
    writing.value = true
    actionError.value = null
    revision++
    try {
      const result = await callOperation<SchemaWriteEnvelopeProjectSetupOut>('projectSetup.setReminders', {
        project_id: projectId.value,
        ...(kind === 'setup' ? { dismiss_setup: true } : { dismiss_updates: true }),
      })
      if (!disposed) { state.value = result.data; readError.value = null }
    } catch (cause) {
      if (!disposed) actionError.value = setupErrorMessage(cause, 'The reminder could not be dismissed.')
    } finally { if (!disposed) writing.value = false }
  }

  async function start(preferences: SchemaMainAgentPreferences): Promise<SchemaSetupAttempt | null> {
    if (writing.value || !state.value?.eligible || state.value.folder_status !== 'available') return null
    writing.value = true
    actionError.value = null
    revision++
    try {
      const result = await callOperation<SchemaWriteEnvelopeSetupStartOut>('projectSetup.start', {
        project_id: projectId.value, entry_kind: 'managed_ui',
        mode: state.value.status === 'updates_available' ? 'update' : 'setup', requested_scope: 'ready_to_use', preferences,
      })
      if (disposed) return null
      state.value = { ...state.value, current_attempt: result.data.attempt }
      readError.value = null
      return result.data.attempt
    } catch (cause) {
      if (!disposed) actionError.value = setupErrorMessage(cause, 'Setup could not be started.')
      return null
    } finally { if (!disposed) writing.value = false }
  }

  const attempt = computed(() => state.value?.current_attempt)
  const ready = computed(() => state.value?.status === 'current' && !!state.value.workflows?.length
    && state.value.workflows.every(setupWorkflowReady)
    && (attempt.value?.workflow_keys ?? []).every(key => state.value!.workflows!.some(item => item.workflow_key === key && setupWorkflowReady(item)))
    && (!attempt.value || (attempt.value.status === 'success' && attempt.value.requested_scope === 'ready_to_use')))
  const preparationOnly = computed(() => !!attempt.value && attempt.value.requested_scope !== 'ready_to_use')
  const attemptStatus = computed(() => {
    const current = attempt.value
    if (!current) return 'unknown'
    if (current.status === 'running') return current.managed?.phase === 'verification' ? 'verifying' : 'preparing'
    if (current.status === 'success') return ready.value ? 'ready' : preparationOnly.value ? 'prepared' : 'unfinished'
    return current.status
  })
  // The executor confirms absence of native evidence under its admission lock.
  // These persisted pre-launch facts only enable the explicit recovery control.
  const neverStarted = computed(() => {
    const current = attempt.value
    return !!current?.managed && current.entry_kind === 'managed_ui'
      && ['cancelled', 'failed'].includes(current.status)
      && current.managed.callback_started === false && current.managed.slot_reserved === false
      && !current.managed.resume_thread_id && !current.thread_id
      && current.partial_changes_possible === false && current.reports?.length === 0 && current.answers?.length === 0
  })

  // Use the identity rendered with the draft/confirmation, never a newer
  // question observed by polling. The daemon remains the revision/CAS owner.
  async function changeAttempt(snapshot: SchemaSetupAttempt, operation: () => Promise<SchemaSetupAttempt>): Promise<boolean> {
    if (writing.value || snapshot.project_id !== projectId.value
      || attempt.value?.request_id !== snapshot.request_id || attempt.value.revision !== snapshot.revision) return false
    const id = projectId.value
    writing.value = true
    actionError.value = null
    revision++
    try {
      const result = await operation()
      if (disposed || id !== projectId.value) return false
      state.value = { ...state.value!, current_attempt: result }
      return true
    } catch (cause) {
      if (!disposed && id === projectId.value) actionError.value = setupErrorMessage(cause, 'Setup could not be continued. Refresh and try again.')
      return false
    } finally {
      if (!disposed) {
        writing.value = false
        await load() // Reconcile stale/conflicting answers and refresh workflow receipts.
      }
    }
  }

  async function answer(snapshot: SchemaSetupAttempt, text: string): Promise<boolean> {
    const question = snapshot.question
    if (!question || question.answer_state !== 'pending' || question.response_channel !== 'managed_ui'
      || !text.trim() || text.length > 8000) return false
    const args: Pick<SchemaProjectSetupAnswerInput, 'project_id' | 'request_id' | 'expected_revision' | 'question_id' | 'question_revision' | 'answer'> = {
      project_id: snapshot.project_id, request_id: snapshot.request_id, expected_revision: snapshot.revision,
      question_id: question.question_id, question_revision: question.revision, answer: text,
    }
    return changeAttempt(snapshot, async () => (await callOperation<SchemaWriteEnvelopeSetupStartOut>('projectSetup.answer', args)).data.attempt)
  }

  async function continueSetup(snapshot: SchemaSetupAttempt): Promise<boolean> {
    const args: Pick<SchemaProjectSetupContinueInput, 'project_id' | 'request_id' | 'expected_revision'> = {
      project_id: snapshot.project_id, request_id: snapshot.request_id, expected_revision: snapshot.revision,
    }
    return changeAttempt(snapshot, async () => (await callOperation<SchemaWriteEnvelopeSetupStartOut>('projectSetup.continue', args)).data.attempt)
  }

  async function cancel(snapshot: SchemaSetupAttempt, externalWorkStopped = false): Promise<boolean> {
    const current = attempt.value
    // Confirmation targets this invocation, even if progress advanced its
    // revision meanwhile. A successor invocation is a different target.
    if (!current || current.request_id !== snapshot.request_id || current.project_id !== snapshot.project_id) return false
    const external = snapshot.entry_kind === 'existing_agent'
    if (external && !externalWorkStopped) return false
    const args: Pick<SchemaProjectSetupCancelInput, 'project_id' | 'request_id'> & Partial<Pick<SchemaProjectSetupCancelInput, 'expected_revision' | 'external_work_stopped'>> = {
      project_id: snapshot.project_id, request_id: snapshot.request_id,
      ...(external ? { expected_revision: snapshot.revision, external_work_stopped: true } : {}),
    }
    return changeAttempt(current, async () => (await callOperation<SchemaWriteEnvelopeSetupAttempt>('projectSetup.cancel', args)).data)
  }
  const actionLabel = computed(() => {
    const current = attempt.value
    if (state.value?.folder_status === 'unavailable') return 'View setup'
    if (current?.status === 'needs_input') return current.question?.response_channel === 'managed_ui' ? 'Answer question' : 'View setup'
    if (current && ['queued', 'running'].includes(current.status)) return 'View setup'
    if (neverStarted.value) return 'View setup'
    if (current?.entry_kind === 'existing_agent' && current.status === 'cancelled') return 'View setup'
    if (current && ['partial', 'failed', 'interrupted', 'cancelled'].includes(current.status)) return 'Continue setup'
    if (state.value?.status === 'updates_available') return 'Update workflows'
    return state.value?.status === 'setup_needed' || (state.value?.status === 'current' && !ready.value) ? 'Set up project' : 'Manage setup'
  })
  const reminder = computed(() => {
    const value = state.value
    if (!value?.eligible || value.folder_status !== 'available'
      || (attempt.value && attempt.value.status !== 'success')) return null
    if ((value.status === 'setup_needed' || (value.status === 'current' && !ready.value)) && !value.setup_reminder_dismissed_at) return 'setup'
    if (value.status === 'updates_available' && !value.update_reminder_dismissed_at) return 'updates'
    return null
  })
  const opensLaunch = computed(() => state.value?.folder_status === 'available'
    && (!attempt.value || attempt.value.status === 'success')
    && (['setup_needed', 'updates_available'].includes(state.value.status) || (state.value.status === 'current' && !ready.value)))

  return { state, attempt, ready, preparationOnly, attemptStatus, neverStarted, error, writing, running, refresh, dismiss, start, answer, continueSetup, cancel, actionLabel, reminder, opensLaunch }
}
