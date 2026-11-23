import { computed, onBeforeUnmount, ref, type Ref } from 'vue'
import type { components } from '@/api'
import { callOperation } from '@/lib/operations'
import { ApiError } from '@/lib/client'
import { agentError } from './useProjectAgents'

export type HarnessRole = 'delivery_agent' | 'review_agent' | 'main_agent'
export type RoleFields = Partial<components['schemas']['TaskDeliveryAgentOverride']>
type Defaults = Partial<components['schemas']['TaskDeliveryDefaults']>
type Tracker = components['schemas']['TrackerSummaryOut']
export type EffectiveRole = RoleFields & { native_settings?: Record<string, unknown>; setting_sources?: Record<string, string> }
type Receipt = { role_settings: { role: HarnessRole; effective: EffectiveRole; project_dir: string } }
const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null)
const changes = (before: RoleFields, after: RoleFields): RoleFields => Object.fromEntries(
  [...new Set([...Object.keys(before), ...Object.keys(after)])].flatMap(key => {
    const field = key as keyof RoleFields
    return same(before[field], after[field]) ? [] : [[key, after[field] ?? null]]
  }),
)

export function useRoleSettings(projectId: Ref<number>, projectDir: Ref<string>, folderReady?: Ref<boolean>) {
  const scope = ref({ project_id: projectId.value, project_dir: projectDir.value })
  const role = ref<HarnessRole>('delivery_agent')
  const defaults = ref<Defaults>({})
  const revision = ref<number | null>(null)
  const drafts = ref<Partial<Record<HarnessRole, RoleFields>>>({})
  const originals = ref<Partial<Record<HarnessRole, RoleFields>>>({})
  const effective = ref<Partial<Record<HarnessRole, EffectiveRole>>>({})
  const error = ref('')
  const effectiveError = ref('')
  const message = ref('')
  const loading = ref(false)
  const saving = ref(false)
  const checking = ref(false)
  const authorized = ref(false)
  let disposed = false
  let generation = 0
  let previewGeneration = 0
  const ownsScope = () => !disposed && scope.value.project_id === projectId.value && scope.value.project_dir === projectDir.value
  const scopeChanged = computed(() => !!scope.value.project_dir && !ownsScope())
  const ready = computed(() => folderReady?.value !== false && ownsScope() && authorized.value && revision.value !== null && !loading.value && !saving.value)
  const draft = computed<RoleFields>(() => drafts.value[role.value] ?? {})
  const dirty = computed(() => Object.entries(drafts.value).some(([key, value]) => Object.keys(changes(originals.value[key as HarnessRole] ?? {}, value)).length > 0))
  const roleDirty = computed(() => Object.keys(changes(originals.value[role.value] ?? {}, draft.value)).length > 0)
  const hostDrafts = new Map<string, RoleFields>()
  function saved(fields: Defaults, selected: HarnessRole): RoleFields {
    if (selected === 'main_agent') return copy(fields.main_agent ?? {})
    return copy({ ...fields[selected], ...fields.agents?.[selected] })
  }
  function rejectAuthority() { authorized.value = false; effective.value = {}; checking.value = false; ++previewGeneration }
  async function checkSaved() {
    if (!ready.value) return
    const selected = role.value, request = ++previewGeneration
    const basis = revision.value
    checking.value = true; effectiveError.value = ''
    delete effective.value[selected]
    try {
      const result = await callOperation<{ data: Receipt; project_id: number }>('tracker.configureDelivery', {
        ...scope.value, expected_revision: basis, role_patch: { role: selected, settings: {} }, preview: true,
      })
      if (!ownsScope() || request !== previewGeneration || basis !== revision.value) return
      if (result.project_id !== scope.value.project_id || result.data.role_settings.role !== selected || result.data.role_settings.project_dir !== scope.value.project_dir) throw new Error('Effective settings belong to another project or role.')
      effective.value[selected] = result.data.role_settings.effective
    } catch (cause) {
      if (ownsScope() && request === previewGeneration) {
        effectiveError.value = agentError(cause)
        if (cause instanceof ApiError && ([401, 403].includes(cause.status) || (cause.data as { code?: string } | undefined)?.code === 'missing_scope')) rejectAuthority()
      }
    } finally { if (request === previewGeneration) checking.value = false }
  }
  async function load() {
    if (folderReady?.value === false || !projectDir.value) return
    // Mount with the project, then bind once its first folder lookup completes.
    // Retain that identity and drafts through later failed refreshes.
    if (!scope.value.project_dir && scope.value.project_id === projectId.value) scope.value = { project_id: projectId.value, project_dir: projectDir.value }
    if (!ownsScope() || loading.value || saving.value) return
    const request = ++generation
    loading.value = true; error.value = ''; message.value = ''; rejectAuthority()
    try {
      const result = await callOperation<{ tracker: Tracker }>('tracker.get', {
        project_id: scope.value.project_id, response_mode: 'raw', task_index_only: true, include_graph: false,
      })
      if (!ownsScope() || request !== generation) return
      if (result.tracker.project_id !== scope.value.project_id || !Number.isInteger(result.tracker.rev)) throw new Error('Settings readback belongs to another project.')
      const next = (result.tracker.config_json?.delivery ?? {}) as Defaults
      for (const selected of ['delivery_agent', 'review_agent', 'main_agent'] as const) {
        // An explicit reload rebases only edited fields; late reads never erase a draft.
        const edits = changes(originals.value[selected] ?? {}, drafts.value[selected] ?? {})
        const original = saved(next, selected)
        originals.value[selected] = original
        drafts.value[selected] = { ...copy(original), ...edits }
      }
      defaults.value = next; revision.value = result.tracker.rev; authorized.value = true
    } catch (cause) { if (ownsScope() && request === generation) error.value = agentError(cause) }
    finally {
      if (request === generation) { loading.value = false; void checkSaved() }
    }
  }
  function selectRole(next: HarnessRole) { if (saving.value) return; role.value = next; error.value = ''; message.value = ''; void checkSaved() }
  function set(field: keyof RoleFields, value: unknown) { drafts.value[role.value] = { ...draft.value, [field]: value }; message.value = '' }
  function changeHost(host: string) {
    const previous = draft.value.host_key || ''
    if (previous === host) return
    const inherited = defaults.value.host_key || effective.value[role.value]?.host_key || 'codex'
    hostDrafts.set(`${role.value}:${previous}`, copy(draft.value))
    if (!previous) hostDrafts.set(`${role.value}:${inherited}`, copy(draft.value))
    drafts.value[role.value] = copy(hostDrafts.get(`${role.value}:${host}`) ?? {
      ...draft.value, host_key: host || null, model: null, model_reasoning_effort: null, provider: null, profile: null,
      ...(role.value === 'main_agent' ? {} : { source_ref: null, instructions: null }),
    })
    if (host && !draft.value.host_key && inherited !== host) drafts.value[role.value]!.host_key = host
    message.value = ''
  }
  async function save() {
    if (!ready.value || !roleDirty.value) return
    const selected = role.value, request = ++generation
    const fields = copy(draft.value)
    const patch = changes(originals.value[selected] ?? {}, fields)
    saving.value = true; error.value = ''; message.value = ''; ++previewGeneration; checking.value = false
    try {
      const result = await callOperation<{ data: Tracker & Receipt; project_id: number }>('tracker.configureDelivery', {
        ...scope.value, expected_revision: revision.value, role_patch: { role: selected, settings: patch },
      })
      if (!ownsScope() || request !== generation) return
      if (result.project_id !== scope.value.project_id || result.data.project_id !== scope.value.project_id || result.data.role_settings.role !== selected || result.data.role_settings.project_dir !== scope.value.project_dir || !Number.isInteger(result.data.rev)) throw new Error('Save readback is unavailable. Reload and reconcile your draft.')
      const next = result.data.config_json?.delivery as Defaults | undefined
      if (!next) throw new Error('Saved role settings are unavailable.')
      const readback = saved(next, selected)
      for (const key of Object.keys(patch) as (keyof RoleFields)[]) {
        if (!same(readback[key], fields[key])) throw new Error('Saved settings differ from your draft. Reload and reconcile before retrying.')
      }
      defaults.value = next; revision.value = result.data.rev
      originals.value[selected] = copy(readback); drafts.value[selected] = copy(readback)
      effective.value[selected] = result.data.role_settings.effective
      message.value = 'Saved for fresh invocations. Active and resumed sessions keep their frozen settings.'
    } catch (cause) {
      if (ownsScope() && request === generation) { error.value = agentError(cause); rejectAuthority() }
    } finally { if (request === generation) saving.value = false }
  }
  onBeforeUnmount(() => { disposed = true; ++generation; ++previewGeneration })
  return { role, defaults, draft, effective, effectiveError, dirty, roleDirty, ready, scopeChanged, loading, saving, checking, error, message, load, selectRole, set, changeHost, save }
}
