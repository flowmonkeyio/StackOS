import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent, ref } from 'vue'
import { callOperation } from '@/lib/operations'
import { ApiError, apiFetch } from '@/lib/client'
import { ProjectSetupOutFolder_status, ProjectSetupOutStatus, SetupAttemptEntry_kind, SetupAttemptStatus, SetupManagedExecutionPhase, SetupQuestionAnswer_state, SetupQuestionResponse_channel } from '@/api'
import HomeConsoleView from '../HomeConsoleView.vue'
import ProjectSetupView from './ProjectSetupView.vue'
import ProjectSetupLaunchDialog from './ProjectSetupLaunchDialog.vue'
import { useProjectSetup } from './useProjectSetup'
import { attemptFixture, setupFixture, setupHosts } from './setupFixtures'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
vi.mock('@/lib/client', async original => ({ ...await original<object>(), apiFetch: vi.fn() }))
let state = setupFixture()
let wrapper: VueWrapper | undefined
beforeEach(() => {
  state = setupFixture()
  vi.mocked(apiFetch).mockResolvedValue({ items: [], accounts: [] })
  vi.mocked(callOperation).mockReset()
  vi.mocked(callOperation).mockImplementation(async (name, args) => {
    if (name === 'projectSetup.get') return args.resolve_launch_preferences
      ? { ...state, launch_preferences: { host_key: 'codex', ...state.launch_preferences } } : state
    if (name === 'hostMcp.status') return setupHosts
    if (name === 'projectSetup.setReminders') {
      state = { ...state, ...(args.dismiss_setup ? { setup_reminder_dismissed_at: '2026-11-12T00:00:00Z' } : {}), ...(args.dismiss_updates ? { update_reminder_dismissed_at: '2026-11-12T00:00:00Z' } : {}) }
      return { project_id: 1, data: state }
    }
    if (name === 'projectSetup.start') {
      state = { ...state, current_attempt: attemptFixture() }
      return { project_id: 1, data: { attempt: state.current_attempt, attached: false } }
    }
    if (name === 'project.portfolio') return { items: [] }
    return {}
  })
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; document.body.innerHTML = '' })

async function home() {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/projects/:id', component: HomeConsoleView },
    { path: '/projects/:id/setup', component: ProjectSetupView },
    { path: '/projects/:id/agents', component: { template: '<p>Agent setup</p>' } },
  ] })
  await router.push('/projects/1'); await router.isReady()
  wrapper = mount({ template: '<RouterView :key="$route.path" />' }, {
    attachTo: document.body,
    global: { plugins: [router, createPinia()], stubs: { teleport: true, ActiveTaskList: true, AgentActivitySection: true, HomeConnectionsSection: true, DeliveryCapacityControl: true } },
  })
  await flushPromises()
  return router
}
function button(label: string) {
  return wrapper!.findAll('button').find(item => item.text() === label)!
}
const writes = () => vi.mocked(callOperation).mock.calls.filter(([name]) => ['projectSetup.start', 'projectSetup.setReminders'].includes(name))

