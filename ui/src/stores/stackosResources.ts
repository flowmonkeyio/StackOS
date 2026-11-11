import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import type { SchemaArtifactOut, SchemaResourceOut, SchemaResourceRecordOut } from '@/api'
import { apiFetch, formatApiError } from '@/lib/client'
import { createSourcePager } from '@/lib/sourcePager'
import { createProjectRequestGate } from '@/lib/stackos/projectRequestGate'

export interface ResourceFilters { pluginSlug?: string | null; resourceKey?: string | null }

export const useStackOsResourcesStore = defineStore('stackosResources', () => {
  const resources = ref<SchemaResourceOut[]>([])
  const recordSource = createSourcePager<SchemaResourceRecordOut>((id) => `/api/v1/projects/${id}/resource-records`)
  const artifactSource = createSourcePager<SchemaArtifactOut>((id) => `/api/v1/projects/${id}/artifacts`)
  const currentProjectId = ref<number | null>(null)
  const schemaLoading = ref(false)
  const schemaError = ref<string | null>(null)
  const schemaRequests = createProjectRequestGate()
  const loading = computed(() => schemaLoading.value || recordSource.page.loading || artifactSource.page.loading)
  const error = computed(() => schemaError.value || recordSource.page.error || artifactSource.page.error)

  function ensureScope(projectId: number) {
    if (currentProjectId.value === projectId) return
    reset()
    currentProjectId.value = projectId
  }
  function params(filters: ResourceFilters) {
    const result: Record<string, string> = {}
    if (filters.pluginSlug) result.plugin_slug = filters.pluginSlug
    return result
  }
  async function refreshSchemas(projectId: number, filters: ResourceFilters = {}) {
    ensureScope(projectId)
    const request = schemaRequests.begin(projectId)
    schemaLoading.value = true
    schemaError.value = null
    resources.value = []
    try {
      const query = new URLSearchParams({ ...params(filters), project_id: String(projectId) })
      const rows = await apiFetch<SchemaResourceOut[]>(`/api/v1/resources?${query}`)
      if (request.isCurrent()) resources.value = rows
    } catch (err) {
      if (request.isCurrent()) schemaError.value = formatApiError(err, 'Failed to load resource schemas')
    } finally {
      const current = request.isCurrent()
      request.finish()
      if (current) schemaLoading.value = false
    }
  }
  function refreshRecords(projectId: number, filters: ResourceFilters = {}) {
    ensureScope(projectId)
    const query = params(filters)
    if (filters.resourceKey) query.resource_key = filters.resourceKey
    return recordSource.refresh(projectId, query)
  }
  function refreshArtifacts(projectId: number, filters: ResourceFilters = {}, reuse = false) {
    ensureScope(projectId)
    return artifactSource.refresh(projectId, { ...params(filters), include_inactive: 'true' }, reuse)
  }
  async function refresh(projectId: number, filters: ResourceFilters = {}) {
    await Promise.all([refreshSchemas(projectId, filters), refreshRecords(projectId, filters), refreshArtifacts(projectId, filters)])
  }
  function reset() {
    schemaRequests.invalidate()
    resources.value = []
    schemaLoading.value = false
    schemaError.value = null
    recordSource.reset()
    artifactSource.reset()
    currentProjectId.value = null
  }
  return {
    resources, records: recordSource.items, artifacts: artifactSource.items,
    recordPage: recordSource.page, artifactPage: artifactSource.page,
    loading, error, schemaLoading, schemaError, currentProjectId,
    refresh, refreshSchemas, refreshRecords, refreshArtifacts,
    loadMoreRecords: recordSource.loadMore, loadMoreArtifacts: artifactSource.loadMore, reset,
  }
})
