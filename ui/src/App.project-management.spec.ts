import { afterEach, expect, it, vi } from 'vitest'
import { DOMWrapper, enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import App from './App.vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import ProjectRenameDialog from '@/components/ProjectRenameDialog.vue'
import { useProjectsStore, type Project } from '@/stores/projects'
import { useToastsStore } from '@/stores/toasts'
import HomeConsoleView from '@/views/HomeConsoleView.vue'
import HomeView from '@/views/HomeView.vue'
import HomeProjectsSection from '@/views/home/HomeProjectsSection.vue'
import { portfolioFixture, ticketSummaryFixture } from '@/views/home/overviewFixtures'
import { agentStatsFixture } from '@/views/home/agentStatsFixtures'

enableAutoUnmount(afterEach)
const originalFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = originalFetch
  useToastsStore().clear()
  vi.restoreAllMocks()
})

it('routes menu intents through the real shell, shared store and canonical operations without changing selection', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const projects = useProjectsStore()
  const rows: Project[] = [1, 2].map((id) => ({
    id, order_index: id, name: id === 1 ? 'Alpha' : 'Beta', slug: `project-${id}`,
    domain: 'example.test', niche: null, locale: 'en-US', is_active: true,
    schedule_json: null, created_at: '', updated_at: '',
  }))
  projects.items = rows
  projects.activeProjectId = 1
  const requests: { url: string; args: Record<string, unknown> }[] = []
  let ordered = rows
  globalThis.fetch = vi.fn(async (input, init) => {
    const url = String(input)
    if (url.startsWith('/api/v1/projects?')) return json({ items: ordered, total_estimate: 2, next_cursor: null })
    if (url.startsWith('/api/v1/operations/')) {
      const args = JSON.parse(String(init?.body)).arguments
      requests.push({ url, args })
      if (url.includes('project.update')) {
        const target = ordered.find((project) => project.id === args.project_id)!
        ordered = ordered.map((project) => project.id === target.id ? { ...project, name: args.patch.name } : project)
        return json({ data: { ...target, name: args.patch.name } })
      }
      if (url.includes('project.move')) {
        ordered = [{ ...ordered[1], order_index: 0 }, ordered[0]]
        return json({ data: ordered[0] })
      }
    }
    return json([])
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { template: '<div />' } },
    { path: '/projects/:id/connections', component: { template: '<div />' } },
  ] })
  await router.push('/projects/1/connections')
  const app = mount(App, { attachTo: document.body, global: { plugins: [pinia, router], stubs: { DesktopUpdatePrompt: true } } })
  await flushPromises()
  const switcher = app.getComponent(ProjectSwitcher)
  await switcher.get('button').trigger('click')
  await flushPromises()
  await switcher.get('button[aria-label="Actions for Alpha"]').trigger('click')
  await switcher.get('[role="menu"]').findAll('button')[0].trigger('click')
  const dialog = new DOMWrapper(document.querySelector<HTMLElement>('.ui-dialog__overlay')!)
  await dialog.get('input').trigger('mousedown')
  expect(switcher.find('[aria-label="Projects"]').exists()).toBe(true)
  await dialog.get('input').setValue('Alpha Studio')
  await dialog.get('form').trigger('submit')
  await flushPromises()
  expect(app.findComponent(ProjectRenameDialog).exists()).toBe(false)
  expect(switcher.get('button').text()).toContain('Alpha Studio')
  expect(document.activeElement).toBe(switcher.get('button[aria-label="Actions for Alpha Studio"]').element)
  expect(projects.activeProject?.name).toBe('Alpha Studio')
  expect(router.currentRoute.value.fullPath).toBe('/projects/1/connections')
  await switcher.get('button[aria-label="Actions for Beta"]').trigger('click')
  await switcher.get('[role="menu"]').findAll('button')[1].trigger('click')
  await flushPromises()
  expect(projects.items.map((project) => project.id)).toEqual([2, 1])
  expect(projects.activeProjectId).toBe(1)
  expect(requests).toEqual([
    { url: '/api/v1/operations/project.update/call', args: { response_mode: 'raw', project_id: 1, scope: 'catalog', patch: { name: 'Alpha Studio' } } },
    { url: '/api/v1/operations/project.move/call', args: { response_mode: 'raw', project_id: 2, scope: 'catalog', placement: 'before', anchor_id: 1 } },
  ])
  // Opening confirmation emits only an exact target intent; no destructive write yet.
  await switcher.get('button[aria-label="Actions for Beta"]').trigger('click')
  await switcher.get('[role="menu"]').findAll('button').at(-1)!.trigger('click')
  expect(switcher.emitted('delete')).toEqual([[2]])
  expect(requests).toHaveLength(2)
  expect(router.currentRoute.value.fullPath).toBe('/projects/1/connections')
})

