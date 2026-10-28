import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import PluginsView from './PluginsView.vue'

const ORIGINAL_FETCH = globalThis.fetch

describe('PluginsView directory', () => {
  beforeEach(() => setActivePinia(createPinia()))

  afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH
    vi.restoreAllMocks()
  })

  it('brings provider marks, cross-catalog search, and provider browsing into the app', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url === '/api/v1/plugins?project_id=1') {
        return json([
          {
            id: 1,
            slug: 'utils',
            name: 'Utilities',
            version: '1.0.0',
            description: 'Research and web extraction tools.',
            source: 'builtin',
            manifest_json: {},
            enabled_for_project: true,
            created_at: '2026-07-11T00:00:00Z',
            updated_at: '2026-07-11T00:00:00Z',
          },
        ])
      }
      if (url === '/api/v1/providers?project_id=1') {
        return json([
          {
            id: 11,
            plugin_id: 1,
            plugin_slug: 'utils',
            key: 'firecrawl',
            name: 'Firecrawl',
            description: 'Crawl and extract websites.',
            auth_type: 'api-key',
            config_json: null,
          },
        ])
      }
      if (url === '/api/v1/capabilities?project_id=1') {
        return json([
          {
            id: 21,
            plugin_id: 1,
            plugin_slug: 'utils',
            key: 'crawl',
            name: 'Web crawling',
            description: '',
            kind: 'tool',
            config_json: null,
          },
        ])
      }
      if (url === '/api/v1/actions?project_id=1') {
        return json([
          {
            id: 31,
            plugin_id: 1,
            plugin_slug: 'utils',
            provider_id: 11,
            provider_key: 'firecrawl',
            capability_key: 'crawl',
            key: 'crawl',
            name: 'Crawl website',
            description: 'Extract a website.',
            operation: 'read',
          },
        ])
      }
      if (url === '/api/v1/resources?project_id=1') return json([])
      if (url === '/api/v1/operations/integration.list/call') return json({
        project_id: 1,
        items: [{
          plugin_slug: 'utils', provider_key: 'firecrawl', state: 'repair_required',
          total_action_count: 9, executable_action_count: 2, hidden_action_count: 4,
          next_action: { tool: 'auth.test', reason: 'Review the Account permissions.' },
        }],
      })
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/plugins', component: PluginsView }],
    })
    await router.push('/projects/1/plugins')
    await router.isReady()

    const wrapper = mount(
      { template: '<RouterView />' },
      { global: { plugins: [router, createPinia()] } },
    )

    await vi.waitFor(() => expect(wrapper.text()).toContain('Utilities'))
    expect(wrapper.text()).toContain('Firecrawl')
    expect(wrapper.get('img').attributes('src')).toBe('/images/integrations/firecrawl-icon.png')
    expect(wrapper.text()).toContain('2 of 9 actions executable')

    const search = wrapper.get<HTMLInputElement>(
      'input[placeholder="Search plugins, providers, capabilities, or actions…"]',
    )
    await search.setValue('crawl website')
    expect(wrapper.text()).toContain('Utilities')

    const providersTab = wrapper
      .findAll('button[role="tab"]')
      .find((button) => button.text().trim() === 'Providers')
    expect(providersTab).toBeDefined()
    await providersTab?.trigger('click')
    expect(wrapper.text()).toContain('Crawl and extract websites.')
    expect(wrapper.text()).toContain('Account repair required')
    expect(wrapper.text()).toContain('4 hidden from agent discovery')
    expect(wrapper.text()).toContain('Review the Account permissions.')
    expect(wrapper.findAll('a').find((link) => link.text() === 'Review connections')?.attributes('href'))
      .toBe('/projects/1/connections?section=services')

    await search.setValue('no matching tool')
    expect(wrapper.text()).toContain('No matching catalog items')
    wrapper.unmount()
  })

  it.each(['failed', 'empty'])('keeps catalog browsing usable when the inventory is %s', async (result) => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url === '/api/v1/operations/integration.list/call') {
        return result === 'failed' ? json({ detail: 'Unavailable' }, 503) : json({ project_id: 1, items: [] })
      }
      if (url === '/api/v1/plugins?project_id=1') return json([{
        id: 1, slug: 'utils', name: 'Utilities', description: 'Tools',
        enabled_for_project: true, source: 'builtin', version: '1.0.0',
      }])
      if (url === '/api/v1/providers?project_id=1') return json([{
        id: 11, plugin_id: 1, plugin_slug: 'utils', key: 'firecrawl', name: 'Firecrawl',
        description: 'Crawl websites.', auth_type: 'api-key',
      }])
      return json([])
    }) as typeof fetch
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/plugins', component: PluginsView }],
    })
    await router.push('/projects/1/plugins')
    await router.isReady()
    const wrapper = mount(PluginsView, { global: { plugins: [router, createPinia()] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('Utilities'))
    expect(wrapper.text()).toContain(result === 'failed'
      ? 'Availability unavailable. The catalog is still available; refresh to retry the integration check.'
      : 'No integration availability reported.')
    const providerLink = wrapper.findAll('button').find((button) => button.text() === 'View provider availability')
    await providerLink?.trigger('click')
    expect(wrapper.text()).toContain('Crawl websites.')
    expect(wrapper.text()).toContain('Availability unavailable')
    expect(wrapper.text()).not.toContain('Account repair required')
    wrapper.unmount()
  })
})

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}
