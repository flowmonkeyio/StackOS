import { computed, onBeforeUnmount, onMounted, provide, ref } from 'vue'
import type { SchemaActiveTaskOut, SchemaActiveTasksOut } from '@/api'
import { callOperation } from '@/lib/operations'
import { formatApiError } from '@/lib/client'
import { markUsageSnapshot, subscribeUsageUpdates, usageSnapshotCheckpoint, type UsageFeedState } from '@/lib/usageUpdates'
import { usageFeedKey } from '@/composables/useUsageFeed'
import { useProjectsStore } from '@/stores/projects'

/** One mounted Home scope, one shared transport, paged bulk reads only. */
export function useActiveTasks(projectId: number | null) {
  const projects = useProjectsStore()
  const rows = ref<SchemaActiveTaskOut[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const asOf = ref<string | null>(null)
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
  let pending: Promise<void> | null = null
  let unsubscribe: (() => void) | null = null

  async function drain(): Promise<void> {
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
          projects.reconcileProjectMetadata(next.map(row => row.project))
          rows.value = next
          asOf.value = page!.as_of
          error.value = null
          snapshotCurrent.value = markUsageSnapshot(projectId, checkpoint)
        } catch (cause) {
          if (retired) return
          if (request !== version) continue
          error.value = formatApiError(cause, 'Could not refresh active tasks. Previously loaded tasks are shown.')
          snapshotCurrent.value = false
        }
        if (request === version) break
      }
    } finally {
      pending = null
      if (!retired) loading.value = false
    }
  }

  function refresh(): Promise<void> {
    if (retired) return Promise.resolve()
    version += 1
    snapshotCurrent.value = false
    // Start after the current SSE batch so usage/status companions coalesce.
    pending ??= Promise.resolve().then(drain)
    return pending
  }

  onMounted(() => {
    unsubscribe = subscribeUsageUpdates(projectId, {
      usage: () => { void refresh() },
      status: () => { void refresh() },
      resume: () => { void refresh() },
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
    version += 1
    unsubscribe?.()
  })
  return { items, loading, error, asOf, feed, refresh }
}
