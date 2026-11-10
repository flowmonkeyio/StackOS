import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView, useRoute } from 'vue-router'
import type { SchemaLoadedWorkflowTemplate } from '@/api'
import { UiCheckbox, UiConfirmDialog, UiFormField, UiInput, UiSelect, UiTextarea } from '@/components/ui'
import { apiFetch } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { useSchedulesStore, type ScheduledJob } from '@/stores/schedules'
import ScheduleEditor from './ScheduleEditor.vue'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
vi.mock('@/lib/client', async original => ({ ...await original<typeof import('@/lib/client')>(), apiFetch: vi.fn() }))
const engineeringInputs = [
  { key: 'goal', name: 'Goal', description: 'Engineering outcome requested by the user or upstream workflow.', type: 'string', required: true },
  { key: 'repo_context', name: 'Repository Context', description: 'Optional known context.', type: 'object', required: false },
  { key: 'release_intent', name: 'Release Intent', type: 'string', required: false },
]
const fields = [
  ...engineeringInputs,
  { key: 'count', name: 'Count', type: 'number', default_json: 4, schema_json: { minimum: 0 } },
  { key: 'flag', name: 'Flag', type: 'boolean', default_json: true },
  { key: 'notes', name: 'Notes', type: 'string', schema_json: { format: 'textarea' } },
  { key: 'mode', name: 'Mode', type: 'string', default_json: 'auto', options_json: ['auto', 'skip'] },
  { key: 'scope', name: 'Scope', type: 'object', schema_json: { properties: { refs: { title: 'References', type: 'array', items: { type: 'string', minLength: 1 } } } } },
]
const loaded = (inputs: object[] = fields): SchemaLoadedWorkflowTemplate => ({
  spec: { inputs, agent_requirements: [{ role: 'researcher' }], steps: [] },
  project_extension: { enabled: true, input_defaults_json: { count: 2, flag: true, repo_context: { refs: ['inherited'] } }, required_input_keys_json: [] },
} as unknown as SchemaLoadedWorkflowTemplate)
let wrapper: VueWrapper | undefined
let rows: ScheduledJob[]
function setup(inputs: object[] = fields, overrides: Record<string, unknown> = {}) {
  setActivePinia(createPinia())
  rows = []
  const workflow = loaded(inputs)
  vi.mocked(apiFetch).mockImplementation(async path => path.endsWith('/schedules') ? rows : path.endsWith('/workflow-templates') ? { templates: [{ key: 'fixture', name: 'Fixture' }, { key: 'second', name: 'Second' }] } : workflow)
  vi.mocked(callOperation).mockImplementation(async (operation, args) => {
    if (operation === 'project.get') return { id: args.project_id, project_dir: '/fixture' }
    if (operation === 'schedule.set') { rows = [{ ...args, id: 1 } as unknown as ScheduledJob]; return { data: rows[0] } }
    return { data: { project_id: args.project_id, project_dir: args.project_dir, items: [] } }
  })
  const job = { kind: 'fixture', name: 'Fixture', template_key: 'fixture', cron_expr: '* * * * *', timezone: 'UTC', enabled: false, inputs_json: { goal: 'Saved goal', ...overrides }, execution_json: { project_dir: '/fixture', host_key: 'claude' } } as unknown as ScheduledJob
  wrapper = mount(ScheduleEditor, { props: { projectId: 1, job }, global: { stubs: { teleport: true } } })
  return { workflow, job }
}
function field(label: string) { return wrapper!.findAllComponents(UiFormField).find(row => row.props('label') === label)! }
async function submit() { await wrapper!.get('form').trigger('submit'); await flushPromises() }
afterEach(() => { wrapper?.unmount(); vi.resetAllMocks() })

