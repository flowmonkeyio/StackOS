// Project schedule setup and actual execution projection. Usage stays backend-owned.

import { ref } from 'vue'
import { defineStore } from 'pinia'

import { apiFetch } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { usageAfter, usageCheckpoint, type UsageProjection } from '@/lib/usageUpdates'
import { createProjectRequestGate } from '@/lib/stackos/projectRequestGate'
import type { components } from '@/api'

export type ScheduledJob = components['schemas']['ScheduledJobOut']
export type ScheduledRun = components['schemas']['ScheduledRunOut']
export type ScheduleConfiguration = components['schemas']['ScheduleUpsertRequest']

export const useSchedulesStore = defineStore('schedules', () => {
  const items = ref<ScheduledJob[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const runs = ref<ScheduledRun[]>([])
  const runsLoading = ref(false)
  const runsError = ref<string | null>(null)
  const nextCursor = ref<number | null>(null)
  const saving = ref(false)
  const currentProjectId = ref<number | null>(null)
  const requests = createProjectRequestGate((projectId) => {
    items.value = []
    runs.value = []
    nextCursor.value = null
    runsError.value = null
    runsLoading.value = false
    error.value = null
    currentProjectId.value = projectId
  })
  const runRequests = createProjectRequestGate()

  function applyUsage(updates?: UsageProjection): void {
    if (!updates) return
    const tasks = new Map(updates.tasks.map(task => [task.key, task]))
    for (const row of runs.value) {
      // Linked child tasks cannot replace a request-owned main thread's usage.
      if (row.native_execution) continue
      const task = row.task_key ? tasks.get(row.task_key) : undefined
      if (task) {
        row.agent_stats = task.agent_stats ?? null
        row.active_execution = task.active_execution ?? null
      }
    }
  }

  async function refreshRuns(projectId: number, more = false): Promise<void> {
    if (currentProjectId.value !== projectId) {
      // Establish the shared scope and discard any prior project's rows.
      const scope = requests.begin(projectId, 'scope', { trackPending: false })
      scope.finish()
    }
    const request = runRequests.begin(projectId, 'runs')
    const checkpoint = usageCheckpoint()
    const cursor = more ? nextCursor.value : null
    runsLoading.value = true
    runsError.value = null
    try {
      const page = await callOperation<components['schemas']['Page_ScheduledRunOut_']>('schedule.runs', {
        project_id: projectId, limit: 50, ...(cursor == null ? {} : { after_id: cursor }),
      })
      if (!request.isCurrent() || currentProjectId.value !== projectId) return
      const rows = page.items ?? []
      runs.value = more ? [...runs.value, ...rows.filter(row => !runs.value.some(old => old.request_id === row.request_id))] : rows
      nextCursor.value = page.next_cursor ?? null
      applyUsage(usageAfter(projectId, checkpoint))
    } catch (err) {
      if (request.isCurrent() && currentProjectId.value === projectId) runsError.value = err instanceof Error ? err.message : 'Failed to load runs'
    } finally {
      const pending = request.finish()
      if (currentProjectId.value === projectId) runsLoading.value = pending
    }
  }

  async function mutate(projectId: number, operation: string, args: Record<string, unknown>): Promise<void> {
    if (saving.value) return
    saving.value = true
    try {
      await callOperation(operation, { project_id: projectId, ...args })
      if (currentProjectId.value === projectId) await refresh(projectId)
    } finally { saving.value = false }
  }

  const save = (projectId: number, configuration: ScheduleConfiguration) => mutate(projectId, 'schedule.set', configuration)
  const toggle = (projectId: number, jobId: number, enabled: boolean) => mutate(projectId, 'schedule.toggle', { job_id: jobId, enabled })
  const remove = (projectId: number, jobId: number) => mutate(projectId, 'schedule.remove', { job_id: jobId })

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
    runRequests.invalidate()
    items.value = []
    loading.value = false
    error.value = null
    runs.value = []
    nextCursor.value = null
    runsLoading.value = false
    runsError.value = null
    currentProjectId.value = null
  }

  return {
    items,
    loading,
    error,
    currentProjectId,
    refresh,
    runs, runsLoading, runsError, nextCursor, saving,
    refreshRuns, applyUsage, save, toggle, remove,
    reset,
  }
})
