// Schedules store — read-only scheduled jobs.

import { ref } from 'vue'
import { defineStore } from 'pinia'

import { apiFetch } from '@/lib/client'
import { createProjectRequestGate } from '@/lib/stackos/projectRequestGate'
import type { components } from '@/api'

export type ScheduledJob = components['schemas']['ScheduledJobOut']

export const useSchedulesStore = defineStore('schedules', () => {
  const items = ref<ScheduledJob[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const currentProjectId = ref<number | null>(null)
  const requests = createProjectRequestGate((projectId) => {
    items.value = []
    error.value = null
    currentProjectId.value = projectId
  })

  async function refresh(projectId: number): Promise<void> {
    const request = requests.begin(projectId, 'list')
    loading.value = true
    error.value = null
    try {
      const rows = await apiFetch<ScheduledJob[]>(`/api/v1/projects/${projectId}/schedules`)
      if (!request.isCurrent()) return
      items.value = Array.isArray(rows) ? rows : []
    } catch (err) {
      if (request.isCurrent()) {
        error.value = err instanceof Error ? err.message : 'failed to load schedules'
      }
    } finally {
      loading.value = request.finish()
    }
  }

  function reset(): void {
    requests.invalidate()
    items.value = []
    loading.value = false
    error.value = null
    currentProjectId.value = null
  }

  return {
    items,
    loading,
    error,
    currentProjectId,
    refresh,
    reset,
  }
})
