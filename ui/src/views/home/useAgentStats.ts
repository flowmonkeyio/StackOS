import { computed, onBeforeUnmount, onMounted, ref, type Ref } from 'vue'
import type { SchemaTrackerAgentStatsOut } from '@/api'
import { callOperation } from '@/lib/operations'
import { formatApiError } from '@/lib/client'
import { localActivityFilters, type AgentStatsFilters } from '@/lib/agentActivity'
import { awaitUsageBaseline, subscribeUsageUpdates } from '@/lib/usageUpdates'
import { createUsageReadOwner } from '@/lib/usageReads'

export function useAgentStats(projectId?: Ref<number>) {
  const visibility = ref<boolean | null>(true)
  const filters = ref<AgentStatsFilters>(localActivityFilters())
  // Grouping changes identities, not dated totals. Keep the current response
  // mounted while regrouping; its own group_by labels both rows and chart.
  const scope = computed(() => JSON.stringify([projectId ? `project:${projectId.value}` : `all:${visibility.value}`, filters.value.start_date, filters.value.end_date, filters.value.timezone]))
  const loaded = ref<{ scope: string; data: SchemaTrackerAgentStatsOut } | null>(null)
  const failure = ref<{ scope: string; message: string } | null>(null)
  const loading = ref(false)
  const summary = computed(() => loaded.value?.scope === scope.value ? loaded.value.data : null)
  const error = computed(() => failure.value?.scope === scope.value ? failure.value.message : null)
  let generation = 0
  let unsubscribe: (() => void) | null = null
  let connectedProject: number | null | undefined
  let disposed = false
  const reads = createUsageReadOwner()
  let requestedKey = ''
  function connect(): void {
    const project = projectId?.value ?? null
    if (disposed || connectedProject === project) return
    unsubscribe?.()
    connectedProject = project
    const refresh = () => { if (connectedProject === project) void load(true) }
    unsubscribe = subscribeUsageUpdates(project, { usage: refresh, status: refresh, resume: refresh, coalesceStatusWithUsage: true })
  }
  onMounted(connect)
  onBeforeUnmount(() => { disposed = true; unsubscribe?.(); reads.stop(); generation += 1 })

  function load(invalidate = false): Promise<void> {
    if (disposed) return Promise.resolve()
    connect()
    const key = JSON.stringify([scope.value, filters.value.group_by])
    if (key !== requestedKey) { generation += 1; requestedKey = key }
    const request = generation
    const requestedScope = scope.value
    const requestedFilters = { ...filters.value }
    const project = projectId?.value ?? null
    const active = visibility.value
    loading.value = true
    return reads.read(`${request}:${key}`, current => read(request, requestedScope, requestedFilters, project, active, current), {
      invalidate, ready: () => awaitUsageBaseline(project),
    }).finally(() => { if (request === generation) loading.value = false })
  }

  async function read(request: number, requestedScope: string, requestedFilters: AgentStatsFilters, project: number | null, active: boolean | null, current: () => boolean): Promise<void> {
    if (disposed) return
    // Project routes remount by scope; explicit/manual loads also reconcile a
    // changed project ref before requesting its data.
    failure.value = null
    const { start_date, end_date } = requestedFilters
    const duration = (Date.parse(end_date) - Date.parse(start_date)) / 86_400_000
    if (!start_date || !end_date || !Number.isFinite(duration) || duration < 0 || duration > 365) {
      failure.value = { scope: requestedScope, message: 'Choose an ordered date range of up to 366 days.' }
      loading.value = false
      return
    }
    try {
      const data = project !== null
        ? await callOperation<SchemaTrackerAgentStatsOut>('tracker.agentStats', { project_id: project, ...requestedFilters })
        : await callOperation<SchemaTrackerAgentStatsOut>('tracker.agentStatsAll', { is_active: active, ...requestedFilters })
      if (current() && request === generation && requestedScope === scope.value) loaded.value = { scope: requestedScope, data }
    } catch (err) {
      if (current() && request === generation && requestedScope === scope.value)
        failure.value = { scope: requestedScope, message: formatApiError(err, 'Could not refresh Agent activity.') }
    }
  }

  async function setVisibility(value: boolean | null): Promise<void> {
    visibility.value = value
    loaded.value = null
    await load()
  }
  async function setFilters(value: Partial<AgentStatsFilters>): Promise<void> {
    filters.value = { ...filters.value, ...value }
    await load()
  }
  return { summary, loading, error, filters, load, setVisibility, setFilters }
}
