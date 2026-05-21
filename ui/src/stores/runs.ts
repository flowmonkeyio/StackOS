// Runs store — read-only list / get / children audit view.

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { apiFetch, formatApiError } from '@/lib/client'
import type { components } from '@/api'

export type Run = components['schemas']['RunOut']
export type RunStatus = components['schemas']['RunStatus']
export type RunKind = components['schemas']['RunKind']
type RunsPage = components['schemas']['PageResponse_RunOut_']

const DEFAULT_LIMIT = 50

export interface RunFilters {
  kind: RunKind | null
  status: RunStatus | null
  parent_run_id: number | null
  since: string | null
  until: string | null
}

export type RunSortKey = 'started_at' | '-started_at' | 'id' | '-id'

export const useRunsStore = defineStore('runs', () => {
  const items = ref<Run[]>([])
  const totalEstimate = ref<number>(0)
  const nextCursor = ref<number | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const currentProjectId = ref<number | null>(null)
  const currentDetail = ref<Run | null>(null)
  const childrenByParent = ref<Map<number, Run[]>>(new Map())
  const filters = ref<RunFilters>({
    kind: null,
    status: null,
    parent_run_id: null,
    since: null,
    until: null,
  })
  const sort = ref<RunSortKey>('-started_at')

  function _buildParams(after?: number | null): URLSearchParams {
    const params = new URLSearchParams({ limit: String(DEFAULT_LIMIT) })
    if (filters.value.kind) params.set('kind', filters.value.kind)
    if (filters.value.status) params.set('status', filters.value.status)
    if (filters.value.parent_run_id !== null) {
      params.set('parent_run_id', String(filters.value.parent_run_id))
    }
    if (after !== undefined && after !== null) params.set('after', String(after))
    return params
  }

  function _ingestPage(page: RunsPage, append: boolean): void {
    items.value = append ? [...items.value, ...page.items] : [...page.items]
    nextCursor.value = page.next_cursor ?? null
    totalEstimate.value = page.total_estimate ?? items.value.length
  }

  async function refresh(projectId: number): Promise<void> {
    currentProjectId.value = projectId
    loading.value = true
    error.value = null
    try {
      const page = await apiFetch<RunsPage>(
        `/api/v1/projects/${projectId}/runs?${_buildParams().toString()}`,
      )
      _ingestPage(page, false)
    } catch (err) {
      error.value = formatApiError(err, 'failed to load runs')
    } finally {
      loading.value = false
    }
  }

  async function loadMore(projectId: number): Promise<void> {
    if (nextCursor.value === null || loading.value) return
    loading.value = true
    try {
      const page = await apiFetch<RunsPage>(
        `/api/v1/projects/${projectId}/runs?${_buildParams(nextCursor.value).toString()}`,
      )
      _ingestPage(page, true)
    } catch (err) {
      error.value = formatApiError(err, 'failed to load more runs')
    } finally {
      loading.value = false
    }
  }

  async function get(runId: number): Promise<Run> {
    const row = await apiFetch<Run>(`/api/v1/runs/${runId}`)
    currentDetail.value = row
    return row
  }

  async function children(runId: number): Promise<Run[]> {
    const rows = await apiFetch<Run[]>(`/api/v1/runs/${runId}/children`)
    childrenByParent.value.set(runId, rows)
    return rows
  }

  function getById(id: number): Run | null {
    if (currentDetail.value?.id === id) return currentDetail.value
    return items.value.find((run) => run.id === id) ?? null
  }

  function setFilter<K extends keyof RunFilters>(key: K, value: RunFilters[K]): void {
    filters.value = { ...filters.value, [key]: value }
  }

  function setSort(key: RunSortKey): void {
    sort.value = key
  }

  const filteredItems = computed<Run[]>(() => {
    let arr = items.value
    if (filters.value.since) arr = arr.filter((run) => run.started_at >= filters.value.since!)
    if (filters.value.until) arr = arr.filter((run) => run.started_at < filters.value.until!)
    const key = sort.value
    const dir = key.startsWith('-') ? -1 : 1
    const field = key.replace(/^-/, '') as 'id' | 'started_at'
    return [...arr].sort((a, b) => {
      const av = a[field] ?? ''
      const bv = b[field] ?? ''
      if (av < bv) return -1 * dir
      if (av > bv) return 1 * dir
      return 0
    })
  })

  function reset(): void {
    items.value = []
    totalEstimate.value = 0
    nextCursor.value = null
    error.value = null
    currentProjectId.value = null
    currentDetail.value = null
    childrenByParent.value = new Map()
    filters.value = {
      kind: null,
      status: null,
      parent_run_id: null,
      since: null,
      until: null,
    }
    sort.value = '-started_at'
  }

  return {
    items,
    totalEstimate,
    nextCursor,
    loading,
    error,
    currentProjectId,
    currentDetail,
    childrenByParent,
    filters,
    sort,
    filteredItems,
    refresh,
    loadMore,
    get,
    children,
    setFilter,
    setSort,
    getById,
    reset,
  }
})
