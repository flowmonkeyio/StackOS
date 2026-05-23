import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import ActionCallsView from './ActionCallsView.vue'

const ORIG_FETCH = globalThis.fetch

describe('ActionCallsView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('renders project action-call audit rows with sanitized details and filters', async () => {
    const requestedUrls: string[] = []

    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      requestedUrls.push(url)
      const catalog = catalogJson(url)
      if (catalog) return catalog

      if (url.includes('/api/v1/projects/1/action-calls')) {
        const status = new URL(url, 'http://stackos.local').searchParams.get('status')
        return json(
          page(
            status === 'failed'
              ? [actionCall({ id: 2, status: 'failed', error: 'provider rejected request' })]
              : [actionCall({ id: 1, status: 'success' })],
          ),
        )
      }

      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/action-calls', component: ActionCallsView }],
    })
    await router.push('/projects/1/action-calls?plugin_slug=utils')
    await router.isReady()

    const wrapper = mount(ActionCallsView, { global: { plugins: [router] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('utils:image.generate'))

    expect(requestedUrls.some((url) => url.includes('plugin_slug=utils'))).toBe(true)
    expect(wrapper.text()).toContain('[redacted]')
    expect(wrapper.text()).not.toContain('sk-secret')
    expect(wrapper.text()).not.toContain('token-secret')

    await clickButton(wrapper, 'Failed')
    await vi.waitFor(() =>
      expect(requestedUrls.some((url) => url.includes('status=failed'))).toBe(true),
    )
    await vi.waitFor(() => expect(wrapper.text()).toContain('provider rejected request'))
  })
})

function catalogJson(url: string): Response | null {
  const now = '2026-01-01T00:00:00Z'
  if (url.includes('/api/v1/plugins')) {
    return json([
      {
        id: 1,
        slug: 'utils',
        name: 'Utilities',
        version: '0.1.0',
        description: '',
        source: 'builtin',
        manifest_json: {},
        enabled_for_project: true,
        created_at: now,
        updated_at: now,
      },
    ])
  }
  if (url.includes('/api/v1/catalog')) return json({ plugins: [] })
  if (url.includes('/api/v1/capabilities')) return json([])
  if (url.includes('/api/v1/providers')) return json([])
  if (url.includes('/api/v1/actions')) {
    return json([
      {
        id: 1,
        plugin_id: 1,
        plugin_slug: 'utils',
        key: 'image.generate',
        name: 'Generate image',
        description: '',
        provider_key: 'openai-images',
        operation: 'image.generate',
        risk_level: 'write',
        input_schema_json: {},
        output_schema_json: {},
        config_json: { connector: 'openai-images' },
        availability: null,
      },
    ])
  }
  if (url.includes('/api/v1/resources')) return json([])
  return null
}

function actionCall({
  id,
  status,
  error = null,
}: {
  id: number
  status: 'success' | 'failed'
  error?: string | null
}) {
  return {
    id,
    project_id: 1,
    run_id: 10,
    run_plan_id: 20,
    run_plan_step_id: 30,
    action_key: 'image.generate',
    plugin_slug: 'utils',
    provider_key: 'openai-images',
    connector_key: 'openai-images',
    operation: 'image.generate',
    status,
    dry_run: false,
    credential_ref: 'cred_safe',
    request_json: { prompt: 'test', api_key: 'sk-secret' },
    response_json: status === 'success' ? { asset_url: '/asset.webp', token: 'token-secret' } : null,
    metadata_json: { credential_ref: 'cred_safe' },
    cost_cents: 2,
    duration_ms: 42,
    error,
    created_at: '2026-01-01T00:00:00Z',
    completed_at: '2026-01-01T00:00:01Z',
  }
}

function page(items: unknown[] = []) {
  return { items, next_cursor: null, total_estimate: items.length }
}

async function clickButton(wrapper: ReturnType<typeof mount>, label: string): Promise<void> {
  const button = wrapper
    .findAll('button')
    .find((candidate) => candidate.text().trim() === label)
  expect(button, `${label} button`).toBeDefined()
  await button?.trigger('click')
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}
