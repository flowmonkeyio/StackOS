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

    await search.setValue('no matching tool')
    expect(wrapper.text()).toContain('No matching catalog items')
  })
})

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}
