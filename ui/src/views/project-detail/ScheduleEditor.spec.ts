import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ScheduleEditor from './ScheduleEditor.vue'
import ScheduleAgentSelectors from './ScheduleAgentSelectors.vue'
import { UiConfirmDialog, UiSelect } from '@/components/ui'
import { callOperation } from '@/lib/operations'
import { apiFetch } from '@/lib/client'
import { useSchedulesStore } from '@/stores/schedules'
import { blankAgent, agentIdentity } from '../agents/useProjectAgents'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
vi.mock('@/lib/client', async original => ({ ...await original<typeof import('@/lib/client')>(), apiFetch: vi.fn() }))
let wrapper: VueWrapper | undefined
const source = { host_key: 'codex' as const, native_name: 'research', source_path: '.codex/agents/research.toml' }
function setup(roleCount = 1, missing = false) {
  setActivePinia(createPinia())
  const roles = ['researcher', 'writer', 'auditor'].slice(0, roleCount)
  vi.mocked(callOperation).mockImplementation(async operation => operation === 'project.get' ? { id: 1, project_dir: '/fixture' } : { data: { project_id: 1, project_dir: '/fixture', items: missing ? [] : [{ project_id: 1, source_ref: source, fields: { ...blankAgent(), name: 'research' } }] } })
  vi.mocked(apiFetch).mockImplementation(async path => path.endsWith('/workflow-templates') ? { templates: [{ key: 'fixture', name: 'Fixture workflow' }] } : { spec: { agent_requirements: roles.map(role => ({ role, purpose: `Work as ${role}`, requirement: 'required' })), steps: [{ id: 'work', title: 'Work', extensions_json: {} }] } })
  wrapper = mount(ScheduleEditor, { props: { projectId: 1, job: { kind: 'fixture', name: 'Fixture', template_key: 'fixture', cron_expr: '* * * * *', timezone: 'UTC', enabled: false, execution_json: { project_dir: '/fixture', agents: missing ? { researcher: { source_ref: source } } : {}, host_key: 'codex' } } as never }, global: { stubs: { teleport: true } } })
  return wrapper
}
const namedSource = (name: string) => ({ ...source, native_name: name, source_path: `.codex/agents/${name}.toml` })
function legacySchedule(execution: object) {
  setActivePinia(createPinia())
  vi.mocked(callOperation).mockImplementation(async operation => operation === 'project.get' ? { id: 1, project_dir: '/fixture' } : { data: { project_id: 1, project_dir: '/fixture', items: ['mapped', 'legacy', 'new'].map(name => ({ project_id: 1, source_ref: namedSource(name), fields: { ...blankAgent(), name } })) } })
  vi.mocked(apiFetch).mockImplementation(async path => path.endsWith('/workflow-templates') ? { templates: [{ key: 'fixture', name: 'Fixture' }] } : { spec: { agent_requirements: ['delivery_agent', 'review_agent'].map(role => ({ role, requirement: 'required' })), steps: [] } })
  wrapper = mount(ScheduleEditor, { props: { projectId: 1, job: { kind: 'fixture', name: 'Fixture', template_key: 'fixture', cron_expr: '* * * * *', timezone: 'UTC', enabled: false, execution_json: { project_dir: '/fixture', host_key: 'codex', ...execution } } as never }, global: { stubs: { teleport: true } } })
  return wrapper
}
afterEach(() => { wrapper?.unmount(); vi.resetAllMocks() })
describe('workflow-specific schedule agents', () => {
  it('uses the configured project folder for a new automation without a directory control', async () => {
    const w = setup(); w.unmount()
    wrapper = mount(ScheduleEditor, { props: { projectId: 1, job: null }, global: { stubs: { teleport: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('Project folder: /fixture')
    expect(wrapper.text()).not.toContain('Working directory')
    const save = vi.spyOn(useSchedulesStore(), 'save').mockResolvedValue()
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(save).toHaveBeenCalledWith(1, expect.objectContaining({ execution_json: { project_dir: '/fixture' } }))
  })
  it('retains a different saved target on unrelated edits and changes it only after confirmation', async () => {
    const w = setup(); await flushPromises()
    vi.mocked(callOperation).mockImplementation(async (operation, args) => operation === 'project.get'
      ? { id: 1, project_dir: '/configured' }
      : { data: { project_id: 1, project_dir: args.project_dir, items: [] } })
    const job = w.getComponent(ScheduleEditor).props('job'); w.unmount()
    wrapper = mount(ScheduleEditor, { props: { projectId: 1, job }, global: { stubs: { teleport: true } } }); await flushPromises()
    expect(wrapper.text()).toContain('Automation uses a different folder')
    const save = vi.spyOn(useSchedulesStore(), 'save').mockResolvedValue()
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(save.mock.calls[0]![1].execution_json?.project_dir).toBe('/fixture')
    await wrapper.findAll('button').find(button => button.text() === 'Use project folder')!.trigger('click')
    const confirm = wrapper.getComponent(UiConfirmDialog)
    expect(confirm.props('modelValue')).toBe(true)
    expect(save).toHaveBeenCalledTimes(1)
    confirm.vm.$emit('update:modelValue', false); await flushPromises()
    expect(wrapper.text()).toContain('Project folder: /fixture')
    await wrapper.findAll('button').find(button => button.text() === 'Use project folder')!.trigger('click')
    confirm.vm.$emit('confirm'); await flushPromises()
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(save.mock.calls[1]![1].execution_json?.project_dir).toBe('/configured')
    expect(wrapper.text()).not.toContain('Automation uses a different folder')
  })
  it('keeps folderless projects usable as disabled drafts and blocks enabling with an actionable message', async () => {
    const w = setup(); w.unmount()
    vi.mocked(callOperation).mockResolvedValue({ id: 1, project_dir: null, project_folder_issue: 'Select a binding once in project setup.' })
    wrapper = mount(ScheduleEditor, { props: { projectId: 1, job: null }, global: { stubs: { teleport: true } } }); await flushPromises()
    const save = vi.spyOn(useSchedulesStore(), 'save').mockResolvedValue()
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(save.mock.calls[0]![1].execution_json).toBeNull()
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(save).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('Select a binding once in project setup')
  })
  it('cannot save a prior project automation after the project changes', async () => {
    const w = setup(); await flushPromises()
    const save = vi.spyOn(useSchedulesStore(), 'save').mockResolvedValue()
    await w.setProps({ projectId: 2 }); await flushPromises()
    expect(w.find('form').exists()).toBe(false)
    expect(w.text()).toContain('Reopen this automation from the current project')
    expect(save).not.toHaveBeenCalled()
    expect(w.text()).not.toContain('/fixture')
  })
  it('reloads changed source/workflow props and rejects an earlier workflow response without repeated reads on selection', async () => {
    let resolveOld!: (value: unknown) => void
    vi.mocked(apiFetch).mockImplementation(async path => path.endsWith('/old') ? new Promise(resolve => { resolveOld = resolve })
      : { spec: { agent_requirements: [{ role: 'current_role' }], steps: [] } })
    vi.mocked(callOperation).mockImplementation(async (_operation, args) => ({ data: { project_id: args.project_id, project_dir: args.project_dir, items: [{ source_ref: source, fields: blankAgent() }] } }))
    wrapper = mount(ScheduleAgentSelectors, { props: { projectId: 1, projectDir: '/fixture', templateKey: 'old', hostKey: 'codex', agents: {}, assignments: {} } })
    await wrapper.setProps({ projectId: 2, projectDir: '/second', templateKey: 'current' }); await flushPromises()
    resolveOld({ spec: { agent_requirements: [{ role: 'stale_role' }], steps: [] } }); await flushPromises()
    expect(wrapper.text()).toContain('current_role'); expect(wrapper.text()).not.toContain('stale_role')
    expect(callOperation).toHaveBeenLastCalledWith('projectAgent.list', expect.objectContaining({ project_id: 2, project_dir: '/second' }))
    const reads = vi.mocked(callOperation).mock.calls.length
    await wrapper.setProps({ agents: { current_role: { source_ref: source } } }); await flushPromises()
    expect(callOperation).toHaveBeenCalledTimes(reads); expect(apiFetch).toHaveBeenCalledTimes(2)
  })
  it('clears workflow roles when the selection is cleared while an earlier read is pending', async () => {
    let resolve!: (value: unknown) => void
    vi.mocked(apiFetch).mockReturnValueOnce(new Promise(done => { resolve = done }))
    vi.mocked(callOperation).mockResolvedValue({ data: { project_id: 1, project_dir: '/fixture', items: [] } })
    wrapper = mount(ScheduleAgentSelectors, { props: { projectId: 1, projectDir: '/fixture', templateKey: 'old', hostKey: 'codex', agents: {}, assignments: {} } })
    await wrapper.setProps({ templateKey: '' }); await flushPromises()
    resolve({ spec: { agent_requirements: [{ role: 'stale_role' }], steps: [] } }); await flushPromises()
    expect(wrapper.text()).not.toContain('stale_role')
    expect(wrapper.text()).not.toContain('Loading workflow roles')
  })
  it.each([1, 3])('renders %i declared roles and saves explicit native references with no inline prompts', async count => {
    const w = setup(count); await flushPromises()
    const pickers = w.getComponent(ScheduleAgentSelectors).findAllComponents(UiSelect)
    expect(pickers.length).toBe(count === 1 ? 1 : 4)
    for (const picker of pickers.slice(0, count)) {
      picker.vm.$emit('update:modelValue', agentIdentity(source))
      await flushPromises()
    }
    await flushPromises()
    const save = vi.spyOn(useSchedulesStore(), 'save').mockResolvedValue()
    await w.get('form').trigger('submit'); await flushPromises()
    const args = save.mock.calls[0]![1]
    expect(Object.keys(args.execution_json!.agents!)).toHaveLength(count)
    for (const agent of Object.values(args.execution_json!.agents!)) expect(agent).toEqual({ host_key: 'codex', source_ref: source })
    expect(w.text()).not.toContain('Delivery agent')
  })
  it('keeps externally missing assignments visible and rejects save until reassigned', async () => {
    const w = setup(1, true); await flushPromises()
    expect(w.text()).toContain('Saved source missing or invalid')
    const save = vi.spyOn(useSchedulesStore(), 'save')
    await w.get('form').trigger('submit'); await flushPromises()
    expect(save).not.toHaveBeenCalled()
    expect(w.text()).toContain('Reassign it before saving')
    expect(w.getComponent(ScheduleAgentSelectors).getComponent(UiSelect).props('options')).toContainEqual(expect.objectContaining({ label: expect.stringContaining('(missing or invalid)') }))
  })
  it('preserves unsupported existing host execution choices', async () => {
    const old = setup(); await flushPromises(); const job = old.getComponent(ScheduleEditor).props('job')!; old.unmount()
    const execution = { project_dir: '/fixture', host_key: 'claude', delivery_agent: { name: 'existing', instructions: 'Keep native host route', profile: 'saved', provider: 'saved-provider', model: 'saved-model' }, mcp_config_ref: { path: '/fixture/routes.toml', sha256: 'a'.repeat(64) } }
    wrapper = mount(ScheduleEditor, { props: { projectId: 1, job: { ...job, execution_json: execution } as never }, global: { stubs: { teleport: true } } }); await flushPromises()
    const w = wrapper
    expect(w.text()).toContain('claude execution settings are preserved')
    expect(w.findAll('textarea')).toHaveLength(0)
    expect(w.text()).toContain('Profile saved')
    expect(w.text()).toContain('/fixture/routes.toml')
    const save = vi.spyOn(useSchedulesStore(), 'save').mockResolvedValue()
    await w.get('form').trigger('submit'); await flushPromises()
    expect(save).toHaveBeenCalledWith(1, expect.objectContaining({ execution_json: execution }))
  })
  it.each(['delivery_agent', 'review_agent'] as const)('preserves the canonical %s choice and both saved representations when unchanged', async role => {
    const execution = { project_dir: '/fixture', host_key: 'codex', agents: { [role]: { source_ref: namedSource('mapped'), model: 'mapped-model' } }, [role]: { source_ref: namedSource('legacy'), model: 'legacy-model', provider: 'saved-provider', profile: 'saved-profile' } }
    const w = legacySchedule(execution); await flushPromises()
    const picker = w.getComponent(ScheduleAgentSelectors).findAllComponents(UiSelect)[role === 'delivery_agent' ? 0 : 1]!
    expect(picker.props('modelValue')).toBe(agentIdentity(namedSource('legacy')))
    const save = vi.spyOn(useSchedulesStore(), 'save').mockResolvedValue()
    await w.get('form').trigger('submit'); await flushPromises()
    expect(save.mock.calls[0]![1].execution_json).toEqual(execution)
  })
  it.each(['delivery_agent', 'review_agent'] as const)('makes explicit %s source selection authoritative over legacy inline settings', async role => {
    const settings = { model: 'retained-model', model_reasoning_effort: 'high', provider: 'saved-provider', profile: 'saved-profile' }
    const other = role === 'delivery_agent' ? 'review_agent' : 'delivery_agent'
    const untouched = { name: 'Other host', instructions: 'Preserve other host route', host_key: 'claude', model: 'other-model' }
    const w = legacySchedule({ [role]: { name: 'Old role', instructions: 'Old copied prompt', ...settings }, [other]: untouched }); await flushPromises()
    const picker = w.getComponent(ScheduleAgentSelectors).findAllComponents(UiSelect)[role === 'delivery_agent' ? 0 : 1]!
    picker.vm.$emit('update:modelValue', agentIdentity(namedSource('new'))); await flushPromises()
    const save = vi.spyOn(useSchedulesStore(), 'save').mockResolvedValue()
    await w.get('form').trigger('submit'); await flushPromises()
    const saved = save.mock.calls[0]![1].execution_json!
    expect(saved[role]).toBeUndefined()
    expect(saved.agents?.[role]).toEqual({ ...settings, host_key: 'codex', source_ref: namedSource('new') })
    expect(saved[other]).toEqual(untouched)
  })
  it.each(['delivery_agent', 'review_agent'] as const)('clears and reassigns %s without leaving a legacy override or revealing a shadowed agent', async role => {
    const w = legacySchedule({ agents: { [role]: { source_ref: namedSource('mapped') } }, [role]: { source_ref: namedSource('legacy') } }); await flushPromises()
    const picker = w.getComponent(ScheduleAgentSelectors).findAllComponents(UiSelect)[role === 'delivery_agent' ? 0 : 1]!
    const save = vi.spyOn(useSchedulesStore(), 'save').mockResolvedValue()
    picker.vm.$emit('update:modelValue', ''); await flushPromises()
    await w.get('form').trigger('submit'); await flushPromises()
    let saved = save.mock.calls[0]![1].execution_json!
    expect(saved[role]).toBeUndefined(); expect(saved.agents?.[role]).toBeUndefined()
    picker.vm.$emit('update:modelValue', agentIdentity(namedSource('new'))); await flushPromises()
    await w.get('form').trigger('submit'); await flushPromises()
    saved = save.mock.calls[1]![1].execution_json!
    expect(saved[role]).toBeUndefined(); expect(saved.agents?.[role]?.source_ref).toEqual(namedSource('new'))
  })
})
