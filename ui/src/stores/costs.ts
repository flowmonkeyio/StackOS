// Costs store — read-only monthly cost and budget data.

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { apiFetch, formatApiError } from '@/lib/client'
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
  const loading = ref(false)
  const error = ref<string | null>(null)
  const currentProjectId = ref<number | null>(null)
  const month = ref<string | null>(null)
  const requests = createProjectRequestGate((projectId) => {
    cost.value = null
    budgets.value = []
    history.value = []
    error.value = null
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
    month.value = ym
    loading.value = true
    error.value = null
    try {
      const params = new URLSearchParams({ month: ym })
      const nextCost = await apiFetch<CostResponse>(
        `/api/v1/projects/${projectId}/cost?${params.toString()}`,
      )
      if (request.isCurrent()) cost.value = nextCost
    } catch (err) {
      if (request.isCurrent()) {
        error.value = formatApiError(err, 'failed to load cost')
      }
    } finally {
      loading.value = request.finish()
    }
  }

  async function refreshBudgets(projectId: number): Promise<void> {
    const request = requests.begin(projectId, 'budgets')
    loading.value = true
    error.value = null
    try {
      const nextBudgets = await apiFetch<IntegrationBudget[]>(
        `/api/v1/projects/${projectId}/budgets`,
      )
      if (request.isCurrent()) budgets.value = nextBudgets
    } catch (err) {
      if (request.isCurrent()) {
        error.value = formatApiError(err, 'failed to load budgets')
      }
    } finally {
      loading.value = request.finish()
    }
  }

  async function refreshHistory(projectId: number, months = 12): Promise<void> {
    const request = requests.begin(projectId, 'history')
    loading.value = true
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
      for (const result of results) {
        if (result.status === 'fulfilled') next.push(result.value)
      }
      next.sort((a, b) => (a.period_start < b.period_start ? -1 : 1))
      if (request.isCurrent()) history.value = next
    } finally {
      loading.value = request.finish()
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
    loading.value = false
    error.value = null
    currentProjectId.value = null
    month.value = null
  }

  return {
    cost,
    budgets,
    history,
    loading,
    error,
    currentProjectId,
    month,
    hasNoSpendYet,
    refreshCost,
    refreshBudgets,
    refreshHistory,
    reset,
  }
})
