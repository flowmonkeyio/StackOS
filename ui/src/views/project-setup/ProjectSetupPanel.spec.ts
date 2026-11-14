import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent, ref } from 'vue'
import type { SchemaSetupAttempt, SchemaSetupWorkflowOut } from '@/api'
import { SetupAttemptRequested_scope, SetupAttemptStatus, SetupManagedExecutionPhase, SetupQuestionAnswer_state, SetupQuestionResponse_channel } from '@/api'
import { ApiError, apiFetch } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import ProjectSetupView from './ProjectSetupView.vue'
import { useProjectSetup } from './useProjectSetup'
import { attemptFixture, setupFixture, setupHosts } from './setupFixtures'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
vi.mock('@/lib/client', async original => ({ ...await original<object>(), apiFetch: vi.fn() }))
let state = setupFixture()
let wrapper: VueWrapper | undefined
const managed = { settings: { model: 'selected-model' }, slot_reserved: false, cancel_requested: false, callback_started: true, binding_id: 1, admission_id: 'fixture', job_ref: 'fixture', diagnostic: null, resume_thread_id: null, phase: SetupManagedExecutionPhase.preparation, adoption_authorized: false, adopted_delivery_digest: null, verification_request_id: null, preflight_failed: false }
const question = { question_id: 'market', revision: 3, prompt: 'Which market should the project use?', workflow_keys: ['fixture.second'], answer_state: SetupQuestionAnswer_state.pending, response_channel: SetupQuestionResponse_channel.managed_ui }
function asking() { return attemptFixture({ status: SetupAttemptStatus.needs_input, revision: 7, thread_id: 'native-exact', managed, question: { ...question, workflow_keys: [...question.workflow_keys] } }) }
function workflow(key: string, patch: Partial<SchemaSetupWorkflowOut> = {}): SchemaSetupWorkflowOut {
  return { workflow_key: key, status: 'current', requested_scope: 'ready_to_use', activation_status: 'verified', missing_activation_claims: [], receipt_id: 1, revision: 1, successful: { verification: { sources: [{ kind: 'template', version: 'v1' }] } }, latest_result: { changes: ['Compatible local instructions kept.'] }, latest_status: 'success', partial_changes: false, diagnostic: null, attempt_ref: null, result_ref: null, ...patch } as SchemaSetupWorkflowOut
}
beforeEach(() => {
  state = setupFixture({ current_attempt: asking() })
  vi.mocked(apiFetch).mockResolvedValue({ items: [] })
  vi.mocked(callOperation).mockReset()
  vi.mocked(callOperation).mockImplementation(async (name, args) => {
    if (name === 'projectSetup.get') return args.resolve_launch_preferences
      ? { ...state, launch_preferences: { host_key: 'codex', ...state.launch_preferences } } : state
    if (name === 'hostMcp.status') return setupHosts
    if (name === 'projectSetup.answer' || name === 'projectSetup.continue') {
      state = { ...state, current_attempt: attemptFixture({ managed, request_id: 11, revision: 8, predecessor_request_id: 10, thread_id: 'native-exact' }) }
      return { data: { attempt: state.current_attempt, attached: false }, project_id: 1 }
    }
    if (name === 'projectSetup.cancel') {
      state = { ...state, current_attempt: attemptFixture({ managed, status: SetupAttemptStatus.cancelled }) }
      return { data: state.current_attempt, project_id: 1 }
    }
    return {}
  })
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; document.body.innerHTML = '' })
async function panel() {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/projects/:id/setup', component: ProjectSetupView },
    { path: '/projects/:id', component: { template: '<p>Home</p>' } },
  ] })
  await router.push('/projects/1/setup'); await router.isReady()
  wrapper = mount({ template: '<RouterView />' }, { attachTo: document.body, global: { plugins: [router, createPinia()], stubs: { teleport: true } } })
  await flushPromises()
  return router
}
function button(label: string) { return wrapper!.findAll('button').find(item => item.text() === label)! }
const writes = () => vi.mocked(callOperation).mock.calls.filter(([name]) => ['projectSetup.answer', 'projectSetup.continue', 'projectSetup.cancel'].includes(name))

