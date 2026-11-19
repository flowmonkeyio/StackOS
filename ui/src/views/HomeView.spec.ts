import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import HomeView from './HomeView.vue'
import { activeTasksFixture } from './home/activeTaskFixtures'
import { agentStatsFixture } from './home/agentStatsFixtures'

const ORIG_FETCH = globalThis.fetch

describe('HomeView', () => {
  it('keeps the page Refresh idle during a grouping read and joins it on explicit Refresh', async () => {
    globalThis.fetch = vi.fn(async input => defaultFetch(String(input))) as typeof fetch
    const wrapper = await mountHome()
    try {
      let finish!: (value: Response) => void
      vi.mocked(fetch).mockImplementation(async input => String(input).includes('tracker.agentStatsAll')
        ? new Promise<Response>(resolve => { finish = resolve }) : defaultFetch(String(input)))
      const chart = wrapper.findComponent({ name: 'AgentActivityChart' }).element
      await wrapper.findAll('button').find(button => button.text() === 'Agent')!.trigger('click')
      await flushPromises()
      const refresh = wrapper.get('.ui-page-header').findAll('button').find(button => button.text() === 'Refresh')!
      expect(refresh.attributes('aria-busy')).toBeUndefined()
      expect(wrapper.findComponent({ name: 'AgentActivityChart' }).element).toBe(chart)
      const calls = vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes('tracker.agentStatsAll')).length
      await refresh.trigger('click'); await flushPromises()
      expect(refresh.attributes('aria-busy')).toBe('true')
      expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes('tracker.agentStatsAll'))).toHaveLength(calls)
      finish(json(agentStatsFixture({ project_id: null, is_active: true, group_by: 'agent' as never })))
      await flushPromises()
      expect(refresh.attributes('aria-busy')).toBeUndefined()
    } finally { wrapper.unmount() }
  })
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

  it('opens all project queues and saves through the real operation client without reloading other widgets', async () => {
    let limits = [3, 3]
    const capacity = () => ({ scope: 'project', supported_min: 1, supported_max: 32,
      projects: limits.map((limit, index) => ({ project_id: index + 1, project_name: `Project ${index + 1}`,
        project_slug: `project-${index + 1}`, configured_limit: limit, running_count: 1, queued_count: 2 })) })
    globalThis.fetch = vi.fn(async (url, init) => {
      if (String(url).includes('tracker.setDeliveryCapacity')) {
        const { arguments: args } = JSON.parse(String(init?.body))
        expect(args.updates).toEqual([{ project_id: 1, max_concurrent_tasks: 6 }, { project_id: 2, max_concurrent_tasks: 5 }])
        limits = [6, 5]
        return json({ data: capacity(), project_id: null, run_id: null })
      }
      return String(url).includes('tracker.deliveryCapacity') ? json(capacity()) : defaultFetch(String(url))
    }) as typeof fetch
    const wrapper = await mountHome()
    try {
      expect(vi.mocked(globalThis.fetch).mock.calls.some(([url]) => String(url).includes('tracker.deliveryCapacity'))).toBe(false)
      const before = vi.mocked(globalThis.fetch).mock.calls.length
      await wrapper.get('[data-popover-trigger]').trigger('click'); await flushPromises()
      expect(wrapper.findAll('tbody tr')).toHaveLength(2)
      const inputs = wrapper.findAll('input[type="number"]')
      await inputs[0]!.setValue(6); await inputs[1]!.setValue(5)
      await wrapper.get('form').trigger('submit'); await flushPromises()
      await wrapper.get('[data-popover-trigger]').trigger('click'); await flushPromises()
      expect(wrapper.findAll('input[type="number"]').map(input => (input.element as HTMLInputElement).value)).toEqual(['6', '5'])
      const calls = vi.mocked(globalThis.fetch).mock.calls.slice(before)
      expect(calls.map(([url]) => String(url))).toEqual([
        '/api/v1/operations/tracker.deliveryCapacity/call', '/api/v1/operations/tracker.setDeliveryCapacity/call',
        '/api/v1/operations/tracker.deliveryCapacity/call',
      ])
      const read = JSON.parse(String(calls[0]![1]?.body)).arguments
      expect(read).toEqual({ response_mode: 'raw' })
      expect(wrapper.find('[aria-label="Agent activity"]').exists()).toBe(true)
      expect(wrapper.find('[aria-label="Connected tools"]').exists()).toBe(true)
    } finally { wrapper.unmount() }
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
    expect(service.findAll('button').filter(button => !button.attributes('aria-haspopup')).map((button) => button.text())).toEqual([
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
    expect(globalThis.fetch).toHaveBeenCalledWith('/api/v1/auth/accounts?summary_only=true', expect.anything())
    expect(globalThis.fetch).not.toHaveBeenCalledWith('/api/v1/auth/accounts', expect.anything())
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

  it('keeps registrations visible while quota loads, fails and succeeds on Refresh', async () => {
    let finish!: (response: Response) => void
    globalThis.fetch = vi.fn(async input => String(input).includes('hostMcp.subscriptions')
      ? new Promise<Response>(resolve => { finish = resolve })
      : defaultFetch(String(input))) as typeof fetch
    const wrapper = await mountHome()
    try {
      const connections = wrapper.get('[aria-label="AI tool connections"]')
      const codex = connections.get('li[aria-label="Codex"]')
      expect(codex.text()).toContain('Connected')
      expect(codex.text()).toContain('Checking quota')
      finish(json({}, 503)); await flushPromises()
      expect(connections.text()).toContain('Couldn’t refresh subscription quotas')
      expect(codex.text()).toContain('Quota unavailable')
      await connections.get('button').trigger('click')
      finish(json({ checked_at: '2091-11-18T18:48:00Z', readings: {
        codex: { plan: 'ChatGPT Pro', meters: [] },
        'claude-code': { plan: null, meters: [
          { scope: 'First', remaining_percent: 25.1, resets_at: '2091-11-19T12:00:00Z' },
          { scope: 'Second', remaining_percent: 25, resets_at: '2091-11-20T18:00:00Z' },
          { scope: 'Third', remaining_percent: 5.1, resets_at: null },
          { scope: 'Fourth', remaining_percent: 5, resets_at: null },
          { scope: 'Fifth', remaining_percent: 100, resets_at: null },
          { scope: 'Sixth', remaining_percent: 0, resets_at: null },
        ] },
        'claude-desktop': { plan: 'Wrong host plan', meters: [] },
        hermes: { plan: 'Wrong host plan', meters: [] },
      } })); await flushPromises()
      expect(connections.text()).not.toContain('Couldn’t refresh')
      expect(codex.text()).toContain('ChatGPT Pro')
      expect(codex.text()).toContain('Quota unavailable')
      expect(codex.find('[role="meter"]').exists()).toBe(false)
      expect(connections.text()).not.toContain('Wrong host plan')
      const claude = connections.get('li[aria-label="Claude Code"]')
      const meters = claude.findAllComponents({ name: 'UiScoreMeter' })
      const expectedMeters = [
        [74.9, 'success'], [75, 'warning'], [94.9, 'warning'], [95, 'danger'],
        [0, 'success'], [100, 'danger'],
      ] as const
      for (const [index, [value, tone]] of expectedMeters.entries()) {
        expect(meters[index]!.props()).toMatchObject({ value, tone })
      }
      expect(claude.findAll('.tabular-nums').map(label => label.text())).toEqual([
        '74.9%', '75%', '94.9%', '95%', '0%', '100%',
      ])
      expect(claude.text()).toContain('First · Resets')
      expect(claude.text()).toContain('Second · Resets')
      expect(claude.text()).toContain('Third · Reset unavailable')
      expect(claude.findAll('time').map(time => time.attributes('datetime'))).toEqual([
        '2091-11-19T12:00:00Z', '2091-11-20T18:00:00Z',
      ])
    } finally { wrapper.unmount() }
  })

  it('shows quota with unknown registration when the initial host-status read fails', async () => {
    globalThis.fetch = vi.fn(async input => String(input).includes('hostMcp.status')
      ? json({}, 503) : defaultFetch(String(input))) as typeof fetch
    const wrapper = await mountHome()
    try {
      const codex = wrapper.get('li[aria-label="Codex"]')
      expect(codex.text()).toContain('Status unavailable')
      expect(codex.text()).toContain('24%')
      expect(codex.text()).not.toContain('Connected')
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
          repair: 'Install the Claude Code CLI to register this host.',
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
          status: 'available_unregistered',
          connection_state: 'available',
          status_label: 'Available',
          message: 'Hermes is available. Choose a profile to connect to StackOS.',
          ok: true,
          available: true,
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
      '1 connected · 3 available · 1 needs attention · 1 not detected',
    )
    expect(wrapper.text()).toContain('Connected')
    expect(wrapper.text()).toContain('Not detected')
    expect(wrapper.text()).toContain('Restart needed')
    expect(wrapper.text()).toContain('Available')
    const connections = wrapper.get('[aria-label="AI tool connections"]')
    expect(connections.findAll('ul > li')).toHaveLength(6)
    expect(connections.findAll('h4').map(heading => heading.text())).toEqual([
      'Codex', 'Claude Code', 'Claude Desktop', 'Gemini CLI', 'Hermes', 'Grok Build',
    ])
    expect(connections.findAll('img').map(image => image.attributes('src'))).toEqual([
      '/images/openai.webp', '/images/claude-code.webp', '/images/claude.webp',
      '/images/gemini.webp', '/images/hermes.webp', '/images/grok.png',
    ])
    const claude = connections.get('li[aria-label="Claude Code"]')
    expect(claude.text()).toContain('Not detected')
    expect(claude.text()).toContain('Install the Claude Code CLI')
    expect(claude.text()).toContain('Claude Max')
    expect(claude.text()).toContain('86%')
    expect(claude.findAll('time')).toHaveLength(1)
    expect(claude.get('[aria-label="Claude Code Fable quota used (%)"]').attributes('aria-valuenow')).toBe('86')
    expect(connections.get('li[aria-label="Claude Desktop"]').text()).toContain('Restart needed')
    expect(connections.get('li[aria-label="Hermes"]').text()).toContain('Choose a profile')
    for (const name of ['Claude Desktop', 'Gemini CLI', 'Hermes']) {
      const card = connections.get(`li[aria-label="${name}"]`)
      expect(card.find('[role="meter"]').exists()).toBe(false)
      expect(card.get('.host-resets').text()).toBe('N/A')
    }
    const service = wrapper.get('[aria-label="System status"]')
    expect(service.findAll('button').filter(button => !button.attributes('aria-haspopup')).map((button) => button.text())).toEqual([
      'Restart',
      'Refresh',
      'Run doctor',
      'Install or repair',
    ])
    expect(service.text()).not.toContain('System details')
    const checked = service.get('time[datetime="2091-11-18T18:48:00Z"]')
    expect(checked.text()).toBe(new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }).format(Date.parse(checked.attributes('datetime')!)))
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
        stubs: { teleport: true, TicketStatusChart: true, AgentActivityChart: true, DeliveryTrendChart: true },
      },
    },
  )
  await flushPromises()
  return wrapper
}

