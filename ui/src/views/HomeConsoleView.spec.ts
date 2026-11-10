import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { apiFetch } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import HomeConsoleView from './HomeConsoleView.vue'
import { portfolioFixture, ticketSummaryFixture } from './home/overviewFixtures'
import { agentStatsFixture } from './home/agentStatsFixtures'
import { localActivityFilters } from '@/lib/agentActivity'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
vi.mock('@/lib/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))

beforeEach(() => {
  vi.mocked(callOperation).mockReset()
  vi.mocked(apiFetch).mockReset()
  vi.mocked(callOperation).mockImplementation(async (name, args) => {
    if (name === 'tracker.agentStats') return agentStatsFixture({ project_id: Number(args.project_id) }) as never
    if (name === 'tracker.ticketCounts')
      return ticketSummaryFixture({ project_id: Number(args.project_id), is_active: null }) as never
    if (name === 'project.portfolio')
      return {
        items: [portfolioFixture({ id: Number(args.project_id) })],
        next_cursor: null,
      } as never
    if (name === 'workflowExtension.list')
      return {
        extensions: [
          { workflow_key: 'finance.followups', enabled: true },
          { workflow_key: 'seo.audit', enabled: false },
        ],
      } as never
    return {} as never
  })
  vi.mocked(apiFetch).mockImplementation(async (url) => {
    if (url.includes('/connections/accounts'))
      return {
        accounts: [
          {
            credential_ref: 'cred_one',
            provider_key: 'stripe',
            display_name: 'My Stripe',
            status: 'connected',
            setup_required: false,
            last_test: { ok: false },
          },
        ],
      } as never
    if (url.includes('/workflow-templates'))
      return {
        templates: [
          { key: 'finance.followups', name: 'Finance follow-ups', plugin_slug: 'finance' },
          { key: 'seo.audit', name: 'SEO audit', plugin_slug: 'seo' },
          { key: 'branding.write', name: 'Writing', plugin_slug: 'branding' },
        ],
      } as never
    return {
      items: [{ ...portfolioFixture(), locale: 'en', niche: null, schedule_json: null }],
      next_cursor: null,
    } as never
  })
})
afterEach(() => {
  document.body.innerHTML = ''
})

async function mountProject() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:id', component: HomeConsoleView },
      { path: '/projects/:id/tasks', component: { template: '<div>Work tickets</div>' } },
      { path: '/projects/:id/workflows/:templateKey', component: { template: '<div>Workflow inspection</div>' } },
    ],
  })
  await router.push('/projects/1')
  await router.isReady()
  const wrapper = mount(
    { template: '<RouterView :key="$route.params.id" />' },
    {
      global: {
        plugins: [router, createPinia()],
        stubs: { TicketStatusChart: true, AgentActivityChart: true, teleport: true },
      },
    },
  )
  await flushPromises()
  return { wrapper, router }
}

describe('project ticket overview', () => {
  it.each(['project', 'repo', 'user'])('keeps the exact configured %s workflow when it has no plugin', async (source) => {
    const key = `${source}.weekly-review`
    const operations = vi.mocked(callOperation).getMockImplementation()!
    vi.mocked(callOperation).mockImplementation(async (name, args) => name === 'workflowExtension.list'
      ? { extensions: [{ workflow_key: key, enabled: true }] } as never
      : operations(name, args))
    const fetch = vi.mocked(apiFetch).getMockImplementation()!
    vi.mocked(apiFetch).mockImplementation(async (url, options) => url.includes('/workflow-templates')
      ? { templates: [{ key, name: 'Weekly review', source, plugin_slug: null }] } as never
      : fetch(url, options))
    const { wrapper, router } = await mountProject()
    try {
      const link = wrapper.get(`a[href="/projects/1/workflows/${key}"]`)
      await link.trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.fullPath).toBe(`/projects/1/workflows/${key}`)
      expect(wrapper.text()).toContain('Workflow inspection')
    } finally { wrapper.unmount() }
  })

  it('retains overview/count polling while usage refresh stays with its shared owner', async () => {
    vi.useFakeTimers()
    const { wrapper } = await mountProject()
    try {
      const count = (operation: string) => vi.mocked(callOperation).mock.calls.filter(([name]) => name === operation).length
      const usage = count('tracker.agentStats'), tickets = count('tracker.ticketCounts')
      expect(usage).toBeGreaterThan(0)
      await vi.advanceTimersByTimeAsync(30_000)
      expect(count('tracker.agentStats')).toBe(usage)
      expect(count('tracker.ticketCounts')).toBeGreaterThan(tickets)
    } finally { wrapper.unmount(); vi.useRealTimers() }
  })
  it('shows current ticket counts and terminal work, and only actual configured workflows/Account state', async () => {
    const { wrapper, router } = await mountProject()
    expect(callOperation).toHaveBeenCalledWith('tracker.ticketCounts', { project_id: 1 })
    expect(callOperation).toHaveBeenCalledWith('tracker.agentStats', { project_id: 1, ...localActivityFilters() })
    expect(wrapper.findComponent({ name: 'AgentActivitySection' }).props('summary').totals.reported_usage.total).toBe(100)
    await wrapper.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click')
    await flushPromises()
    expect(vi.mocked(callOperation).mock.calls.filter(([name]) => name === 'tracker.agentStats')).toHaveLength(2)
    expect(wrapper.text()).toContain('Tickets by status')
    expect(wrapper.text()).toContain('Current snapshot · 4 tickets')
    expect(wrapper.text()).not.toContain('Latest action calls')
    expect(wrapper.text()).not.toContain('7 days')
    expect(wrapper.findComponent({ name: 'TicketStatusChart' }).props('total')).toBe(4)
    expect(wrapper.text()).toContain('Delivered report')
    expect(wrapper.text()).toContain('Complete')
    expect(wrapper.text()).toContain('Test failed')
    expect(wrapper.text()).toContain('Finance follow-ups')
    expect(wrapper.text()).not.toContain('SEO audit')
    expect(wrapper.text()).not.toContain('Writing')
    expect(wrapper.text()).not.toContain('Agents are working')
    expect(
      wrapper.find('a[href="/projects/1/plugins/finance/workflows/finance.followups"]').exists(),
    ).toBe(true)
    wrapper.findComponent({ name: 'TicketStatusChart' }).vm.$emit('select', 'failed')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/projects/1/tasks')
    expect(router.currentRoute.value.query).toEqual({ ticket_status: 'failed' })
    wrapper.unmount()
  })

  it('does not expose a late response from the previous project', async () => {
    let resolveOld!: (value: unknown) => void
    const old = new Promise((resolve) => {
      resolveOld = resolve
    })
    const existing = vi.mocked(callOperation).getMockImplementation()!
    vi.mocked(callOperation).mockImplementation(async (name, args) => {
      if ((name === 'tracker.ticketCounts' || name === 'tracker.agentStats') && args.project_id === 1) return (await old) as never
      if (name === 'tracker.ticketCounts')
        return ticketSummaryFixture({ project_id: 2, total_count: 9 }) as never
      return existing(name, args)
    })
    const { wrapper, router } = await mountProject()
    await router.push('/projects/2')
    await flushPromises()
    resolveOld(ticketSummaryFixture({ project_id: 1, total_count: 99 }))
    await flushPromises()
    expect(wrapper.findComponent({ name: 'TicketStatusChart' }).props('total')).toBe(9)
    expect(wrapper.findComponent({ name: 'AgentActivitySection' }).props('summary').project_id).toBe(2)
    wrapper.unmount()
  })
})
