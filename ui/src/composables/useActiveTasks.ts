import { computed, getCurrentInstance, onBeforeUnmount, onMounted, provide, ref, type App } from 'vue'
import type { SchemaActiveTaskOut, SchemaActiveTasksOut } from '@/api'
import { callOperation } from '@/lib/operations'
import { formatApiError } from '@/lib/client'
import { markUsageSnapshot, subscribeUsageUpdates, usageSnapshotCheckpoint, type UsageFeedState } from '@/lib/usageUpdates'
import { createUsageReadOwner } from '@/lib/usageReads'
import { usageFeedKey } from '@/composables/useUsageFeed'
import { useProjectsStore } from '@/stores/projects'

// Retain only the last global and project Home per app. No transport or
// freshness survives navigation; every mount revalidates its exact scope.
const retained = new WeakMap<App, Map<'global' | 'project', { projectId: number | null; rows: SchemaActiveTaskOut[]; asOf: string }>>()

/** One mounted Home scope, one shared transport, paged bulk reads only. */
export function useActiveTasks(projectId: number | null) {
  const projects = useProjectsStore()
  const app = getCurrentInstance()!.appContext.app
  const snapshots = retained.get(app) ?? new Map()
  retained.set(app, snapshots)
  const slot = projectId === null ? 'global' : 'project'
  const previous = snapshots.get(slot)
  const cached = previous?.projectId === projectId ? previous : null
  const rows = ref<SchemaActiveTaskOut[]>(cached?.rows ?? [])
  const loading = ref(true)
  const error = ref<string | null>(null)
  const asOf = ref<string | null>(cached?.asOf ?? null)
  const transport = ref<UsageFeedState>({ status: 'connecting', receivedAt: null })
  const snapshotCurrent = ref(false)
  const feed = computed<UsageFeedState>(() => ({
    ...transport.value,
    status: snapshotCurrent.value && !error.value ? transport.value.status
      : transport.value.status === 'live' ? 'reconnecting' : transport.value.status,
  }))
  provide(usageFeedKey, feed)
  const items = computed(() => rows.value.flatMap((row) => {
    if (projects.isProjectDeleted(row.project_id)) return []
    const current = projects.getById(row.project_id)
    if (current?.cleanup_json || (projectId === null && current?.is_active === false)) return []
    return [{ ...row, project: current ?? row.project }]
  }))
  let retired = false
  let version = 0
  const reads = createUsageReadOwner()
  let unsubscribe: (() => void) | null = null

  async function drain(current: () => boolean): Promise<void> {
    loading.value = true
    try {
      while (!retired) {
        const request = version
        const checkpoint = usageSnapshotCheckpoint(projectId)
        try {
          const next: SchemaActiveTaskOut[] = []
          let cursor: number | null = null
          let page: SchemaActiveTasksOut
          do {
            page = await callOperation<SchemaActiveTasksOut>(
              projectId === null ? 'tracker.activeTasksAll' : 'tracker.activeTasks', {
                ...(projectId === null ? {} : { project_id: projectId }),
                limit: 200, ...(cursor === null ? {} : { after_id: cursor }),
              },
            )
            if (retired) return
            if (request !== version) break
            if (page.scope !== (projectId === null ? 'global' : 'project') || page.project_id !== projectId
              || page.items.some(row => projectId !== null && row.project_id !== projectId))
              throw new Error('Active task response scope did not match the requested Home.')
            next.push(...page.items)
            const after = page.next_cursor ?? null
            if (after !== null && (after <= (cursor ?? 0) || !page.items.length))
              throw new Error('Active task pagination did not advance.')
            cursor = after
          } while (cursor !== null)
          if (request !== version) continue
          const accepted = current()
          // A first complete scoped response is useful even when the stream
          // crossed it. Display it without granting freshness or retaining it
          // for return; the ordered trailing read still owns acceptance.
          if (!accepted && asOf.value !== null) return
          projects.reconcileProjectMetadata(next.map(row => row.project))
          rows.value = next
          asOf.value = page!.as_of
          error.value = null
          snapshotCurrent.value = accepted && markUsageSnapshot(projectId, checkpoint)
          if (accepted) snapshots.set(slot, { projectId, rows: next, asOf: page!.as_of })
        } catch (cause) {
          if (retired) return
          if (request !== version) continue
          error.value = formatApiError(cause, 'Could not refresh active tasks. Previously loaded tasks are shown.')
          snapshotCurrent.value = false
        }
        if (request === version) break
      }
    } finally {
      if (!retired) loading.value = false
    }
  }

  function refresh(invalidate = false): Promise<void> {
    if (retired) return Promise.resolve()
    loading.value = true
    return reads.read(String(projectId), drain, { invalidate })
  }

  onMounted(() => {
    unsubscribe = subscribeUsageUpdates(projectId, {
      coalesceStatusWithUsage: true,
      usage: () => { void refresh(true) },
      status: () => { void refresh(true) },
      resume: () => { void refresh(true) },
      connection: state => {
        transport.value = state
        if (state.status !== 'live') snapshotCurrent.value = false
      },
      error: cause => {
        error.value = formatApiError(cause, 'Active task updates are reconnecting.')
        snapshotCurrent.value = false
      },
    })
    void refresh()
  })
  onBeforeUnmount(() => {
    retired = true
    reads.stop()
    version += 1
    unsubscribe?.()
  })
  return { items, loading, error, asOf, feed, refresh }
}
