// @vitest-environment node

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

  it.each(['cost', 'budgets'] as const)('keeps sibling errors independent when %s completes last', async last => {
    const slow = deferred<void>()
    globalThis.fetch = vi.fn(async input => {
      const isCost = String(input).includes('/cost?')
      if (isCost === (last === 'cost')) { await slow.promise; return Response.json(isCost ? ZERO_COST : []) }
      return Response.json({ detail: 'Sibling read unavailable' }, { status: 503 })
    }) as typeof fetch
    const store = useCostsStore()
    const pending = last === 'cost' ? store.refreshCost(1) : store.refreshBudgets(1)
    await (last === 'cost' ? store.refreshBudgets(1) : store.refreshCost(1))
    expect(store.error).toContain('Sibling read unavailable')
    expect(store.loading).toBe(true)
    slow.resolve(); await pending
    expect(store.error).toContain('Sibling read unavailable')
    globalThis.fetch = vi.fn(async () => Response.json(last === 'cost' ? ZERO_COST : [])) as typeof fetch
    await (last === 'cost' ? store.refreshCost(1) : store.refreshBudgets(1))
    expect(store.error).toContain('Sibling read unavailable')
    expect(store.loading).toBe(false)
  })

  it('reports all and partial failed history months while retaining accepted values for failed months', async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-11-13T00:00:00Z'))
    try {
      const store = useCostsStore()
      globalThis.fetch = vi.fn(async input => {
        const ym = new URL(String(input), 'http://localhost').searchParams.get('month')!
        return Response.json({ ...ZERO_COST, period_start: `${ym}-01T00:00:00Z`, total_usd: 7 })
      }) as typeof fetch
      await store.refreshHistory(1, 2)
      globalThis.fetch = vi.fn(async input => String(input).includes('month=2026-11')
        ? Response.json({ detail: 'November unavailable' }, { status: 503 })
        : Response.json({ ...ZERO_COST, period_start: '2026-10-01T00:00:00Z', total_usd: 8 })) as typeof fetch
      await store.refreshHistory(1, 2)
      expect(store.history.map(row => row.total_usd)).toEqual([8, 7])
      expect(store.historyErrors['2026-11']).toContain('November unavailable')
      globalThis.fetch = vi.fn(async () => Response.json({ detail: 'History unavailable' }, { status: 503 })) as typeof fetch
      await store.refreshHistory(2, 2)
      expect(store.history).toEqual([])
      expect(Object.keys(store.historyErrors)).toEqual(['2026-11', '2026-10'])
      expect(store.error).toContain('History unavailable')
    } finally { vi.useRealTimers() }
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

  it('clears incompatible months and ignores obsolete same-key pending and scope-exit results', async () => {
    globalThis.fetch = vi.fn(async () => Response.json(ZERO_COST)) as typeof fetch
    const store = useCostsStore()
    await store.refreshCost(1, '2026-05')
    const late = deferred<void>()
    globalThis.fetch = vi.fn(async () => { await late.promise; return Response.json(ZERO_COST) }) as typeof fetch
    const old = store.refreshCost(1, '2026-06')
    expect(store.cost).toBeNull()
    globalThis.fetch = vi.fn(async () => Response.json({ ...ZERO_COST, total_usd: 8 })) as typeof fetch
    await store.refreshCost(1, '2026-07')
    expect(store.costLoading).toBe(false)
    late.resolve(); await old
    expect(store.cost?.total_usd).toBe(8)
    expect(store.month).toBe('2026-07')
    const history = deferred<void>()
    globalThis.fetch = vi.fn(async () => { await history.promise; return Response.json(ZERO_COST) }) as typeof fetch
    const pending = store.refreshHistory(1, 2)
    store.reset()
    history.resolve(); await pending
    expect(store.history).toEqual([])
    expect(store.currentProjectId).toBeNull()
    expect(store.loading).toBe(false)
  })

  it('establishes empty history only from a complete read and retires it on range changes or denial', async () => {
    const store = useCostsStore()
    let count = 0
    globalThis.fetch = vi.fn(async input => {
      const month = new URL(String(input), 'http://localhost').searchParams.get('month')!
      const row = { ...ZERO_COST, period_start: `${month}-01T00:00:00Z` }
      return ++count === 1 ? Response.json(row) : Response.json({ detail: 'Partial history' }, { status: 503 })
    }) as typeof fetch
    await store.refreshHistory(1, 2)
    expect(store.historyEmpty).toBe(false)
    globalThis.fetch = vi.fn(async input => {
      const month = new URL(String(input), 'http://localhost').searchParams.get('month')!
      return Response.json({ ...ZERO_COST, period_start: `${month}-01T00:00:00Z` })
    }) as typeof fetch
    await store.refreshHistory(1, 2)
    expect(store.historyEmpty).toBe(true)
    globalThis.fetch = vi.fn(async () => Response.json({ detail: 'Temporary' }, { status: 503 })) as typeof fetch
    await store.refreshHistory(1, 2)
    expect(store.historyEmpty).toBe(true)
    const changedRange = store.refreshHistory(1, 3)
    expect(store.historyEmpty).toBe(false)
    await changedRange
    globalThis.fetch = vi.fn(async input => {
      const month = new URL(String(input), 'http://localhost').searchParams.get('month')!
      return Response.json({ ...ZERO_COST, period_start: `${month}-01T00:00:00Z` })
    }) as typeof fetch
    await store.refreshHistory(1, 3)
    expect(store.historyEmpty).toBe(true)
    globalThis.fetch = vi.fn(async () => Response.json({ detail: 'Denied' }, { status: 403 })) as typeof fetch
    await store.refreshHistory(1, 3)
    expect(store.historyEmpty).toBe(false)
    expect(store.history).toEqual([])
  })
})

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  const promise = new Promise<T>((next) => {
    resolve = next
  })
  return { promise, resolve }
}
