import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { apiFetch } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { useProjectsStore } from '@/stores/projects'
import HomeConsoleView from './HomeConsoleView.vue'
import { portfolioFixture } from './home/overviewFixtures'
import { activeTaskFixture, activeTasksFixture } from './home/activeTaskFixtures'
import { agentStatsFixture } from './home/agentStatsFixtures'
import { localActivityFilters } from '@/lib/agentActivity'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
vi.mock('@/lib/client', async original => ({ ...(await original<object>()), apiFetch: vi.fn() }))

beforeEach(() => {
  vi.mocked(callOperation).mockReset()
  vi.mocked(apiFetch).mockReset()
  vi.mocked(callOperation).mockImplementation(async (name, args) => {
    if (name === 'tracker.deliveryCapacity') return { scope: 'local-user-daemon', configured_limit: 7, running_count: 2, queued_count: 1, supported_min: 1, supported_max: 32 } as never
    const id = Number(args.project_id)
    if (name === 'tracker.agentStats') return agentStatsFixture({ project_id: id }) as never
    if (name === 'tracker.activeTasks') return activeTasksFixture(id, [activeTaskFixture({ project_id: id, key: 'work / beta' })]) as never
    if (name === 'project.portfolio') return { items: [portfolioFixture({ id })], next_cursor: null } as never
    return {} as never
  })
  vi.mocked(apiFetch).mockImplementation(async url => url.includes('/connections/accounts')
    ? { accounts: [{ credential_ref: 'cred_one', provider_key: 'stripe', display_name: 'My Stripe', status: 'connected', setup_required: false, last_test: { ok: false } }] } as never
    : { items: [], next_cursor: null } as never)
})
afterEach(() => { document.body.innerHTML = '' })

async function mountProject() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:id', component: HomeConsoleView },
      { path: '/projects/:id/tasks', component: { template: '<div>Task graph</div>' } },
      { path: '/projects/:id/connections', component: { template: '<div>Connections</div>' } },
    ],
  })
  await router.push('/projects/1')
  await router.isReady()
  const pinia = createPinia()
  const wrapper = mount({ template: '<RouterView :key="$route.params.id" />' }, {
    global: { plugins: [router, pinia], stubs: { AgentActivityChart: true, teleport: true } },
  })
  await flushPromises()
  return { wrapper, router, projects: useProjectsStore(pinia) }
}

describe('project Home active work', () => {
  it('uses the same daemon capacity when switching projects without writing', async () => {
    const { wrapper, router } = await mountProject()
    try {
      expect(wrapper.get('[data-popover-trigger]').text()).toBe('2 of 7 running')
      await wrapper.get('[data-popover-trigger]').trigger('click')
      await wrapper.get('input[type="number"]').setValue(12)
      await router.push('/projects/2')
      await flushPromises()
      expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
      expect(wrapper.get('[data-popover-trigger]').text()).toBe('2 of 7 running')
      const capacityCalls = vi.mocked(callOperation).mock.calls.filter(([name]) => name.includes('Capacity'))
      expect(capacityCalls.length).toBeGreaterThanOrEqual(2)
      expect(capacityCalls.every(([name, args]) => name === 'tracker.deliveryCapacity' && Object.keys(args).length === 0)).toBe(true)
    } finally { wrapper.unmount() }
  })
  it('uses the selected scope, retained Usage and connections in order, and opens its graph', async () => {
    const { wrapper, router } = await mountProject()
    try {
      expect(wrapper.findAll('[aria-label="Active tasks"], [aria-label="Agent activity"], [aria-label="Connected tools"]').map(node => node.attributes('aria-label')))
        .toEqual(['Active tasks', 'Agent activity', 'Connected tools'])
      expect(callOperation).toHaveBeenCalledWith('tracker.activeTasks', { project_id: 1, limit: 200 })
      expect(callOperation).toHaveBeenCalledWith('tracker.agentStats', { project_id: 1, ...localActivityFilters() })
      expect(wrapper.findComponent({ name: 'AgentActivitySection' }).props('summary').totals.reported_usage.total).toBe(100)
      expect(wrapper.text()).not.toContain('Delivered report')
      expect(wrapper.text()).not.toContain('Tickets by status')
      expect(wrapper.text()).not.toContain('Configured workflows')
      expect(wrapper.find('[aria-label="Connected tools"] [role="img"][aria-label="Test failed"]').exists()).toBe(true)
      expect(wrapper.get('a[href="/projects/1/connections?section=services"]').text()).toBe('Manage connections')
      expect(wrapper.find('.active-task-row__project').exists()).toBe(false)
      const active = wrapper.get('[aria-label="Active tasks"]')
      const before = active.text()
      const reads = vi.mocked(callOperation).mock.calls.filter(([name]) => name === 'tracker.activeTasks').length
      wrapper.findComponent({ name: 'AgentActivitySection' }).vm.$emit('filters', { start_date: '2026-11-02', end_date: '2026-11-05' })
      await flushPromises()
      expect(active.text()).toBe(before)
      expect(vi.mocked(callOperation).mock.calls.filter(([name]) => name === 'tracker.activeTasks')).toHaveLength(reads)
      await active.get('a').trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/projects/1/tasks')
      expect(router.currentRoute.value.query).toEqual({ view: 'graph', task: 'work / beta' })
      expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    } finally { wrapper.unmount() }
  })

  it('retains overview polling without polling task lifetime metrics or aggregate Usage', async () => {
    vi.useFakeTimers()
    const { wrapper } = await mountProject()
    try {
      const count = (operation: string) => vi.mocked(callOperation).mock.calls.filter(([name]) => name === operation).length
      const usage = count('tracker.agentStats'), active = count('tracker.activeTasks'), overview = count('project.portfolio')
      await vi.advanceTimersByTimeAsync(30_000)
      expect(count('tracker.agentStats')).toBe(usage)
      expect(count('tracker.activeTasks')).toBe(active)
      expect(count('project.portfolio')).toBeGreaterThan(overview)
    } finally { wrapper.unmount(); vi.useRealTimers() }
  })

  it('does not expose old-scope Usage or task data after switching projects', async () => {
    let resolveOld!: (value: unknown) => void
    const old = new Promise(resolve => { resolveOld = resolve })
    const existing = vi.mocked(callOperation).getMockImplementation()!
    vi.mocked(callOperation).mockImplementation(async (name, args) =>
      (name === 'tracker.activeTasks' || name === 'tracker.agentStats') && args.project_id === 1
        ? await old as never : existing(name, args))
    const { wrapper, router } = await mountProject()
    try {
      await router.push('/projects/2')
      await flushPromises()
      resolveOld(activeTasksFixture(1, [activeTaskFixture({ title: 'Stale old project work' })]))
      await flushPromises()
      expect(wrapper.text()).not.toContain('Stale old project work')
      expect(wrapper.findComponent({ name: 'AgentActivitySection' }).props('summary').project_id).toBe(2)
      expect(wrapper.get('.active-task-row__link').attributes('href')).toContain('/projects/2/tasks')
    } finally { wrapper.unmount() }
  })

  it('keeps the canonical newer project heading after a delayed overview', async () => {
    const { wrapper, projects } = await mountProject()
    try {
      projects.items = [{ ...portfolioFixture(), locale: 'en', niche: null, schedule_json: null, order_index: 0,
        name: 'Renamed canonical project', updated_at: '2026-11-11T20:00:00Z' }] as never
      await wrapper.findAll('button').find(button => button.text() === 'Refresh')!.trigger('click')
      await flushPromises()
      expect(wrapper.get('h1').text()).toContain('Renamed canonical project')
    } finally { wrapper.unmount() }
  })
})
