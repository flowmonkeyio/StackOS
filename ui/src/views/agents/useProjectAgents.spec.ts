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
// Actual projectAgent REST denial envelope from _service / _error, not a 403 substitution.
const missingScope = () => new ApiError('Project agent source requires repair.', 422, {
  detail: 'Project agent source requires repair.', code: -32602, retryable: false,
  data: { code: 'missing_scope', repair: 'Choose an accessible folder bound to this project.' },
  retry_after: null, hint: null,
})
function setup(onDraftReplaced?: (fields: AgentFields | null) => void) {
  let agents!: ReturnType<typeof useProjectAgents>
  const id = ref(1), dir = ref('/fixture')
  const wrapper = mount(defineComponent({ setup() { agents = useProjectAgents(id, dir, onDraftReplaced); return () => null } }))
  cleanup = () => wrapper.unmount()
  return { agents, id, dir }
}
describe('native project agents', () => {
  it.each(['list', 'get', 'create', 'update'])('clears retained authority after the real %s missing_scope denial', async operation => {
    const { agents } = setup(); await flushPromises(); await agents.edit(definition())
    agents.draft.value!.description = 'Retained until confirmed scope loss'
    if (operation === 'create') { agents.add(); agents.draft.value!.name = 'new_agent' }
    vi.mocked(callOperation).mockRejectedValueOnce(missingScope())
    if (operation === 'list') await agents.refresh()
    else if (operation === 'get') await agents.edit(definition())
    else await agents.save()
    expect(vi.mocked(callOperation).mock.calls.some(([name]) => name === `projectAgent.${operation}`)).toBe(true)
    expect(agents.items.value).toEqual([])
    expect(agents.selected.value).toBeNull()
    expect(agents.draft.value).toBeNull()
    expect(agents.ready.value).toBe(false)
    expect(agents.error.value).toContain('missing_scope')
    const calls = vi.mocked(callOperation).mock.calls.length
    await agents.save(); await agents.edit(definition())
    expect(vi.mocked(callOperation).mock.calls).toHaveLength(calls)
  })
  it('does not treat an ordinary 422 source validation as scope loss', async () => {
    const { agents } = setup(); await flushPromises(); await agents.edit(definition())
    agents.draft.value!.description = 'Unchanged draft'
    vi.mocked(callOperation).mockRejectedValueOnce(new ApiError('Unsupported', 422, { detail: 'Unsupported', data: { code: 'unsupported_setting', repair: 'Use supported fields.' } }))
    await agents.save()
    expect(agents.draft.value!.description).toBe('Unchanged draft')
    expect(agents.items.value).toHaveLength(1)
    expect(agents.ready.value).toBe(true)
    expect(agents.error.value).toContain('unsupported_setting')
  })
  it('invalidates a late editor read after coded scope loss', async () => {
    const { agents } = setup(); await flushPromises(); await agents.edit(definition())
    let finishRead!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(resolve => { finishRead = resolve }))
    const pendingRead = agents.edit(definition())
    vi.mocked(callOperation).mockRejectedValueOnce(missingScope()); await agents.refresh()
    finishRead(definition()); await pendingRead
    expect(agents.items.value).toEqual([])
    expect(agents.draft.value).toBeNull()
    expect(agents.ready.value).toBe(false)
    expect(agents.error.value).toContain('missing_scope')
  })
  it('revokes retained sources and ignores a late editor read after list denial', async () => {
    const { agents } = setup(); await flushPromises(); await agents.edit(definition())
    let resolveRead!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(resolve => { resolveRead = resolve }))
    const read = agents.edit(definition())
    vi.mocked(callOperation).mockRejectedValueOnce(new ApiError('Denied', 403, { detail: 'Denied' }))
    await agents.refresh()
    resolveRead(definition()); await read
    expect(agents.draft.value).toBeNull()
    expect(agents.items.value).toEqual([])
    expect(agents.ready.value).toBe(false)
    expect(agents.error.value).toContain('Denied')
    const calls = vi.mocked(callOperation).mock.calls.length
    await agents.save()
    expect(vi.mocked(callOperation).mock.calls).toHaveLength(calls)
  })
  beforeEach(() => { vi.mocked(callOperation).mockImplementation(async (operation) => operation === 'projectAgent.list' ? { data: { project_id: 1, project_dir: '/fixture', items: [definition()] } } : definition()) })
  afterEach(() => { cleanup?.(); vi.resetAllMocks() })
  it('reads full source and sends only changed fields with both preconditions, never dispatching', async () => {
    const { agents } = setup(); await flushPromises(); await agents.edit(definition())
    expect(callOperation).toHaveBeenCalledWith('projectAgent.list', expect.objectContaining({ response_mode: 'compact' }))
    expect(agents.items.value).toEqual([definition()])
    expect(agents.draft.value?.developer_instructions).toBe(definition().fields!.developer_instructions)
    agents.draft.value!.description = 'Changed'
    vi.mocked(callOperation).mockImplementation(async operation => operation === 'projectAgent.update' ? { project_id: 1, data: { ...definition(), fields: { ...definition().fields, description: 'Changed' } } } : { data: { project_id: 1, project_dir: '/fixture', items: [definition()] } })
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
      ? new Promise(resolve => { resolveOld = resolve }) : { data: { project_id: 1, project_dir: '/second', items: [next] } })
    const { agents, dir } = setup()
    dir.value = '/second'; await agents.refresh()
    resolveOld({ data: { project_id: 1, project_dir: '/fixture', items: [definition()] } }); await flushPromises()
    expect(agents.items.value).toEqual([next]); expect(agents.error.value).toBe('')
  })
  it('ignores pending reads after cleanup without starting another scoped request', async () => {
    let resolve!: (value: unknown) => void
    vi.mocked(callOperation).mockReturnValueOnce(new Promise(done => { resolve = done }))
    const { agents, dir } = setup()
    cleanup?.(); cleanup = undefined; dir.value = '/second'
    resolve({ data: { project_id: 1, project_dir: '/fixture', items: [definition()] } }); await flushPromises()
    expect(agents.items.value).toEqual([])
    expect(vi.mocked(callOperation).mock.calls).toEqual([['projectAgent.list', expect.objectContaining({ project_dir: '/fixture' })]])
  })
  it('ignores a late save after switching projects and never writes the old draft to the next scope', async () => {
    const { agents, id, dir } = setup(); await flushPromises(); await agents.edit(definition())
    agents.draft.value!.description = 'Old project edit'
    let resolveSave!: (value: unknown) => void
    const next = { ...definition('next'), project_id: 2, project_dir: '/second' }
    vi.mocked(callOperation).mockImplementation(async operation => operation === 'projectAgent.update'
      ? new Promise(resolve => { resolveSave = resolve })
      : { data: { project_id: 2, project_dir: '/second', items: [next] } })
    const save = agents.save()
    id.value = 2; dir.value = '/second'; await agents.refresh()
    expect(agents.draft.value).toBeNull()
    resolveSave({ project_id: 1, data: { ...definition(), fields: { ...definition().fields, description: 'Old project edit' } } }); await save
    expect(agents.items.value).toEqual([next])
    expect(agents.draft.value).toBeNull(); expect(agents.message.value).toBe('')
    await agents.save()
    const writes = vi.mocked(callOperation).mock.calls.filter(([operation]) => /create|update/.test(operation))
    expect(writes).toEqual([['projectAgent.update', expect.objectContaining({ project_id: 1, project_dir: '/fixture' })]])
  })
  it('rejects a late full-source read across rapid project switches back to the original folder', async () => {
    const { agents, id, dir } = setup(); await flushPromises()
    let resolveRead!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(resolve => { resolveRead = resolve }))
    const read = agents.edit(definition())
    id.value = 2; dir.value = ''; await agents.refresh()
    id.value = 1; dir.value = '/fixture'; await agents.refresh()
    resolveRead(definition()); await read
    expect(agents.items.value).toEqual([definition()])
    expect(agents.draft.value).toBeNull(); expect(agents.reading.value).toBe(false)
    expect(callOperation).not.toHaveBeenCalledWith('projectAgent.update', expect.anything())
  })
})
