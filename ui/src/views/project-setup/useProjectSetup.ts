import { computed, getCurrentInstance, onBeforeUnmount, ref, type App, type Ref } from 'vue'
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

export function readProjectSetup(projectId: number, resolveLaunchPreferences = false, summaryOnly = false): Promise<SchemaProjectSetupOut> {
  return callOperation<SchemaProjectSetupOut>('projectSetup.get', {
    project_id: projectId, ...(resolveLaunchPreferences ? { resolve_launch_preferences: true } : {}),
    ...(summaryOnly ? { summary_only: true } : {}),
  })
}

type SetupReminder = 'setup' | 'updates' | null
const retainedReminders = new WeakMap<App, { projectId: number; reminder: SetupReminder; updateCount: number }>()

function projectReminder(value: SchemaProjectSetupOut, readyToUse: boolean): SetupReminder {
  if (!value.eligible || value.folder_status !== 'available' || (value.work && value.work.run_status !== 'completed')
    || (value.current_attempt && value.current_attempt.status !== 'success')) return null
  if ((value.status === 'setup_needed' || (value.status === 'current' && !readyToUse)) && !value.setup_reminder_dismissed_at) return 'setup'
  if (value.status === 'updates_available' && !value.update_reminder_dismissed_at) return 'updates'
  return null
}

function setupReady(value: SchemaProjectSetupOut | null): boolean {
  return value?.status === 'current' && !!value.workflows?.length
    && (!value.work || value.work.run_status === 'completed')
    && value.workflows.every(setupWorkflowReady)
    && (value.current_attempt?.workflow_keys ?? []).every(key => value.workflows!.some(item => item.workflow_key === key && setupWorkflowReady(item)))
    && (!value.current_attempt || (value.current_attempt.status === 'success' && value.current_attempt.requested_scope === 'ready_to_use'))
}

