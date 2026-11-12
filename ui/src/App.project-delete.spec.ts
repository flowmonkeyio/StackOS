import { afterEach, expect, it, vi } from 'vitest'
import { DOMWrapper, enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import App from './App.vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import ProjectDeleteDialog from '@/components/ProjectDeleteDialog.vue'
import { useProjectsStore, type Project } from '@/stores/projects'
import { useToastsStore } from '@/stores/toasts'
import HomeView from '@/views/HomeView.vue'
import ActiveTaskList from '@/views/home/ActiveTaskList.vue'
import { activeTaskFixture, activeTasksFixture } from '@/views/home/activeTaskFixtures'
import { portfolioFixture, ticketSummaryFixture } from '@/views/home/overviewFixtures'
import { agentStatsFixture } from '@/views/home/agentStatsFixtures'

enableAutoUnmount(afterEach)
const originalFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = originalFetch
  useToastsStore().clear()
  vi.restoreAllMocks()
})

it('consumes rename, reorder and confirmed deletion through the shell, preserving noncurrent routes and reaching Overview after the last project', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const projects = useProjectsStore()
  let rows: Project[] = [1, 2].map((id) => ({
    id, order_index: id, name: id === 1 ? 'Alpha' : 'Beta', slug: `fixture-${id}`,
    domain: 'fixture.test', niche: null, locale: 'en-US', is_active: true,
    schedule_json: null, created_at: '', updated_at: '',
  }))
  projects.items = rows
  projects.activeProjectId = 1
  const writes: { url: string; args: Record<string, unknown> }[] = []
  globalThis.fetch = vi.fn(async (input, init) => {
    const url = String(input)
    if (url.startsWith('/api/v1/projects?')) return json({ items: rows, next_cursor: null, total_estimate: rows.length })
    if (url.includes('project.update')) {
      const args = JSON.parse(String(init?.body)).arguments
      writes.push({ url, args })
      rows = rows.map((p) => p.id === args.project_id ? { ...p, name: args.patch.name } : p)
      return json({ data: rows.find((p) => p.id === args.project_id) })
    }
    if (url.includes('project.move')) {
      const args = JSON.parse(String(init?.body)).arguments
      writes.push({ url, args })
      rows = [{ ...rows[1], order_index: 0 }, rows[0]]
      return json({ data: rows[0] })
    }
    if (url.includes('project.delete')) {
      const args = JSON.parse(String(init?.body)).arguments
      writes.push({ url, args })
      rows = rows.filter((p) => p.id !== args.project_id)
      return json({ data: { project_id: args.project_id, status: 'deleted', blockers: [] } })
    }
    return json([])
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { template: '<h1>Overview</h1>' } },
    { path: '/projects/:id/connections', component: { template: '<h1>Connections</h1>' } },
  ] })
  await router.push('/projects/1/connections?section=bots')
  const app = mount(App, { attachTo: document.body, global: { plugins: [pinia, router], stubs: { DesktopUpdatePrompt: true } } })
  const selector = app.getComponent(ProjectSwitcher)
  await flushPromises()
  await selector.get('button').trigger('click')
  await flushPromises()
  async function action(name: string, label: string) {
    const opener = selector.get(`button[aria-label="Actions for ${name}"]`)
    ;(opener.element as HTMLButtonElement).focus()
    await opener.trigger('click')
    await selector.get('[role="menu"]').findAll('button').find((button) => button.text() === label)!.trigger('click')
    await flushPromises()
  }
  const dialog = () => new DOMWrapper(document.querySelector<HTMLElement>('.ui-dialog__overlay')!)
  await action('Alpha', 'Rename project…')
  await dialog().get('input').setValue('Alpha renamed')
  await dialog().get('form').trigger('submit')
  await flushPromises()
  await action('Beta', 'Move to top')
  expect(projects.items.map((p) => p.id)).toEqual([2, 1])
  await action('Beta', 'Delete project…')
  expect(dialog().text()).toContain('fixture-2')
  await dialog().findAll('button').find((button) => button.text() === 'Cancel')!.trigger('click')
  await flushPromises()
  expect(writes).toHaveLength(2)
  expect(document.activeElement).toBe(selector.get('button[aria-label="Actions for Beta"]').element)
  await action('Beta', 'Delete project…')
  await dialog().findAll('button').find((button) => button.text() === 'Delete project')!.trigger('click')
  await flushPromises()
  expect(app.findComponent(ProjectDeleteDialog).exists()).toBe(false)
  expect(projects.items.map((p) => p.id)).toEqual([1])
  expect(router.currentRoute.value.fullPath).toBe('/projects/1/connections?section=bots')
  expect(selector.find('[data-project-select="2"]').exists()).toBe(false)
  expect(document.activeElement).toBe(selector.get('button').element)
  await action('Alpha renamed', 'Delete project…')
  expect(dialog().get('h2').text()).toBe('Delete Alpha renamed?')
  expect(dialog().text()).toContain('fixture-1')
  await dialog().findAll('button').find((button) => button.text() === 'Delete project')!.trigger('click')
  await flushPromises()
  expect(projects.items).toEqual([])
  expect(projects.activeProjectId).toBeNull()
  expect(router.currentRoute.value.path).toBe('/')
  expect(app.get('h1').text()).toBe('Overview')
  expect(app.get('nav[aria-label="Project navigation"]').findAll('a')).toHaveLength(0)
  expect(selector.get('button').text()).toContain('No project selected')
  expect(writes.filter((write) => write.url.includes('project.delete')).map((write) => write.args)).toEqual([
    { response_mode: 'raw', scope: 'catalog', project_id: 2, confirmation: { permanent: true, project_id: 2, project_slug: 'fixture-2' } },
    { response_mode: 'raw', scope: 'catalog', project_id: 1, confirmation: { permanent: true, project_id: 1, project_slug: 'fixture-1' } },
  ])
})

