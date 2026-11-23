import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { callOperation } from '@/lib/operations'
import { useRoleSettings } from './useRoleSettings'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
let wrapper: VueWrapper | undefined
function setup() {
  const project = ref(1), directory = ref('/project-a')
  let settings!: ReturnType<typeof useRoleSettings>
  wrapper = mount(defineComponent({ setup() { settings = useRoleSettings(project, directory); return () => null } }))
  return { settings, project, directory }
}
const defaults = { host_key: 'claude-code', agents: { delivery_agent: { name: 'writer', instructions: 'Build', model: 'sonnet' }, review_agent: { name: 'reviewer', instructions: 'Review' } } }
const tracker = (project_id = 1, delivery = defaults, rev = 7) => ({ project_id, rev, config_json: { delivery } })
function reads() {
  vi.mocked(callOperation).mockImplementation(async (name, args) => name === 'tracker.get'
    ? { tracker: args.projection === 'ui' ? { ...tracker(), config_json: null } : tracker() }
    : { project_id: 1, data: { role_settings: { role: (args.role_patch as { role: string }).role, project_dir: '/project-a', effective: { name: 'writer', host_key: 'claude-code', model: 'sonnet' } } } })
}
afterEach(() => { wrapper?.unmount(); vi.resetAllMocks() })
describe('role defaults ownership', () => {
  it('retains each role and host draft, merges only edits and checks durable readback', async () => {
    reads()
    const { settings } = setup()
    await settings.load(); await flushPromises()
    expect(settings.draft.value.model).toBe('sonnet')
    settings.set('instructions', 'Unsaved build')
    settings.changeHost('grok'); settings.set('model', 'grok-4.7')
    settings.changeHost('claude-code')
    expect(settings.draft.value.instructions).toBe('Unsaved build')
    settings.selectRole('review_agent'); settings.set('instructions', 'Unsaved review')
    settings.selectRole('delivery_agent'); await flushPromises()
    expect(settings.draft.value.instructions).toBe('Unsaved build')
    const saved = { ...defaults, agents: { ...defaults.agents, delivery_agent: { ...defaults.agents.delivery_agent, instructions: 'Unsaved build' } } }
    vi.mocked(callOperation).mockResolvedValueOnce({ project_id: 1, data: { ...tracker(1, saved, 8), role_settings: { role: 'delivery_agent', project_dir: '/project-a', effective: { host_key: 'claude-code' } } } })
    await settings.save()
    expect(callOperation).toHaveBeenLastCalledWith('tracker.configureDelivery', {
      project_id: 1, project_dir: '/project-a', expected_revision: 7,
      role_patch: { role: 'delivery_agent', settings: { instructions: 'Unsaved build' } },
    })
    expect(settings.roleDirty.value).toBe(false)
    expect(settings.dirty.value).toBe(true) // The review draft survives the delivery save.
    expect(settings.message.value).toContain('Saved for fresh invocations')
  })
  it('retains a rejected save and requires an explicit reload before another write', async () => {
    reads(); const { settings } = setup(); await settings.load(); await flushPromises()
    settings.set('model', 'new-model')
    vi.mocked(callOperation).mockRejectedValueOnce(new Error('Stale revision'))
    await settings.save()
    expect(settings.draft.value.model).toBe('new-model')
    expect(settings.ready.value).toBe(false)
    expect(settings.message.value).toBe('')
    await settings.load(); await flushPromises()
    expect(settings.draft.value.model).toBe('new-model')
    expect(settings.ready.value).toBe(true)
  })
  it('restores the selected app after the inherited project app changes on reload', async () => {
    reads(); const { settings } = setup(); await settings.load(); await flushPromises()
    settings.set('instructions', 'Claude draft')
    settings.changeHost('grok')
    vi.mocked(callOperation).mockResolvedValueOnce({ tracker: tracker(1, { ...defaults, host_key: 'grok' }, 8) })
    await settings.load(); await flushPromises()
    settings.changeHost('claude-code')
    expect(settings.draft.value.host_key).toBe('claude-code')
    expect(settings.draft.value.instructions).toBe('Claude draft')
    settings.changeHost('')
    expect(settings.draft.value.host_key).toBeUndefined()
    expect(settings.defaults.value.host_key).toBe('grok')
  })
  it.each(['load', 'save'] as const)('discards an old project %s response and keeps its current scope unauthorized', async operation => {
    reads(); const { settings, project, directory } = setup()
    if (operation === 'save') { await settings.load(); await flushPromises(); settings.set('model', 'new-model') }
    let resolve!: (value: unknown) => void
    vi.mocked(callOperation).mockReturnValueOnce(new Promise(done => { resolve = done }))
    const pending = operation === 'save' ? settings.save() : settings.load()
    project.value = 2; directory.value = '/project-b'
    resolve(operation === 'save' ? { project_id: 1, data: tracker() } : { tracker: tracker() })
    await pending; await flushPromises()
    expect(settings.ready.value).toBe(false)
    expect(settings.message.value).toBe('')
    expect(settings.defaults.value).toEqual(operation === 'save' ? defaults : {})
  })
  it('ignores late effective-role reads and rejects foreign successful readback', async () => {
    reads(); const { settings } = setup(); await settings.load(); await flushPromises()
    let resolve!: (value: unknown) => void
    vi.mocked(callOperation).mockReturnValueOnce(new Promise(done => { resolve = done }))
    settings.selectRole('review_agent')
    settings.selectRole('main_agent'); await flushPromises()
    resolve({ project_id: 1, data: { role_settings: { role: 'review_agent', project_dir: '/project-a', effective: { model: 'old-review' } } } })
    await flushPromises()
    expect(settings.effective.value.review_agent).toBeUndefined()
    settings.set('model', 'main-model')
    vi.mocked(callOperation).mockResolvedValueOnce({ project_id: 2, data: tracker(2) })
    await settings.save()
    expect(settings.message.value).toBe('')
    expect(settings.draft.value.model).toBe('main-model')
    expect(settings.ready.value).toBe(false)
  })
})
