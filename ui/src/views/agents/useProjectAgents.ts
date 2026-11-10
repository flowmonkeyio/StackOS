import { computed, onBeforeUnmount, ref, watch, type Ref } from 'vue'
import type { components } from '@/api'
import { ApiError, formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'

export type AgentDefinition = components['schemas']['AgentDefinition']
export type AgentFields = components['schemas']['AgentFields']
// The operation's compact projection omits only the full instruction body.
export type AgentSummary = Omit<AgentDefinition, 'fields'> & { fields: Omit<AgentFields, 'developer_instructions'> | null }
export type AgentSourceRef = components['schemas']['AgentSourceRef']
type AgentPatch = components['schemas']['AgentPatch']
export const agentIdentity = (source: AgentSourceRef) => JSON.stringify(source)
export const blankAgent = (): AgentFields => ({ name: '', description: '', developer_instructions: '', model: null, model_reasoning_effort: null, model_reasoning_summary: null, model_verbosity: null, features: null, skills: null })
const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const canonical = (value: unknown): string => {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (value && typeof value === 'object') return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, child]) => `${JSON.stringify(key)}:${canonical(child)}`).join(',')}}`
  return JSON.stringify(value)
}
function changedFields(before: Record<string, unknown>, after: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries([...new Set([...Object.keys(before), ...Object.keys(after)])].flatMap(key => {
    const previous = before[key], next = after[key]
    if (canonical(previous) === canonical(next)) return []
    if (previous && next && typeof previous === 'object' && typeof next === 'object' && !Array.isArray(previous) && !Array.isArray(next)) return [[key, changedFields(previous as Record<string, unknown>, next as Record<string, unknown>)]]
    return [[key, next ?? null]]
  }))
}

export function agentError(cause: unknown): string {
  const data = cause instanceof ApiError ? cause.data as { code?: string; repair?: string } | undefined : undefined
  return [formatApiError(cause), data?.code, data?.repair].filter(Boolean).join(' ')
}

export function useProjectAgents(projectId: Ref<number>, projectDir: Ref<string>) {
  const items = ref<AgentSummary[]>([])
  const diagnostics = ref<Record<string, unknown>[]>([])
  const loading = ref(false)
  const reading = ref(false)
  const saving = ref(false)
  const error = ref('')
  const message = ref('')
  const selected = ref<AgentDefinition | null>(null)
  const draft = ref<AgentFields | null>(null)
  const original = ref<AgentFields | null>(null)
  let generation = 0
  let readGeneration = 0
  const dirty = computed(() => draft.value !== null && canonical(draft.value) !== canonical(original.value))
  const scope = () => ({ project_id: projectId.value, project_dir: projectDir.value, host_key: 'codex' })

  function close() { ++readGeneration; reading.value = false; selected.value = null; draft.value = null; original.value = null; message.value = ''; error.value = '' }
  async function refresh() {
    const request = ++generation
    error.value = ''
    if (!projectDir.value) { items.value = []; diagnostics.value = []; loading.value = false; return }
    loading.value = true
    try {
      const result = await callOperation<Omit<components['schemas']['AgentList'], 'items'> & { items?: AgentSummary[] }>('projectAgent.list', { ...scope(), response_mode: 'compact' })
      if (request !== generation) return
      if (result.project_id !== projectId.value || result.project_dir !== projectDir.value) throw new Error('Agent sources belong to a different project folder.')
      items.value = result.items ?? []
      diagnostics.value = result.diagnostics ?? []
    } catch (cause) { if (request === generation) error.value = agentError(cause) }
    finally { if (request === generation) loading.value = false }
  }
  async function edit(row: Pick<AgentDefinition, 'source_ref'>) {
    const request = ++readGeneration
    const currentScope = generation
    reading.value = true; error.value = ''; message.value = ''
    try {
      const result = await callOperation<AgentDefinition>('projectAgent.get', { ...scope(), source_ref: row.source_ref })
      if (request !== readGeneration || currentScope !== generation) return
      if (result.project_id !== projectId.value || result.project_dir !== projectDir.value || agentIdentity(result.source_ref) !== agentIdentity(row.source_ref)) throw new Error('Agent readback identity changed. Refresh the list.')
      if (!result.fields) throw new Error('This definition is unavailable. Repair the project file and refresh.')
      selected.value = result; draft.value = copy(result.fields); original.value = copy(result.fields)
    } catch (cause) { if (request === readGeneration && currentScope === generation) error.value = agentError(cause) }
    finally { if (request === readGeneration) reading.value = false }
  }
  function add() { close(); draft.value = blankAgent() }
  async function save() {
    if (!draft.value || saving.value || !projectDir.value) return
    const currentScope = generation
    const fields = copy(draft.value)
    const row = selected.value
    saving.value = true; error.value = ''; message.value = ''
    try {
      const patch = changedFields(original.value ?? {}, fields) as AgentPatch
      const result = await callOperation<{ data: AgentDefinition; project_id: number }>(row ? 'projectAgent.update' : 'projectAgent.create', {
        ...scope(), ...(row ? { source_ref: row.source_ref, expected_digest: row.digest, expected_registration_digest: row.registration_digest, patch } : { fields }),
      })
      if (currentScope !== generation) return
      if (result.project_id !== projectId.value || result.data.project_id !== projectId.value || result.data.project_dir !== projectDir.value || !result.data.fields || !result.data.digest || result.data.source_ref.native_name !== fields.name || (row && result.data.source_ref.source_path !== row.source_ref.source_path)) throw new Error('Save readback is unavailable. Refresh before retrying.')
      // The native write owner returns its exact disk readback, including preserved settings.
      if (canonical(result.data.fields) !== canonical(fields)) throw new Error('Saved source differs from the draft. Refresh and reconcile before retrying.')
      selected.value = result.data; draft.value = copy(result.data.fields); original.value = copy(result.data.fields)
      message.value = 'Saved in the project file.'
      await refresh()
    } catch (cause) { if (currentScope === generation) error.value = agentError(cause) }
    finally { saving.value = false }
  }
  watch([projectId, projectDir], () => { ++generation; close(); items.value = []; diagnostics.value = []; void refresh() }, { immediate: true })
  onBeforeUnmount(() => { ++generation; ++readGeneration })
  return { items, diagnostics, loading, reading, saving, error, message, selected, draft, dirty, refresh, edit, add, close, save }
}