export function useProjectSetup(projectId: Ref<number>, options: { retainReminder?: boolean; summaryOnly?: boolean } = {}) {
  const app = getCurrentInstance()?.appContext.app
  const previous = app && options.retainReminder ? retainedReminders.get(app) : null
  const initialReminder = computed(() => previous?.projectId === projectId.value ? previous : null)
  // Home retains only public reminder geometry/copy. Setup state, attempts and
  // launch readiness still require the current authoritative read.
  const receivedState = ref<SchemaProjectSetupOut | null>(null)
  const state = computed({
    get: () => receivedState.value?.project_id === projectId.value ? receivedState.value : null,
    set: (value: SchemaProjectSetupOut | null) => { receivedState.value = value },
  })
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
      const result = await readProjectSetup(id, false, options.summaryOnly)
      if (disposed || current !== revision || id !== projectId.value || result.project_id !== id) return
      state.value = result
      readError.value = null
    } catch (cause) {
      if (!disposed && current === revision && id === projectId.value) readError.value = setupErrorMessage(cause, 'Project setup could not be loaded.')
    }
  }
  const { refresh, running } = usePolling(load)
  onBeforeUnmount(() => {
    const last = receivedState.value
    if (app && last) retainedReminders.set(app, {
      projectId: last.project_id, reminder: projectReminder(last, setupReady(last)), updateCount: last.update_count,
    })
    disposed = true; revision++
  })

  async function dismiss(kind: 'setup' | 'updates'): Promise<void> {
    if (writing.value) return
    const id = projectId.value
    writing.value = true
    actionError.value = null
    revision++
    try {
      const result = await callOperation<SchemaWriteEnvelopeProjectSetupOut>('projectSetup.setReminders', {
        project_id: id,
        ...(kind === 'setup' ? { dismiss_setup: true } : { dismiss_updates: true }),
      })
      if (!disposed && id === projectId.value && result.data.project_id === id) { state.value = result.data; readError.value = null }
    } catch (cause) {
      if (!disposed && id === projectId.value) actionError.value = setupErrorMessage(cause, 'The reminder could not be dismissed.')
    } finally { if (!disposed) writing.value = false }
  }

  async function start(preferences: SchemaMainAgentPreferences): Promise<SchemaSetupAttempt | null> {
    if (writing.value || !state.value?.eligible || state.value.folder_status !== 'available'
      || (work.value && work.value.run_status !== 'completed')) return null
    const id = projectId.value
    writing.value = true
    actionError.value = null
    revision++
    try {
      const result = await callOperation<SchemaWriteEnvelopeSetupStartOut>('projectSetup.start', {
        project_id: id, entry_kind: 'managed_ui',
        mode: state.value.status === 'updates_available' ? 'update' : 'setup', requested_scope: 'ready_to_use', preferences,
      })
      if (disposed || id !== projectId.value || result.data.attempt.project_id !== id) return null
      state.value = { ...state.value!, current_attempt: result.data.attempt, work: null }
      readError.value = null
      return result.data.attempt
    } catch (cause) {
      if (!disposed && id === projectId.value) actionError.value = setupErrorMessage(cause, 'Setup could not be started.')
      return null
    } finally {
      if (!disposed) { writing.value = false; await load() }
    }
  }

  const attempt = computed(() => state.value?.current_attempt)
  const work = computed(() => state.value?.work)
  const workLink = computed(() => work.value ? `/projects/${projectId.value}/tasks?view=graph&task=${encodeURIComponent(work.value.task_key)}` : null)
  const ready = computed(() => setupReady(state.value))
  const preparationOnly = computed(() => !!attempt.value && attempt.value.requested_scope !== 'ready_to_use')
  const attemptStatus = computed(() => {
    const current = attempt.value
    if (!current) return 'unknown'
    const execution = work.value
    if (execution) {
      if (execution.run_status === 'aborted') return 'cancelled'
      if (execution.delivery_status === 'queued' || execution.delivery_status === 'running') return execution.delivery_status
      if (current.question?.answer_state === 'pending') return 'needs_input'
      if (execution.run_status === 'failed') return 'failed'
      if (execution.delivery_status === 'stopped') return 'interrupted'
      if (execution.run_status !== 'completed') return 'unfinished'
    }
    if (current.status === 'running') return current.managed?.phase === 'verification' ? 'verifying' : 'preparing'
    if (current.status === 'success') return ready.value ? 'ready' : preparationOnly.value ? 'prepared' : 'unfinished'
    return current.status
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
      if (disposed || id !== projectId.value || result.project_id !== id) return false
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
    if (!work.value?.can_answer || !question || question.answer_state !== 'pending' || question.response_channel !== 'managed_ui'
      || !text.trim() || text.length > 8000) return false
    const args: Pick<SchemaProjectSetupAnswerInput, 'project_id' | 'request_id' | 'expected_revision' | 'question_id' | 'question_revision' | 'answer'> = {
      project_id: snapshot.project_id, request_id: snapshot.request_id, expected_revision: snapshot.revision,
      question_id: question.question_id, question_revision: question.revision, answer: text,
    }
    return changeAttempt(snapshot, async () => (await callOperation<SchemaWriteEnvelopeSetupStartOut>('projectSetup.answer', args)).data.attempt)
  }

  async function continueSetup(snapshot: SchemaSetupAttempt): Promise<boolean> {
    if (!work.value?.can_continue) return false
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
    if (work.value) {
      if (work.value.can_answer) return 'Answer question'
      if (work.value.run_status !== 'completed') return 'View setup'
    }
    if (current?.status === 'needs_input') return 'View setup'
    if (current && ['queued', 'running'].includes(current.status)) return 'View setup'
    if (current?.entry_kind === 'existing_agent' && current.status === 'cancelled') return 'View setup'
    if (current && ['partial', 'failed', 'interrupted', 'cancelled'].includes(current.status)) return 'View setup'
    if (state.value?.status === 'updates_available') return 'Update workflows'
    return state.value?.status === 'setup_needed' || (state.value?.status === 'current' && !ready.value) ? 'Set up project' : 'Manage setup'
  })
  const reminder = computed(() => {
    const value = state.value
    return value ? projectReminder(value, ready.value) : initialReminder.value?.reminder ?? null
  })
  const reminderUpdateCount = computed(() => state.value?.update_count ?? initialReminder.value?.updateCount ?? 0)
  const opensLaunch = computed(() => state.value?.folder_status === 'available'
    && (!work.value || work.value.run_status === 'completed')
    && (!attempt.value || attempt.value.status === 'success')
    && (['setup_needed', 'updates_available'].includes(state.value.status) || (state.value.status === 'current' && !ready.value)))

  return { state, attempt, work, workLink, ready, preparationOnly, attemptStatus, error, writing, running, refresh, dismiss, start, answer, continueSetup, cancel, actionLabel, reminder, reminderUpdateCount, opensLaunch }
}
