import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ProjectAgents from './agents/ProjectAgents.vue'
import { blankAgent } from './agents/useProjectAgents'
import { callOperation } from '@/lib/operations'
import { ApiError } from '@/lib/client'
import { UiFormField, UiInput, UiSelect, UiTextarea } from '@/components/ui'
import { createMemoryHistory, createRouter } from 'vue-router'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
let wrapper: VueWrapper | undefined
const row = { project_id: 1, project_dir: '/fixture/project', source_ref: { host_key: 'codex', native_name: 'seo_keyword_research', source_path: '.codex/agents/seo-keyword-research.toml' }, digest: 'a'.repeat(64), registration_digest: null, fields: { ...blankAgent(), name: 'seo_keyword_research', description: 'Find opportunities', developer_instructions: 'Full native instructions' }, capabilities: { edit: true }, inherited_fields: ['model'], unsupported_fields: ['future_native'] }
const catalog = { ok: true, items: [
  { host_key: 'codex', agent_execution: { models: ['catalog-model', 'second-model', 'catalog-model'], reasoning_efforts: { 'catalog-model': ['low', 'high', 'max'], 'second-model': ['low', 'high'] } } },
  { host_key: 'claude', agent_execution: { models: ['other-host-model'] } },
] }
function setup(items = [row]) {
  vi.mocked(callOperation).mockImplementation(async (operation, args) => {
    if (operation === 'project.get') return { id: 1, primary_workspace_binding_id: 17, project_dir: '/fixture/project' }
    if (operation === 'projectAgent.list') return { data: { project_id: 1, project_dir: '/fixture/project', items } }
    if (operation === 'hostMcp.status') return catalog
    return items.find(item => item.source_ref.native_name === (args.source_ref as typeof row.source_ref)?.native_name) ?? row
  })
  wrapper = mount(ProjectAgents, { props: { projectId: 1 }, global: { stubs: { teleport: true } } })
  return wrapper
}
afterEach(() => { wrapper?.unmount(); vi.resetAllMocks() })
describe('Agents list and native editor', () => {
  it.each([null, 'native-custom-model'])('directly selects native effort with model %s and saves/reopens without pinning a model', async model => {
    const saved = { ...row, native_reasoning_efforts: ['minimal', 'high'], fields: { ...row.fields, model } }
    const w = setup([saved]); await flushPromises(); await w.get('.agent-row').trigger('click'); await flushPromises()
    const region = w.get('.agent-editor').element
    const field = (label: string) => w.findAllComponents(UiFormField).find(item => item.props('label') === label)!
    expect(callOperation).not.toHaveBeenCalledWith('projectAgent.update', expect.anything())
    await field('Reasoning effort').get('[role="combobox"]').trigger('click')
    const high = field('Reasoning effort').findAll('[role="option"]').find(option => option.text() === 'high')
    expect(high).toBeDefined()
    await high!.trigger('click')
    const readback = { ...saved, digest: 'b'.repeat(64), fields: { ...saved.fields, model_reasoning_effort: 'high' } }
    vi.mocked(callOperation).mockResolvedValueOnce({ project_id: 1, data: readback })
    await w.get('form').trigger('submit'); await flushPromises()
    expect(callOperation).toHaveBeenCalledWith('projectAgent.update', expect.objectContaining({ patch: { model_reasoning_effort: 'high' } }))
    expect(field('Model').getComponent(UiSelect).props('modelValue')).toBe(model ?? '')
    expect(w.get('.agent-editor').element).toBe(region)
    vi.mocked(callOperation).mockResolvedValueOnce(readback)
    await w.get('.agent-row').trigger('click'); await flushPromises()
    expect(field('Reasoning effort').getComponent(UiSelect).props('modelValue')).toBe('high')
    expect(field('Model').getComponent(UiSelect).props('modelValue')).toBe(model ?? '')
    expect(w.text()).toContain('No changes')
  })
  it('reserves one editor slot for the first deferred read and keeps it through completion', async () => {
    const w = setup(); await flushPromises()
    let resolve!: (value: unknown) => void
    vi.mocked(callOperation).mockReturnValueOnce(new Promise(done => { resolve = done }))
    await w.get('.agent-row').trigger('click')
    const region = w.get('.agent-editor').element
    expect(w.get('.agents-layout').classes()).toContain('agents-layout--editing')
    expect(w.get('.agents-layout').element.children).toHaveLength(2)
    expect(w.get('.agent-editor').attributes('aria-busy')).toBe('true')
    expect(w.get('.agent-editor').text()).toContain('Reading full project instructions')
    const focus = vi.spyOn(HTMLElement.prototype, 'focus')
    resolve(row); await flushPromises()
    expect(w.get('.agent-editor').element).toBe(region)
    expect(w.get('.agents-layout').element.children).toHaveLength(2)
    expect(w.get('.agent-editor').attributes('aria-busy')).toBe('false')
    expect(focus).toHaveBeenCalledWith({ preventScroll: true })
    focus.mockRestore()
    expect(callOperation).not.toHaveBeenCalledWith('projectAgent.update', expect.anything())
  })
  it.each(['failure', 'stale'] as const)('keeps the last source and save target honest after a deferred %s read', async outcome => {
    const second = { ...row, source_ref: { ...row.source_ref, native_name: 'delivery', source_path: '.codex/agents/delivery.toml' }, fields: { ...row.fields, name: 'delivery' } }
    const w = setup([row, second]); await flushPromises(); await w.get('.agent-row').trigger('click'); await flushPromises()
    let resolve!: (value: unknown) => void
    let reject!: (cause: unknown) => void
    vi.mocked(callOperation).mockReturnValueOnce(new Promise((done, fail) => { resolve = done; reject = fail }))
    await w.findAll('.agent-row')[1]!.trigger('click')
    const region = w.get('.agent-editor').element
    if (outcome === 'failure') reject(new Error('Read unavailable')); else resolve(row)
    await flushPromises()
    expect(w.get('.agent-editor').element).toBe(region)
    expect(w.get('.agents-layout').element.children).toHaveLength(2)
    expect(w.get('h2').text()).toBe('SEO keyword research')
    expect(w.text()).toContain('Last loaded agent: SEO keyword research')
    await w.get('textarea').setValue('Edit last loaded source')
    vi.mocked(callOperation).mockResolvedValueOnce({ project_id: 1, data: { ...row, fields: { ...row.fields, description: 'Edit last loaded source' } } })
    await w.get('form').trigger('submit'); await flushPromises()
    expect(callOperation).toHaveBeenCalledWith('projectAgent.update', expect.objectContaining({ source_ref: row.source_ref, patch: { description: 'Edit last loaded source' } }))
  })
  it.each(['high', 'retired-effort'])('retains saved effort %s through catalog completion, refresh and close without a write', async effort => {
    const saved = { ...row, fields: { ...row.fields, model: null, model_reasoning_effort: effort } }
    const w = setup([saved]); await flushPromises(); await w.get('.agent-row').trigger('click'); await flushPromises()
    const effortSelect = () => w.findAllComponents(UiFormField).find(item => item.props('label') === 'Reasoning effort')!.getComponent(UiSelect)
    expect(effortSelect().props('modelValue')).toBe(effort)
    expect(effortSelect().props('options')).toContainEqual({ value: effort, label: `${effort} (not verified for this model)` })
    await w.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click'); await flushPromises()
    expect(effortSelect().props('modelValue')).toBe(effort)
    expect(w.text()).toContain('No changes')
    await w.get('[aria-label="Close agent editor"]').trigger('click'); await flushPromises()
    expect(callOperation).not.toHaveBeenCalledWith('projectAgent.update', expect.anything())
  })
  it.each(['failure', 'stale'] as const)('attributes a rejected retained-source save after a %s switch to the save source', async outcome => {
    const second = { ...row, source_ref: { ...row.source_ref, native_name: 'delivery', source_path: '.codex/agents/delivery.toml' }, fields: { ...row.fields, name: 'delivery' } }
    const w = setup([row, second]); await flushPromises(); await w.get('.agent-row').trigger('click'); await flushPromises()
    if (outcome === 'failure') vi.mocked(callOperation).mockRejectedValueOnce(new Error('Delivery read failed'))
    else vi.mocked(callOperation).mockResolvedValueOnce(row)
    await w.findAll('.agent-row')[1]!.trigger('click'); await flushPromises()
    expect(w.get('[role="alert"]').text()).toContain('Agent could not be opened')
    expect(w.get('[role="alert"]').text()).toContain('Delivery:')
    await w.get('textarea').setValue('Unsaved retained description')
    vi.mocked(callOperation).mockRejectedValueOnce(new ApiError('Source changed', 409, { detail: 'Source changed', data: { code: 'stale_source', repair: 'Refresh the definition and reconcile the draft before saving.' } }))
    await w.get('form').trigger('submit'); await flushPromises()
    const alert = w.get('[role="alert"]').text()
    expect(alert).toContain('Agent could not be saved')
    expect(alert).toContain('SEO keyword research:')
    expect(alert).toContain('stale_source')
    expect(alert).not.toContain('Delivery:')
    expect(alert).not.toContain('could not be opened')
    expect(w.get('textarea').element.value).toBe('Unsaved retained description')
    expect(w.get('h2').text()).toBe('SEO keyword research')
    expect(w.get('.agents-layout').element.children).toHaveLength(2)
    expect(callOperation).toHaveBeenCalledWith('projectAgent.update', expect.objectContaining({ source_ref: row.source_ref, expected_digest: row.digest, patch: { description: 'Unsaved retained description' } }))
  })
  it('retains the last identity inside the busy slot and ignores a read completed after close', async () => {
    const second = { ...row, source_ref: { ...row.source_ref, native_name: 'delivery', source_path: '.codex/agents/delivery.toml' }, fields: { ...row.fields, name: 'delivery' } }
    const w = setup([row, second]); await flushPromises()
    await w.get('.agent-row').trigger('click'); await flushPromises()
    const region = w.get('.agent-editor').element
    let resolve!: (value: unknown) => void
    vi.mocked(callOperation).mockReturnValueOnce(new Promise(done => { resolve = done }))
    await w.findAll('.agent-row')[1]!.trigger('click')
    expect(w.get('.agents-layout').element.children).toHaveLength(2)
    expect(w.get('.agent-editor').element).toBe(region)
    expect(w.get('.agent-editor').text()).toContain('Opening Delivery')
    expect(w.get('h2').text()).toBe('SEO keyword research')
    expect(w.get('fieldset').attributes('disabled')).toBeDefined()
    await w.get('[aria-label="Close agent editor"]').trigger('click')
    const focus = vi.spyOn(HTMLElement.prototype, 'focus')
    resolve(second); await flushPromises()
    expect(w.find('.agent-editor').exists()).toBe(false)
    expect(focus).not.toHaveBeenCalled()
    focus.mockRestore()
    expect(callOperation).not.toHaveBeenCalledWith('projectAgent.update', expect.anything())
  })
  it('uses capability efforts and retains compatible or unchanged choices on deliberate model changes', async () => {
    const saved = { ...row, fields: { ...row.fields, model: 'catalog-model', model_reasoning_effort: 'high' } }
    const w = setup([saved]); await flushPromises(); await w.get('.agent-row').trigger('click'); await flushPromises()
    const field = (label: string) => w.findAllComponents(UiFormField).find(item => item.props('label') === label)!.getComponent(UiSelect)
    expect(field('Reasoning effort').props('options').map(option => typeof option === 'object' ? option.value : option)).toEqual(['', 'low', 'high', 'max'])
    field('Model').vm.$emit('update:modelValue', 'second-model'); await flushPromises()
    expect(field('Reasoning effort').props('modelValue')).toBe('high')
    field('Reasoning effort').vm.$emit('update:modelValue', 'low'); await flushPromises()
    field('Model').vm.$emit('update:modelValue', 'second-model'); await flushPromises()
    expect(field('Reasoning effort').props('modelValue')).toBe('low')
    field('Reasoning effort').vm.$emit('update:modelValue', 'max'); await flushPromises()
    field('Model').vm.$emit('update:modelValue', 'catalog-model'); await flushPromises()
    expect(field('Reasoning effort').props('modelValue')).toBe('max')
    field('Model').vm.$emit('update:modelValue', 'second-model'); await flushPromises()
    expect(field('Reasoning effort').props('modelValue')).toBe('')
  })
  it('shows a failed editor read and recovers without replacing the accepted list', async () => {
    const w = setup(); await flushPromises()
    const list = w.get('.agent-list').element
    vi.mocked(callOperation).mockRejectedValueOnce(new ApiError('Read unavailable', 503, { detail: 'Read unavailable' }))
    await w.get('.agent-row').trigger('click'); await flushPromises()
    expect(w.get('.agent-list').element).toBe(list)
    expect(w.get('[aria-label="Agent editor"]').text()).toContain('Read unavailable')
    expect(w.findAll('[role="alert"]').some(node => node.text().includes('Read unavailable'))).toBe(true)
    await w.get('.agent-row').trigger('click'); await flushPromises()
    expect(w.find('[aria-label="Agent editor"]').exists()).toBe(true)
    expect(w.text()).not.toContain('Read unavailable')
  })
  it('shows stale-source save repair while retaining the draft and reconciles through a fresh read', async () => {
    const w = setup(); await flushPromises(); await w.get('.agent-row').trigger('click'); await flushPromises()
    await w.get('textarea').setValue('Unsaved description')
    vi.mocked(callOperation).mockRejectedValueOnce(new ApiError('Source changed', 409, { detail: 'Source changed', data: { code: 'stale_source', repair: 'Refresh the definition and reconcile the draft before saving.' } }))
    await w.get('form').trigger('submit'); await flushPromises()
    expect(w.get('textarea').element.value).toBe('Unsaved description')
    expect(w.findAll('[role="alert"]').some(node => node.text().includes('stale_source') && node.text().includes('reconcile the draft'))).toBe(true)
    expect(w.text()).not.toContain('Saved in the project file.')
    const fresh = { ...row, digest: 'b'.repeat(64) }
    vi.mocked(callOperation).mockResolvedValueOnce(fresh)
    await w.get('.agent-row').trigger('click')
    await w.findAll('button').find(button => button.text() === 'Discard changes')!.trigger('click'); await flushPromises()
    await w.get('textarea').setValue('Reconciled description')
    vi.mocked(callOperation).mockResolvedValueOnce({ project_id: 1, data: { ...fresh, fields: { ...fresh.fields, description: 'Reconciled description' } } })
    await w.get('form').trigger('submit'); await flushPromises()
    expect(callOperation).toHaveBeenCalledWith('projectAgent.update', expect.objectContaining({ expected_digest: fresh.digest, patch: { description: 'Reconciled description' } }))
    expect(w.text()).not.toContain('stale_source')
    expect(w.text()).toContain('Saved in the project file.')
  })
  it.each(['cancel', 'confirm'] as const)('uses discard wording for navigation after %s of a guarded refresh', async refreshAction => {
    setup(); wrapper!.unmount()
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/agents/:id', component: ProjectAgents, props: route => ({ projectId: Number(route.params.id) }) }, { path: '/other', component: { template: '<div>Other page</div>' } }] })
    await router.push('/agents/1'); await router.isReady()
    wrapper = mount({ template: '<router-view />' }, { global: { plugins: [router], stubs: { teleport: true } } }); await flushPromises()
    await wrapper.get('.agent-row').trigger('click'); await flushPromises()
    await wrapper.get('input[pattern]').setValue('unsaved_name')
    await wrapper.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click')
    await wrapper.findAll('button').find(button => button.text() === (refreshAction === 'cancel' ? 'Cancel' : 'Refresh sources'))!.trigger('click'); await flushPromises()
    expect(wrapper.get<HTMLInputElement>('input[pattern]').element.value).toBe('unsaved_name')
    // Both route-update and route-leave must replace the current action's prompt.
    const update = router.push('/agents/2'); await flushPromises()
    expect(wrapper.get('[role="dialog"]').text()).toContain('Discard unsaved changes?')
    expect(wrapper.get('[role="dialog"]').text()).not.toContain('Refresh sources')
    await wrapper.findAll('button').find(button => button.text() === 'Cancel')!.trigger('click'); await update
    expect(router.currentRoute.value.path).toBe('/agents/1')
    expect(wrapper.get<HTMLInputElement>('input[pattern]').element.value).toBe('unsaved_name')
    const leave = router.push('/other'); await flushPromises()
    expect(wrapper.get('[role="dialog"]').text()).toContain('Discard unsaved changes?')
    await wrapper.findAll('button').find(button => button.text() === 'Discard changes')!.trigger('click'); await leave
    expect(router.currentRoute.value.path).toBe('/other')
  })
  it('keeps the accepted empty presentation mounted when a list refresh fails', async () => {
    const w = setup([]); await flushPromises()
    const empty = w.findAll('p').find(node => node.text().includes('No project agents yet'))!.element
    const basics = vi.mocked(callOperation).getMockImplementation()!
    vi.mocked(callOperation).mockImplementation((name, args) => name === 'projectAgent.list'
      ? Promise.reject(new Error('Temporary list failure')) : basics(name, args))
    await w.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click'); await flushPromises()
    expect(w.findAll('p').find(node => node.text().includes('No project agents yet'))?.element).toBe(empty)
    expect(w.text()).toContain('Temporary list failure')
  })
  it('retains the editor, draft and list through guarded same-folder refresh and transient failure', async () => {
    const w = setup(); await flushPromises()
    await w.get('.agent-row').trigger('click'); await flushPromises()
    const editor = w.get('aside').element, list = w.get('.agent-list').element
    await w.get('input[pattern]').setValue('unsaved_name')
    const basics = vi.mocked(callOperation).getMockImplementation()!
    let rejectFolder!: (cause: unknown) => void
    vi.mocked(callOperation).mockImplementation((name, args) => name === 'project.get'
      ? new Promise((_resolve, reject) => { rejectFolder = reject }) : basics(name, args))
    await w.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click')
    await w.findAll('button').find(button => button.text() === 'Refresh sources')!.trigger('click')
    expect(w.get('aside').element).toBe(editor)
    expect(w.get('.agent-list').element).toBe(list)
    expect(w.get<HTMLInputElement>('input[pattern]').element.value).toBe('unsaved_name')
    expect(w.findAll('button').find(button => button.text() === 'Save changes')!.attributes('disabled')).toBeDefined()
    rejectFolder(new Error('Offline')); await flushPromises()
    expect(w.get('aside').element).toBe(editor)
    expect(w.text()).toContain('Offline')
    expect(w.get<HTMLInputElement>('input[pattern]').element.value).toBe('unsaved_name')
    vi.mocked(callOperation).mockImplementation(basics)
    await w.findAll('button').find(button => button.text() === 'Retry project folder')!.trigger('click')
    await w.findAll('button').find(button => button.text() === 'Refresh sources')!.trigger('click'); await flushPromises()
    expect(w.get('aside').element).toBe(editor)
    expect(w.get('.agent-list').element).toBe(list)
    expect(w.get<HTMLInputElement>('input[pattern]').element.value).toBe('unsaved_name')
    expect(w.findAll('button').find(button => button.text() === 'Save changes')!.attributes('disabled')).toBeUndefined()
  })
  it('clears the old draft when current folder validation confirms a different folder or denial', async () => {
    const w = setup(); await flushPromises()
    await w.get('.agent-row').trigger('click'); await flushPromises()
    const basics = vi.mocked(callOperation).getMockImplementation()!
    let resolveList!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementation((name, args) => name === 'project.get'
      ? Promise.resolve({ id: 1, project_dir: '/second' }) : name === 'projectAgent.list'
        ? new Promise(resolve => { resolveList = resolve }) : basics(name, args))
    await w.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click'); await flushPromises()
    expect(w.find('aside').exists()).toBe(false)
    expect(w.find('.agent-row').exists()).toBe(false)
    resolveList({ data: { project_id: 1, project_dir: '/fixture/project', items: [row] } }); await flushPromises()
    expect(w.find('aside').exists()).toBe(false)
    expect(w.find('.agent-row').exists()).toBe(false)
    expect(w.text()).toContain('different project folder')
  })
  it('rejects a wrong-project folder response without exposing or editing its agents', async () => {
    vi.mocked(callOperation).mockResolvedValue({ id: 2, project_dir: '/other-project' })
    wrapper = mount(ProjectAgents, { props: { projectId: 1 } }); await flushPromises()
    expect(wrapper.text()).toContain('Project folder belongs to a different project')
    expect(wrapper.text()).not.toContain('/other-project')
    expect(callOperation).toHaveBeenCalledTimes(1)
    expect(wrapper.findAll('button').find(button => button.text() === 'Add agent')!.attributes('disabled')).toBeDefined()
  })
  it('uses the configured folder without listing or choosing task workspaces', async () => {
    vi.mocked(callOperation).mockImplementation(async operation => operation === 'project.get'
      ? { id: 1, primary_workspace_binding_id: 17, project_dir: '/fixture/project' }
      : { data: { project_id: 1, project_dir: '/fixture/project', items: [row] } })
    wrapper = mount(ProjectAgents, { props: { projectId: 1 } }); await flushPromises()
    expect(callOperation).toHaveBeenCalledWith('projectAgent.list', expect.objectContaining({ project_id: 1, project_dir: '/fixture/project' }))
    expect(wrapper.text()).not.toContain('/other')
    expect(wrapper.text()).not.toContain('result.filter')
    expect(wrapper.findAllComponents(UiFormField).some(field => field.props('label') === 'Project folder')).toBe(false)
    const add = wrapper.findAll('button').find(button => button.text() === 'Add agent')!
    expect(add.attributes('disabled')).toBeUndefined()
    await add.trigger('click')
    expect(wrapper.get('[aria-label="Agent editor"]').text()).toContain('Add agent')
    expect(vi.mocked(callOperation).mock.calls.map(([name]) => name)).toEqual(['project.get', 'projectAgent.list', 'hostMcp.status'])
  })
  it('shows an unbound folder state for an empty raw envelope without reading or writing agent files', async () => {
    vi.mocked(callOperation).mockResolvedValue({ id: 1, project_dir: null, project_folder_issue: 'Set the project folder once in project setup.' })
    wrapper = mount(ProjectAgents, { props: { projectId: 1 } }); await flushPromises()
    expect(wrapper.text()).toContain('Set the project folder once in project setup')
    expect(wrapper.findAll('button').find(button => button.text() === 'Add agent')!.attributes('disabled')).toBeDefined()
    expect(callOperation).toHaveBeenCalledTimes(1)
  })
  it.each([
    ['directoryless', 'Local agents need a project folder'],
    ['missing', 'Project folder is not selected'],
    ['unavailable', 'Project folder is unavailable'],
    ['conflict', 'Project folder binding conflicts'],
  ])('shows the %s state without onboarding or choosing another directory', async (status, title) => {
    vi.mocked(callOperation).mockResolvedValue({ id: 1, project_dir: null, project_folder_status: status, project_folder_issue: 'Project folder state from its owner.' })
    wrapper = mount(ProjectAgents, { props: { projectId: 1 } }); await flushPromises()
    expect(wrapper.text()).toContain(title)
    expect(wrapper.text()).not.toContain('Project folder needs setup')
    expect(wrapper.find('.agent-row').exists()).toBe(false)
    expect(wrapper.find('aside').exists()).toBe(false)
    expect(wrapper.findAll('button').find(button => button.text() === 'Add agent')!.attributes('disabled')).toBeDefined()
    expect(vi.mocked(callOperation).mock.calls.map(([name]) => name)).toEqual(['project.get'])
  })
  it('retries the selected project owner and loads only its explicitly repaired binding', async () => {
    vi.mocked(callOperation).mockResolvedValue({ id: 1, project_dir: null, project_folder_status: 'missing', project_folder_issue: 'Select an existing binding in project setup.' })
    wrapper = mount(ProjectAgents, { props: { projectId: 1 } }); await flushPromises()
    vi.mocked(callOperation).mockImplementation(async operation => operation === 'project.get'
      ? { id: 1, project_dir: '/fixture/project', project_folder_status: 'ready' }
      : { data: { project_id: 1, project_dir: '/fixture/project', items: [row] } })
    await wrapper.findAll('button').find(button => button.text() === 'Retry project folder')!.trigger('click'); await flushPromises()
    expect(wrapper.find('.agent-row').exists()).toBe(true)
    expect(vi.mocked(callOperation).mock.calls.map(([name]) => name)).toEqual(['project.get', 'project.get', 'projectAgent.list'])
    expect(vi.mocked(callOperation).mock.calls.every(([, args]) => args.project_id === 1)).toBe(true)
  })
  it('rechecks the authoritative binding on Refresh instead of retaining an unavailable folder', async () => {
    const w = setup(); await flushPromises()
    vi.mocked(callOperation).mockResolvedValue({ id: 1, project_dir: null, project_folder_status: 'unavailable', project_folder_issue: 'Reconnect the configured folder and retry.' })
    await w.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click'); await flushPromises()
    expect(callOperation).toHaveBeenLastCalledWith('project.get', expect.objectContaining({ project_id: 1 }))
    expect(w.find('.agent-row').exists()).toBe(false)
    expect(w.text()).toContain('Project folder is unavailable')
  })
  it('lists actual domains, opens full instructions and protects dirty drafts from refresh', async () => {
    const w = setup(); await flushPromises()
    expect(w.text()).not.toContain('/fixture/project')
    await w.get('.agent-row').trigger('click'); await flushPromises()
    expect(w.get('textarea').element.value).toBe('Find opportunities')
    expect(w.findAll('textarea')[1]!.element.value).toBe('Full native instructions')
    expect(w.text()).toContain('.codex/agents/seo-keyword-research.toml')
    expect(w.text()).toContain('future_native')
    expect(w.findAllComponents(UiSelect)[0]!.props('options')[0]).toEqual({ value: '', label: 'Default (inherited)' })
    await w.get('input[pattern]').setValue('new_name')
    await w.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click')
    expect(w.text()).toContain('Refresh project sources?')
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
    expect(vi.mocked(callOperation).mock.calls.map(([name]) => name)).toEqual(['project.get', 'projectAgent.list', 'hostMcp.status'])
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
  it('clears the previous project draft and agents while the next folder is loading', async () => {
    const w = setup(); await flushPromises()
    await w.get('.agent-row').trigger('click'); await flushPromises()
    let resolve!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementation(async operation => operation === 'project.get'
      ? new Promise(done => { resolve = done }) : { data: { project_id: 2, project_dir: '/second', items: [] } })
    await w.setProps({ projectId: 2 }); await flushPromises()
    expect(w.find('.agent-row').exists()).toBe(false)
    expect(w.find('[aria-label="Agent editor"]').exists()).toBe(false)
    expect(w.text()).not.toContain('/fixture/project')
    resolve({ id: 2, project_dir: '/second' }); await flushPromises()
    expect(callOperation).toHaveBeenLastCalledWith('projectAgent.list', expect.objectContaining({ project_id: 2, project_dir: '/second' }))
  })
  it('loads a changed project before rendering and ignores its predecessor’s late folder lookup', async () => {
    let resolveOld!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementation(async (operation, args) => operation === 'project.get'
      ? args.project_id === 1 ? new Promise(resolve => { resolveOld = resolve }) : { id: 2, project_dir: '/second' }
      : { data: { project_id: 2, project_dir: '/second', items: [] } })
    wrapper = mount(ProjectAgents, { props: { projectId: 1 } })
    await wrapper.setProps({ projectId: 2 }); await flushPromises()
    resolveOld({ id: 1, project_dir: '/first' }); await flushPromises()
    expect(wrapper.text()).not.toContain('Project folder:')
    expect(wrapper.text()).not.toContain('/first')
    expect(callOperation).toHaveBeenCalledWith('projectAgent.list', expect.objectContaining({ project_id: 2, project_dir: '/second' }))
    expect(callOperation).not.toHaveBeenCalledWith('projectAgent.list', expect.objectContaining({ project_id: 2, project_dir: '/first' }))
  })
  it('does not start a source read when a folder lookup finishes after unmount', async () => {
    let resolve!: (value: unknown) => void
    vi.mocked(callOperation).mockReturnValueOnce(new Promise(done => { resolve = done }))
    wrapper = mount(ProjectAgents, { props: { projectId: 1 } })
    wrapper.unmount(); wrapper = undefined
    resolve({ id: 1, project_dir: '/fixture/project' }); await flushPromises()
    expect(callOperation).toHaveBeenCalledTimes(1)
  })
  it('renders the selected host catalog alongside inherited, saved unavailable and custom choices', async () => {
    const saved = { ...row, fields: { ...row.fields, model: 'retired-model' } }
    const w = setup([saved]); await flushPromises()
    await w.get('.agent-row').trigger('click'); await flushPromises()
    const field = w.findAllComponents(UiFormField).find(item => item.props('label') === 'Model')!
    await field.get('[role="combobox"]').trigger('click')
    expect(field.findAll('[role="option"]').map(option => option.text())).toEqual([
      'Default (inherited)', 'catalog-model', 'second-model', 'retired-model (not in catalog)', 'Custom model…',
    ])
    expect(w.text()).toContain('Codex project file')
    expect(w.findAllComponents(UiFormField).some(item => item.props('label') === 'Agent app')).toBe(false)
    expect(w.text()).not.toContain('Project folder:')
    expect(w.text()).not.toContain('/fixture/project')
    expect(w.text()).toContain(saved.source_ref.source_path)
  })
  it('keeps custom model and instruction edits through catalog loading, failure and retry', async () => {
    const saved = { ...row, fields: { ...row.fields, model_reasoning_effort: 'retained-effort' } }
    const w = setup([saved]); await flushPromises()
    const original = vi.mocked(callOperation).getMockImplementation()!
    let reject!: (cause: unknown) => void
    vi.mocked(callOperation).mockImplementation((operation, args, options) => operation === 'hostMcp.status'
      ? new Promise((_resolve, fail) => { reject = fail }) : original(operation, args, options))
    await w.get('.agent-row').trigger('click'); await flushPromises()
    const field = (label: string) => w.findAllComponents(UiFormField).find(item => item.props('label') === label)!
    expect(w.text()).toContain('Loading model catalog…')
    expect(field('Model').get('[role="combobox"]').attributes('disabled')).toBeUndefined()
    field('Model').getComponent(UiSelect).vm.$emit('update:modelValue', '__custom__'); await flushPromises()
    await field('Custom model').get('input').setValue('unsaved-model')
    await field('Instructions').get('textarea').setValue('Unsaved full instructions')
    reject(new Error('Catalog offline')); await flushPromises()
    expect(w.text()).toContain('Catalog offline')
    vi.mocked(callOperation).mockImplementation((operation, args, options) => operation === 'hostMcp.status'
      ? Promise.resolve(catalog) : original(operation, args, options))
    await w.findAll('button').find(button => button.text() === 'Retry models')!.trigger('click'); await flushPromises()
    expect(field('Custom model').get<HTMLInputElement>('input').element.value).toBe('unsaved-model')
    expect(field('Instructions').get<HTMLTextAreaElement>('textarea').element.value).toBe('Unsaved full instructions')
    expect(field('Reasoning effort').getComponent(UiSelect).props('modelValue')).toBe('retained-effort')
    expect(w.text()).toContain('Unsaved changes')
    expect(field('Model').getComponent(UiSelect).props('options')).toContainEqual({ value: 'catalog-model', label: 'catalog-model' })
    expect(w.text()).not.toContain('Catalog offline')
  })
  it.each(['catalog-model', null])('round trips model %s using the existing source digest guards', async model => {
    const saved = { ...row, fields: { ...row.fields, model: 'retired-model' as string | null } }
    const w = setup([saved]); await flushPromises()
    await w.get('.agent-row').trigger('click'); await flushPromises()
    const field = () => w.findAllComponents(UiFormField).find(item => item.props('label') === 'Model')!
    await field().get('[role="combobox"]').trigger('click')
    await field().findAll('[role="option"]').find(option => option.text() === (model ?? 'Default (inherited)'))!.trigger('click')
    const readback = { ...saved, digest: 'b'.repeat(64), fields: { ...saved.fields, model } }
    vi.mocked(callOperation).mockResolvedValueOnce({ project_id: 1, data: readback })
    await w.get('form').trigger('submit'); await flushPromises()
    expect(callOperation).toHaveBeenCalledWith('projectAgent.update', expect.objectContaining({
      source_ref: saved.source_ref, expected_digest: saved.digest, expected_registration_digest: null, patch: { model },
    }))
    expect(field().getComponent(UiSelect).props('modelValue')).toBe(model ?? '')
    expect(w.text()).toContain('No changes')
    vi.mocked(callOperation).mockResolvedValueOnce(readback)
    await w.get('.agent-row').trigger('click'); await flushPromises()
    expect(field().getComponent(UiSelect).props('modelValue')).toBe(model ?? '')
  })
  it('explains a missing host catalog and keeps inherited/custom settings usable', async () => {
    const w = setup(); await flushPromises()
    const original = vi.mocked(callOperation).getMockImplementation()!
    vi.mocked(callOperation).mockImplementation((operation, args, options) => operation === 'hostMcp.status'
      ? Promise.resolve({ ok: true, items: [{ host_key: 'codex' }] }) : original(operation, args, options))
    await w.get('.agent-row').trigger('click'); await flushPromises()
    expect(w.text()).toContain('Model catalog unavailable')
    expect(w.findAll('button').some(button => button.text() === 'Retry models')).toBe(true)
    const model = w.findAllComponents(UiFormField).find(item => item.props('label') === 'Model')!
    expect(model.get('[role="combobox"]').attributes('disabled')).toBeUndefined()
    expect(model.getComponent(UiSelect).props('options')).toContainEqual({ value: '', label: 'Default (inherited)' })
  })
})
