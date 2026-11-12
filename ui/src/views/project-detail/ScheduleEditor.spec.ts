import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView, useRoute } from 'vue-router'
import ScheduleEditor from './ScheduleEditor.vue'
import { UiConfirmDialog, UiFormField, UiInput, UiSelect, UiTextarea } from '@/components/ui'
import { callOperation } from '@/lib/operations'
import { apiFetch } from '@/lib/client'
import { useSchedulesStore, type ScheduledJob } from '@/stores/schedules'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
vi.mock('@/lib/client', async original => ({ ...await original<typeof import('@/lib/client')>(), apiFetch: vi.fn() }))
let wrapper: VueWrapper | undefined
let rows: ScheduledJob[]
const saved = (): ScheduledJob => ({ id: 1, project_id: 1, kind: 'stable', name: 'Review', template_key: 'fixture', goal: 'Saved goal', cron_expr: '0 9 * * *', timezone: 'UTC', enabled: false,
  last_request_id: null, last_run_at: null, last_run_status: null, next_run_at: null,
  inputs_json: { goal: 'Original context', count: 0, flag: false, unknown: [{ exact: true }], ref: 'credential_safe' },
  execution_json: { project_dir: '/fixture', host_key: 'codex', mcp_config_ref: { path: '/fixture/routes.toml', sha256: 'a'.repeat(64) } },
})
function mountEditor(job: ScheduledJob | null = saved()) {
  wrapper = mount(ScheduleEditor, { props: { projectId: 1, job }, global: { stubs: { teleport: true, RouterLink: { template: '<a><slot /></a>' } } } })
  return wrapper
}
function field(label: string) { return wrapper!.findAllComponents(UiFormField).find(row => row.props('label') === label)! }
async function submit() { await wrapper!.get('form').trigger('submit'); await flushPromises() }
async function mode(value: string) { await wrapper!.get(`input[type="radio"][value="${value}"]`).setValue(true) }
function confirmation(title: string) { return wrapper!.findAllComponents(UiConfirmDialog).find(dialog => dialog.props('title') === title)! }

beforeEach(() => {
  setActivePinia(createPinia()); rows = []
  vi.mocked(apiFetch).mockImplementation(async path => path.endsWith('/schedules') ? rows : { templates: [{ key: 'fixture', name: 'Fixture workflow' }, { key: 'second', name: 'Second workflow' }] })
  vi.mocked(callOperation).mockImplementation(async (operation, args) => {
    if (operation === 'project.get') return { id: args.project_id, project_dir: '/fixture' }
    if (operation === 'schedule.set') { rows = [{ ...args, id: 1 } as unknown as ScheduledJob]; return { data: rows[0] } }
    throw new Error(`Unexpected operation ${operation}`)
  })
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.resetAllMocks() })

