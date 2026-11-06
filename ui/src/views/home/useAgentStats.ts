import { computed, onBeforeUnmount, ref, type Ref } from 'vue'
import type { SchemaTrackerAgentStatsOut } from '@/api'
import { callOperation } from '@/lib/operations'
import { formatApiError } from '@/lib/client'

export function useAgentStats(projectId?: Ref<number>) {
  const visibility = ref<boolean | null>(true)
  const scope = computed(() => projectId ? `project:${projectId.value}` : `all:${visibility.value}`)
  const loaded = ref<{ scope: string; data: SchemaTrackerAgentStatsOut } | null>(null)
  const failure = ref<{ scope: string; message: string } | null>(null)
  const loading = ref(false)
  const summary = computed(() => loaded.value?.scope === scope.value ? loaded.value.data : null)
  const error = computed(() => failure.value?.scope === scope.value ? failure.value.message : null)
  let generation = 0
  onBeforeUnmount(() => { generation += 1 })

  async function load(): Promise<void> {
    const request = ++generation
    const requestedScope = scope.value
    loading.value = true
    failure.value = null
    try {
      const data = projectId
        ? await callOperation<SchemaTrackerAgentStatsOut>('tracker.agentStats', { project_id: projectId.value })
        : await callOperation<SchemaTrackerAgentStatsOut>('tracker.agentStatsAll', { is_active: visibility.value })
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
  return { summary, loading, error, load, setVisibility }
}
