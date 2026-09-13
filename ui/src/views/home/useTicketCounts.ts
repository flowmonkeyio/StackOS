import { onBeforeUnmount, ref, type Ref } from 'vue'
import { callOperation } from '@/lib/operations'
import { formatApiError } from '@/lib/client'
import type { TicketCountSummary } from './ticketOverview'

export function useTicketCounts(projectId?: Ref<number>) {
  const summary = ref<TicketCountSummary | null>(null)
  const visibility = ref<boolean | null>(true)
  const loading = ref(false)
  const error = ref<string | null>(null)
  let generation = 0
  onBeforeUnmount(() => {
    generation += 1
  })

  async function load(): Promise<void> {
    const request = ++generation
    loading.value = true
    error.value = null
    try {
      const result = await callOperation<TicketCountSummary>(
        projectId ? 'tracker.ticketCounts' : 'tracker.ticketCountsAll',
        projectId ? { project_id: projectId.value } : { is_active: visibility.value },
      )
      if (request === generation) summary.value = result
    } catch (err) {
      if (request === generation)
        error.value = formatApiError(err, 'Could not refresh ticket counts.')
    } finally {
      if (request === generation) loading.value = false
    }
  }

  async function setVisibility(value: boolean | null): Promise<void> {
    visibility.value = value
    summary.value = null
    await load()
  }
  return { summary, visibility, loading, error, load, setVisibility }
}
