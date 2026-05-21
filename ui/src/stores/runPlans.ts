import { ref } from 'vue'
import { defineStore } from 'pinia'

import type {
  SchemaPageResponseRunPlanSummaryOut,
  SchemaRunPlanOut,
  SchemaRunPlanSummaryOut,
} from '@/api'
import { apiFetch, formatApiError } from '@/lib/client'

export const useRunPlansStore = defineStore('runPlans', () => {
  const items = ref<SchemaRunPlanSummaryOut[]>([])
  const current = ref<SchemaRunPlanOut | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function refresh(projectId: number, filters: { runId?: number | null } = {}): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const params = new URLSearchParams({ limit: '50' })
      if (filters.runId) params.set('run_id', String(filters.runId))
      const page = await apiFetch<SchemaPageResponseRunPlanSummaryOut>(
        `/api/v1/projects/${projectId}/run-plans?${params.toString()}`,
      )
      items.value = page.items
    } catch (err) {
      error.value = formatApiError(err, 'failed to load run plans')
    } finally {
      loading.value = false
    }
  }

  async function get(runPlanId: number): Promise<SchemaRunPlanOut | null> {
    loading.value = true
    error.value = null
    try {
      current.value = await apiFetch<SchemaRunPlanOut>(`/api/v1/run-plans/${runPlanId}`)
      return current.value
    } catch (err) {
      error.value = formatApiError(err, 'failed to load run plan')
      return null
    } finally {
      loading.value = false
    }
  }

  return { items, current, loading, error, refresh, get }
})
