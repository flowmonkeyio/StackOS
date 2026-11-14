import { computed, getCurrentInstance, onBeforeUnmount, onMounted, provide, ref, type App, type Ref } from 'vue'
import type { SchemaTrackerAgentStatsOut } from '@/api'
import { callOperation } from '@/lib/operations'
import { formatApiError } from '@/lib/client'
import { localActivityFilters, type AgentStatsFilters } from '@/lib/agentActivity'
import { markUsageSnapshot, subscribeUsageUpdates, usageSnapshotCheckpoint, usageSnapshotMatches, type UsageFeedState } from '@/lib/usageUpdates'
import { usageFeedKey } from '@/composables/useUsageFeed'
import { createUsageReadOwner } from '@/lib/usageReads'

type RetainedStats = { scope: string; data: SchemaTrackerAgentStatsOut }
const retained = new WeakMap<App, Map<'global' | 'project', RetainedStats>>()

export function useAgentStats(projectId?: Ref<number>) {
  const transport = ref<UsageFeedState>({ status: 'connecting', receivedAt: null })
  const snapshotCurrent = ref(false)
  const feed = computed<UsageFeedState>(() => ({ ...transport.value,
    status: !snapshotCurrent.value && transport.value.status === 'live' ? 'reconnecting' : transport.value.status,
  }))
  provide(usageFeedKey, feed)
  const visibility = ref<boolean | null>(true)
  const filters = ref<AgentStatsFilters>(localActivityFilters())
  // Grouping changes identities, not dated totals. Keep the current response
  // mounted while regrouping; its own group_by labels both rows and chart.
  const scope = computed(() => JSON.stringify([projectId ? `project:${projectId.value}` : `all:${visibility.value}`, filters.value.start_date, filters.value.end_date, filters.value.timezone]))
  const app = getCurrentInstance()!.appContext.app
  const snapshots = retained.get(app) ?? new Map()
  retained.set(app, snapshots)
  const slot = projectId ? 'project' : 'global'
  const previous = snapshots.get(slot)
  const loaded = ref<RetainedStats | null>(previous?.scope === scope.value ? previous : null)
  const failure = ref<{ scope: string; message: string } | null>(null)
  const loading = ref(true)
  const summary = computed(() => loaded.value?.scope === scope.value ? loaded.value.data : null)
  const error = computed(() => failure.value?.scope === scope.value ? failure.value.message : null)
  let generation = 0
  let unsubscribe: (() => void) | null = null
  let connectedProject: number | null | undefined
  let disposed = false
  const reads = createUsageReadOwner()
  let requestedKey = ''
  let acceptedSnapshot: { checkpoint: ReturnType<typeof usageSnapshotCheckpoint>; token?: string | null } | null = null
  function connect(): void {
    const project = projectId?.value ?? null
    if (disposed || connectedProject === project) return
    unsubscribe?.()
    connectedProject = project
    const refresh = () => { if (connectedProject === project) void load(true) }
    transport.value = { status: 'connecting', receivedAt: null }
    snapshotCurrent.value = false
    acceptedSnapshot = null
    unsubscribe = subscribeUsageUpdates(project, { usage: (_updates, initial) => {
      if (initial && acceptedSnapshot && summary.value && !error.value && markUsageSnapshot(project, acceptedSnapshot.checkpoint, acceptedSnapshot.token)) snapshotCurrent.value = true
      else if (connectedProject === project) void load(!initial)
    }, status: refresh, resume: refresh, coalesceStatusWithUsage: true,
      connection: state => {
        if (disposed || connectedProject !== project) return
        transport.value = state
        if (state.status !== 'live') snapshotCurrent.value = false
      },
    })
  }
  onMounted(connect)
  onBeforeUnmount(() => { disposed = true; unsubscribe?.(); reads.stop(); generation += 1 })

  function load(invalidate = false): Promise<void> {
    if (disposed) return Promise.resolve()
    connect()
    const key = JSON.stringify([scope.value, filters.value.group_by])
    if (key !== requestedKey) { generation += 1; requestedKey = key; acceptedSnapshot = null }
    const request = generation
    const requestedScope = scope.value
    const requestedFilters = { ...filters.value }
    const project = projectId?.value ?? null
    const active = visibility.value
    loading.value = true
    return reads.read(`${request}:${key}`, current => read(request, requestedScope, requestedFilters, project, active, current), {
      invalidate,
    }).finally(() => { if (request === generation) loading.value = false })
  }

  async function read(request: number, requestedScope: string, requestedFilters: AgentStatsFilters, project: number | null, active: boolean | null, current: () => boolean): Promise<void> {
    if (disposed || request !== generation || requestedScope !== scope.value) return
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
      const checkpoint = usageSnapshotCheckpoint(project)
      const data = project !== null
        ? await callOperation<SchemaTrackerAgentStatsOut>('tracker.agentStats', { project_id: project, ...requestedFilters })
        : await callOperation<SchemaTrackerAgentStatsOut>('tracker.agentStatsAll', { is_active: active, ...requestedFilters })
      if (request === generation && requestedScope === scope.value && !disposed) {
        if (current() && !usageSnapshotMatches(project, checkpoint, data.read_token)) void load(true)
        if (!current() && summary.value) return
        if (data.project_id !== project) throw new Error('Agent activity response scope did not match the requested Home.')
        loaded.value = { scope: requestedScope, data }
        // Do not starve first content when updates cross a slow read. Its own
        // as_of remains visible; only the ordered replacement grants freshness
        // and becomes the retained return snapshot.
        snapshotCurrent.value = current() && markUsageSnapshot(project, checkpoint, data.read_token)
        if (current()) {
          acceptedSnapshot = { checkpoint, token: data.read_token }
          snapshots.set(slot, loaded.value)
        }
      }
    } catch (err) {
      if (current() && request === generation && requestedScope === scope.value) {
        failure.value = { scope: requestedScope, message: formatApiError(err, 'Could not refresh Agent activity.') }
        snapshotCurrent.value = false
      }
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