describe('setup detail questions and recovery', () => {
  it.each(['cancelled', 'failed'] as const)('can retry confirmed never-started %s work without changing settings', async status => {
    state.current_attempt = attemptFixture({ status: SetupAttemptStatus[status], managed: { ...managed, callback_started: false }, thread_id: null })
    await panel()
    const retry = button(status === 'failed' ? 'Retry setup' : 'Start again')
    expect(retry.attributes('disabled')).toBeUndefined()
    expect(wrapper!.text()).toContain('before the agent started')
    await Promise.all([retry.trigger('click'), retry.trigger('click')]); await flushPromises()
    expect(writes()).toHaveLength(1)
    expect(callOperation).toHaveBeenCalledWith('projectSetup.continue', { project_id: 1, request_id: 10, expected_revision: 1 })
  })
  it.each(['callback', 'partial', 'resume', 'reserved'] as const)('does not offer a first launch for uncertain %s work', async uncertainty => {
    state.current_attempt = attemptFixture({ status: SetupAttemptStatus.cancelled, thread_id: null, managed: { ...managed, callback_started: uncertainty === 'callback', slot_reserved: uncertainty === 'reserved', resume_thread_id: uncertainty === 'resume' ? 'prior-native' : null }, partial_changes_possible: uncertainty === 'partial' })
    await panel()
    expect(button('Start again').attributes('disabled')).toBeDefined()
    expect(writes()).toHaveLength(0)
  })
  it('focuses the concrete question and sends the rendered identity once through the typed envelope', async () => {
    await panel()
    expect(document.activeElement?.tagName).toBe('TEXTAREA')
    expect(button('Send answer').attributes('disabled')).toBeDefined()
    await wrapper!.get('textarea').setValue('United Kingdom')
    await Promise.all([wrapper!.get('form').trigger('submit'), wrapper!.get('form').trigger('submit')]); await flushPromises()
    expect(writes()).toHaveLength(1)
    expect(callOperation).toHaveBeenCalledWith('projectSetup.answer', { project_id: 1, request_id: 10, expected_revision: 7, question_id: 'market', question_revision: 3, answer: 'United Kingdom' })
    expect(wrapper!.find('textarea').exists()).toBe(false)
    expect(wrapper!.text()).toContain('Setup is queued')
  })
  it('keeps the draft and readable error after failure and polling, supports keyboard retry', async () => {
    await panel(); await wrapper!.get('textarea').setValue('United Kingdom')
    vi.mocked(callOperation).mockRejectedValueOnce(new ApiError('Previous turn is still retiring. Check again.', 409, null))
    await wrapper!.get('form').trigger('submit'); await flushPromises()
    expect(wrapper!.get('textarea').element.value).toBe('United Kingdom')
    expect(wrapper!.get('[role="alert"]').text()).toContain('Previous turn is still retiring')
    await button('Refresh').trigger('click'); await flushPromises()
    expect(wrapper!.get('[role="alert"]').text()).toContain('Previous turn is still retiring')
    await wrapper!.get('textarea').trigger('keydown', { key: 'Enter', ctrlKey: true }); await flushPromises()
    expect(writes()).toHaveLength(2)
    expect(wrapper!.find('textarea').exists()).toBe(false)
  })
  it('replaces a stale question draft after conflict without replaying it to the new question', async () => {
    await panel(); await wrapper!.get('textarea').setValue('Old answer')
    state = { ...state, current_attempt: asking() }
    state.current_attempt!.question = { ...state.current_attempt!.question!, revision: 4, prompt: 'Which audience needs this?' }
    vi.mocked(callOperation).mockRejectedValueOnce(new ApiError('Question changed. Review the current question.', 409, null))
    await wrapper!.get('form').trigger('submit'); await flushPromises()
    expect(wrapper!.text()).toContain('Which audience needs this?')
    expect(wrapper!.get('textarea').element.value).toBe('')
    expect(writes()).toHaveLength(1)
  })
  it('explains existing-session answers without a form or unsupported host link', async () => {
    state.current_attempt = { ...asking(), entry_kind: 'existing_agent', managed: null, question: { ...asking().question!, response_channel: 'existing_agent' } } as SchemaSetupAttempt
    await panel()
    expect(wrapper!.text()).toContain('Answer in the agent session where you started setup.')
    expect(wrapper!.find('textarea').exists()).toBe(false)
    expect(wrapper!.find('a[href*="conversation"]').exists()).toBe(false)
    expect(button('Cancel setup')).toBeDefined()
  })
  it('requires a stopped-work acknowledgement for the captured external revision, then offers a separate launch', async () => {
    state.current_attempt = { ...asking(), entry_kind: 'existing_agent', managed: null, question: null } as SchemaSetupAttempt
    await panel()
    await button('Cancel setup').trigger('click'); await flushPromises()
    expect(button('Stop setup').attributes('disabled')).toBeDefined()
    expect(wrapper!.text()).toContain('Stop the previous setup work in your agent app first.')
    await button('Keep working').trigger('click'); await flushPromises()
    expect(writes()).toHaveLength(0)
    await button('Cancel setup').trigger('click'); await flushPromises()
    expect(wrapper!.get('input[type="checkbox"]').element).toHaveProperty('checked', false)
    await wrapper!.get('input[type="checkbox"]').setValue(true)
    vi.mocked(callOperation).mockImplementationOnce(async (_name, args) => {
      state = { ...state, current_attempt: { ...state.current_attempt!, status: SetupAttemptStatus.cancelled, revision: 8 } }
      expect(args).toEqual({ project_id: 1, request_id: 10, expected_revision: 7, external_work_stopped: true })
      return { data: state.current_attempt }
    })
    await button('Stop setup').trigger('click'); await flushPromises()
    expect(button('Set up again')).toBeDefined()
    expect(wrapper!.find('[role="dialog"]').exists()).toBe(false)
    expect(vi.mocked(callOperation).mock.calls.some(([name]) => name === 'projectSetup.start')).toBe(false)
    await button('Set up again').trigger('click'); await flushPromises()
    expect(wrapper!.find('[role="dialog"]').exists()).toBe(true)
    expect(vi.mocked(callOperation).mock.calls.some(([name]) => name === 'projectSetup.start')).toBe(false)
  })
  it('sends the captured external revision after polling advances, refreshes on 409 and requires a fresh decision', async () => {
    let controller!: ReturnType<typeof useProjectSetup>
    state.current_attempt = { ...asking(), entry_kind: 'existing_agent', managed: null } as SchemaSetupAttempt
    wrapper = mount(defineComponent({ setup() { controller = useProjectSetup(ref(1)); return () => null } }))
    await flushPromises()
    const snapshot = { ...controller.attempt.value! }
    state = { ...state, current_attempt: { ...snapshot, revision: 8 } }
    await controller.refresh()
    vi.mocked(callOperation).mockRejectedValueOnce(new ApiError('Review the current revision', 409, null))
    expect(await controller.cancel(snapshot, true)).toBe(false)
    expect(callOperation).toHaveBeenCalledWith('projectSetup.cancel', { project_id: 1, request_id: 10, expected_revision: 7, external_work_stopped: true })
    expect(controller.error.value).toBe('Review the current revision')
    expect(controller.attempt.value?.revision).toBe(8)
  })
  it('retains a cancelled external question as history without asking for an answer on its revoked session', async () => {
    state.current_attempt = { ...asking(), entry_kind: 'existing_agent', status: SetupAttemptStatus.cancelled, managed: null } as SchemaSetupAttempt
    await panel()
    expect(wrapper!.text()).not.toContain('Answer in the agent session where you started setup.')
    expect(wrapper!.find('textarea').exists()).toBe(false)
    expect(button('Set up again')).toBeDefined()
    expect(writes()).toHaveLength(0)
    expect(state.current_attempt?.question?.prompt).toBe(question.prompt)
  })
  it.each(['queued', 'running', 'needs_input', 'success', 'partial', 'failed', 'interrupted', 'cancelled'] as const)('retains one shell and completed results in %s', async status => {
    state.current_attempt = attemptFixture({ status: SetupAttemptStatus[status], thread_id: 'native-exact', managed, ...(status === 'needs_input' ? { question: asking().question } : {}) })
    state.workflows = [workflow('fixture.first')]
    await panel()
    expect(wrapper!.text()).toContain('Compatible local instructions kept.')
    expect(wrapper!.findAll('h1')).toHaveLength(1)
    expect(writes()).toHaveLength(0)
    if (['partial', 'failed', 'interrupted', 'cancelled'].includes(status)) {
      expect(button(status === 'failed' ? 'Retry remaining' : status === 'interrupted' ? 'Resume setup' : status === 'cancelled' ? 'Start again' : 'Continue remaining')).toBeDefined()
      expect(wrapper!.text()).not.toContain('Your project is ready')
    }
  })
  it('reports mixed outcomes and old verified versions without global readiness or invented override questions', async () => {
    state.current_attempt = attemptFixture({ status: SetupAttemptStatus.partial, managed, thread_id: 'native-exact', partial_changes_possible: true })
    state.workflows = [workflow('fixture.first'), workflow('fixture.second', { latest_status: 'failed', status: 'update_available' as SchemaSetupWorkflowOut['status'], partial_changes: true, latest_result: { diagnostics: ['Check conflicting instruction.'], changes: ['Edited local guidance.'] } })]
    await panel()
    expect(wrapper!.text()).toContain('Some files may have changed')
    expect(wrapper!.text()).toContain('Last verified setup: v1')
    expect(wrapper!.text()).toContain('Check conflicting instruction.')
    expect(wrapper!.text()).not.toContain('Your project is ready')
    expect(wrapper!.find('textarea').exists()).toBe(false)
    await button('Continue remaining').trigger('click'); await flushPromises()
    expect(callOperation).toHaveBeenCalledWith('projectSetup.continue', { project_id: 1, request_id: 10, expected_revision: 1 })
    expect(wrapper!.text()).toContain('Compatible local instructions kept.')
  })
  it('dismisses the page without mutation; cancellation is separately confirmed', async () => {
    state.current_attempt = attemptFixture({ managed, status: SetupAttemptStatus.running })
    const router = await panel()
    await button('Cancel setup').trigger('click'); await flushPromises()
    expect(writes()).toHaveLength(0)
    expect(wrapper!.get('[role="dialog"]').text()).toContain('Completed workflow results are kept')
    await button('Keep working').trigger('click'); await flushPromises()
    expect(writes()).toHaveLength(0)
    await wrapper!.get('a[href="/projects/1"]').trigger('click'); await flushPromises()
    expect(router.currentRoute.value.path).toBe('/projects/1'); expect(writes()).toHaveLength(0)
    await router.push('/projects/1/setup'); await flushPromises()
    await button('Cancel setup').trigger('click'); await flushPromises()
    await button('Stop setup').trigger('click'); await flushPromises()
    expect(writes()).toHaveLength(1)
    expect(wrapper!.text()).toContain('Setup was cancelled')
  })
  it('does not claim ready while activation/updates remain or a failed attempt exists', async () => {
    state.current_attempt = attemptFixture({ status: SetupAttemptStatus.success })
    await panel()
    expect(wrapper!.text()).not.toContain('Your project is ready')
    state = setupFixture({ ...state, status: 'current' as typeof state.status, workflows: [workflow('fixture.first')] })
    await button('Refresh').trigger('click'); await flushPromises()
    expect(wrapper!.text()).toContain('Your project is ready')
    expect(wrapper!.text()).toContain("Your agent will use the project's configured workflows automatically.")
  })
  it('keeps completed copy concise and retains paths and the agent report in details', async () => {
    state = setupFixture({ status: 'current' as typeof state.status,
      current_attempt: attemptFixture({ status: SetupAttemptStatus.success, partial_changes_possible: true, progress: { message: 'Checked /private/fixture/guide.md; verbose evidence retained.', evidence_ref: 'fixture/checked' } }),
      workflows: [workflow('fixture.first', { latest_result: { changes: ['/private/fixture/guide.md'] } })],
    })
    await panel()
    expect(wrapper!.get('table tbody tr:first-child td:nth-child(2) > p').text()).toBe('Workflow setup and activation are verified.')
    expect(wrapper!.text()).not.toContain('Some files may have changed')
    expect(wrapper!.get('section[aria-labelledby="setup-state-heading"]').find('[role="status"]').exists()).toBe(false)
    expect(wrapper!.findAll('details').some(item => item.text().includes('Agent result') && item.text().includes('/private/fixture/guide.md'))).toBe(true)
  })
  it('does not turn a retained input-needed result into a new unanswered question', async () => {
    state.current_attempt = attemptFixture({ status: SetupAttemptStatus.cancelled, managed, thread_id: 'native-exact', question: null })
    state.workflows = [workflow('fixture.second', { latest_status: 'needs_input', status: 'update_available' as SchemaSetupWorkflowOut['status'] })]
    await panel()
    expect(wrapper!.text()).not.toContain('waiting for your answer')
    expect(wrapper!.find('textarea').exists()).toBe(false)
    expect(button('Start again')).toBeDefined()
  })
  it.each(['pending', 'blocked', 'unknown'] as const)('never labels %s activation ready and shows the missing coverage', async activation => {
    state = setupFixture({ status: 'current' as typeof state.status,
      current_attempt: attemptFixture({ status: SetupAttemptStatus.success }),
      workflows: [workflow('fixture.first'), workflow('fixture.second', {
        activation_status: activation as SchemaSetupWorkflowOut['activation_status'],
        missing_activation_claims: ['native_consumption'], diagnostic: 'Agent loading still needs verification.',
      })],
    })
    await panel()
    expect(wrapper!.text()).not.toContain('Your project is ready')
    expect(wrapper!.text()).toContain('1 of 2 workflows ready')
    expect(wrapper!.text()).toContain('Agent loading still needs verification.')
    expect(wrapper!.text()).toContain('Verify that the agent loaded the saved setup.')
    expect(wrapper!.findAll('button').some(item => /activate/i.test(item.text()))).toBe(false)
  })
  it('labels explicit infrastructure-only completion as preparation', async () => {
    state = setupFixture({ status: 'current' as typeof state.status,
      current_attempt: attemptFixture({ status: SetupAttemptStatus.success, requested_scope: SetupAttemptRequested_scope.infrastructure_only }),
      workflows: [workflow('fixture.first', { requested_scope: 'infrastructure_only' as SchemaSetupWorkflowOut['requested_scope'], activation_status: 'unknown' as SchemaSetupWorkflowOut['activation_status'] })],
    })
    await panel()
    expect(wrapper!.text()).toContain('Project preparation is complete')
    expect(wrapper!.text()).toContain('Prepared')
    expect(wrapper!.text()).not.toContain('Your project is ready')
  })
  it('does not infer readiness from legacy receipts or a missing selected workflow', async () => {
    const legacy = workflow('fixture.first')
    // Historical API responses omitted scope and activation fields.
    delete (legacy as Partial<SchemaSetupWorkflowOut>).requested_scope
    delete (legacy as Partial<SchemaSetupWorkflowOut>).activation_status
    state = setupFixture({ status: 'current' as typeof state.status,
      current_attempt: attemptFixture({ status: SetupAttemptStatus.success }), workflows: [legacy],
    })
    await panel()
    expect(wrapper!.text()).not.toContain('Your project is ready')
    expect(wrapper!.text()).toContain('Prepared')
    state = { ...state, workflows: [workflow('fixture.first')],
      current_attempt: attemptFixture({ status: SetupAttemptStatus.success, workflow_keys: ['fixture.first', 'fixture.missing'] }),
    }
    await button('Refresh').trigger('click'); await flushPromises()
    expect(wrapper!.text()).not.toContain('Your project is ready')
    expect(wrapper!.text()).toContain('fixture.missing')
  })
  it('retains results through refresh and advances preparation to verification without a new action', async () => {
    state.current_attempt = attemptFixture({ status: SetupAttemptStatus.running, managed, requested_scope: SetupAttemptRequested_scope.ready_to_use })
    state.workflows = [workflow('fixture.first')]
    await panel()
    expect(wrapper!.text()).toContain('Your agent is preparing the project')
    let finish!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    await button('Refresh').trigger('click')
    expect(wrapper!.text()).toContain('Compatible local instructions kept.')
    expect(wrapper!.text()).not.toContain('Loading project setup')
    finish({ ...state, current_attempt: { ...state.current_attempt, revision: 2, managed: { ...managed, phase: 'verification' } } })
    await flushPromises()
    expect(wrapper!.text()).toContain('Your agent is verifying workflow readiness')
    expect(button('Cancel setup')).toBeDefined()
    expect(writes()).toHaveLength(0)
  })
  it('blocks competing actions and ignores a late continuation after disposal', async () => {
    let controller!: ReturnType<typeof useProjectSetup>
    wrapper = mount(defineComponent({ setup() { controller = useProjectSetup(ref(1)); return () => null } }))
    await flushPromises()
    const snapshot = controller.attempt.value!
    let finish!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const submission = controller.answer(snapshot, 'One answer')
    expect(await controller.cancel(snapshot)).toBe(false)
    wrapper.unmount(); wrapper = undefined
    finish({ data: { attempt: attemptFixture({ request_id: 12 }) } }); await submission
    expect(controller.attempt.value?.request_id).toBe(10)
  })
  it('keeps cancellation bound to the confirmed invocation when progress advances, never its successor', async () => {
    let controller!: ReturnType<typeof useProjectSetup>
    wrapper = mount(defineComponent({ setup() { controller = useProjectSetup(ref(1)); return () => null } }))
    await flushPromises()
    const snapshot = controller.attempt.value!
    expect(await controller.cancel({ ...snapshot, project_id: 2 })).toBe(false)
    expect(writes()).toHaveLength(0)
    state.current_attempt = { ...snapshot, revision: snapshot.revision + 1 }
    await controller.refresh()
    expect(await controller.cancel(snapshot)).toBe(true)
    state.current_attempt = attemptFixture({ request_id: 11, managed })
    await controller.refresh()
    expect(await controller.cancel(snapshot)).toBe(false)
    expect(writes()).toHaveLength(1)
  })
})