describe('simple automation setup', () => {
  it('renders simple controls and saves/reopens a prompt through the store without execution', async () => {
    mountEditor(null); await useSchedulesStore().refresh(1); await flushPromises()
    expect(wrapper!.findAll('input[type="radio"]').map(input => input.attributes('value'))).toEqual(['workflow', 'prompt'])
    expect(wrapper!.findAllComponents(UiSelect)).toHaveLength(0)
    expect(wrapper!.findAll('textarea')).toHaveLength(1)
    expect(wrapper!.text()).not.toMatch(/Project folder:|Execution options|Agent app|JSON|role/i)
    await field('Name').getComponent(UiInput).get('input').setValue('Daily prompt')
    await field('Goal / instructions').getComponent(UiTextarea).get('textarea').setValue('Inspect\nand report')
    await wrapper!.get('input[type="checkbox"]').setValue(true); await submit()
    expect(rows[0]).toMatchObject({ name: 'Daily prompt', goal: 'Inspect\nand report', template_key: null, enabled: true, execution_json: null, inputs_json: null })
    expect(rows[0]!.kind).toMatch(/^automation-/); expect(useSchedulesStore().items[0]).toEqual(rows[0])
    wrapper!.unmount(); mountEditor(rows[0]); await flushPromises()
    expect(wrapper!.get('input[value="prompt"]').element).toHaveProperty('checked', true)
    expect(field('Goal / instructions').getComponent(UiTextarea).props('modelValue')).toBe('Inspect\nand report')
    expect(vi.mocked(callOperation).mock.calls.map(([operation]) => operation)).toEqual(['project.get', 'schedule.set', 'project.get'])
  })
  it('retains migrated context, identity, schedule and native settings across edits and mode/workflow changes', async () => {
    const job = saved(); mountEditor(job); await useSchedulesStore().refresh(1); await flushPromises()
    await field('Name').getComponent(UiInput).get('input').setValue('Renamed')
    field('Workflow').getComponent(UiSelect).vm.$emit('update:modelValue', 'second'); await submit()
    expect(rows[0]).toMatchObject({ kind: 'stable', name: 'Renamed', goal: job.goal, cron_expr: job.cron_expr, timezone: job.timezone, inputs_json: job.inputs_json, execution_json: job.execution_json, template_key: 'second' })
    expect(apiFetch).toHaveBeenCalledTimes(3)
    await mode('prompt'); await submit()
    expect(rows[0]).toMatchObject({ template_key: null, goal: job.goal, inputs_json: job.inputs_json })
    await mode('workflow'); await submit(); expect(field('Workflow').props('error')).toContain('Choose a workflow')
    field('Workflow').getComponent(UiSelect).vm.$emit('update:modelValue', 'fixture'); await submit()
    expect(rows[0]!.inputs_json).toEqual(job.inputs_json)
  })
  it('saves incomplete disabled drafts and links required goal errors when enabling', async () => {
    mountEditor(null); await flushPromises(); await submit()
    expect(rows[0]).toMatchObject({ enabled: false, goal: null, template_key: null })
    await wrapper!.get('input[type="checkbox"]').setValue(true); await submit()
    const goal = field('Goal / instructions'); expect(goal.props('error')).toContain('Enter a goal')
    expect(goal.get('textarea').attributes('aria-describedby')?.split(' ')).toContain(goal.get('[role="alert"]').attributes('id'))
    expect(vi.mocked(callOperation).mock.calls.filter(([operation]) => operation === 'schedule.set')).toHaveLength(1)
  })
  it('preserves edits through catalog failure/retry and canonical save rejection/recovery', async () => {
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error('Catalog offline')); mountEditor(); await flushPromises()
    expect(wrapper!.text()).toContain('Catalog offline')
    await wrapper!.findAll('button').find(button => button.text() === 'Retry workflows')!.trigger('click'); await flushPromises()
    vi.spyOn(useSchedulesStore(), 'save').mockRejectedValueOnce(new Error('Canonical setup rejected'))
    await field('Goal / instructions').getComponent(UiTextarea).get('textarea').setValue('Retained goal'); await submit()
    expect(wrapper!.text()).toContain('Canonical setup rejected')
    expect(field('Goal / instructions').getComponent(UiTextarea).props('modelValue')).toBe('Retained goal')
    await submit(); expect(wrapper!.emitted('saved')).toHaveLength(1)
  })
  it('locks pending controls, refuses duplicate submission/close and recovers from rejection', async () => {
    mountEditor(); await flushPromises(); let reject!: (cause: Error) => void
    vi.mocked(callOperation).mockImplementation(async () => new Promise((_, fail) => { reject = fail }))
    await wrapper!.get('form').trigger('submit'); await flushPromises()
    expect(useSchedulesStore().saving).toBe(true)
    expect(wrapper!.findAll('form input, form textarea, form button').every(input => input.attributes('disabled') !== undefined)).toBe(true)
    await wrapper!.get('form').trigger('submit')
    await wrapper!.findAll('button').find(button => button.text() === 'Cancel')!.trigger('click')
    expect(wrapper!.emitted('close')).toBeUndefined()
    reject(new Error('Save failed')); await flushPromises()
    expect(useSchedulesStore().saving).toBe(false); expect(wrapper!.text()).toContain('Save failed')
    expect(vi.mocked(callOperation).mock.calls.filter(([operation]) => operation === 'schedule.set')).toHaveLength(1)
  })
  it('permits folderless drafts and shows actionable setup for enabling', async () => {
    vi.mocked(callOperation).mockResolvedValue({ id: 1, project_dir: null, project_folder_issue: 'Select a binding once in project setup.' })
    mountEditor(null); await flushPromises(); expect(wrapper!.text()).toContain('Open setup'); await submit()
    expect(wrapper!.emitted('saved')).toHaveLength(1)
    await wrapper!.get('input[type="checkbox"]').setValue(true); await submit()
    expect(wrapper!.text()).toContain('Select a binding once in project setup.'); expect(wrapper!.emitted('saved')).toHaveLength(1)
  })
  it('hides paths and retains a legacy folder fence until explicit confirmation', async () => {
    vi.mocked(callOperation).mockResolvedValue({ id: 1, project_dir: '/configured' }); mountEditor(); await flushPromises()
    const save = vi.spyOn(useSchedulesStore(), 'save').mockResolvedValue(); expect(wrapper!.text()).not.toMatch(/\/configured|\/fixture/)
    await submit(); expect(save.mock.calls[0]![1].execution_json?.project_dir).toBe('/fixture')
    await wrapper!.findAll('button').find(button => button.text() === 'Use project folder')!.trigger('click')
    confirmation('Use the configured project folder?').vm.$emit('update:modelValue', false); await flushPromises()
    await submit(); expect(save.mock.calls[1]![1].execution_json?.project_dir).toBe('/fixture')
    await wrapper!.findAll('button').find(button => button.text() === 'Use project folder')!.trigger('click')
    confirmation('Use the configured project folder?').vm.$emit('confirm'); await flushPromises()
    await submit(); expect(save.mock.calls[2]![1].execution_json?.project_dir).toBe('/configured')
  })
  it('protects close and beforeunload, and ignores late catalog responses after project change', async () => {
    let resolve!: (value: unknown) => void; vi.mocked(apiFetch).mockReturnValueOnce(new Promise(done => { resolve = done }))
    mountEditor(); await flushPromises(); await field('Goal / instructions').getComponent(UiTextarea).get('textarea').setValue('Unsaved')
    const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); expect(event.defaultPrevented).toBe(true)
    await wrapper!.findAll('button').find(button => button.text() === 'Cancel')!.trigger('click')
    expect(confirmation('Discard automation edits?').props('modelValue')).toBe(true)
    confirmation('Discard automation edits?').vm.$emit('update:modelValue', false); await flushPromises()
    expect(field('Goal / instructions').getComponent(UiTextarea).props('modelValue')).toBe('Unsaved')
    await wrapper!.setProps({ projectId: 2 }); resolve({ templates: [{ key: 'late', name: 'Wrong project' }] }); await flushPromises()
    expect(wrapper!.find('form').exists()).toBe(false); expect(wrapper!.text()).not.toContain('Wrong project'); expect(rows).toHaveLength(0)
  })
  it('keeps the nested unsaved confirmation body, focus and dismissal inside the shared overlay stack', async () => {
    wrapper = mount(ScheduleEditor, { attachTo: document.body, props: { projectId: 1, job: saved() } })
    await flushPromises()
    const goal = document.querySelector<HTMLTextAreaElement>('textarea')!
    goal.value = 'Unsaved goal'
    goal.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()
    const cancel = Array.from(document.querySelectorAll<HTMLButtonElement>('.ui-dialog__footer button')).find(button => button.textContent?.trim() === 'Cancel')!
    const openConfirmation = async () => { cancel.focus(); cancel.click(); await flushPromises() }
    await openConfirmation()
    const frames = () => Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"]'))
    const confirm = frames().at(-1)!
    expect(frames()).toHaveLength(2)
    expect(confirm.querySelector('.ui-dialog__header')!.textContent).not.toContain('Your unsaved')
    expect(confirm.querySelector('.ui-dialog__body')!.textContent).toContain('Your unsaved automation changes will be lost.')
    expect(confirm.contains(document.activeElement)).toBe(true)
    const confirmAction = confirm.querySelector<HTMLButtonElement>('.ui-dialog__footer button:last-child')!
    confirmAction.focus()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(confirm.querySelector('[aria-label="Close dialog"]'))
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(frames()).toHaveLength(1)
    expect(document.activeElement).toBe(cancel)
    expect(goal.value).toBe('Unsaved goal')
    await openConfirmation()
    document.querySelectorAll<HTMLElement>('.ui-dialog__overlay').item(1).click()
    await flushPromises()
    expect(frames()).toHaveLength(1)
    expect(document.activeElement).toBe(cancel)
    expect(wrapper!.emitted('close')).toBeUndefined()
    await openConfirmation()
    frames().at(-1)!.querySelector<HTMLButtonElement>('.ui-dialog__footer button:last-child')!.click()
    await flushPromises()
    expect(wrapper!.emitted('close')).toHaveLength(1)
  })
  it('guards route/project changes and keeps edits when navigation is cancelled', async () => {
    const job = saved(); const page = defineComponent({ setup() { const route = useRoute(); return () => h(ScheduleEditor, { projectId: Number(route.params.id), job }) } })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/projects/:id/schedules', component: page }, { path: '/other', component: { template: '<p>Other</p>' } }] })
    await router.push('/projects/1/schedules'); await router.isReady()
    wrapper = mount(RouterView, { global: { plugins: [router], stubs: { teleport: true } } }); await flushPromises()
    await field('Goal / instructions').getComponent(UiTextarea).get('textarea').setValue('Unsaved')
    const navigation = router.push('/projects/2/schedules'); await flushPromises()
    confirmation('Discard automation edits?').vm.$emit('update:modelValue', false); await navigation; await flushPromises()
    expect(router.currentRoute.value.params.id).toBe('1'); expect(field('Goal / instructions').getComponent(UiTextarea).props('modelValue')).toBe('Unsaved')
    const leave = router.push('/other'); await flushPromises(); confirmation('Discard automation edits?').vm.$emit('confirm'); await leave; await flushPromises()
    expect(router.currentRoute.value.path).toBe('/other'); expect(rows).toHaveLength(0)
  })
})
