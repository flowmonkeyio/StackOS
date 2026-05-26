import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import ResourceExplorerView from './ResourceExplorerView.vue'

const ORIG_FETCH = globalThis.fetch

function page(items: unknown[] = []) {
  return { items, next_cursor: null, total_estimate: items.length }
}

describe('ResourceExplorerView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('renders generic resources and redacts record details', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url.includes('/api/v1/plugins')) {
        return json([
          plugin('core', 'StackOS Core'),
          plugin('utils', 'Utilities'),
        ])
      }
      if (url.includes('/api/v1/catalog')) throw new Error('unexpected aggregate catalog request')
      if (url.includes('/api/v1/capabilities')) return json([])
      if (url.includes('/api/v1/providers')) return json([])
      if (url.includes('/api/v1/actions')) return json([])
      if (url.includes('/api/v1/resources')) {
        return json([
          {
            id: 1,
            plugin_id: 1,
            plugin_slug: 'core',
            key: 'learning',
            name: 'Learning',
            description: 'Durable observation.',
            schema_json: { type: 'object' },
            ui_schema_json: null,
            config_json: null,
          },
        ])
      }
      if (url.includes('/resource-records')) {
        return json(
          page([
            {
              id: 12,
              project_id: 1,
              resource_id: 1,
              plugin_slug: 'core',
              resource_key: 'learning',
              external_id: 'lesson-1',
              title: 'Lesson',
              data_json: { body: 'Use short hooks.', api_key: 'record-secret' },
              provenance_json: null,
              created_at: '2026-01-01T00:00:00Z',
              updated_at: '2026-01-02T00:00:00Z',
            },
          ]),
        )
      }
      if (url.includes('/artifacts')) return json(page())
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/resources', component: ResourceExplorerView }],
    })
    await router.push('/projects/1/resources')
    await router.isReady()

    const w = mount(ResourceExplorerView, { global: { plugins: [router] } })
    await vi.waitFor(() => expect(w.text()).toContain('Learning'))

    expect(w.text()).toContain('Lesson')
    expect(w.text()).toContain('[redacted]')
    expect(w.text()).not.toContain('record-secret')
  })
})

function plugin(slug: string, name: string) {
  return {
    id: slug === 'core' ? 1 : 2,
    slug,
    name,
    version: '0.1.0',
    description: '',
    source: 'builtin',
    manifest_json: {},
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    enabled_for_project: true,
  }
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}
