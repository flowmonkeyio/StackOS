import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import App from './App.vue'
import appRouter from './router'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import { useProjectsStore, type Project } from '@/stores/projects'

const ORIG_FETCH = globalThis.fetch

afterEach(() => {
  globalThis.fetch = ORIG_FETCH
  vi.restoreAllMocks()
})

it.each([null, 7])(
  'opens installation-wide backup controls with selected project %s',
  async (projectId) => {
    const requests: Array<{ url: string; args: Record<string, unknown> | undefined }> = []
    const items: Project[] =
      projectId === null
        ? []
        : [
            {
              id: projectId,
              name: 'Synthetic project',
              slug: 'synthetic',
              domain: 'example.test',
              niche: 'platform',
              locale: 'en-US',
              is_active: true,
              schedule_json: null,
              created_at: '2026-06-01T00:00:00Z',
              updated_at: '2026-06-01T00:00:00Z',
            },
          ]
    let enabled = true
    const status = () => ({
      configured: true,
      ready: false,
      enabled,
      interval_hours: 24,
      keep_last: 30,
      targets: [],
      project_ids: [],
      destination: '/synthetic/backups',
      recipient_fingerprint: 'fixture',
      last_attempt: null,
      last_success: null,
      last_error: {
        code: 'source_unavailable',
        message: 'An enrolled folder is unavailable.',
        next_action: 'Repair the enrolled path.',
      },
    })
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      const args = init?.body ? JSON.parse(String(init.body)).arguments : undefined
      requests.push({ url, args })
      if (url === '/api/v1/health')
        return json({
          db_status: 'ok',
          scheduler_running: true,
          version: '2.1.37',
          daemon_uptime_s: 10,
        })
      if (url === '/api/v1/operations/hostMcp.status/call') return json({ ok: true, items: [] })
      if (
        url === '/api/v1/operations/project.portfolio/call' ||
        url.startsWith('/api/v1/projects?')
      ) {
        return json({ items, next_cursor: null, total_estimate: items.length })
      }
      if (url === '/api/v1/operations/tracker.ticketCountsAll/call') {
        return json({
          project_id: null,
          is_active: true,
          as_of: '2026-10-15T00:00:00Z',
          total_count: 0,
          ticket_counts: {},
        })
      }
      if (url.startsWith('/api/v1/plugins')) return json([])
      if (url === '/api/v1/operations/backup.status/call') return json(status())
      if (url === '/api/v1/operations/backup.configure/call') {
        enabled = args.enabled
        return json({ data: status() })
      }
      return json({})
    }) as typeof fetch

    const pinia = createPinia()
    setActivePinia(pinia)
    const projects = useProjectsStore()
    projects.items = items
    projects.activeProjectId = projectId
    const router = createRouter({
      history: createMemoryHistory(),
      routes: appRouter.options.routes,
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(App, {
      global: {
        plugins: [pinia, router],
        stubs: { DesktopUpdatePrompt: true, TicketStatusChart: true, teleport: true },
      },
    })
    await flushPromises()
    expect(wrapper.find('[aria-label="Local backups"]').exists()).toBe(false)
    const projectNav = wrapper.get('nav[aria-label="Project navigation"]')
    const appNav = wrapper.get('nav[aria-label="App navigation"]')
    expect(appNav.findAll('a').map((link) => [link.text(), link.attributes('href')])).toEqual([
      ['Overview', '/'],
      ['Accounts', '/accounts'],
      ['Backups', '/setup'],
    ])
    expect(projectNav.element.nextElementSibling).toBe(appNav.element)
    expect(projectNav.element.previousElementSibling).toBe(
      wrapper.getComponent(ProjectSwitcher).element.parentElement,
    )
    expect(projectNav.find('a[href="/setup"]').exists()).toBe(false)
    const backupsLink = appNav.get('a[href="/setup"]')
    if (projectId === null) {
      expect(wrapper.text()).toContain('Connect your first project to see its navigation here.')
    } else {
      expect(projectNav.find(`a[href="/projects/${projectId}/setup"]`).exists()).toBe(false)
      expect(projectNav.text()).toContain('Configure')
      for (const surface of ['connections', 'schedules', 'cost-budget', 'plugins']) {
        expect(projectNav.find(`a[href="/projects/${projectId}/${surface}"]`).exists()).toBe(true)
      }
    }

    const beforeNavigation = requests.length
    await backupsLink.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/setup')
    expect(wrapper.get('h1').text()).toBe('Backups')
    expect(backupsLink.attributes('aria-current')).toBe('page')
    const backups = wrapper.get('[aria-label="Local backups"]')
    expect(wrapper.text()).toContain('Automatic backups for this installation.')
    expect(backups.text()).toContain('Automatic backups on')
    await backups.get('[data-dropdown-trigger]').trigger('click')
    await backups
      .findAll('[role="menuitem"]')
      .find((item) => item.text() === 'Backup settings')!
      .trigger('click')
    await flushPromises()
    await backups.get('input[type="checkbox"]').setValue(false)
    await backups.get('form').trigger('submit')
    await flushPromises()
    expect(backups.text()).toContain('Backup settings saved.')
    expect(backups.text()).toContain('Automatic backups paused')
    expect(requests.slice(beforeNavigation)).toEqual([
      { url: '/api/v1/operations/backup.status/call', args: { response_mode: 'raw' } },
      {
        url: '/api/v1/operations/backup.configure/call',
        args: { response_mode: 'raw', enabled: false },
      },
    ])
    expect(projects.items).toEqual(items)
    expect(projects.activeProjectId).toBe(projectId)
    wrapper.unmount()

    const beforeReload = requests.length
    const reloadRouter = createRouter({
      history: createMemoryHistory(),
      routes: appRouter.options.routes,
    })
    await reloadRouter.push('/setup')
    await reloadRouter.isReady()
    const reloaded = mount(App, {
      global: { plugins: [pinia, reloadRouter], stubs: { DesktopUpdatePrompt: true } },
    })
    await flushPromises()
    expect(reloaded.get('h1').text()).toBe('Backups')
    expect(reloaded.get('[aria-label="Local backups"]').text()).toContain(
      'Automatic backups paused',
    )
    expect(requests.slice(beforeReload)).toEqual([
      { url: '/api/v1/operations/backup.status/call', args: { response_mode: 'raw' } },
    ])
    reloaded.unmount()
  },
)

it('removes project Setup while retaining app and project configuration routes', () => {
  const paths = appRouter.getRoutes().map((route) => route.path)
  expect(paths).not.toContain('/projects/:id/setup')
  expect(appRouter.hasRoute('project-detail-setup')).toBe(false)
  expect(paths).toEqual(expect.arrayContaining([
    '/setup',
    '/accounts',
    '/projects/:id',
    '/projects/:id/connections',
    '/projects/:id/schedules',
    '/projects/:id/cost-budget',
    '/projects/:id/plugins',
  ]))
})

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}
