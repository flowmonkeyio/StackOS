import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ProjectAgents from './agents/ProjectAgents.vue'
import { blankAgent } from './agents/useProjectAgents'
import { callOperation } from '@/lib/operations'
import { UiFormField, UiInput, UiSelect, UiTextarea } from '@/components/ui'
import { createMemoryHistory, createRouter } from 'vue-router'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
let wrapper: VueWrapper | undefined
const row = { project_id: 1, project_dir: '/fixture/project', source_ref: { host_key: 'codex', native_name: 'seo_keyword_research', source_path: '.codex/agents/seo-keyword-research.toml' }, digest: 'a'.repeat(64), registration_digest: null, fields: { ...blankAgent(), name: 'seo_keyword_research', description: 'Find opportunities', developer_instructions: 'Full native instructions' }, capabilities: { edit: true }, inherited_fields: ['model'], unsupported_fields: ['future_native'] }
function setup(items = [row]) {
  vi.mocked(callOperation).mockImplementation(async (operation, args) => {
    if (operation === 'workspace.listBindings') return [{ project_id: 1, last_known_root: '/fixture/project' }]
    if (operation === 'projectAgent.list') return { project_id: 1, project_dir: '/fixture/project', items }
    return items.find(item => item.source_ref.native_name === (args.source_ref as typeof row.source_ref)?.native_name) ?? row
  })
  wrapper = mount(ProjectAgents, { props: { projectId: 1 }, global: { stubs: { teleport: true } } })
  return wrapper
}
afterEach(() => { wrapper?.unmount(); vi.resetAllMocks() })
describe('Agents list and native editor', () => {
  it('lists actual domains, opens full instructions and protects dirty drafts from refresh', async () => {
    const w = setup(); await flushPromises()
    expect(w.text()).toContain('/fixture/project')
    await w.get('.agent-row').trigger('click'); await flushPromises()
    expect(w.get('textarea').element.value).toBe('Find opportunities')
    expect(w.findAll('textarea')[1]!.element.value).toBe('Full native instructions')
    expect(w.text()).toContain('.codex/agents/seo-keyword-research.toml')
    expect(w.text()).toContain('future_native')
    expect(w.findAllComponents(UiSelect)[0]!.props('options')[0]).toEqual({ value: '', label: 'Default (inherited)' })
    await w.get('input[pattern]').setValue('new_name')
    await w.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click')
    expect(w.text()).toContain('Discard unsaved changes?')
    expect(w.get<HTMLInputElement>('input[pattern]').element.value).toBe('new_name')
    await w.findAll('button').find(button => button.text() === 'Cancel')!.trigger('click')
    expect(w.get<HTMLInputElement>('input[pattern]').element.value).toBe('new_name')
    expect(callOperation).not.toHaveBeenCalledWith('projectAgent.update', expect.anything())
  })
  it('searches without hiding the draft and distinguishes empty folder from failed discovery', async () => {
    const w = setup(); await flushPromises()
    await w.get('.agent-row').trigger('click'); await flushPromises()
    await w.get('input[aria-label="Find an agent"]').setValue('unmatched')
    expect(w.text()).toContain('No matching agents')
    expect(w.find('aside').exists()).toBe(true)
    vi.mocked(callOperation).mockRejectedValueOnce(new Error('Folder unreadable'))
    await w.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click'); await flushPromises()
    expect(w.text()).toContain('Folder unreadable')
    expect(w.text()).not.toContain('No project agents yet')
  })
  it('uses Add to edit a file draft and presents inheritance without starting work', async () => {
    const w = setup([]); await flushPromises()
    expect(w.text()).toContain('No project agents yet')
    await w.findAll('button').find(button => button.text() === 'Add agent')!.trigger('click')
    expect(w.get('aside').text()).toContain('Saving starts nothing')
    expect(w.text()).toContain('inherit task, project, then agent app settings')
    expect(vi.mocked(callOperation).mock.calls.map(([name]) => name)).toEqual(['workspace.listBindings', 'projectAgent.list'])
  })
  it('retains a dirty draft when route navigation is cancelled and leaves only after explicit discard', async () => {
    setup(); wrapper!.unmount()
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/agents', component: ProjectAgents, props: { projectId: 1 } }, { path: '/other', component: { template: '<div>Other page</div>' } }] })
    await router.push('/agents'); await router.isReady()
    wrapper = mount({ template: '<router-view />' }, { global: { plugins: [router], stubs: { teleport: true } } })
    await flushPromises()
    await wrapper.get('.agent-row').trigger('click'); await flushPromises()
    await wrapper.get('input[pattern]').setValue('changed')
    const cancelled = router.push('/other'); await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === 'Cancel')!.trigger('click')
    await cancelled
    expect(router.currentRoute.value.path).toBe('/agents')
    expect(wrapper.get<HTMLInputElement>('input[pattern]').element.value).toBe('changed')
    const leaving = router.push('/other'); await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === 'Discard changes')!.trigger('click')
    await leaving
    expect(router.currentRoute.value.path).toBe('/other')
  })
  it('saves full instructions, a custom native model and explicit reasoning effort through the editor', async () => {
    const w = setup(); await flushPromises()
    await w.get('.agent-row').trigger('click'); await flushPromises()
    const field = (label: string) => w.findAllComponents(UiFormField).find(item => item.props('label') === label)!
    field('Model').getComponent(UiSelect).vm.$emit('update:modelValue', '__custom__'); await flushPromises()
    field('Custom model').getComponent(UiInput).vm.$emit('update:modelValue', 'native-custom-model')
    field('Reasoning effort').getComponent(UiSelect).vm.$emit('update:modelValue', 'high')
    field('Instructions').getComponent(UiTextarea).vm.$emit('update:modelValue', 'Revised full instructions\nKeep every paragraph.')
    await flushPromises()
    const patch = { model: 'native-custom-model', model_reasoning_effort: 'high', developer_instructions: 'Revised full instructions\nKeep every paragraph.' }
    vi.mocked(callOperation).mockResolvedValueOnce({ project_id: 1, data: { ...row, fields: { ...row.fields, ...patch } } })
    await w.get('form').trigger('submit'); await flushPromises()
    expect(callOperation).toHaveBeenCalledWith('projectAgent.update', expect.objectContaining({ patch }))
    expect(w.text()).toContain('Saved in the project file.')
    expect(field('Instructions').getComponent(UiTextarea).props('modelValue')).toBe(patch.developer_instructions)
  })
  it('retains raw option drafts and custom model choice on failed save, then resets them for a new definition', async () => {
    const other = { ...row, source_ref: { ...row.source_ref, native_name: 'finance_receipts', source_path: '.codex/agents/finance-receipts.toml' }, fields: { ...row.fields, name: 'finance_receipts', features: { apps: false } } }
    const w = setup([row, other]); await flushPromises()
    await w.get('.agent-row').trigger('click'); await flushPromises()
    const field = (label: string) => w.findAllComponents(UiFormField).find(item => item.props('label') === label)!
    const raw = '{ "shell_tool": false }'
    field('Feature restrictions').getComponent(UiTextarea).vm.$emit('update:modelValue', raw)
    field('Model').getComponent(UiSelect).vm.$emit('update:modelValue', '__custom__'); await flushPromises()
    field('Custom model').getComponent(UiInput).vm.$emit('update:modelValue', 'custom-model')
    field('Instructions').getComponent(UiTextarea).vm.$emit('update:modelValue', 'Retained instructions')
    await flushPromises()
    vi.mocked(callOperation).mockRejectedValueOnce(new Error('External conflict'))
    await w.get('form').trigger('submit'); await flushPromises()
    expect(field('Feature restrictions').getComponent(UiTextarea).props('modelValue')).toBe(raw)
    expect(field('Instructions').getComponent(UiTextarea).props('modelValue')).toBe('Retained instructions')
    expect(field('Custom model').exists()).toBe(true)
    await w.findAll('.agent-row')[1]!.trigger('click')
    await w.findAll('button').find(button => button.text() === 'Discard changes')!.trigger('click'); await flushPromises()
    expect(w.findAllComponents(UiFormField).some(item => item.props('label') === 'Custom model')).toBe(false)
    expect(JSON.parse(String(field('Feature restrictions').getComponent(UiTextarea).props('modelValue')))).toEqual({ apps: false })
  })
  it('protects a dirty folder switch and loads the chosen folder explicitly after discard', async () => {
    const w = setup(); await flushPromises()
    vi.mocked(callOperation).mockImplementation(async (operation, args) => operation === 'workspace.listBindings'
      ? [{ project_id: 1, last_known_root: '/first' }, { project_id: 1, last_known_root: '/second' }]
      : operation === 'projectAgent.list' ? { project_id: 1, project_dir: args.project_dir, items: [row] }
        : { ...row, project_dir: args.project_dir })
    // Re-enter through a new project instance to discover both explicit roots.
    w.unmount()
    wrapper = mount(ProjectAgents, { props: { projectId: 1 }, global: { stubs: { teleport: true } } })
    await flushPromises()
    const folder = wrapper.findAllComponents(UiFormField).find(item => item.props('label') === 'Project folder')!.getComponent(UiSelect)
    folder.vm.$emit('update:modelValue', '/first'); await flushPromises()
    await wrapper.get('.agent-row').trigger('click'); await flushPromises()
    await wrapper.get('input[pattern]').setValue('unsaved_name')
    folder.vm.$emit('update:modelValue', '/second'); await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === 'Cancel')!.trigger('click')
    expect(wrapper.get<HTMLInputElement>('input[pattern]').element.value).toBe('unsaved_name')
    folder.vm.$emit('update:modelValue', '/second'); await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === 'Discard changes')!.trigger('click'); await flushPromises()
    expect(wrapper.find('[aria-label="Agent editor"]').exists()).toBe(false)
    expect(callOperation).toHaveBeenLastCalledWith('projectAgent.list', expect.objectContaining({ project_dir: '/second' }))
  })
  it('loads a changed project before rendering and ignores its predecessor’s late folder lookup', async () => {
    let resolveOld!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementation(async (operation, args) => operation === 'workspace.listBindings'
      ? args.project_id === 1 ? new Promise(resolve => { resolveOld = resolve }) : [{ project_id: 2, last_known_root: '/second' }]
      : { project_id: 2, project_dir: '/second', items: [] })
    wrapper = mount(ProjectAgents, { props: { projectId: 1 } })
    await wrapper.setProps({ projectId: 2 }); await flushPromises()
    resolveOld([{ project_id: 1, last_known_root: '/first' }]); await flushPromises()
    expect(wrapper.text()).toContain('/second')
    expect(wrapper.text()).not.toContain('/first')
    expect(callOperation).toHaveBeenCalledWith('projectAgent.list', expect.objectContaining({ project_id: 2, project_dir: '/second' }))
    expect(callOperation).not.toHaveBeenCalledWith('projectAgent.list', expect.objectContaining({ project_id: 2, project_dir: '/first' }))
  })
  it('does not start a source read when a folder lookup finishes after unmount', async () => {
    let resolve!: (value: unknown) => void
    vi.mocked(callOperation).mockReturnValueOnce(new Promise(done => { resolve = done }))
    wrapper = mount(ProjectAgents, { props: { projectId: 1 } })
    wrapper.unmount(); wrapper = undefined
    resolve([{ project_id: 1, last_known_root: '/fixture/project' }]); await flushPromises()
    expect(callOperation).toHaveBeenCalledTimes(1)
  })
})
