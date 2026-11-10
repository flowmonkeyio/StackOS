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
  vi.mocked(callOperation).mockImplementation(async operation => {
    if (operation === 'workspace.listBindings') return [{ project_id: 1, last_known_root: '/fixture/project' }]
    if (operation === 'projectAgent.list') return { project_id: 1, project_dir: '/fixture/project', items }
    return row
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
})
