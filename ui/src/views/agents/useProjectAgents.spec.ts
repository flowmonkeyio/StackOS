// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { ApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { blankAgent, useProjectAgents, type AgentDefinition, type AgentFields } from './useProjectAgents'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
export const definition = (name = 'research'): AgentDefinition => ({ project_id: 1, project_dir: '/fixture', source_ref: { host_key: 'codex', native_name: name, source_path: `.codex/agents/${name}.toml` }, digest: 'a'.repeat(64), registration_digest: null, fields: { ...blankAgent(), name, description: 'Research description', developer_instructions: 'Complete instructions\n'.repeat(100), model: 'native-model', model_reasoning_effort: 'high' }, capabilities: { read: true, edit: true }, unsupported_fields: ['future_option'] })
let cleanup: (() => void) | undefined
function setup(onDraftReplaced?: (fields: AgentFields | null) => void) {
  let agents!: ReturnType<typeof useProjectAgents>
  const id = ref(1), dir = ref('/fixture')
  const wrapper = mount(defineComponent({ setup() { agents = useProjectAgents(id, dir, onDraftReplaced); return () => null } }))
  cleanup = () => wrapper.unmount()
  return { agents, id, dir }
}
describe('native project agents', () => {
  beforeEach(() => { vi.mocked(callOperation).mockImplementation(async (operation) => operation === 'projectAgent.list' ? { project_id: 1, project_dir: '/fixture', items: [definition()] } : definition()) })
  afterEach(() => { cleanup?.(); vi.resetAllMocks() })
  it('reads full source and sends only changed fields with both preconditions, never dispatching', async () => {
    const { agents } = setup(); await flushPromises(); await agents.edit(definition())
    expect(callOperation).toHaveBeenCalledWith('projectAgent.list', expect.objectContaining({ response_mode: 'compact' }))
    expect(agents.draft.value?.developer_instructions).toBe(definition().fields!.developer_instructions)
    agents.draft.value!.description = 'Changed'
    vi.mocked(callOperation).mockImplementation(async operation => operation === 'projectAgent.update' ? { project_id: 1, data: { ...definition(), fields: { ...definition().fields, description: 'Changed' } } } : { project_id: 1, project_dir: '/fixture', items: [definition()] })
    await agents.save()
    expect(callOperation).toHaveBeenCalledWith('projectAgent.update', expect.objectContaining({ expected_digest: 'a'.repeat(64), expected_registration_digest: null, patch: { description: 'Changed' } }))
    expect(agents.dirty.value).toBe(false)
    expect(agents.message.value).toBe('Saved in the project file.')
    expect(vi.mocked(callOperation).mock.calls.every(([operation]) => operation.startsWith('projectAgent.'))).toBe(true)
  })
  it('retains complete drafts and exposes stale-source and referenced-name repairs', async () => {
    const { agents } = setup(); await flushPromises(); await agents.edit(definition())
    agents.draft.value!.name = 'new_name'
    for (const code of ['stale_source', 'referenced_identity']) {
      vi.mocked(callOperation).mockRejectedValueOnce(new ApiError('Conflict', 409, { detail: 'Conflict', data: { code, repair: 'Reassign saved references before changing the native name.' } }))
      await agents.save()
      expect(agents.draft.value!.name).toBe('new_name')
      expect(agents.error.value).toContain(code)
      expect(agents.dirty.value).toBe(true)
    }
  })
  it('creates a real-source request with inherited fields and accepts exact readback', async () => {
    const { agents } = setup(); await flushPromises(); agents.add()
    agents.draft.value = { ...blankAgent(), name: 'research', description: 'Research', developer_instructions: 'Full text' }
    const saved = { ...definition(), fields: { ...agents.draft.value } }
    vi.mocked(callOperation).mockResolvedValueOnce({ project_id: 1, data: saved })
    await agents.save()
    expect(callOperation).toHaveBeenCalledWith('projectAgent.create', expect.objectContaining({ fields: saved.fields }))
    expect(agents.selected.value!.fields!.model).toBeNull()
  })
  it('rejects mismatched save readback while preserving the draft', async () => {
    const { agents } = setup(); await flushPromises(); await agents.edit(definition())
    agents.draft.value!.model = null
    vi.mocked(callOperation).mockResolvedValueOnce({ project_id: 1, data: definition() })
    await agents.save()
    expect(agents.error.value).toContain('differs from the draft')
    expect(agents.draft.value!.model).toBeNull()
  })
  it('discards late reads after folder changes', async () => {
    let resolve!: (value: unknown) => void
    const { agents, dir } = setup(); await flushPromises()
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(done => { resolve = done }))
    const edit = agents.edit(definition()); dir.value = ''; await flushPromises()
    resolve(definition()); await edit
    expect(agents.draft.value).toBeNull()
    expect(agents.items.value).toEqual([])
  })
  it('patches only changed native restriction leaves and explicitly removes cleared leaves', async () => {
    const row = definition()
    row.fields!.features = { shell_tool: false, apps: false }
    const { agents } = setup(); await flushPromises()
    vi.mocked(callOperation).mockResolvedValueOnce(row)
    await agents.edit(row)
    agents.draft.value!.features = { shell_tool: false }
    vi.mocked(callOperation).mockResolvedValueOnce({ project_id: 1, data: { ...row, fields: { ...row.fields, features: { shell_tool: false } } } })
    await agents.save()
    expect(callOperation).toHaveBeenCalledWith('projectAgent.update', expect.objectContaining({ patch: { features: { apps: null } } }))
  })
  it.each(['features', 'skills'] as const)('accepts native null readback after removing the final %s restriction and uses the new digest', async key => {
    const row = definition()
    row.fields![key] = key === 'features' ? { shell_tool: false } : { include_instructions: false }
    const { agents } = setup(); await flushPromises()
    vi.mocked(callOperation).mockResolvedValueOnce(row); await agents.edit(row)
    agents.draft.value![key] = {}
    const readback = { ...row, digest: 'b'.repeat(64), fields: { ...row.fields!, [key]: null } }
    vi.mocked(callOperation).mockResolvedValueOnce({ project_id: 1, data: readback })
    await agents.save()
    expect(agents.error.value).toBe('')
    expect(agents.dirty.value).toBe(false)
    expect(agents.draft.value![key]).toBeNull()
    expect(agents.selected.value!.digest).toBe(readback.digest)
    const leaf = key === 'features' ? 'shell_tool' : 'include_instructions'
    expect(callOperation).toHaveBeenCalledWith('projectAgent.update', expect.objectContaining({ patch: { [key]: { [leaf]: null } } }))
    agents.draft.value!.description = 'Second edit'
    vi.mocked(callOperation).mockResolvedValueOnce({ project_id: 1, data: { ...readback, fields: { ...readback.fields, description: 'Second edit' } } })
    await agents.save()
    expect(callOperation).toHaveBeenLastCalledWith('projectAgent.list', expect.anything())
    expect(callOperation).toHaveBeenCalledWith('projectAgent.update', expect.objectContaining({ expected_digest: readback.digest, patch: { description: 'Second edit' } }))
    expect(agents.error.value).toBe('')
  })
  it.each(['features', 'skills'] as const)('accepts empty %s on create as inherited native readback', async key => {
    const { agents } = setup(); await flushPromises(); agents.add()
    agents.draft.value = { ...blankAgent(), name: 'research', description: 'Research', developer_instructions: 'Full text', [key]: {} }
    const readback = { ...definition(), fields: { ...agents.draft.value, [key]: null } }
    vi.mocked(callOperation).mockResolvedValueOnce({ project_id: 1, data: readback }); await agents.save()
    expect(agents.error.value).toBe(''); expect(agents.dirty.value).toBe(false)
    expect(agents.selected.value?.digest).toBe(readback.digest)
  })
  it.each(['features', 'skills'] as const)('accepts explicit %s inheritance but rejects a different nonempty native setting', async key => {
    const row = definition()
    row.fields![key] = key === 'features' ? { shell_tool: false } : { include_instructions: false }
    const { agents } = setup(); await flushPromises()
    vi.mocked(callOperation).mockResolvedValueOnce(row); await agents.edit(row)
    agents.draft.value![key] = null
    vi.mocked(callOperation).mockResolvedValueOnce({ project_id: 1, data: { ...row, fields: { ...row.fields, [key]: null } } }); await agents.save()
    expect(agents.error.value).toBe('')
    agents.draft.value![key] = {}
    vi.mocked(callOperation).mockResolvedValueOnce({ project_id: 1, data: row }); await agents.save()
    expect(agents.error.value).toContain('differs from the draft')
    expect(agents.dirty.value).toBe(true)
  })
  it('notifies editor buffers only when a definition is replaced, preserving failed-save drafts', async () => {
    const replaced = vi.fn()
    const { agents } = setup(replaced); await flushPromises(); await agents.edit(definition())
    replaced.mockClear()
    agents.draft.value!.description = 'Unsaved'
    vi.mocked(callOperation).mockRejectedValueOnce(new Error('Conflict')); await agents.save()
    expect(replaced).not.toHaveBeenCalled()
    vi.mocked(callOperation).mockResolvedValueOnce({ project_id: 1, data: { ...definition(), fields: { ...definition().fields, description: 'Unsaved' } } }); await agents.save()
    expect(replaced).toHaveBeenCalledWith(expect.objectContaining({ description: 'Unsaved' }))
    agents.close()
    expect(replaced).toHaveBeenLastCalledWith(null)
    agents.add()
    expect(replaced).toHaveBeenLastCalledWith(blankAgent())
  })
  it('refreshes an explicit folder boundary and rejects the previous list response', async () => {
    let resolveOld!: (value: unknown) => void
    const next = { ...definition('next'), project_dir: '/second' }
    vi.mocked(callOperation).mockImplementation(async (_operation, args) => args.project_dir === '/fixture'
      ? new Promise(resolve => { resolveOld = resolve }) : { project_id: 1, project_dir: '/second', items: [next] })
    const { agents, dir } = setup()
    dir.value = '/second'; await agents.refresh()
    resolveOld({ project_id: 1, project_dir: '/fixture', items: [definition()] }); await flushPromises()
    expect(agents.items.value).toEqual([next]); expect(agents.error.value).toBe('')
  })
  it('ignores pending reads after cleanup without starting another scoped request', async () => {
    let resolve!: (value: unknown) => void
    vi.mocked(callOperation).mockReturnValueOnce(new Promise(done => { resolve = done }))
    const { agents, dir } = setup()
    cleanup?.(); cleanup = undefined; dir.value = '/second'
    resolve({ project_id: 1, project_dir: '/fixture', items: [definition()] }); await flushPromises()
    expect(agents.items.value).toEqual([])
    expect(vi.mocked(callOperation).mock.calls).toEqual([['projectAgent.list', expect.objectContaining({ project_dir: '/fixture' })]])
  })
})
