import { computed, ref } from 'vue'
import { callOperation } from '@/lib/operations'
import { formatApiError } from '@/lib/client'
import { useProjectsStore } from '@/stores/projects'
import type { TrackerStatus } from '@/lib/task-tracker/types'
import type { OverviewPage, PortfolioProject } from './ticketOverview'

export interface PortfolioFilters {
  query: string
  is_active: boolean | null
  sort: 'recent' | 'name'
  ticket_status: TrackerStatus | null
}

export function useHomePortfolioInsights() {
  const projects = useProjectsStore()
  const rows = ref<PortfolioProject[]>([])
  const items = computed(() => rows.value.filter((project) => !projects.isProjectDeleted(project.id)))
  const loading = ref(false)
  const error = ref<string | null>(null)
  const nextCursor = ref<number | null>(null)
  const sourceTotal = ref<number | null>(null)
  const total = computed(() => sourceTotal.value === null ? null
    : Math.max(items.value.length, sourceTotal.value - rows.value.filter((project) => projects.isProjectDeleted(project.id)).length))
  const filters = ref<PortfolioFilters>({
    query: '',
    is_active: true,
    sort: 'recent',
    ticket_status: null,
  })
  let generation = 0

  async function load(append = false): Promise<void> {
    const request = ++generation
    loading.value = true
    error.value = null
    try {
      const page = await callOperation<OverviewPage<PortfolioProject>>('project.portfolio', {
        ...filters.value,
        limit: 50,
        ...(append && nextCursor.value !== null ? { after_id: nextCursor.value } : {}),
      })
      if (request !== generation) return
      projects.reconcileProjectMetadata(page.items)
      rows.value = append ? [...rows.value, ...page.items] : page.items
      nextCursor.value = page.next_cursor ?? null
      sourceTotal.value = page.total_estimate ?? null
    } catch (err) {
      if (request === generation)
        error.value = formatApiError(
          err,
          'Could not refresh projects. Previously loaded records are shown.',
        )
    } finally {
      if (request === generation) loading.value = false
    }
  }

  async function setFilters(next: PortfolioFilters): Promise<void> {
    filters.value = next
    rows.value = []
    nextCursor.value = null
    sourceTotal.value = null
    await load()
  }

  async function loadMore(): Promise<void> {
    if (loading.value || nextCursor.value === null) return
    await load(true)
  }

  return { items, loading, error, nextCursor, total, filters, load, loadMore, setFilters }
}
