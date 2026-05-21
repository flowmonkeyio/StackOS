import { ref } from 'vue'
import { defineStore } from 'pinia'

import type {
  SchemaLoadedWorkflowTemplate,
  SchemaWorkflowTemplateListOut,
  SchemaWorkflowTemplateSummaryOut,
} from '@/api'
import { apiFetch, formatApiError } from '@/lib/client'

export const useWorkflowTemplatesStore = defineStore('workflowTemplates', () => {
  const items = ref<SchemaWorkflowTemplateSummaryOut[]>([])
  const selected = ref<SchemaLoadedWorkflowTemplate | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function refresh(projectId: number): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const body = await apiFetch<SchemaWorkflowTemplateListOut>(
        `/api/v1/projects/${projectId}/workflow-templates`,
      )
      items.value = body.templates
      if (!selected.value && body.templates.length > 0) {
        await describe(projectId, body.templates[0].key, body.templates[0].plugin_slug)
      }
    } catch (err) {
      error.value = formatApiError(err, 'failed to load workflow templates')
    } finally {
      loading.value = false
    }
  }

  async function describe(
    projectId: number,
    key: string,
    pluginSlug?: string | null,
  ): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const params = new URLSearchParams()
      if (pluginSlug) params.set('plugin_slug', pluginSlug)
      const suffix = params.toString() ? `?${params.toString()}` : ''
      selected.value = await apiFetch<SchemaLoadedWorkflowTemplate>(
        `/api/v1/projects/${projectId}/workflow-templates/${encodeURIComponent(key)}${suffix}`,
      )
    } catch (err) {
      error.value = formatApiError(err, 'failed to load workflow template')
    } finally {
      loading.value = false
    }
  }

  function reset(): void {
    items.value = []
    selected.value = null
    loading.value = false
    error.value = null
  }

  return { items, selected, loading, error, refresh, describe, reset }
})
