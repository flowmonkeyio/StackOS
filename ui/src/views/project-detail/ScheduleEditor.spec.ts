import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ScheduleEditor from './ScheduleEditor.vue'
import ScheduleAgentSelectors from './ScheduleAgentSelectors.vue'
import { UiSelect } from '@/components/ui'
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
  vi.mocked(callOperation).mockResolvedValue({ project_id: 1, project_dir: '/fixture', items: missing ? [] : [{ project_id: 1, source_ref: source, fields: { ...blankAgent(), name: 'research' } }] })
  vi.mocked(apiFetch).mockImplementation(async path => path.endsWith('/workflow-templates') ? { templates: [{ key: 'fixture', name: 'Fixture workflow' }] } : { spec: { agent_requirements: roles.map(role => ({ role, purpose: `Work as ${role}`, requirement: 'required' })), steps: [{ id: 'work', title: 'Work', extensions_json: {} }] } })
  wrapper = mount(ScheduleEditor, { props: { projectId: 1, job: { kind: 'fixture', name: 'Fixture', template_key: 'fixture', cron_expr: '* * * * *', timezone: 'UTC', enabled: false, execution_json: { project_dir: '/fixture', agents: missing ? { researcher: { source_ref: source } } : {}, host_key: 'codex' } } as never }, global: { stubs: { teleport: true } } })
  return wrapper
}
afterEach(() => { wrapper?.unmount(); vi.resetAllMocks() })
describe('workflow-specific schedule agents', () => {
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
    const w = setup(); await flushPromises()
    const textareas = w.findAll('textarea')
    await textareas.at(-1)!.setValue('{"host_key":"claude","delivery_agent":{"name":"existing","instructions":"Keep native host route","profile":"saved"}}')
    expect(w.text()).toContain('claude execution settings are preserved')
    const save = vi.spyOn(useSchedulesStore(), 'save').mockResolvedValue()
    await w.get('form').trigger('submit'); await flushPromises()
    expect(save).toHaveBeenCalledWith(1, expect.objectContaining({ execution_json: expect.objectContaining({ host_key: 'claude', delivery_agent: { name: 'existing', instructions: 'Keep native host route', profile: 'saved' } }) }))
  })
})