it('removes the target from the actual Overview list and selector while a late portfolio read retains its old row', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const projects = useProjectsStore()
  let rows: Project[] = [1, 2].map((id) => ({
    ...portfolioFixture({ id, name: id === 1 ? 'Alpha' : 'Beta' }),
    slug: `fixture-${id}`, order_index: id, locale: 'en-US', niche: null, schedule_json: null,
  }))
  projects.items = rows
  projects.activeProjectId = 2
  let deferPortfolio = false
  let completePortfolio!: () => void
  globalThis.fetch = vi.fn(async (input) => {
    const url = String(input)
    if (url.startsWith('/api/v1/projects?')) return json({ items: rows, next_cursor: null, total_estimate: rows.length })
    if (url.includes('/project.portfolio/') || url.includes('/tracker.activeTasks')) {
      const page = url.includes('tracker.activeTasks')
        ? activeTasksFixture(null, rows.map(project => activeTaskFixture({ project_id: project.id, project: { ...project }, title: 'Fixture work' })))
        : { items: rows, next_cursor: null, total_estimate: rows.length }
      if (deferPortfolio) return new Promise<Response>((resolve) => { completePortfolio = () => resolve(json(page)) })
      return json(page)
    }
    if (url.includes('/project.delete/')) {
      rows = rows.filter((p) => p.id !== 1)
      return json({ data: { project_id: 1, status: 'deleted', blockers: [] } })
    }
    if (url.includes('tracker.ticketCounts')) return json(ticketSummaryFixture())
    if (url.includes('tracker.agentStats')) return json(agentStatsFixture())
    return json([])
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: HomeView }] })
  await router.push('/')
  const app = mount(App, { attachTo: document.body, global: { plugins: [pinia, router], stubs: {
    DesktopUpdatePrompt: true, HomeSystemStatusCard: true, AgentActivitySection: true,
    HomePortfolioOverview: true, TicketStatusChart: true,
  } } })
  await flushPromises()
  const list = () => app.getComponent(ActiveTaskList).text()
  expect(list()).toContain('Alpha')
  deferPortfolio = true
  await app.getComponent(HomeView).findAll('button').find((button) => button.text() === 'Refresh')!.trigger('click')
  await flushPromises()
  const selector = app.getComponent(ProjectSwitcher)
  await selector.get('button').trigger('click')
  await flushPromises()
  await selector.get('button[aria-label="Actions for Alpha"]').trigger('click')
  await selector.get('[role="menu"]').findAll('button').find((button) => button.text() === 'Delete project…')!.trigger('click')
  await flushPromises()
  const dialog = new DOMWrapper(document.querySelector<HTMLElement>('.ui-dialog__overlay')!)
  await dialog.findAll('button').find((button) => button.text() === 'Delete project')!.trigger('click')
  await flushPromises()
  expect(list()).not.toContain('Alpha')
  expect(list()).toContain('Beta')
  completePortfolio()
  await flushPromises()
  expect(list()).not.toContain('Alpha')
  expect(selector.find('[data-project-select="1"]').exists()).toBe(false)
  expect(projects.activeProjectId).toBe(2)
  expect(router.currentRoute.value.path).toBe('/')
})

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
}