describe('friendly schedule workflow inputs', () => {
  it('shares one effective load with roles and saves typed form values through the schedule store and readback', async () => {
    const { job } = setup(fields, { count: 0, flag: false, release_intent: '', unknown: [{ exact: true }], scope: { refs: ['first'], unknown: { retained: true } } })
    await useSchedulesStore().refresh(1); await flushPromises()
    expect(vi.mocked(apiFetch).mock.calls.filter(([path]) => path.endsWith('/workflow-templates/fixture'))).toHaveLength(1)
    expect(field('Goal').props('required')).toBe(true)
    expect(field('Release Intent').props('showOptional')).toBe(true)
    expect(field('Count').getComponent(UiInput).props('modelValue')).toBe(0)
    expect(field('Flag').getComponent(UiCheckbox).props('modelValue')).toBe(false)
    expect(wrapper!.text()).toContain('Saved structured value')
    expect(wrapper!.findAll('textarea')).toHaveLength(1)
    field('Goal').getComponent(UiInput).vm.$emit('update:modelValue', 'Entered goal')
    field('Notes').getComponent(UiTextarea).vm.$emit('update:modelValue', 'Multiple\nlines')
    field('Mode').getComponent(UiSelect).vm.$emit('update:modelValue', '1')
    await flushPromises(); await submit()
    const result = useSchedulesStore().items[0]!
    expect(result.inputs_json).toEqual({ ...job.inputs_json, repo_context: { refs: ['inherited'] }, notes: 'Multiple\nlines', mode: 'skip', goal: 'Entered goal' })
    expect(vi.mocked(callOperation).mock.calls.filter(([op]) => /runPlan|startDelivery|action\./.test(op))).toEqual([])
    wrapper!.unmount()
    wrapper = mount(ScheduleEditor, { props: { projectId: 1, job: result }, global: { stubs: { teleport: true } } }); await flushPromises()
    expect(field('Goal').getComponent(UiInput).props('modelValue')).toBe('Entered goal')
    expect(field('Notes').getComponent(UiTextarea).props('modelValue')).toBe('Multiple\nlines')
    expect(field('Mode').getComponent(UiSelect).props('modelValue')).toBe('1')
    expect(field('Flag').getComponent(UiCheckbox).props('modelValue')).toBe(false)
  })
  it('keeps input errors local, permits missing opaque setup in disabled drafts and prevents enabling', async () => {
    setup([{ key: 'setup', name: 'Evidence setup', type: 'object', required: true }]); await flushPromises()
    await submit()
    expect(rows).toHaveLength(1)
    wrapper!.findAllComponents(UiCheckbox).find(row => row.props('label') === 'Enabled')!.vm.$emit('update:modelValue', true)
    await flushPromises(); await submit()
    expect(vi.mocked(callOperation).mock.calls.filter(([op]) => op === 'schedule.set')).toHaveLength(1)
    expect(field('Evidence setup').props('error')).toContain('workflow setup')
    expect(wrapper!.text()).not.toContain('Could not save automation')
  })
  it('reports invalid numbers and required text on their fields without a server write', async () => {
    setup(fields, { goal: '', count: -1 }); await flushPromises()
    wrapper!.findAllComponents(UiCheckbox).find(row => row.props('label') === 'Enabled')!.vm.$emit('update:modelValue', true)
    await flushPromises(); await submit()
    expect(field('Goal').props('error')).toContain('required')
    expect(field('Count').props('error')).toContain('0 or more')
    expect(rows).toHaveLength(0)
  })
  it.each(['add', 'clear'])('rejects a blank numeric list item after %s through native controls and retains valid zero', async path => {
    setup([{ key: 'quantities', name: 'Quantities', type: 'array', required: true, schema_json: { items: { type: 'number' } } }], path === 'clear' ? { quantities: [4] } : {})
    await flushPromises()
    const enabled = wrapper!.findAllComponents(UiCheckbox).find(row => row.props('label') === 'Enabled')!.get('input')
    await enabled.setValue(true)
    if (path === 'add') await field('Quantities').findAll('button').find(button => button.text() === 'Add item')!.trigger('click')
    else await field('1').get('input[type="number"]').setValue('')
    await submit()
    expect(rows).toHaveLength(0)
    expect(field('1').props('error')).toBe('Enter a valid number.')
    expect(field('1').get('input[type="number"]').attributes('aria-invalid')).toBe('true')
    expect(field('Quantities').props('error')).toContain('Item 1: Enter a valid number.')
    // A supplied invalid item stays invalid in an inert draft; it is not an absent optional field.
    await enabled.setValue(false); await submit()
    expect(rows).toHaveLength(0)
    expect(field('1').getComponent(UiInput).props('modelValue')).toBeNull()
    await enabled.setValue(true)
    await field('1').get('input[type="number"]').setValue('0')
    await submit()
    expect(rows).toHaveLength(1)
    expect(rows[0]!.inputs_json!.quantities).toEqual([0])
    expect(vi.mocked(callOperation).mock.calls.filter(([operation]) => operation === 'schedule.set')).toHaveLength(1)
  })
  it('links boolean field errors to the checkbox and keeps false as a valid required value', async () => {
    const { workflow } = setup([{ key: 'choice', name: 'Choice', type: 'boolean', required: true }])
    workflow.project_extension!.input_defaults_json = {}
    await flushPromises()
    wrapper!.findAllComponents(UiCheckbox).find(row => row.props('label') === 'Enabled')!.vm.$emit('update:modelValue', true)
    await flushPromises(); await submit()
    const choice = field('Choice')
    const checkbox = choice.get('input[type="checkbox"]')
    expect(checkbox.attributes('aria-describedby')).toBe(choice.get('[role="alert"]').attributes('id'))
    choice.getComponent(UiCheckbox).vm.$emit('update:modelValue', false)
    await flushPromises(); await submit()
    expect(rows[0]!.inputs_json!.choice).toBe(false)
  })
  it('edits bounded properties and list items while keeping unknown object properties', async () => {
    setup(fields, { scope: { refs: ['first'], extra: { preserved: true } } }); await flushPromises()
    field('1').getComponent(UiInput).vm.$emit('update:modelValue', 'updated')
    await flushPromises()
    await field('References').findAll('button').find(button => button.text() === 'Add item')!.trigger('click')
    field('2').getComponent(UiInput).vm.$emit('update:modelValue', 'second')
    await flushPromises(); await submit()
    expect(rows[0]!.inputs_json!.scope).toEqual({ refs: ['updated', 'second'], extra: { preserved: true } })
  })
  it('saves entered false, zero and typed options, and leaves optional absent objects absent', async () => {
    const { workflow } = setup([...fields, { key: 'choice', name: 'Typed choice', type: 'string', options_json: [false, 0, ''] }, { key: 'optional', name: 'Optional object', type: 'object', schema_json: { properties: { required_child: { type: 'string' } }, required: ['required_child'] } }])
    workflow.project_extension!.input_defaults_json = {}
    await flushPromises()
    field('Count').getComponent(UiInput).vm.$emit('update:modelValue', 0)
    field('Flag').getComponent(UiCheckbox).vm.$emit('update:modelValue', false)
    field('Typed choice').getComponent(UiSelect).vm.$emit('update:modelValue', '1')
    wrapper!.findAllComponents(UiCheckbox).find(row => row.props('label') === 'Enabled')!.vm.$emit('update:modelValue', true)
    await flushPromises(); await submit()
    expect(rows[0]!.inputs_json).toMatchObject({ count: 0, flag: false, choice: 0 })
    expect(rows[0]!.inputs_json).not.toHaveProperty('optional')
    expect(field('required_child').props('error')).toBeNull()
    field('Mode').getComponent(UiSelect).vm.$emit('update:modelValue', '')
    await flushPromises(); await submit()
    expect(rows[0]!.inputs_json!.mode).toBeNull()
    const saved = rows[0]!; wrapper!.unmount()
    wrapper = mount(ScheduleEditor, { props: { projectId: 1, job: saved }, global: { stubs: { teleport: true } } }); await flushPromises()
    expect(field('Mode').getComponent(UiSelect).props('modelValue')).toBe('')
  })
  it('confirms a workflow change before clearing values and ignores the old pending response', async () => {
    setup(); let resolveOld!: (value: unknown) => void
    vi.mocked(apiFetch).mockImplementation(async path => path.endsWith('/fixture') ? new Promise(resolve => { resolveOld = resolve }) : path.endsWith('/second') ? loaded([{ key: 'current', name: 'Current field', type: 'string' }]) : { templates: [] })
    // Remount with the old metadata request pending.
    const job = wrapper!.getComponent(ScheduleEditor).props('job')!; wrapper!.unmount()
    wrapper = mount(ScheduleEditor, { props: { projectId: 1, job }, global: { stubs: { teleport: true } } }); await flushPromises()
    field('Workflow').getComponent(UiSelect).vm.$emit('update:modelValue', 'second'); await flushPromises()
    const confirmation = wrapper!.findAllComponents(UiConfirmDialog).find(dialog => dialog.props('title') === 'Change workflow?')!
    expect(confirmation.props('modelValue')).toBe(true)
    confirmation.vm.$emit('update:modelValue', false); await flushPromises()
    expect(field('Workflow').getComponent(UiSelect).props('modelValue')).toBe('fixture')
    field('Workflow').getComponent(UiSelect).vm.$emit('update:modelValue', 'second'); await flushPromises()
    confirmation.vm.$emit('confirm'); await flushPromises()
    resolveOld(loaded()); await flushPromises()
    expect(wrapper!.text()).toContain('Current field')
    expect(wrapper!.text()).not.toContain('Repository Context')
    await submit()
    expect(rows[0]!.template_key).toBe('second')
    expect(rows[0]!.inputs_json).not.toHaveProperty('goal')
  })
  it('does not repopulate fields after a project switch or unmount', async () => {
    setup(); let resolve!: (value: unknown) => void
    vi.mocked(apiFetch).mockImplementation(async path => path.endsWith('/fixture') ? new Promise(done => { resolve = done }) : { templates: [] })
    const job = wrapper!.getComponent(ScheduleEditor).props('job')!; wrapper!.unmount()
    wrapper = mount(ScheduleEditor, { props: { projectId: 1, job }, global: { stubs: { teleport: true } } }); await flushPromises()
    await wrapper.setProps({ projectId: 2 }); resolve(loaded()); await flushPromises()
    expect(wrapper.text()).not.toContain('Repository Context')
    expect(wrapper.find('form').exists()).toBe(false)
    expect(rows).toHaveLength(0)
    wrapper.unmount(); await flushPromises()
  })
  it('preserves input state while retrying metadata failure and leaves canonical server errors visible', async () => {
    setup(); const job = wrapper!.getComponent(ScheduleEditor).props('job')!; wrapper!.unmount()
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error('Workflow offline')).mockRejectedValueOnce(new Error('Workflow offline'))
    wrapper = mount(ScheduleEditor, { props: { projectId: 1, job }, global: { stubs: { teleport: true } } }); await flushPromises()
    expect(wrapper.text()).toContain('Workflow offline')
    await wrapper.findAll('button').find(button => button.text() === 'Retry workflow')!.trigger('click'); await flushPromises()
    expect(field('Goal').getComponent(UiInput).props('modelValue')).toBe('Saved goal')
    vi.spyOn(useSchedulesStore(), 'save').mockRejectedValue(new Error('Canonical validation rejected setup'))
    await submit()
    expect(wrapper.text()).toContain('Canonical validation rejected setup')
    expect(field('Goal').getComponent(UiInput).props('modelValue')).toBe('Saved goal')
  })
  it('protects edited values when closing the dialog', async () => {
    setup(); await flushPromises()
    field('Goal').getComponent(UiInput).vm.$emit('update:modelValue', 'Unsaved'); await flushPromises()
    await wrapper!.findAll('button').find(button => button.text() === 'Cancel')!.trigger('click'); await flushPromises()
    const confirm = wrapper!.findAllComponents(UiConfirmDialog).find(dialog => dialog.props('title') === 'Discard automation edits?')!
    expect(confirm.props('modelValue')).toBe(true)
    expect(wrapper!.emitted('close')).toBeUndefined()
    confirm.vm.$emit('update:modelValue', false); await flushPromises()
    expect(field('Goal').getComponent(UiInput).props('modelValue')).toBe('Unsaved')
  })
  it('warns before route/project changes and keeps edits when navigation is cancelled', async () => {
    const { job } = setup(); wrapper!.unmount()
    const page = defineComponent({ setup() { const route = useRoute(); return () => h(ScheduleEditor, { projectId: Number(route.params.id), job }) } })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/projects/:id/schedules', component: page }, { path: '/other', component: { template: '<p>Other page</p>' } }] })
    await router.push('/projects/1/schedules'); await router.isReady()
    wrapper = mount(RouterView, { global: { plugins: [router], stubs: { teleport: true } } }); await flushPromises()
    field('Goal').getComponent(UiInput).vm.$emit('update:modelValue', 'Unsaved'); await flushPromises()
    const navigation = router.push('/projects/2/schedules'); await flushPromises()
    const confirm = wrapper.findAllComponents(UiConfirmDialog).find(dialog => dialog.props('title') === 'Discard automation edits?')!
    expect(confirm.props('modelValue')).toBe(true)
    expect(router.currentRoute.value.params.id).toBe('1')
    confirm.vm.$emit('update:modelValue', false); await navigation; await flushPromises()
    expect(router.currentRoute.value.params.id).toBe('1')
    expect(field('Goal').getComponent(UiInput).props('modelValue')).toBe('Unsaved')
    const leave = router.push('/other'); await flushPromises()
    confirm.vm.$emit('confirm'); await leave; await flushPromises()
    expect(router.currentRoute.value.path).toBe('/other')
    expect(rows).toHaveLength(0)
  })
})
