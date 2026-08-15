import { ref } from 'vue'
import { defineStore } from 'pinia'

import type {
  SchemaLoadedWorkflowTemplate,
  SchemaWorkflowTemplateListOut,
  SchemaWorkflowTemplateSummaryOut,
} from '@/api'
import { apiFetch, formatApiError } from '@/lib/client'
import { createProjectRequestGate } from '@/lib/stackos/projectRequestGate'

export const useWorkflowTemplatesStore = defineStore('workflowTemplates', () => {
  const items = ref<SchemaWorkflowTemplateSummaryOut[]>([])
  const selected = ref<SchemaLoadedWorkflowTemplate | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const currentProjectId = ref<number | null>(null)
  const requests = createProjectRequestGate((projectId) => {
    items.value = []
    selected.value = null
    error.value = null
    currentProjectId.value = projectId
  })

  async function refresh(projectId: number, pluginSlug?: string | null): Promise<void> {
    const request = requests.begin(projectId, 'list')
    requests.invalidateOperation('detail')
    items.value = []
    selected.value = null
    loading.value = true
    error.value = null
    try {
      const params = new URLSearchParams()
      if (pluginSlug) params.set('plugin_slug', pluginSlug)
      const suffix = params.toString() ? `?${params.toString()}` : ''
      const body = await apiFetch<SchemaWorkflowTemplateListOut>(
        `/api/v1/projects/${projectId}/workflow-templates${suffix}`,
      )
      if (!request.isCurrent()) return
      items.value = body.templates
      if (!selected.value && body.templates.length > 0) {
        await describe(projectId, body.templates[0].key, body.templates[0].plugin_slug)
      }
    } catch (err) {
      if (request.isCurrent()) {
        error.value = formatApiError(err, 'failed to load workflow templates')
      }
    } finally {
      loading.value = request.finish()
    }
  }

  async function describe(
    projectId: number,
    key: string,
    pluginSlug?: string | null,
  ): Promise<void> {
    const request = requests.begin(projectId, 'detail')
    loading.value = true
    error.value = null
    try {
      const params = new URLSearchParams()
      if (pluginSlug) params.set('plugin_slug', pluginSlug)
      const suffix = params.toString() ? `?${params.toString()}` : ''
      const nextSelected = await apiFetch<SchemaLoadedWorkflowTemplate>(
        `/api/v1/projects/${projectId}/workflow-templates/${encodeURIComponent(key)}${suffix}`,
      )
      if (request.isCurrent()) selected.value = nextSelected
    } catch (err) {
      if (request.isCurrent()) {
        error.value = formatApiError(err, 'failed to load workflow template')
      }
    } finally {
      loading.value = request.finish()
    }
  }

  function reset(): void {
    requests.invalidate()
    items.value = []
    selected.value = null
    loading.value = false
    error.value = null
    currentProjectId.value = null
  }

  return { items, selected, loading, error, currentProjectId, refresh, describe, reset }
})
