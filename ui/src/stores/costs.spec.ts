import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useCostsStore } from './costs'

const ORIG_FETCH = globalThis.fetch

const ZERO_COST = {
  by_integration: { dataforseo: 0, firecrawl: 0 },
  total_usd: 0,
  period_start: '2026-05-01T00:00:00Z',
  period_end: '2026-06-01T00:00:00Z',
}

const BUDGET = {
  id: 1,
  project_id: 1,
  kind: 'dataforseo',
  monthly_budget_usd: 50,
  alert_threshold_pct: 80,
  current_month_spend: 0,
  current_month_calls: 0,
  qps: 1,
  last_reset: '2026-05-01T00:00:00Z',
}

describe('costs store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })
  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('refreshCost() loads the response and exposes hasNoSpendYet', async () => {
    globalThis.fetch = vi.fn(async () => {
      return new Response(JSON.stringify(ZERO_COST), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    }) as typeof fetch
    const store = useCostsStore()
    await store.refreshCost(1, '2026-05')
    expect(store.cost?.total_usd).toBe(0)
    expect(store.hasNoSpendYet).toBe(true)
  })

  it('refreshBudgets() loads configured budget rows without probing missing kinds', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url.endsWith('/api/v1/projects/1/budgets')) {
        return new Response(JSON.stringify([BUDGET]), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }
      return new Response(JSON.stringify({ detail: 'unexpected request' }), {
        status: 500,
        headers: { 'content-type': 'application/json' },
      })
    }) as typeof fetch
    const store = useCostsStore()
    await store.refreshBudgets(1)
    expect(store.budgets.length).toBe(1)
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
  })

  it('refreshBudgets() treats an empty list as no configured budget rows', async () => {
    globalThis.fetch = vi.fn(async () => {
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    }) as typeof fetch
    const store = useCostsStore()
    await store.refreshBudgets(1)
    expect(store.budgets).toEqual([])
    expect(store.error).toBeNull()
  })

  it('refreshBudgets() surfaces route failures instead of hiding them as empty state', async () => {
    globalThis.fetch = vi.fn(async () => {
      return new Response(JSON.stringify({ detail: 'Not Found' }), {
        status: 404,
        headers: { 'content-type': 'application/json' },
      })
    }) as typeof fetch
    const store = useCostsStore()
    await store.refreshBudgets(1)
    expect(store.budgets).toEqual([])
    expect(store.error).toContain('Not Found')
  })

  it('does not expose budget mutation methods to the UI store', () => {
    const store = useCostsStore()
    expect((store as unknown as Record<string, unknown>).upsertBudget).toBeUndefined()
  })

  it('refreshHistory() pulls 12 months in parallel', async () => {
    let calls = 0
    globalThis.fetch = vi.fn(async () => {
      calls += 1
      return new Response(JSON.stringify(ZERO_COST), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    }) as typeof fetch
    const store = useCostsStore()
    await store.refreshHistory(1, 12)
    expect(calls).toBe(12)
    expect(store.history.length).toBe(12)
  })

  it('keeps cost data scoped to the newest project request', async () => {
    const projectOne = deferred<void>()
    globalThis.fetch = vi.fn(async (input) => {
      const projectId = String(input).includes('/projects/1/') ? 1 : 2
      if (projectId === 1) await projectOne.promise
      return new Response(
        JSON.stringify({ ...ZERO_COST, total_usd: projectId }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      )
    }) as typeof fetch
    const store = useCostsStore()

    const older = store.refreshCost(1, '2026-05')
    await store.refreshCost(2, '2026-05')
    projectOne.resolve()
    await older

    expect(store.currentProjectId).toBe(2)
    expect(store.cost?.total_usd).toBe(2)
  })
})

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  const promise = new Promise<T>((next) => {
    resolve = next
  })
  return { promise, resolve }
}
