import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import HomeView from './HomeView.vue'
import { activeTasksFixture } from './home/activeTaskFixtures'
import { agentStatsFixture } from './home/agentStatsFixtures'

const ORIG_FETCH = globalThis.fetch

describe('HomeView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    Reflect.deleteProperty(window, 'stackosDesktop')
    vi.restoreAllMocks()
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    Reflect.deleteProperty(window, 'stackosDesktop')
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  it('shows real host reads and disabled native lifecycle controls in a plain browser', async () => {
    globalThis.fetch = vi.fn(async (input) => defaultFetch(String(input))) as typeof fetch

    const wrapper = await mountHome()
    await vi.waitFor(() => expect(wrapper.text()).toContain('Local service'))

    expect(wrapper.text()).toContain('Running')
    expect(wrapper.find('[aria-label="Local backups"]').exists()).toBe(false)
    expect(globalThis.fetch).not.toHaveBeenCalledWith(
      '/api/v1/operations/backup.status/call', expect.anything(),
    )
    const service = wrapper.get('[aria-label="System status"]')
    expect(service.findAll('button').map((button) => button.text())).toEqual([
      'Restart',
      'Refresh',
      'Run doctor',
      'Install or repair',
    ])
    for (const label of ['Restart', 'Run doctor', 'Install or repair']) {
      expect(
        service
          .findAll('button')
          .find((button) => button.text() === label)
          ?.attributes('disabled'),
      ).toBeDefined()
    }
    expect(
      service
        .findAll('button')
        .find((button) => button.text() === 'Refresh')
        ?.attributes('disabled'),
    ).toBeUndefined()
    expect(service.text()).toContain('Service controls live in the StackOS desktop app.')
    expect(wrapper.text()).toContain('AI tool connections')
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/v1/operations/hostMcp.status/call',
      expect.anything(),
    )
    const guide = wrapper.get('a[href="https://stackos.flowmonkey.io/getting-started"]')
    expect(guide.text()).toContain('Getting started')
    expect(guide.attributes('target')).toBe('_blank')
    expect(guide.attributes('rel')).toBe('noopener noreferrer')
  })


  it('orders sections and keeps lifetime work independent of Usage date and project visibility', async () => {
    globalThis.fetch = vi.fn(async input => defaultFetch(String(input))) as typeof fetch
    const wrapper = await mountHome()
    try {
      expect(wrapper.findAll('[aria-label="System status"], [aria-label="Active tasks"], [aria-label="Agent activity"], [aria-label="Connected tools"]').map(node => node.attributes('aria-label')))
        .toEqual(['System status', 'Active tasks', 'Agent activity', 'Connected tools'])
      const active = wrapper.get('[aria-label="Active tasks"]')
      const before = active.text()
      const calls = () => vi.mocked(globalThis.fetch).mock.calls.filter(([url]) => String(url).includes('tracker.activeTasksAll')).length
      const reads = calls()
      const usage = wrapper.findComponent({ name: 'AgentActivitySection' })
      usage.vm.$emit('filters', { start_date: '2026-11-02', end_date: '2026-11-05' })
      await flushPromises()
      await wrapper.findAll('[role="tab"]').find(button => button.text() === 'Archived')!.trigger('click')
      await flushPromises()
      expect(calls()).toBe(reads)
      expect(active.text()).toBe(before)
      expect(wrapper.text()).not.toContain('Tickets by status')
      expect(wrapper.text()).not.toContain('Latest tracked work')
      expect(globalThis.fetch).toHaveBeenCalledWith('/api/v1/operations/tracker.agentStatsAll/call',
        expect.objectContaining({ body: expect.stringContaining('"is_active":false') }))
      await active.get('a').trigger('click')
      await flushPromises()
      expect(wrapper.text()).toContain('Task graph')
      expect(wrapper.vm.$route.fullPath).toBe('/projects/1/tasks?view=graph&task=active-work')
    } finally { wrapper.unmount() }
  })

  it('shows an honest empty active list while retaining the other sections', async () => {
    globalThis.fetch = vi.fn(async input => String(input).includes('tracker.activeTasksAll')
      ? json(activeTasksFixture(null, [])) : defaultFetch(String(input))) as typeof fetch
    const wrapper = await mountHome()
    try {
      expect(wrapper.text()).toContain('No tasks are in progress.')
      expect(wrapper.find('[aria-label="Agent activity"]').exists()).toBe(true)
      expect(wrapper.find('[aria-label="Connected tools"]').exists()).toBe(true)
    } finally { wrapper.unmount() }
  })

  it('renders fast desktop AI-tool connection status in user-facing language', async () => {
    globalThis.fetch = vi.fn(async (input) => defaultFetch(String(input))) as typeof fetch
    const hostStatuses = vi.fn(async () => ({
      ok: false,
      items: [
        host({}),
        host({
          host_key: 'claude-code',
          display_name: 'Claude Code',
          status: 'absent',
          connection_state: 'unavailable',
          status_label: 'Not detected',
          ok: true,
          available: false,
        }),
        host({
          host_key: 'claude-desktop',
          display_name: 'Claude Desktop',
          status: 'restart_required',
          connection_state: 'restart_required',
          status_label: 'Restart needed',
          ok: false,
          available: true,
          blocking: true,
          needs_restart: true,
        }),
        host({
          host_key: 'gemini-cli',
          display_name: 'Gemini CLI',
          status: 'available_unregistered',
          connection_state: 'available',
          status_label: 'Available',
          ok: true,
          available: true,
        }),
        host({
          host_key: 'hermes',
          display_name: 'Hermes',
          status: 'absent',
          connection_state: 'unavailable',
          status_label: 'Not detected',
          ok: true,
          available: false,
        }),
        host({
          host_key: 'grok',
          display_name: 'Grok',
          status: 'available_unregistered',
          connection_state: 'available',
          status_label: 'Available',
          ok: true,
          available: true,
        }),
      ],
    }))
    const installOrRepair = vi.fn()
    const restartService = vi.fn()
    const runDoctor = vi.fn()
    Object.defineProperty(window, 'stackosDesktop', {
      configurable: true,
      value: {
        status: vi.fn(),
        installOrRepair,
        restartService,
        runDoctor,
        hostStatuses,
        checkForUpdates: vi.fn(),
        downloadUpdate: vi.fn(),
        installUpdate: vi.fn(),
        updateState: vi.fn(),
      },
    })

    const wrapper = await mountHome()

    await vi.waitFor(() => expect(wrapper.text()).toContain('AI tool connections'))
    expect(hostStatuses).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain(
      '1 connected · 2 available · 1 needs attention · 2 not detected',
    )
    expect(wrapper.text()).toContain('Connected')
    expect(wrapper.text()).toContain('Not detected')
    expect(wrapper.text()).toContain('Restart needed')
    expect(wrapper.text()).toContain('Available')
    const connections = wrapper.get('[aria-label="AI tool connections"]')
    expect(connections.findAll('ul > li')).toHaveLength(6)
    expect(connections.findAll('img').map(image => image.attributes('alt'))).toEqual([
      'ChatGPT / Codex', 'Claude Code', 'Claude Desktop', 'Gemini CLI', 'Hermes', 'Grok',
    ])
    expect(wrapper.get('img[alt="ChatGPT / Codex"]').attributes('src')).toBe('/images/openai.webp')
    expect(wrapper.get('img[alt="Claude Code"]').attributes('src')).toBe('/images/claude-code.webp')
    expect(wrapper.get('img[alt="Claude Desktop"]').attributes('src')).toBe('/images/claude.webp')
    expect(wrapper.get('img[alt="Gemini CLI"]').attributes('src')).toBe('/images/gemini.webp')
    expect(wrapper.get('img[alt="Hermes"]').attributes('src')).toBe('/images/hermes.webp')
    expect(wrapper.get('img[alt="Grok"]').attributes('src')).toBe('/images/grok.png')
    expect(wrapper.findAll('p[title] > span.truncate')).toHaveLength(6)
    const service = wrapper.get('[aria-label="System status"]')
    expect(service.findAll('button').map((button) => button.text())).toEqual([
      'Restart',
      'Refresh',
      'Run doctor',
      'Install or repair',
    ])
    expect(service.text()).not.toContain('System details')
    expect(restartService).not.toHaveBeenCalled()
    expect(runDoctor).not.toHaveBeenCalled()
    expect(installOrRepair).not.toHaveBeenCalled()
  })
})

