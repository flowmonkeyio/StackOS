import { onBeforeUnmount, ref } from 'vue'

import { PatternListInputSource } from '@/api'
import type {
  SchemaPatternDeleteInput,
  SchemaPatternDetail,
  SchemaPatternGetInput,
  SchemaPatternListInput,
  SchemaPatternPage,
  SchemaPatternSummary,
  SchemaPatternUpsertInput,
  SchemaWriteEnvelopePatternSummary,
} from '@/api'
import { ApiError, formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'

export interface PatternDraft {
  key: string
  title: string
  summary: string
  tags: string[]
  body: string
}

export function usePatterns() {
  const page = ref<SchemaPatternPage | null>(null)
  const loading = ref(false)
  const error = ref('')
  const query = ref('')
  const source = ref(PatternListInputSource.all)
  const tags = ref<string[]>([])
  const cursor = ref<number | null>(null)
  const previous = ref<(number | null)[]>([])
  const detail = ref<SchemaPatternDetail | null>(null)
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
      const args: Pick<SchemaPatternListInput, 'query' | 'source' | 'tags' | 'limit' | 'cursor'> = {
        query: query.value || null,
        source: source.value,
        tags: [...tags.value],
        limit: 25,
        cursor: cursor.value,
      }
      const result = await callOperation<SchemaPatternPage>(
        'pattern.list',
        args,
        { signal: listAbort.signal },
      )
      if (!disposed && request === listRequest) page.value = result
    } catch (err) {
      if (!disposed && request === listRequest) error.value = formatApiError(err)
    } finally {
      if (!disposed && request === listRequest) loading.value = false
    }
  }

  function filter(values: { query?: string; source?: SchemaPatternListInput['source']; tags?: string[] }) {
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
      const args: Pick<SchemaPatternGetInput, 'pattern_id'> = { pattern_id: id }
      const result = await callOperation<SchemaPatternDetail>(
        'pattern.get',
        args,
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
    const args: Pick<SchemaPatternUpsertInput, 'key' | 'title' | 'summary' | 'body' | 'tags'>
      & Partial<Pick<SchemaPatternUpsertInput, 'expected_etag'>> = {
      ...draft,
      ...(etag ? { expected_etag: etag } : {}),
    }
    const result = await callOperation<SchemaWriteEnvelopePatternSummary>('pattern.upsert', args)
    return result.data
  }

  async function remove(entry: Pick<SchemaPatternSummary, 'id' | 'etag'>) {
    const args: Pick<SchemaPatternDeleteInput, 'pattern_id' | 'expected_etag'> = {
      pattern_id: entry.id,
      expected_etag: entry.etag,
    }
    await callOperation<SchemaWriteEnvelopePatternSummary>('pattern.delete', args)
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
