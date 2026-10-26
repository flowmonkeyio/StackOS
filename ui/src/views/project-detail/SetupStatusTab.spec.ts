import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
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

      if (url === '/api/v1/operations/backup.status/call') {
        return json({
          configured: false, ready: false, enabled: false, interval_hours: 24,
          keep_last: 30, destination: '/private/backups', targets: [], project_ids: [],
          recipient_fingerprint: null, last_attempt: null, last_success: null, last_error: null,
        })
      }

      if (url === '/api/v1/health') {
        return json({
          daemon_uptime_s: 14,
          db_status: 'ok',
          scheduler_running: true,
          version: '1.0.1',
          milestone: 'M10',
        })
      }
      if (url === '/api/v1/projects/1/connections/accounts') {
        return json({
          project_id: 1,
          provider_key: null,
          providers: [{ key: 'firecrawl', name: 'Firecrawl', plugin_slug: 'utils' }],
          accounts: [
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

    await vi.waitFor(() => expect(wrapper.text()).toContain('This project is ready for connected agents'))
    expect(wrapper.text()).toContain('Setup path')
    expect(wrapper.text()).toContain('Required runtime state first')
    expect(wrapper.text()).toContain('Optional setup items')
    // The few human checks, not the 16-row registry checklist.
    expect(wrapper.text()).toContain('Local service')
    expect(wrapper.text()).toContain('Background automation')
    expect(wrapper.text()).toContain('Connections')
    expect(wrapper.text()).toContain('Agent actions')
    // Setup destinations.
    expect(wrapper.text()).toContain('Automation')
    expect(wrapper.text()).toContain('Spend')
    expect(wrapper.text()).toContain('Plugins')
    expect(wrapper.get('[aria-label="Local backups"]').text()).toContain('Automatic backups are off')
    expect(wrapper.text()).toContain('StackOS version')
    expect(wrapper.text()).toContain('1.0.1')
    // No registry jargon, no raw credential refs.
    expect(wrapper.text()).not.toContain('Operation contracts')
    expect(wrapper.text()).not.toContain('cred_firecrawl')

    expect(requestedUrls).toContain('/api/v1/health')
    expect(requestedUrls).toContain('/api/v1/projects/1/connections/accounts')
    expect(requestedUrls).toContain('/api/v1/operations/integration.list/call')
    expect(requestedUrls).toContain('/api/v1/operations/backup.status/call')
    wrapper.unmount()
  })

  it('keeps install-wide backup actions available while project readiness is loading', async () => {
    const requests: Array<{ url: string; args: Record<string, unknown> }> = []
    const status = {
      configured: true, ready: true, enabled: false, interval_hours: 24,
      keep_last: 30, destination: '/private/backups', targets: [], project_ids: [],
      recipient_fingerprint: 'synthetic-public-key',
      last_attempt: null, last_success: null, last_error: null,
    }
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (init?.body) requests.push({ url, args: JSON.parse(String(init.body)).arguments })
      if (url === '/api/v1/health') return new Promise<Response>(() => {})
      if (url === '/api/v1/operations/backup.status/call') return json(status)
      if (url === '/api/v1/operations/backup.configure/call') {
        return json({ data: { ...status, enabled: true } })
      }
      return json({})
    }) as typeof fetch
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/setup', component: SetupStatusTab }],
    })
    await router.push('/projects/7/setup')
    await router.isReady()
    const wrapper = mount(SetupStatusTab, { global: { plugins: [router] } })
    await flushPromises()

    const backups = wrapper.get('[aria-label="Local backups"]')
    expect(backups.text()).toContain('This StackOS installation, project Finance data, and enrolled external folders.')
    await backups.get('input[type="checkbox"]').setValue(true)
    await backups.get('form').trigger('submit')
    await flushPromises()
    expect(backups.text()).toContain('Backup settings saved.')
    await backups.findAll('button').find((button) => button.text() === 'Back up now')!.trigger('click')
    await flushPromises()
    expect(backups.text()).toContain('Encrypted backup created.')
    expect(requests.filter(({ url }) => url.includes('/backup.'))).toEqual([
      { url: '/api/v1/operations/backup.status/call', args: { response_mode: 'raw' } },
      { url: '/api/v1/operations/backup.configure/call', args: { response_mode: 'raw', enabled: true } },
      { url: '/api/v1/operations/backup.create/call', args: { response_mode: 'raw' } },
      { url: '/api/v1/operations/backup.status/call', args: { response_mode: 'raw' } },
    ])
    wrapper.unmount()
  })
})

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}