it.each(['/projects/1', '/'])(
  'updates the real route label after rename and retains it across a late portfolio response on %s',
  async (path) => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const projects = useProjectsStore()
    let project: Project = {
      ...portfolioFixture({ name: 'Alpha' }),
      order_index: 1, locale: 'en-US', niche: null, schedule_json: null,
    }
    projects.items = [project]
    projects.activeProjectId = project.id
    let deferPortfolio = false
    let finishPortfolio!: () => void
    const writes: unknown[] = []
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (url.startsWith('/api/v1/projects?')) return json({ items: [project], next_cursor: null, total_estimate: 1 })
      if (url.includes('/operations/project.update/')) {
        const args = JSON.parse(String(init?.body)).arguments
        writes.push(args)
        project = { ...project, name: args.patch.name }
        return json({ data: project })
      }
      if (url.includes('/operations/project.portfolio/')) {
        const page = { items: [portfolioFixture({ ...project })], next_cursor: null, total_estimate: 1 }
        if (deferPortfolio) return new Promise<Response>((resolve) => { finishPortfolio = () => resolve(json(page)) })
        return json(page)
      }
      if (url.includes('tracker.ticketCounts')) return json(ticketSummaryFixture())
      if (url.includes('tracker.agentStats')) return json(agentStatsFixture())
      if (url.includes('workflowExtension.list')) return json({ extensions: [] })
      if (url.includes('/workflow-templates')) return json({ templates: [] })
      if (url.includes('/connections/accounts')) return json({ accounts: [] })
      return json([])
    })
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/', component: HomeView },
      { path: '/projects/:id', component: HomeConsoleView },
    ] })
    await router.push(path)
    await router.isReady()
    const app = mount(App, {
      attachTo: document.body,
      global: { plugins: [pinia, router], stubs: {
        DesktopUpdatePrompt: true, HomeSystemStatusCard: true, AgentActivitySection: true,
        HomePortfolioOverview: true, TicketStatusChart: true,
      } },
    })
    await flushPromises()
    const view = path === '/' ? app.getComponent(HomeView) : app.getComponent(HomeConsoleView)
    const visibleLabel = () => path === '/'
      ? app.getComponent(HomeProjectsSection).get('table tbody tr td').text()
      : view.get('h1').text()
    expect(visibleLabel()).toContain('Alpha')

    // This read started with the old name and will finish after the rename.
    deferPortfolio = true
    await view.findAll('button').find((button) => button.text() === 'Refresh')!.trigger('click')
    await flushPromises()
    expect(finishPortfolio).toBeTypeOf('function')
    const selector = app.getComponent(ProjectSwitcher)
    await selector.get('button').trigger('click')
    await flushPromises()
    await selector.get('button[aria-label="Actions for Alpha"]').trigger('click')
    await selector.get('[role="menu"]').findAll('button')[0].trigger('click')
    const dialog = new DOMWrapper(document.querySelector<HTMLElement>('.ui-dialog__overlay')!)
    await dialog.get('input').setValue('Alpha Studio')
    await dialog.get('form').trigger('submit')
    await flushPromises()

    expect(visibleLabel()).toContain('Alpha Studio')
    expect(app.findComponent(ProjectRenameDialog).exists()).toBe(false)
    expect(selector.get('button').text()).toContain('Alpha Studio')
    expect(writes).toEqual([{ response_mode: 'raw', project_id: 1, scope: 'catalog', patch: { name: 'Alpha Studio' } }])
    expect(projects.activeProjectId).toBe(1)
    expect(projects.getById(1)?.slug).toBe('business-one')
    expect(router.currentRoute.value.fullPath).toBe(path)

    finishPortfolio()
    await flushPromises()
    expect(visibleLabel()).toContain('Alpha Studio')
    expect(projects.activeProject?.name).toBe('Alpha Studio')
    expect(router.currentRoute.value.fullPath).toBe(path)
  },
)

