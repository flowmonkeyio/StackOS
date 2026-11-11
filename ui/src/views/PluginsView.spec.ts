import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
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
          { id: 2, slug: 'seo', name: 'Other tools', description: 'Uses utils guides.', version: '1.0.0', source: 'builtin', enabled_for_project: false },
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
          { id: 12, plugin_id: 2, plugin_slug: 'seo', key: 'archive', name: 'Archive service', description: 'Uses utils guides.', auth_type: 'none' },
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
    expect(wrapper.text()).not.toContain('Open Operations')
    expect(wrapper.find('a[href$="/operations"]').exists()).toBe(false)

    await wrapper.get('section[aria-label="Utilities"] button').trigger('click')
    expect(wrapper.text()).toContain('Firecrawl')
    // A service whose description mentions the slug must not leak into this exact-plugin view.
    expect(wrapper.text()).not.toContain('Archive service')
    await wrapper.findAll('button').find((button) => button.text() === 'Clear all')?.trigger('click')
    expect(wrapper.text()).toContain('Archive service')

    const search = wrapper.get<HTMLInputElement>(
      'input[placeholder="Search plugins or services"]',
    )
    await search.setValue('crawl website')
    expect(wrapper.text()).toContain('Utilities')

    const providersTab = wrapper
      .findAll('button[role="tab"]')
      .find((button) => button.text().trim() === 'Services')
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
    await wrapper.findAll('button').find((button) => button.text() === 'Clear search')?.trigger('click')
    expect(wrapper.text()).toContain('Archive service')

    const pluginsTab = wrapper.findAll('button[role="tab"]').find((button) => button.text() === 'Plugins')
    await pluginsTab?.trigger('click')
    expect(wrapper.findAll('h3').map((heading) => heading.text())).toEqual(['Other tools', 'Utilities'])
    await wrapper.get('button[aria-label="Sort catalog"]').trigger('click')
    await wrapper.findAll('[role="option"]').find((option) => option.text() === 'Most actions')?.trigger('click')
    expect(wrapper.findAll('h3').map((heading) => heading.text())).toEqual(['Utilities', 'Other tools'])
    await wrapper.findAll('button').find((button) => button.text() === 'Refresh')?.trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Utilities'))
    expect(vi.mocked(fetch).mock.calls.filter(([input]) => String(input).endsWith('integration.list/call'))).toHaveLength(2)
    expect(vi.mocked(fetch).mock.calls.every(([input, init]) => !init?.method || init.method === 'GET' || String(input).endsWith('integration.list/call'))).toBe(true)
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
      : 'Availability unavailable')
    const providerLink = wrapper.findAll('button').find((button) => button.text() === 'View services')
    await providerLink?.trigger('click')
    expect(wrapper.text()).toContain('Crawl websites.')
    expect(wrapper.text()).toContain('Availability unavailable')
    expect(wrapper.text()).not.toContain('Account repair required')
    wrapper.unmount()
  })

  it('returns to the same plugin list while clearing the chip keeps all services in the current project', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url.startsWith('/api/v1/plugins?')) return json([
        { id: 1, slug: 'utils', name: 'Utilities', enabled_for_project: true },
        { id: 2, slug: 'seo', name: 'SEO', enabled_for_project: true },
      ])
      if (url.startsWith('/api/v1/providers?')) return json([
        { id: 11, plugin_id: 1, plugin_slug: 'utils', key: 'crawl', name: 'Crawler', auth_type: 'none' },
        { id: 12, plugin_id: 2, plugin_slug: 'seo', key: 'audit', name: 'Auditor', auth_type: 'none' },
      ])
      if (url.endsWith('integration.list/call')) return json({ project_id: 1, items: [] })
      return json([])
    }) as typeof fetch
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/projects/:id/plugins', component: PluginsView }] })
    await router.push('/projects/1/plugins')
    const wrapper = mount(PluginsView, { global: { plugins: [router, createPinia()] } })
    await flushPromises()
    const search = wrapper.get<HTMLInputElement>('input[placeholder="Search plugins or services"]')
    await search.setValue('Utilities')
    await wrapper.get('button[aria-label="Sort catalog"]').trigger('click')
    await wrapper.findAll('[role="option"]').find(option => option.text() === 'Most actions')!.trigger('click')
    await wrapper.get('section[aria-label="Utilities"] button').trigger('click')
    expect(search.element.value).toBe('')
    expect(wrapper.text()).toContain('Crawler')
    expect(wrapper.text()).not.toContain('Auditor')
    const back = () => wrapper.findAll('button').find(button => button.text() === 'Back to plugins')!
    expect(back()).toBeDefined()
    await wrapper.get('button[aria-label="Remove Plugin filter: Utilities"]').trigger('click')
    expect(wrapper.get('[role="tab"][aria-selected="true"]').text()).toBe('Services')
    expect(wrapper.text()).toContain('Auditor')
    expect(back()).toBeDefined()
    await search.setValue('no service')
    await wrapper.get('button[aria-label="Sort catalog"]').trigger('click')
    await wrapper.findAll('[role="option"]').find(option => option.text() === 'Name')!.trigger('click')
    const calls = vi.mocked(fetch).mock.calls.length
    await back().trigger('click')
    expect(router.currentRoute.value.path).toBe('/projects/1/plugins')
    expect(wrapper.get('[role="tab"][aria-selected="true"]').text()).toBe('Plugins')
    expect(search.element.value).toBe('Utilities')
    expect(wrapper.get('button[aria-label="Sort catalog"]').text()).toBe('Most actions')
    expect(wrapper.findAll('h3').map(heading => heading.text())).toEqual(['Utilities'])
    expect(wrapper.find('.ui-badge[aria-label^="Remove Plugin"]').exists()).toBe(false)
    expect(vi.mocked(fetch).mock.calls).toHaveLength(calls)
    await wrapper.get('section[aria-label="Utilities"] button').trigger('click')
    await router.push('/projects/2/plugins'); await flushPromises()
    await search.setValue('Auditor')
    await back().trigger('click')
    expect(router.currentRoute.value.path).toBe('/projects/2/plugins')
    expect(search.element.value).toBe('Auditor')
    wrapper.unmount()
  })

  it.each(['empty', 'failed', 'no-services'])('supports the %s catalog read state and refresh', async (state) => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url === '/api/v1/plugins?project_id=1') {
        if (state === 'failed') return json({ detail: 'Catalog read failed' }, 503)
        return json(state === 'empty' ? [] : [{ id: 1, slug: 'agency', name: 'Agency', enabled_for_project: true }])
      }
      if (url.endsWith('integration.list/call')) return json({ project_id: 1, items: [] })
      return json([])
    }) as typeof fetch
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/projects/:id/plugins', component: PluginsView }] })
    await router.push('/projects/1/plugins')
    const wrapper = mount(PluginsView, { global: { plugins: [router, createPinia()] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain(state === 'failed' ? 'Catalog read failed' : state === 'empty' ? 'No plugins installed' : 'Agency'))
    if (state === 'no-services') {
      await wrapper.findAll('button[role="tab"]').find((button) => button.text() === 'Services')?.trigger('click')
      expect(wrapper.text()).toContain('No services available')
      expect(wrapper.text()).not.toContain('No plugins installed')
    }
    expect(wrapper.findAll('button').find((button) => button.text() === 'Refresh')).toBeDefined()
    wrapper.unmount()
  })
})

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}
