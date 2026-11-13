// Smoke tests for CostBudgetTab.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import CostBudgetTab from './CostBudgetTab.vue'

const ORIG_FETCH = globalThis.fetch
const wrappers: ReturnType<typeof mount>[] = []
const costFor = (input: unknown, total_usd = 7) => ({ by_integration: { dataforseo: total_usd }, total_usd,
  period_start: `${new URL(String(input), 'http://localhost').searchParams.get('month')}-01T00:00:00Z`, period_end: '2026-12-01T00:00:00Z' })
async function open() {
  const router = mountTab()
  await router.isReady()
  const wrapper = mount(CostBudgetTab, { global: { plugins: [router] } })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}

function mountTab() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/projects/:id/cost-budget',
        name: 'project-detail-cost-budget',
        component: CostBudgetTab,
      },
    ],
  })
  void router.push('/projects/1/cost-budget')
  return router
}

describe('CostBudgetTab', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })
  afterEach(() => {
    wrappers.splice(0).forEach(wrapper => wrapper.unmount())
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('renders the no-spend badge when total_usd=0', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url.includes('/cost')) {
        return new Response(
          JSON.stringify({
            by_integration: { dataforseo: 0 },
            total_usd: 0,
            period_start: '2026-05-01T00:00:00Z',
            period_end: '2026-06-01T00:00:00Z',
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        )
      }
      return new Response(JSON.stringify({ detail: 'not found' }), {
        status: 404,
        headers: { 'content-type': 'application/json' },
      })
    }) as typeof fetch
    const router = mountTab()
    await router.isReady()
    const w = mount(CostBudgetTab, { global: { plugins: [router] } })
    wrappers.push(w)
    await new Promise((r) => setTimeout(r, 50))
    expect(w.text()).toContain('Current month')
    expect(w.text()).toContain('No spend recorded yet')
    expect(w.text()).toContain('Budget caps')
  })

  it('shows unknown values while initially pending or unavailable, and accepted zero only after success', async () => {
    let release!: () => void
    const pending = new Promise<void>(resolve => { release = resolve })
    globalThis.fetch = vi.fn(async () => { await pending; return Response.json({ detail: 'Unavailable' }, { status: 503 }) }) as typeof fetch
    const w = await open()
    expect(w.findAll('.ui-metric-card .ui-skeleton')).toHaveLength(4)
    expect(w.text()).not.toMatch(/\$0\.00|No budget caps|No cost history yet|No spend recorded yet/)
    release(); await flushPromises()
    expect(w.findAll('.ui-metric-card').every(card => card.text().includes('—'))).toBe(true)
    expect(w.text()).toContain('History is incomplete')
    expect(w.text()).toContain('Current spend is unavailable')
    expect(w.text()).not.toMatch(/\$0\.00|No budget caps|No cost history yet|No spend recorded yet/)
    globalThis.fetch = vi.fn(async input => Response.json(String(input).includes('/budgets') ? [] : costFor(input, 0))) as typeof fetch
    await w.get('button').trigger('click'); await flushPromises()
    expect(w.text()).toContain('$0.00')
    expect(w.text()).toContain('No budget caps')
    expect(w.text()).toContain('No cost history yet')
  })

  it('retains accepted metric/table nodes during refresh and marks failed retained history months stale', async () => {
    globalThis.fetch = vi.fn(async input => Response.json(String(input).includes('/budgets') ? [] : costFor(input))) as typeof fetch
    const w = await open()
    const table = w.get('table').element
    const metric = w.get('.ui-metric-card span.font-semibold').element
    let release!: () => void
    const pending = new Promise<void>(resolve => { release = resolve })
    globalThis.fetch = vi.fn(async () => { await pending; return Response.json({ detail: 'Refresh unavailable' }, { status: 503 }) }) as typeof fetch
    await w.get('button').trigger('click'); await flushPromises()
    expect(w.get('table').element).toBe(table)
    expect(w.get('.ui-metric-card span.font-semibold').element).toBe(metric)
    expect(w.text()).toContain('$7.00')
    expect(w.text()).toContain('No budget caps')
    release(); await flushPromises()
    expect(w.text()).toContain('Showing last accepted spend')
    expect(w.text()).toContain('(stale)')
    expect(w.text()).not.toContain('No cost history yet')
    expect(w.get('table').element).toBe(table)
  })

  it('keeps the accepted zero-history presentation mounted through failure and recovery', async () => {
    globalThis.fetch = vi.fn(async input => Response.json(String(input).includes('/budgets') ? [] : costFor(input, 0))) as typeof fetch
    const w = await open()
    const history = w.get('section[aria-label="12-month history"]')
    const empty = history.get('.ui-empty-state').element
    let release!: () => void
    const pending = new Promise<void>(resolve => { release = resolve })
    globalThis.fetch = vi.fn(async input => {
      if (String(input).includes('/budgets')) return Response.json([])
      await pending
      return Response.json({ detail: 'History refresh failed' }, { status: 503 })
    }) as typeof fetch
    await w.get('button').trigger('click'); await flushPromises()
    expect(history.get('.ui-empty-state').element).toBe(empty)
    release(); await flushPromises()
    expect(history.get('.ui-empty-state').element).toBe(empty)
    expect(history.findComponent({ name: 'UiSparkline' }).exists()).toBe(false)
    expect(history.text()).toContain('History is incomplete')
    globalThis.fetch = vi.fn(async input => Response.json(String(input).includes('/budgets') ? [] : costFor(input, 0))) as typeof fetch
    await w.get('button').trigger('click'); await flushPromises()
    expect(history.get('.ui-empty-state').element).toBe(empty)
    expect(history.text()).not.toContain('History is incomplete')
  })
})