function defaultFetch(url: string): Response {
  if (url.includes('hostMcp.listAccounts')) return json({ hosts: {} })
  if (url.includes('tracker.deliveryCapacity')) return json({ scope: 'project', projects: [1, 2].map(id => ({ project_id: id, project_name: `Project ${id}`, project_slug: `project-${id}`, configured_limit: 3, running_count: 1, queued_count: 0 })), supported_min: 1, supported_max: 32 })
  if (url === '/api/v1/operations/tracker.agentStatsAll/call') return json(agentStatsFixture({ project_id: null, is_active: true }))
  if (url.includes('tracker.activeTasksAll')) return json(activeTasksFixture())
  if (url === '/api/v1/auth/accounts?summary_only=true') return json({ accounts: [], providers: [], summary_only: true })
  if (url.includes('hostMcp.subscriptions')) return json({
    checked_at: '2091-11-18T18:48:00Z',
    readings: {
      codex: { plan: 'ChatGPT Pro', meters: [{ scope: 'Weekly', remaining_percent: 76, resets_at: null }] },
      'claude-code': { plan: 'Claude Max', meters: [
        { scope: 'All models', remaining_percent: 55, resets_at: '2091-11-20T15:59:59.999Z' },
        { scope: 'Fable', remaining_percent: 14, resets_at: '2091-11-20T16:00:00.004Z' },
      ] },
      'gemini-cli': null,
    },
  })
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