async function mountHome() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: HomeView }, { path: '/projects/:id/tasks', component: { template: '<div>Task graph</div>' } }],
  })
  await router.push('/')
  await router.isReady()
  const wrapper = mount(
    { template: '<RouterView />' },
    {
      global: {
        plugins: [router, createPinia()],
        stubs: { teleport: true, TicketStatusChart: true, AgentActivityChart: true },
      },
    },
  )
  await flushPromises()
  return wrapper
}

function defaultFetch(url: string): Response {
  if (url.includes('tracker.deliveryCapacity')) return json({ scope: 'local-user-daemon', configured_limit: 3, running_count: 1, queued_count: 0, supported_min: 1, supported_max: 32 })
  if (url === '/api/v1/operations/tracker.agentStatsAll/call') return json(agentStatsFixture({ project_id: null, is_active: true }))
  if (url.includes('tracker.activeTasksAll')) return json(activeTasksFixture())
  if (url === '/api/v1/auth/accounts') return json({ accounts: [], providers: [] })
  if (url === '/api/v1/operations/hostMcp.status/call') {
    return json({
      ok: true,
      items: [
        host({}),
        host({ host_key: 'claude-code', display_name: 'Claude Code' }),
        host({ host_key: 'claude-desktop', display_name: 'Claude Desktop' }),
        host({ host_key: 'gemini-cli', display_name: 'Gemini CLI' }),
        host({ host_key: 'hermes', display_name: 'Hermes' }),
        host({ host_key: 'grok', display_name: 'Grok' }),
      ],
    })
  }
  if (url === '/api/v1/health') {
    return json({
      db_status: 'ok',
      scheduler_running: true,
      version: '1.0.3',
      daemon_uptime_s: 120,
    })
  }
  return json({})
}

function host(overrides: Record<string, unknown>) {
  return {
    host_key: 'codex',
    surface: 'shared-config',
    status: 'registered_current',
    message: 'host status message',
    ok: true,
    available: true,
    advisory: false,
    blocking: false,
    needs_restart: false,
    command: [],
    config_path: null,
    repair: null,
    warnings: [],
    display_name: 'ChatGPT / Codex',
    connection_state: 'connected',
    status_label: 'Connected',
    ...overrides,
  }
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
