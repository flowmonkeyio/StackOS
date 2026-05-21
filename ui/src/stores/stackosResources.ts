import { ref } from 'vue'
import { defineStore } from 'pinia'

import type {
  SchemaArtifactOut,
  SchemaPageResponseArtifactOut,
  SchemaPageResponseResourceRecordOut,
  SchemaResourceOut,
  SchemaResourceRecordOut,
} from '@/api'
import { apiFetch, formatApiError } from '@/lib/client'

export const useStackOsResourcesStore = defineStore('stackosResources', () => {
  const resources = ref<SchemaResourceOut[]>([])
  const records = ref<SchemaResourceRecordOut[]>([])
  const artifacts = ref<SchemaArtifactOut[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function refresh(
    projectId: number,
    filters: { pluginSlug?: string | null; resourceKey?: string | null } = {},
  ): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const resourceParams = new URLSearchParams()
      const recordParams = new URLSearchParams({ limit: '50' })
      const artifactParams = new URLSearchParams({ limit: '50' })
      if (filters.pluginSlug) {
        resourceParams.set('plugin_slug', filters.pluginSlug)
        recordParams.set('plugin_slug', filters.pluginSlug)
        artifactParams.set('plugin_slug', filters.pluginSlug)
      }
      if (filters.resourceKey) recordParams.set('resource_key', filters.resourceKey)
      const resourceSuffix = resourceParams.toString() ? `?${resourceParams.toString()}` : ''
      const [resourceRows, recordPage, artifactPage] = await Promise.all([
        apiFetch<SchemaResourceOut[]>(`/api/v1/resources${resourceSuffix}`),
        apiFetch<SchemaPageResponseResourceRecordOut>(
          `/api/v1/projects/${projectId}/resource-records?${recordParams.toString()}`,
        ),
        apiFetch<SchemaPageResponseArtifactOut>(
          `/api/v1/projects/${projectId}/artifacts?${artifactParams.toString()}`,
        ),
      ])
      resources.value = resourceRows
      records.value = recordPage.items
      artifacts.value = artifactPage.items
    } catch (err) {
      error.value = formatApiError(err, 'failed to load resources')
    } finally {
      loading.value = false
    }
  }

  return { resources, records, artifacts, loading, error, refresh }
})
