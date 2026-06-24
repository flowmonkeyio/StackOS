import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import SetupStatusTab from './SetupStatusTab.vue'

const ORIG_FETCH = globalThis.fetch

describe('SetupStatusTab (calm readiness)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('shows a plain-language readiness verdict, the checks that matter, and setup links', async () => {
    const requestedUrls: string[] = []
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      requestedUrls.push(url)

      if (url === '/api/v1/health') {
        return json({
          daemon_uptime_s: 14,
          db_status: 'ok',
          scheduler_running: true,
          version: '1.0.0',
          milestone: 'M10',
        })
      }
      if (url === '/api/v1/projects/1/auth/status') {
        return json({
          project_id: 1,
          provider_key: null,
          providers: [{ key: 'firecrawl', name: 'Firecrawl', plugin_slug: 'utils' }],
          connections: [
            {
              credential_ref: 'cred_firecrawl',
              provider_key: 'firecrawl',
              label: 'Firecrawl',
              status: 'connected',
              scopes: [],
              expires_at: null,
              revoked_at: null,
            },
          ],
        })
      }
      if (url === '/api/v1/operations/integration.list/call') {
        return json({
          project_id: 1,
          items: [],
          count: 3,
          connected_count: 1,
          hidden_action_count: 0,
          filters: { project_id: 1 },
        })
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/projects/:id/setup', component: SetupStatusTab },
        { path: '/projects/:id/connections', component: { template: '<div />' } },
        { path: '/projects/:id/schedules', component: { template: '<div />' } },
        { path: '/projects/:id/cost-budget', component: { template: '<div />' } },
        { path: '/projects/:id/plugins', component: { template: '<div />' } },
      ],
    })
    await router.push('/projects/1/setup')
    await router.isReady()

    const wrapper = mount(SetupStatusTab, { global: { plugins: [router] } })

    await vi.waitFor(() => expect(wrapper.text()).toContain('Ready to run agent work'))
    // The few human checks, not the 16-row registry checklist.
    expect(wrapper.text()).toContain('Local service')
    expect(wrapper.text()).toContain('Background automation')
    expect(wrapper.text()).toContain('Connections')
    expect(wrapper.text()).toContain('Agent actions')
    // Setup destinations.
    expect(wrapper.text()).toContain('Automation')
    expect(wrapper.text()).toContain('Spend')
    expect(wrapper.text()).toContain('Plugins')
    expect(wrapper.text()).toContain('StackOS v1.0.0')
    // No registry jargon, no raw credential refs.
    expect(wrapper.text()).not.toContain('Operation contracts')
    expect(wrapper.text()).not.toContain('cred_firecrawl')

    expect(requestedUrls).toContain('/api/v1/health')
    expect(requestedUrls).toContain('/api/v1/projects/1/auth/status')
    expect(requestedUrls).toContain('/api/v1/operations/integration.list/call')
  })
})

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}
