import { computed, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import type { SchemaTrackerAgentStatsOut } from '@/api'
import { callOperation } from '@/lib/operations'
import { formatApiError } from '@/lib/client'
import { localActivityFilters, type AgentStatsFilters } from '@/lib/agentActivity'
import { subscribeUsageUpdates } from '@/lib/usageUpdates'

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
  let refreshing = false
  let pending = false
  async function refresh(): Promise<void> {
    if (refreshing) { pending = true; return }
    refreshing = true
    do { pending = false; await load() } while (pending)
    refreshing = false
  }
  function connect(): void {
    unsubscribe?.()
    unsubscribe = subscribeUsageUpdates(projectId?.value ?? null, { usage: () => { void refresh() } })
  }
  onMounted(connect)
  if (projectId) watch(projectId, connect)
  onBeforeUnmount(() => { unsubscribe?.(); pending = false; generation += 1 })

  async function load(): Promise<void> {
    const request = ++generation
    const requestedScope = scope.value
    loading.value = true
    failure.value = null
    const { start_date, end_date } = filters.value
    const duration = (Date.parse(end_date) - Date.parse(start_date)) / 86_400_000
    if (!start_date || !end_date || !Number.isFinite(duration) || duration < 0 || duration > 365) {
      failure.value = { scope: requestedScope, message: 'Choose an ordered date range of up to 366 days.' }
      loading.value = false
      return
    }
    try {
      const data = projectId
        ? await callOperation<SchemaTrackerAgentStatsOut>('tracker.agentStats', { project_id: projectId.value, ...filters.value })
        : await callOperation<SchemaTrackerAgentStatsOut>('tracker.agentStatsAll', { is_active: visibility.value, ...filters.value })
      if (request === generation && requestedScope === scope.value) loaded.value = { scope: requestedScope, data }
    } catch (err) {
      if (request === generation && requestedScope === scope.value)
        failure.value = { scope: requestedScope, message: formatApiError(err, 'Could not refresh Agent activity.') }
    } finally {
      if (request === generation) loading.value = false
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
