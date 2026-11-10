import { reactive, shallowRef } from 'vue'
import { apiFetch, formatApiError } from '@/lib/client'
import { createProjectRequestGate } from '@/lib/stackos/projectRequestGate'

export interface SourcePageState {
  next_cursor: number | null
  total_estimate: number | null
  loaded: boolean
  loading: boolean
  error: string | null
}

/** Paging mechanism only. Instances belong to the existing source stores. */
export function createSourcePager<T extends { id: number }>(path: (projectId: number) => string) {
  const items = shallowRef<T[]>([])
  const page = reactive<SourcePageState>({
    next_cursor: null, total_estimate: null, loaded: false, loading: false, error: null,
  })
  let scope: { projectId: number; params: Record<string, string>; key: string } | null = null
  const requests = createProjectRequestGate()
  let pending: Promise<void> | null = null

  function reset() {
    requests.invalidate()
    scope = null
    pending = null
    items.value = []
    Object.assign(page, { next_cursor: null, total_estimate: null, loaded: false, loading: false, error: null })
  }

  async function read(append: boolean): Promise<void> {
    if (!scope) return
    const currentScope = scope
    const request = requests.begin(currentScope.projectId, 'page')
    const cursor = append ? page.next_cursor : null
    page.loading = true
    page.error = null
    const params = new URLSearchParams({ ...currentScope.params, limit: '50' })
    if (cursor !== null) params.set('after', String(cursor))
    try {
      const result = await apiFetch<{ items: T[]; next_cursor?: number | null; total_estimate?: number | null }>(`${path(currentScope.projectId)}?${params}`)
      if (!request.isCurrent()) return
      // Overlap is possible after a refresh; exact source IDs own identity.
      const merged = new Map((append ? items.value : []).map((item) => [item.id, item]))
      for (const item of result.items) merged.set(item.id, item)
      items.value = [...merged.values()]
      page.next_cursor = result.next_cursor ?? null
      page.total_estimate = result.total_estimate ?? null
      page.loaded = true
    } catch (err) {
      if (request.isCurrent()) page.error = formatApiError(err, 'Failed to load saved content')
    } finally {
      const current = request.isCurrent()
      request.finish()
      if (current) {
        page.loading = false
        pending = null
      }
    }
  }

  function refresh(projectId: number, params: Record<string, string> = {}, reuse = false): Promise<void> {
    if (!Number.isSafeInteger(projectId) || projectId <= 0) throw new TypeError('Invalid project ID')
    const key = JSON.stringify([projectId, Object.entries(params).sort()])
    if (scope?.key !== key) reset()
    else if (reuse) {
      if (pending) return pending
      if (page.loaded && !page.error) return Promise.resolve()
    }
    scope = { projectId, params: { ...params }, key }
    // Refresh retains compatible reading state until the replacement page arrives.
    pending = read(false)
    return pending
  }

  function loadMore(): Promise<void> {
    if (pending) return pending
    if (!scope || !page.loaded || page.next_cursor === null) return Promise.resolve()
    pending = read(true)
    return pending
  }

  return { items, page, refresh, loadMore, reset }
}
