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
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  it('renders data drilldown schema details and redacts record details', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url.includes('/api/v1/plugins')) {
        return json([
          plugin('core', 'StackOS Core'),
          plugin('communications', 'Communications'),
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
            schema_json: { type: 'object', properties: { body: { type: 'string' } } },
            ui_schema_json: { layout: 'compact' },
            config_json: { api_key: 'schema-secret' },
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
    await router.push('/projects/1/resources?plugin_slug=communications')
    await router.isReady()

    const w = mount({ template: '<RouterView />' }, { global: { plugins: [router] } })
    await vi.waitFor(() => expect(w.text()).toContain('Learning'))

    expect(w.text()).toContain('Communications Data')
    const schemaRow = w
      .findAll('tr')
      .find((row) => row.text().includes('Learning') && row.text().includes('Durable observation.'))
    await schemaRow?.trigger('click')
    await vi.waitFor(() =>
      expect(document.body.textContent ?? '').toContain('Schema Details'),
    )
    expect(document.body.textContent ?? '').toContain('Schema JSON')
    expect(document.body.textContent ?? '').toContain('UI Schema JSON')
    expect(w.text()).toContain('Lesson')
    const recordRow = w
      .findAll('tr')
      .find((row) => row.text().includes('Lesson') && row.text().includes('learning'))
    await recordRow?.trigger('click')
    await vi.waitFor(() =>
      expect(document.body.textContent ?? '').toContain('[redacted]'),
    )
    expect(document.body.textContent ?? '').not.toContain('schema-secret')
    expect(document.body.textContent ?? '').not.toContain('record-secret')
    expect(schemaRow?.attributes('tabindex')).toBe('0')
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