describe('Home project setup', () => {
  it('opens the cancelled external record even when retained receipts make aggregate setup current', async () => {
    state = setupFixture({ status: ProjectSetupOutStatus.current, configured_count: 1,
      current_attempt: attemptFixture({ entry_kind: SetupAttemptEntry_kind.existing_agent, status: SetupAttemptStatus.cancelled }) })
    const router = await home()
    await button('View setup').trigger('click'); await flushPromises()
    expect(router.currentRoute.value.path).toBe('/projects/1/setup')
    expect(button('Set up again')).toBeDefined()
    expect(wrapper!.find('[role="dialog"]').exists()).toBe(false)
    expect(writes()).toHaveLength(0)
  })
  it.each(['cancelled', 'failed'] as const)('opens actionable pre-launch %s recovery without a new agent picker', async status => {
    state.current_attempt = attemptFixture({ status: SetupAttemptStatus[status], managed: { settings: { host_key: 'codex', model: 'original-model' }, binding_id: 1, admission_id: 'fixture', job_ref: 'fixture', callback_started: false, slot_reserved: false, cancel_requested: status === 'cancelled', resume_thread_id: null, diagnostic: null, phase: SetupManagedExecutionPhase.preparation, adoption_authorized: false, adopted_delivery_digest: null, verification_request_id: null } })
    state.launch_preferences = { host_key: 'codex', model: 'newer-saved-model' }
    const router = await home()
    await button('View setup').trigger('click'); await flushPromises()
    expect(router.currentRoute.value.path).toBe('/projects/1/setup')
    expect(wrapper!.find('[role="dialog"]').exists()).toBe(false)
    expect(button(status === 'failed' ? 'Retry setup' : 'Start again').attributes('disabled')).toBeUndefined()
    expect(writes()).toHaveLength(0)
  })
  it('excludes directoryless projects, including prompts, actions and counts', async () => {
    state = setupFixture({ eligible: false, status: ProjectSetupOutStatus.ineligible, folder_status: ProjectSetupOutFolder_status.directoryless, update_count: 7 })
    await home()
    expect(wrapper!.text()).not.toContain('Set up project')
    expect(wrapper!.find('[aria-label="Project setup reminder"]').exists()).toBe(false)
    expect(wrapper!.find('[aria-label*="workflow updates"]').exists()).toBe(false)
    expect(writes()).toEqual([])
  })

  it('dismisses each reminder permanently through its server owner and retains the quiet action after reload/new versions', async () => {
    const router = await home()
    await button("Don't show again").trigger('click'); await flushPromises()
    expect(callOperation).toHaveBeenCalledWith('projectSetup.setReminders', { project_id: 1, dismiss_setup: true })
    expect(wrapper!.find('[aria-label="Project setup reminder"]').exists()).toBe(false)
    expect(button('Set up project')).toBeDefined()
    state = setupFixture({ ...state, status: ProjectSetupOutStatus.updates_available, update_count: 2 })
    await router.push('/projects/1/setup'); await router.push('/projects/1'); await flushPromises()
    expect(wrapper!.text()).toContain('Workflow updates available')
    await button("Don't show again").trigger('click'); await flushPromises()
    expect(callOperation).toHaveBeenCalledWith('projectSetup.setReminders', { project_id: 1, dismiss_updates: true })
    state = { ...state, update_count: 3 }
    await router.push('/projects/1/setup'); await router.push('/projects/1'); await flushPromises()
    expect(wrapper!.find('[aria-label="Project setup reminder"]').exists()).toBe(false)
    expect(wrapper!.get('[aria-label="3 workflow updates available"]').text()).toContain('3')
    const before = writes().length
    await button('Update workflows · 3').trigger('click'); await flushPromises()
    expect(writes()).toHaveLength(before)
  })

  it('offers detected supported choices and a recommendation, launches once and returns to the retained attempt', async () => {
    const router = await home()
    await button('Set up project').trigger('click'); await flushPromises()
    expect(wrapper!.text()).toContain('catalog-model (Recommended)')
    expect(wrapper!.text()).not.toContain('Desktop app')
    const start = button('Start setup')
    await Promise.all([start.trigger('click'), start.trigger('click')]); await flushPromises()
    expect(vi.mocked(callOperation).mock.calls.filter(([name]) => name === 'projectSetup.start')).toHaveLength(1)
    expect(callOperation).toHaveBeenCalledWith('projectSetup.start', { project_id: 1, entry_kind: 'managed_ui', mode: 'setup', preferences: { host_key: 'codex', model: 'catalog-model', model_reasoning_effort: null, provider: null, profile: null } })
    expect(router.currentRoute.value.path).toBe('/projects/1/setup')
    expect(wrapper!.text()).toContain('Setup is queued')
    await router.push('/projects/1'); await flushPromises()
    await button('View setup').trigger('click'); await flushPromises()
    expect(writes()).toHaveLength(1)
  })

  it('reuses saved update choices and known workflows with optional change', async () => {
    state = setupFixture({ status: ProjectSetupOutStatus.updates_available, update_count: 2, launch_preferences: { host_key: 'codex', model: 'second-model' } })
    await home(); await button('Review updates').trigger('click'); await flushPromises()
    expect(wrapper!.text()).toContain('Codex · Model: second-model · Reasoning: Agent default')
    expect(wrapper!.find('[role="combobox"]').exists()).toBe(false)
    expect(button('Change')).toBeDefined()
    await button('Update workflows').trigger('click'); await flushPromises()
    expect(callOperation).toHaveBeenCalledWith('projectSetup.start', expect.objectContaining({ mode: 'update', preferences: expect.objectContaining({ model: 'second-model' }) }))
  })

  it('offers a default model and sends an explicit clear for the saved host', async () => {
    state = setupFixture({ launch_preferences: { host_key: 'codex', model: 'second-model', provider: 'saved-provider' } })
    await home(); await button('Set up project').trigger('click'); await flushPromises()
    await button('Change').trigger('click'); await flushPromises()
    const modelSelect = wrapper!.findAllComponents({ name: 'UiSelect' })[1]!
    expect(modelSelect.props('options').some((item: { value: string }) => item.value === '')).toBe(true)
    wrapper!.findAllComponents({ name: 'UiSelect' })[0]!.vm.$emit('update:modelValue', 'codex')
    await flushPromises()
    expect(modelSelect.props('modelValue')).toBe('second-model')
    modelSelect.vm.$emit('update:modelValue', '')
    await flushPromises()
    await button('Start setup').trigger('click'); await flushPromises()
    expect(callOperation).toHaveBeenCalledWith('projectSetup.start', expect.objectContaining({ preferences: expect.objectContaining({ model: null, model_reasoning_effort: null }) }))
  })

  it('disables an open launch when its bound folder becomes unavailable', async () => {
    const start = vi.fn()
    wrapper = mount(ProjectSetupLaunchDialog, { props: { state: setupFixture(), pending: false, error: null, start }, global: { stubs: { teleport: true, RouterLink: true } } })
    await flushPromises()
    expect(button('Start setup').attributes('disabled')).toBeUndefined()
    await wrapper.setProps({ state: setupFixture({ folder_status: ProjectSetupOutFolder_status.unavailable }) })
    expect(button('Start setup').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('Restore access')
    expect(start).not.toHaveBeenCalled()
  })

  it('keeps an unavailable saved host and explains the prerequisite without launching', async () => {
    state = setupFixture({ launch_preferences: { host_key: 'missing-host', model: 'saved-model' } })
    await home(); await button('Set up project').trigger('click'); await flushPromises()
    expect(wrapper!.text()).toContain('missing-host · Model: saved-model')
    expect(wrapper!.text()).toContain('selected agent app is unavailable')
    expect(button('Start setup').attributes('disabled')).toBeDefined()
    expect(wrapper!.get('a[href="/projects/1/agents"]').text()).toBe('Open agent setup')
    expect(writes()).toEqual([])
  })

  it('preserves unavailable-folder history and opens repair without a launch dialog', async () => {
    state = setupFixture({ status: ProjectSetupOutStatus.folder_unavailable, folder_status: ProjectSetupOutFolder_status.unavailable, current_attempt: attemptFixture({ status: SetupAttemptStatus.success }) })
    await home()
    expect(wrapper!.text()).toContain('Setup history is retained')
    await button('View setup').trigger('click'); await flushPromises()
    expect(wrapper!.text()).toContain('Project folder unavailable')
    expect(wrapper!.text()).toContain('history and reminder choices are retained')
    expect(wrapper!.find('[role="dialog"]').exists()).toBe(false)
    expect(writes()).toEqual([])
  })

  it('routes question/running states to internal details and surfaces launch errors', async () => {
    const router = await home()
    await button('Set up project').trigger('click'); await flushPromises()
    vi.mocked(callOperation).mockRejectedValueOnce(new ApiError('Selected model needs authentication', 409, { detail: 'Selected model needs authentication', data: { next_action: 'repair_agent_auth' } }, 'request-fixture'))
    await button('Start setup').trigger('click'); await flushPromises()
    expect(wrapper!.get('[role="alert"]').text()).toContain('Selected model needs authentication')
    expect(wrapper!.get('[role="alert"]').text()).not.toContain('repair_agent_auth')
    expect(wrapper!.get('[role="alert"]').text()).not.toContain('request-fixture')
    state = { ...state, current_attempt: attemptFixture({ status: SetupAttemptStatus.needs_input, question: { question_id: 'q1', revision: 1, prompt: 'What do you want to accomplish?', workflow_keys: [], response_channel: SetupQuestionResponse_channel.managed_ui, answer_state: SetupQuestionAnswer_state.pending } }) }
    await router.push('/projects/1/setup'); await router.push('/projects/1'); await flushPromises()
    await button('Answer question').trigger('click'); await flushPromises()
    expect(wrapper!.text()).toContain('What do you want to accomplish?')
    expect(wrapper!.find('a[href*="conversation"]').exists()).toBe(false)
  })

  it('ignores a stale status response after dismissal and after disposal', async () => {
    let finish!: (value: unknown) => void
    let controller!: ReturnType<typeof useProjectSetup>
    wrapper = mount(defineComponent({ setup() { controller = useProjectSetup(ref(1)); return () => null } }))
    await flushPromises()
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const read = controller.refresh()
    await controller.dismiss('setup')
    finish(setupFixture()); await read
    expect(controller.state.value?.setup_reminder_dismissed_at).toBeTruthy()
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const lateRead = controller.refresh()
    wrapper.unmount(); wrapper = undefined
    finish(setupFixture({ update_count: 99 })); await lateRead
    expect(controller.state.value?.update_count).toBe(0)
  })
})
