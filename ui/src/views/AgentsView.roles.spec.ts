import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { UiFormField, UiSelect, UiTextarea } from '@/components/ui'
import { ApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import AgentsView from './AgentsView.vue'
import ProjectRoleSettings from './agents/ProjectRoleSettings.vue'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
let wrapper: VueWrapper | undefined
afterEach(() => { wrapper?.unmount(); vi.resetAllMocks() })
const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const delivery = {
  host_key: 'claude-code',
  agents: {
    delivery_agent: { host_key: 'claude-code', name: 'writer', instructions: 'Build fixture' },
    review_agent: { host_key: 'claude-code', name: 'reviewer', instructions: 'Review fixture' },
  },
  main_agent: { host_key: 'codex', model: 'gpt-6.1-sol' },
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (cause: unknown) => void
  return { promise: new Promise<T>((done, fail) => { resolve = done; reject = fail }), resolve, reject }
}
type Folder = { id: number; project_dir: string; project_folder_status: string }
async function setup() {
  let folderRead: (() => Promise<Folder>) | undefined
  let saveResult: Promise<unknown> | undefined
  const folder = (id = 1): Folder => ({ id, project_dir: `/fixture/project-${id}`, project_folder_status: 'ready' })
  vi.mocked(callOperation).mockImplementation(async (name, args) => {
    const id = Number(args.project_id)
    if (name === 'project.get') {
      const next = folderRead; folderRead = undefined
      return next ? next() : folder(id)
    }
    if (name === 'projectAgent.list') return { data: { project_id: id, project_dir: args.project_dir, items: [] } }
    if (name === 'hostMcp.status') return { items: ['codex', 'claude-code', 'grok'].map(host_key => ({ host_key, display_name: host_key, available: true, agent_execution: { runnable: 'available', sandboxes: ['workspace-write'], models: ['gpt-6.1-sol', 'gpt-6-astra', 'grok-4.7'], reasoning_efforts: {} } })) }
    if (name === 'tracker.get') return { tracker: { project_id: id, rev: 7, config_json: { delivery: copy(delivery) } } }
    if (name === 'tracker.configureDelivery') {
      if (!args.preview && saveResult) return saveResult
      const role = (args.role_patch as { role: string }).role
      return { project_id: id, data: { role_settings: { role, project_dir: args.project_dir, effective: { host_key: 'claude-code' } } } }
    }
    throw new Error(`Unexpected operation ${name}`)
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/projects/:id/agents', component: AgentsView }] })
  await router.push('/projects/1/agents'); await router.isReady()
  wrapper = mount({ template: '<RouterView />' }, { global: { plugins: [router], stubs: {
    teleport: true,
    // Presentation only: keep the real route, folder owner and role component.
    ProjectPageHeader: { template: '<header><h1>Agents</h1><slot name="actions" /></header>' },
  } } })
  await flushPromises()
  const button = (text: string) => wrapper!.findAll('button').find(item => item.text() === text)!
  await button('Configure roles').trigger('click'); await flushPromises()
  const field = (label: string) => wrapper!.findAllComponents(UiFormField).find(item => item.props('label') === label)!
  const selectRole = async (value: string) => { field('Role').getComponent(UiSelect).vm.$emit('update:modelValue', value); await flushPromises() }
  const instructions = () => field('Role instructions').getComponent(UiTextarea)
  await selectRole('review_agent')
  return { router, folder, button, field, selectRole, instructions,
    nextFolder: (next: Promise<Folder>) => { folderRead = () => next },
    nextSave: (next: Promise<unknown>) => { saveResult = next },
  }
}

describe('Agents route role refresh lifetime', () => {
  it.each(['success', 'failure', 'denied'] as const)('retains role and host drafts through a %s folder refresh', async outcome => {
    const ui = await setup()
    await ui.instructions().setValue('Review draft survives refresh')
    await ui.selectRole('delivery_agent')
    await ui.instructions().setValue('Build draft survives refresh')
    ui.field('Agent app').getComponent(UiSelect).vm.$emit('update:modelValue', 'grok'); await flushPromises()
    await ui.instructions().setValue('Grok draft survives refresh')
    ui.field('Agent app').getComponent(UiSelect).vm.$emit('update:modelValue', 'claude-code'); await flushPromises()
    await ui.selectRole('main_agent')
    ui.field('Model fallback').getComponent(UiSelect).vm.$emit('update:modelValue', 'gpt-6-astra'); await flushPromises()
    await ui.selectRole('review_agent')
    const instance = wrapper!.getComponent(ProjectRoleSettings).element
    const form = wrapper!.get('form[aria-label="Role settings"]').element
    const pending = deferred<Folder>(); ui.nextFolder(pending.promise)
    await ui.button('Refresh').trigger('click'); await flushPromises()
    expect(wrapper!.find('form[aria-label="Role settings"]').exists()).toBe(true)
    expect(wrapper!.getComponent(ProjectRoleSettings).element).toBe(instance)
    expect(wrapper!.get('form[aria-label="Role settings"]').element).toBe(form)
    expect(ui.button('Save role').attributes('disabled')).toBeDefined()
    expect(ui.instructions().props('modelValue')).toBe('Review draft survives refresh')
    if (outcome === 'success') pending.resolve(ui.folder())
    else pending.reject(outcome === 'denied' ? new ApiError('Denied folder', 403, null) : new Error('Folder offline'))
    await flushPromises()
    expect(ui.instructions().props('modelValue')).toBe('Review draft survives refresh')
    if (outcome !== 'success') {
      expect(ui.button('Save role').attributes('disabled')).toBeDefined()
      expect(wrapper!.text()).not.toContain('Effective saved:')
      await ui.button(outcome === 'denied' ? 'Retry project folder' : 'Refresh').trigger('click'); await flushPromises()
    }
    expect(wrapper!.getComponent(ProjectRoleSettings).element).toBe(instance)
    await ui.selectRole('main_agent')
    expect(ui.field('Model fallback').getComponent(UiSelect).props('modelValue')).toBe('gpt-6-astra')
    await ui.selectRole('delivery_agent')
    expect(ui.instructions().props('modelValue')).toBe('Build draft survives refresh')
    ui.field('Agent app').getComponent(UiSelect).vm.$emit('update:modelValue', 'grok'); await flushPromises()
    expect(ui.instructions().props('modelValue')).toBe('Grok draft survives refresh')
    expect(callOperation).not.toHaveBeenCalledWith('tracker.configureDelivery', expect.not.objectContaining({ preview: true }))
  })

  it('retains drafts but rejects writes after the selected folder changes', async () => {
    const ui = await setup()
    await ui.instructions().setValue('Draft for original folder')
    ui.nextFolder(Promise.resolve({ ...ui.folder(), project_dir: '/fixture/replacement' }))
    await ui.button('Refresh').trigger('click'); await flushPromises()
    expect(ui.instructions().props('modelValue')).toBe('Draft for original folder')
    expect(wrapper!.text()).toContain('The project folder changed.')
    expect(wrapper!.text()).not.toContain('Effective saved:')
    expect(ui.button('Save role').attributes('disabled')).toBeDefined()
    await wrapper!.get('form[aria-label="Role settings"]').trigger('submit'); await flushPromises()
    expect(vi.mocked(callOperation).mock.calls.filter(([name, args]) => name === 'tracker.configureDelivery' && !args.preview)).toHaveLength(0)
  })

  it('requires explicit discard on project changes and ignores an old folder response', async () => {
    const ui = await setup()
    await ui.instructions().setValue('Draft belongs to project one')
    const pending = deferred<Folder>(); ui.nextFolder(pending.promise)
    await ui.button('Refresh').trigger('click'); await flushPromises()
    const cancelled = ui.router.push('/projects/2/agents'); await flushPromises()
    expect(wrapper!.get('[role="dialog"]').text()).toContain('Discard role changes?')
    await ui.button('Cancel').trigger('click'); await cancelled
    expect(ui.router.currentRoute.value.params.id).toBe('1')
    expect(ui.instructions().props('modelValue')).toBe('Draft belongs to project one')
    const navigation = ui.router.push('/projects/2/agents'); await flushPromises()
    await ui.button('Discard changes').trigger('click'); await navigation; await flushPromises()
    pending.resolve(ui.folder()); await flushPromises()
    await ui.button('Configure roles').trigger('click'); await flushPromises()
    await ui.selectRole('review_agent')
    expect(ui.instructions().props('modelValue')).toBe('Review fixture')
    const previews = vi.mocked(callOperation).mock.calls.filter(([name]) => name === 'tracker.configureDelivery')
    expect(previews.at(-1)?.[1]).toEqual(expect.objectContaining({ project_id: 2, project_dir: '/fixture/project-2' }))
  })

  it('prevents header refresh from destroying an in-flight role save', async () => {
    const ui = await setup()
    await ui.instructions().setValue('Saved review draft')
    const pending = deferred<unknown>(); ui.nextSave(pending.promise)
    const instance = wrapper!.getComponent(ProjectRoleSettings).element
    const reads = vi.mocked(callOperation).mock.calls.filter(([name]) => name === 'project.get').length
    await wrapper!.get('form[aria-label="Role settings"]').trigger('submit'); await flushPromises()
    expect(ui.button('Refresh').attributes('disabled')).toBeDefined()
    await ui.button('Refresh').trigger('click'); await flushPromises()
    expect(vi.mocked(callOperation).mock.calls.filter(([name]) => name === 'project.get')).toHaveLength(reads)
    expect(wrapper!.getComponent(ProjectRoleSettings).element).toBe(instance)
    const saved = copy(delivery); saved.agents.review_agent.instructions = 'Saved review draft'
    pending.resolve({ project_id: 1, data: { project_id: 1, rev: 8, config_json: { delivery: saved }, role_settings: { role: 'review_agent', project_dir: ui.folder().project_dir, effective: { host_key: 'claude-code' } } } })
    await flushPromises()
    expect(ui.instructions().props('modelValue')).toBe('Saved review draft')
    expect(wrapper!.text()).toContain('Saved for fresh invocations')
    expect(ui.button('Refresh').attributes('disabled')).toBeUndefined()
  })
})
