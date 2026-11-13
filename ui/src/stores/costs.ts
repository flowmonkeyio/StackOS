// Costs store — read-only monthly cost and budget data.

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError, apiFetch, formatApiError } from '@/lib/client'
import { createProjectRequestGate } from '@/lib/stackos/projectRequestGate'
import type { components } from '@/api'

export type CostResponse = components['schemas']['CostResponse']
export type IntegrationBudget = components['schemas']['IntegrationBudgetOut']

export const INTEGRATION_KINDS = [
  'dataforseo',
  'firecrawl',
  'openai-images',
  'reddit',
  'google-paa',
  'jina',
  'ahrefs',
] as const

export type IntegrationKind = (typeof INTEGRATION_KINDS)[number]

export const useCostsStore = defineStore('costs', () => {
  const cost = ref<CostResponse | null>(null)
  const budgets = ref<IntegrationBudget[]>([])
  const history = ref<CostResponse[]>([])
  const costLoading = ref(false)
  const budgetsLoading = ref(false)
  const historyLoading = ref(false)
  const budgetsLoaded = ref(false)
  const costError = ref<string | null>(null)
  const budgetsError = ref<string | null>(null)
  const historyErrors = ref<Record<string, string>>({})
  const loading = computed(() => costLoading.value || budgetsLoading.value || historyLoading.value)
  const error = computed(() => costError.value || budgetsError.value || Object.values(historyErrors.value)[0] || null)
  const currentProjectId = ref<number | null>(null)
  const month = ref<string | null>(null)
  const requests = createProjectRequestGate((projectId) => {
    cost.value = null
    budgets.value = []
    history.value = []
    costLoading.value = budgetsLoading.value = historyLoading.value = false
    budgetsLoaded.value = false
    costError.value = budgetsError.value = null
    historyErrors.value = {}
    currentProjectId.value = projectId
    month.value = null
  })

  function defaultMonth(): string {
    const d = new Date()
    return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`
  }

  async function refreshCost(projectId: number, ym: string | null = null): Promise<void> {
    const request = requests.begin(projectId, 'cost')
    if (!ym) ym = defaultMonth()
    if (month.value !== ym) cost.value = null
    month.value = ym
    costLoading.value = true
    costError.value = null
    try {
      const params = new URLSearchParams({ month: ym })
      const nextCost = await apiFetch<CostResponse>(
        `/api/v1/projects/${projectId}/cost?${params.toString()}`,
      )
      if (request.isCurrent()) cost.value = nextCost
    } catch (err) {
      if (request.isCurrent()) {
        costError.value = formatApiError(err, 'failed to load cost')
        if (err instanceof ApiError && [401, 403, 404].includes(err.status)) cost.value = null
      }
    } finally {
      const current = request.isCurrent()
      request.finish()
      if (current) costLoading.value = false
    }
  }

  async function refreshBudgets(projectId: number): Promise<void> {
    const request = requests.begin(projectId, 'budgets')
    budgetsLoading.value = true
    budgetsError.value = null
    try {
      const nextBudgets = await apiFetch<IntegrationBudget[]>(
        `/api/v1/projects/${projectId}/budgets`,
      )
      if (request.isCurrent()) {
        budgets.value = nextBudgets
        budgetsLoaded.value = true
      }
    } catch (err) {
      if (request.isCurrent()) {
        budgetsError.value = formatApiError(err, 'failed to load budgets')
        if (err instanceof ApiError && [401, 403, 404].includes(err.status)) {
          budgets.value = []
          budgetsLoaded.value = false
        }
      }
    } finally {
      const current = request.isCurrent()
      request.finish()
      if (current) budgetsLoading.value = false
    }
  }

  async function refreshHistory(projectId: number, months = 12): Promise<void> {
    const request = requests.begin(projectId, 'history')
    historyLoading.value = true
    try {
      const now = new Date()
      const ymRange: string[] = []
      for (let i = 0; i < months; i++) {
        const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - i, 1))
        const ym = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`
        ymRange.push(ym)
      }
      const results = await Promise.allSettled(
        ymRange.map(async (ym) => {
          const params = new URLSearchParams({ month: ym })
          return apiFetch<CostResponse>(
            `/api/v1/projects/${projectId}/cost?${params.toString()}`,
          )
        }),
      )
      const next: CostResponse[] = []
      const failures: Record<string, string> = {}
      if (request.isCurrent()) {
        for (const [index, result] of results.entries()) {
          const ym = ymRange[index]!
          if (result.status === 'fulfilled') next.push(result.value)
          else {
            failures[ym] = formatApiError(result.reason, 'Failed to load history')
            const previous = history.value.find(row => row.period_start.startsWith(ym))
            const denied = result.reason instanceof ApiError && [401, 403, 404].includes(result.reason.status)
            if (previous && !denied) next.push(previous)
          }
        }
        next.sort((a, b) => a.period_start.localeCompare(b.period_start))
        history.value = next
        historyErrors.value = failures
      }
    } finally {
      const current = request.isCurrent()
      request.finish()
      if (current) historyLoading.value = false
    }
  }

  const hasNoSpendYet = computed<boolean>(() => {
    if (!cost.value) return false
    return cost.value.total_usd <= 0
  })

  function reset(): void {
    requests.invalidate()
    cost.value = null
    budgets.value = []
    history.value = []
    costLoading.value = budgetsLoading.value = historyLoading.value = false
    budgetsLoaded.value = false
    costError.value = budgetsError.value = null
    historyErrors.value = {}
    currentProjectId.value = null
    month.value = null
  }

  return {
    cost,
    budgets,
    history,
    loading,
    error,
    costLoading,
    budgetsLoading,
    historyLoading,
    budgetsLoaded,
    costError,
    budgetsError,
    historyErrors,
    currentProjectId,
    month,
    hasNoSpendYet,
    refreshCost,
    refreshBudgets,
    refreshHistory,
    reset,
  }
})
