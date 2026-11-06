import { computed, ref, type ComputedRef } from 'vue'

import { useProjectScopedLoader } from '@/composables/useProjectScopedLoader'
import { formatApiError } from '@/lib/client'
import type { DesktopHostStatusesResult, DesktopMcpHostStatus } from '@/lib/desktop'
import { callOperation } from '@/lib/operations'
import type { DeliveryDefaults, TrackerSummary } from '@/lib/task-tracker/types'

export type AgentRole = 'delivery_agent' | 'review_agent'

function copy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function deliveryOf(tracker: TrackerSummary): DeliveryDefaults {
  return copy((tracker.config_json?.delivery ?? {}) as DeliveryDefaults)
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (value !== null && typeof value === 'object') {
    return `{${Object.entries(value)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
      .join(',')}}`
  }
  return JSON.stringify(value)
}

export function useAgentDefaults(projectId: ComputedRef<number>) {
  const draft = ref<DeliveryDefaults>({})
  const saved = ref<DeliveryDefaults>({})
  const hosts = ref<DesktopMcpHostStatus[]>([])
  const tracker = ref<TrackerSummary | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')
  const message = ref('')
  let generation = 0
  let disposed = false

  const dirty = computed(() => JSON.stringify(draft.value) !== JSON.stringify(saved.value))
  const ready = computed(() => tracker.value?.project_id === projectId.value && !loading.value)
  const canSave = computed(() => ready.value && dirty.value && !saving.value)

  function applyTracker(value: TrackerSummary, id: number): void {
    if (value.project_id !== id)
      throw new Error('The defaults response belongs to another project. Reload this project.')
    tracker.value = value
    saved.value = deliveryOf(value)
    draft.value = copy(saved.value)
  }

  async function load(): Promise<void> {
    const id = projectId.value
    const request = ++generation
    tracker.value = null
    draft.value = {}
    saved.value = {}
    hosts.value = []
    error.value = ''
    message.value = ''
    saving.value = false
    loading.value = true
    try {
      const [status, capabilities] = await Promise.all([
        callOperation<{ tracker: TrackerSummary }>('tracker.status', { project_id: id }),
        callOperation<DesktopHostStatusesResult>('hostMcp.status', {}),
      ])
      if (disposed || request !== generation) return
      if (!Array.isArray(capabilities.items))
        throw new Error('Agent execution choices are unavailable. Reload to try again.')
      applyTracker(status.tracker, id)
      hosts.value = capabilities.items
    } catch (err) {
      if (!disposed && request === generation)
        error.value = formatApiError(err, 'Could not load agent defaults.')
    } finally {
      if (!disposed && request === generation) loading.value = false
    }
  }

  function setField(role: AgentRole, field: string, value: string | number | null): void {
    if (!ready.value || saving.value) return
    const text = String(value ?? '')
    draft.value = {
      ...draft.value,
      [role]: { ...draft.value[role], [field]: text === '' ? null : text },
    }
    message.value = ''
  }

  async function save(): Promise<void> {
    if (!canSave.value) return
    const id = projectId.value
    const request = generation
    saving.value = true
    error.value = ''
    message.value = ''
    try {
      const result = await callOperation<{ data: TrackerSummary; project_id: number }>(
        'tracker.configureDelivery',
        { project_id: id, delivery: copy(draft.value) },
      )
      if (disposed || request !== generation) return
      if (result.project_id !== id || result.data.project_id !== id)
        throw new Error('The save response belongs to another project. Reload to verify settings.')
      // Read through the canonical owner again; never report a write as proof of readback.
      const status = await callOperation<{ tracker: TrackerSummary }>('tracker.status', {
        project_id: id,
      })
      if (disposed || request !== generation) return
      if (canonical(deliveryOf(status.tracker)) !== canonical(deliveryOf(result.data))) {
        throw new Error(
          'Saved defaults changed before readback. Reload to verify the current settings.',
        )
      }
      applyTracker(status.tracker, id)
      message.value = `Saved and reloaded project defaults (revision ${status.tracker.rev}).`
    } catch (err) {
      if (!disposed && request === generation)
        error.value = formatApiError(
          err,
          'Could not save agent defaults. Reload to verify before retrying.',
        )
    } finally {
      if (!disposed && request === generation) saving.value = false
    }
  }

  // Project-keyed component replacement owns scope changes; the shared loader
  // starts this instance and invalidates its requests when the scope exits.
  useProjectScopedLoader({
    projectId,
    load,
    onScopeExit: () => {
      disposed = true
      generation += 1
    },
  })

  return {
    draft,
    saved,
    hosts,
    tracker,
    loading,
    saving,
    error,
    message,
    dirty,
    ready,
    canSave,
    load,
    setField,
    save,
  }
}
