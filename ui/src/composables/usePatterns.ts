import { onBeforeUnmount, ref } from 'vue'

import { ApiError, formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'

export interface PatternSummary {
  id: number
  key: string
  title: string
  summary: string
  tags: string[]
  etag: string
  source: 'custom' | 'stackos' | 'customized'
  source_label: string
  updated_at: string
}

export interface PatternDetail extends PatternSummary {
  body: string
}
export interface PatternPage {
  items: PatternSummary[]
  next_cursor: number | null
  total_estimate: number
  catalog_total: number
}
export interface PatternDraft {
  key: string
  title: string
  summary: string
  tags: string[]
  body: string
}

export function usePatterns() {
  const page = ref<PatternPage | null>(null)
  const loading = ref(false)
  const error = ref('')
  const query = ref('')
  const source = ref('all')
  const tags = ref<string[]>([])
  const cursor = ref<number | null>(null)
  const previous = ref<(number | null)[]>([])
  const detail = ref<PatternDetail | null>(null)
  const detailLoading = ref(false)
  const detailError = ref('')
  const missing = ref(false)
  let listRequest = 0
  let detailRequest = 0
  let listAbort: AbortController | null = null
  let detailAbort: AbortController | null = null
  let disposed = false

  async function load() {
    const request = ++listRequest
    listAbort?.abort()
    listAbort = new AbortController()
    loading.value = true
    error.value = ''
    page.value = null
    try {
      const result = await callOperation<PatternPage>(
        'pattern.list',
        {
          query: query.value || null,
          source: source.value,
          tags: [...tags.value],
          limit: 25,
          cursor: cursor.value,
        },
        { signal: listAbort.signal },
      )
      if (!disposed && request === listRequest) page.value = result
    } catch (err) {
      if (!disposed && request === listRequest) error.value = formatApiError(err)
    } finally {
      if (!disposed && request === listRequest) loading.value = false
    }
  }

  function filter(values: { query?: string; source?: string; tags?: string[] }) {
    if (values.query !== undefined) query.value = values.query
    if (values.source !== undefined) source.value = values.source
    if (values.tags !== undefined) tags.value = values.tags
    cursor.value = null
    previous.value = []
    return load()
  }

  function next() {
    if (!page.value?.next_cursor || loading.value) return
    previous.value.push(cursor.value)
    cursor.value = page.value.next_cursor
    return load()
  }

  function back() {
    if (!previous.value.length || loading.value) return
    cursor.value = previous.value.pop() ?? null
    return load()
  }

  async function select(id: number | null) {
    const request = ++detailRequest
    detailAbort?.abort()
    detail.value = null
    detailError.value = ''
    missing.value = false
    detailLoading.value = id !== null
    if (id === null) return
    if (!Number.isSafeInteger(id) || id <= 0) {
      missing.value = true
      detailLoading.value = false
      return
    }
    detailAbort = new AbortController()
    try {
      const result = await callOperation<PatternDetail>(
        'pattern.get',
        { pattern_id: id },
        { signal: detailAbort.signal },
      )
      if (!disposed && request === detailRequest) detail.value = result
    } catch (err) {
      if (!disposed && request === detailRequest) {
        if (err instanceof ApiError && err.status === 404) missing.value = true
        else detailError.value = formatApiError(err)
      }
    } finally {
      if (!disposed && request === detailRequest) detailLoading.value = false
    }
  }

  async function save(draft: PatternDraft, etag?: string) {
    const result = await callOperation<{ data: PatternSummary }>('pattern.upsert', {
      ...draft,
      ...(etag ? { expected_etag: etag } : {}),
    })
    return result.data
  }

  async function remove(entry: PatternSummary) {
    await callOperation('pattern.delete', { pattern_id: entry.id, expected_etag: entry.etag })
  }

  onBeforeUnmount(() => {
    disposed = true
    listAbort?.abort()
    detailAbort?.abort()
  })

  return {
    page,
    loading,
    error,
    query,
    source,
    tags,
    previous,
    detail,
    detailLoading,
    detailError,
    missing,
    load,
    filter,
    next,
    back,
    select,
    save,
    remove,
  }
}
