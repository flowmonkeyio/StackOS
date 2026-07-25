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
    const guide = wrapper.get('a[href="https://stackos.flowmonkey.io/getting-started"]')
    expect(guide.text()).toContain('Getting started')
    expect(guide.attributes('target')).toBe('_blank')
    expect(guide.attributes('rel')).toBe('noopener noreferrer')
  })

  it('turns an empty portfolio into a plain-language first step', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url === '/api/v1/projects?limit=50') {
        return json({ items: [], next_cursor: null, total_estimate: 0 })
      }
      return defaultFetch(url)
    }) as typeof fetch

    const wrapper = await mountHome()
    await vi.waitFor(() => expect(wrapper.text()).toContain('Ready for your first project'))

    expect(wrapper.text()).not.toContain('binds a deliberate workspace')
    const guides = wrapper.findAll('a[href="https://stackos.flowmonkey.io/getting-started"]')
    expect(guides).toHaveLength(2)
    expect(guides.some((guide) => guide.text().includes('Open getting started'))).toBe(true)
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
      ],
    }))
    Object.defineProperty(window, 'stackosDesktop', {
      configurable: true,
      value: {
        status: vi.fn(),
        installOrRepair: vi.fn(),
        restartService: vi.fn(),
        runDoctor: vi.fn(),
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
    expect(wrapper.text()).toContain('1 connected · 1 available · 1 needs attention · 2 not detected')
    expect(wrapper.text()).toContain('Connected')
    expect(wrapper.text()).toContain('Not detected')
    expect(wrapper.text()).toContain('Restart needed')
    expect(wrapper.text()).toContain('Available')
    expect(wrapper.findAll('ul.grid-cols-5 > li')).toHaveLength(5)
    expect(wrapper.get('img[alt="ChatGPT / Codex"]').attributes('src')).toBe('/images/openai.webp')
    expect(wrapper.get('img[alt="Claude Code"]').attributes('src')).toBe('/images/claude-code.webp')
    expect(wrapper.get('img[alt="Claude Desktop"]').attributes('src')).toBe('/images/claude.webp')
    expect(wrapper.get('img[alt="Gemini CLI"]').attributes('src')).toBe('/images/gemini.webp')
    expect(wrapper.get('img[alt="Hermes"]').attributes('src')).toBe('/images/hermes.webp')
    expect(wrapper.findAll('p[title] > span.truncate')).toHaveLength(5)
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