it.each(['/projects/1', '/'])(
  'reconciles a newer canonical name through the existing Refresh flow on %s',
  async (path) => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const projects = useProjectsStore()
    let project: Project = {
      ...portfolioFixture({ name: 'Alpha' }),
      order_index: 1, locale: 'en-US', niche: null, schedule_json: null,
    }
    projects.items = [project]
    projects.activeProjectId = 1
    const portfolioRequests: unknown[] = []
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (url.startsWith('/api/v1/projects?')) return json({ items: [project], next_cursor: null, total_estimate: 1 })
      if (url.includes('/operations/project.portfolio/')) {
        portfolioRequests.push(JSON.parse(String(init?.body)).arguments)
        return json({ items: [portfolioFixture({ ...project })], next_cursor: null, total_estimate: 1 })
      }
      if (url.includes('tracker.ticketCounts')) return json(ticketSummaryFixture())
      if (url.includes('tracker.agentStats')) return json(agentStatsFixture())
      if (url.includes('workflowExtension.list')) return json({ extensions: [] })
      if (url.includes('/workflow-templates')) return json({ templates: [] })
      if (url.includes('/connections/accounts')) return json({ accounts: [] })
      return json([])
    })
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/', component: HomeView },
      { path: '/projects/:id', component: HomeConsoleView },
    ] })
    await router.push(path)
    const app = mount(App, {
      attachTo: document.body,
      global: { plugins: [pinia, router], stubs: {
        DesktopUpdatePrompt: true, HomeSystemStatusCard: true, AgentActivitySection: true,
        HomePortfolioOverview: true, TicketStatusChart: true,
      } },
    })
    await flushPromises()
    const view = path === '/' ? app.getComponent(HomeView) : app.getComponent(HomeConsoleView)
    const visibleLabel = () => path === '/'
      ? app.getComponent(HomeProjectsSection).get('table tbody tr td').text()
      : view.get('h1').text()
    expect(visibleLabel()).toContain('Alpha')
    const beforeReads = portfolioRequests.length
    const refresh = view.findAll('button').find((button) => button.text() === 'Refresh')!
    project = { ...project, name: 'Alpha Agency', updated_at: '2026-09-07T21:00:00Z', domain: 'updated.test' }
    await refresh.trigger('click')
    await flushPromises()
    expect(portfolioRequests.length).toBeGreaterThan(beforeReads)
    expect(visibleLabel()).toContain('Alpha Agency')
    expect(app.getComponent(ProjectSwitcher).get('button').text()).toContain('Alpha Agency')
    expect(projects.activeProject?.name).toBe('Alpha Agency')
    expect(projects.activeProject?.domain).toBe('updated.test')
    expect(projects.activeProject?.slug).toBe('business-one')
    expect(projects.activeProjectId).toBe(1)
    expect(router.currentRoute.value.fullPath).toBe(path)

    // A subsequent older authoritative snapshot cannot undo the newer record.
    project = { ...project, name: 'Alpha', updated_at: '2026-09-07T20:00:00Z' }
    await refresh.trigger('click')
    await flushPromises()
    expect(visibleLabel()).toContain('Alpha Agency')
    expect(projects.activeProject?.name).toBe('Alpha Agency')
    expect(router.currentRoute.value.fullPath).toBe(path)
  },
)

function json(value: unknown): Response {
  return new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } })
}
