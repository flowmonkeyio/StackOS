import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import HomeView from './HomeView.vue'

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

  it('does not render host status controls in a plain browser', async () => {
    globalThis.fetch = vi.fn(async (input) => defaultFetch(String(input))) as typeof fetch

    const wrapper = await mountHome()
    await vi.waitFor(() => expect(wrapper.text()).toContain('Local service'))

    expect(wrapper.text()).toContain('Running')
    expect(wrapper.text()).not.toContain('Agent hosts')
  })

  it('renders desktop host connection status from doctor mcp_hosts', async () => {
    globalThis.fetch = vi.fn(async (input) => defaultFetch(String(input))) as typeof fetch
    const runDoctor = vi.fn(async () => ({
      ok: false,
      parsed: {
        ok: false,
        code: 9,
        info: {
          mcp_hosts: [
            host({ host_key: 'codex', status: 'registered_current', ok: true, available: true }),
            host({ host_key: 'claude-code', status: 'absent', ok: true, available: false }),
            host({
              host_key: 'claude-desktop',
              status: 'restart_required',
              ok: true,
              available: true,
              needs_restart: true,
            }),
            host({
              host_key: 'gemini-cli',
              status: 'available_unregistered',
              ok: false,
              available: true,
              blocking: true,
            }),
          ],
        },
      },
    }))
    Object.defineProperty(window, 'stackosDesktop', {
      configurable: true,
      value: {
        status: vi.fn(),
        installOrRepair: vi.fn(),
        restartService: vi.fn(),
        runDoctor,
        checkForUpdates: vi.fn(),
        downloadUpdate: vi.fn(),
        installUpdate: vi.fn(),
        updateState: vi.fn(),
      },
    })

    const wrapper = await mountHome()

    await vi.waitFor(() => expect(wrapper.text()).toContain('Agent hosts'))
    expect(runDoctor).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('1/3 connected · 1 not installed')
    expect(wrapper.text()).toContain('Codex')
    expect(wrapper.text()).toContain('Connected')
    expect(wrapper.text()).toContain('Claude Code')
    expect(wrapper.text()).toContain('Not installed')
    expect(wrapper.text()).toContain('Claude Desktop')
    expect(wrapper.text()).toContain('Restart required')
    expect(wrapper.text()).toContain('Gemini CLI')
    expect(wrapper.text()).toContain('Not connected')
  })
})

async function mountHome() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: HomeView }],
  })
  await router.push('/')
  await router.isReady()
  const wrapper = mount(
    { template: '<RouterView />' },
    {
      global: {
        plugins: [router, createPinia()],
        stubs: { teleport: true },
      },
    },
  )
  await flushPromises()
  return wrapper
}

function defaultFetch(url: string): Response {
  if (url === '/api/v1/health') {
    return json({
      db_status: 'ok',
      scheduler_running: true,
      version: '1.0.3',
      daemon_uptime_s: 120,
    })
  }
  if (url === '/api/v1/projects?limit=50') {
    return json({
      items: [
        {
          id: 1,
          slug: 'demo',
          name: 'Demo',
          domain: 'example.com',
          locale: 'en',
          is_active: true,
          created_at: '2026-06-26T00:00:00Z',
          updated_at: '2026-06-26T00:00:00Z',
        },
      ],
      next_cursor: null,
      total_estimate: 1,
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
    ...overrides,
  }
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
