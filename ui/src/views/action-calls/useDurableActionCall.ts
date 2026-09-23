import { ref } from 'vue'

import type {
  SchemaActionCallDurableItemsOut,
  SchemaDurableActionItemOut,
  SchemaDurableActionJobOut,
  SchemaWriteEnvelopeDurableActionJobOut,
} from '@/api'
import { ApiError, formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'

export type DurableActionJob = SchemaDurableActionJobOut
export type DurableActionItem = SchemaDurableActionItemOut
export type DurableActionCallItems = SchemaActionCallDurableItemsOut

export type DurableActionControl = 'pause' | 'resume' | 'cancel' | 'retry'

export interface DurableActionControlRequest {
  action: DurableActionControl
  itemIds?: number[]
}

interface Selection {
  projectId: number
  actionCallId: number
}

/**
 * Own the one extra read/mutation lifecycle that begins after an audit row is
 * selected. The ledger remains an ordinary ActionCall reader; only a durable
 * parent call asks the registered lifecycle operations for sealed job state.
 */
export function useDurableActionCall() {
  const durable = ref<DurableActionCallItems | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const controlling = ref<DurableActionControl | null>(null)
  let selected: Selection | null = null
  let generation = 0

  async function load(projectId: number, actionCallId: number): Promise<void> {
    if (
      selected === null ||
      selected.projectId !== projectId ||
      selected.actionCallId !== actionCallId
    ) {
      selected = { projectId, actionCallId }
    }
    const request = ++generation
    loading.value = true
    error.value = null
    durable.value = null
    try {
      const result = await callOperation<unknown>('actionCall.items', {
        project_id: projectId,
        action_call_id: actionCallId,
      })
      if (request !== generation) return
      if (!isDurableActionCallItems(result)) {
        throw new Error('durable delivery details returned an invalid response')
      }
      durable.value = result
    } catch (cause) {
      if (request !== generation) return
      // An ordinary audit call has no durable job. The drawer remains useful
      // for its normal action-call facts without presenting a false warning.
      if (cause instanceof ApiError && cause.status === 404) return
      error.value = formatApiError(cause, 'failed to load durable delivery details')
    } finally {
      if (request === generation) loading.value = false
    }
  }

  async function refresh(): Promise<void> {
    if (!selected) return
    await load(selected.projectId, selected.actionCallId)
  }

  async function control(
    request: DurableActionControlRequest,
    intentSummary?: string,
  ): Promise<boolean> {
    if (controlling.value !== null) return false
    const { action } = request
    const current = selected
    const currentDurable = durable.value
    if (!current || !currentDurable) return false
    const itemIds = normalizeItemIds(request.itemIds)
    if (action === 'retry' && itemIds.length === 0) {
      error.value = 'Select at least one delivery item to retry.'
      return false
    }
    controlling.value = action
    error.value = null
    try {
      const argumentsJson: Record<string, unknown> = {
        project_id: current.projectId,
        action_call_id: current.actionCallId,
      }
      if (action === 'resume' || action === 'retry') {
        argumentsJson.confirm_direct = true
        argumentsJson.intent_summary =
          intentSummary?.trim() || `Operator confirmed durable delivery ${action}.`
      }
      if (action === 'retry') {
        argumentsJson.item_ids = itemIds
      }
      const result = await callOperation<SchemaWriteEnvelopeDurableActionJobOut>(
        `actionCall.${action}`,
        argumentsJson,
      )
      if (selected !== current) return false
      durable.value = { ...currentDurable, job: result.data }
      await refresh()
      return true
    } catch (cause) {
      if (selected !== current) return false
      error.value = formatApiError(cause, `failed to ${action} durable delivery`)
      return false
    } finally {
      if (selected === current) controlling.value = null
    }
  }

  function clear(): void {
    generation += 1
    selected = null
    durable.value = null
    loading.value = false
    error.value = null
    controlling.value = null
  }

  return { durable, loading, error, controlling, load, refresh, control, clear }
}

function normalizeItemIds(value: number[] | undefined): number[] {
  if (!value) return []
  return [...new Set(value.filter((itemId) => Number.isSafeInteger(itemId) && itemId > 0))]
}

function isDurableActionCallItems(value: unknown): value is DurableActionCallItems {
  if (!isRecord(value) || !Array.isArray(value.items) || !isRecord(value.job)) return false
  const job = value.job
  return (
    typeof value.action_call_id === 'number' &&
    typeof value.count === 'number' &&
    typeof job.id === 'number' &&
    typeof job.item_count === 'number' &&
    typeof job.state === 'string'
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
