import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'

import ActionCallsView from './ActionCallsView.vue'

const ORIG_FETCH = globalThis.fetch

describe('ActionCallsView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    document.body.innerHTML = ''
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
              : status === 'running'
                ? [actionCall({ id: 3, status: 'running' })]
                : [
                    actionCall({ id: 1, status: 'success' }),
                    actionCall({ id: 3, status: 'running' }),
                  ],
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
    expect(wrapper.text()).not.toContain('Action call #1')
    expect(wrapper.get('button[aria-label="Filter to running calls"]').text()).toContain('1')

    await clickButton(wrapper, 'Running')
    await vi.waitFor(() =>
      expect(requestedUrls.some((url) => url.includes('status=running'))).toBe(true),
    )
    await vi.waitFor(() => expect(wrapper.text()).toContain('#3'))

    await clickButton(wrapper, 'All')
    await vi.waitFor(() => expect(wrapper.text()).toContain('#1'))

    await emitRowClick(wrapper, actionCall({ id: 1, status: 'success' }))
    await vi.waitFor(() => expect(document.body.textContent ?? '').toContain('Action call #1'))
    const detailText = document.body.textContent ?? ''
    expect(detailText).toContain('Execution Target')
    expect(detailText).toContain('Run Context')
    expect(detailText).toContain('Execution Context')
    expect(detailText).toContain('ctx_provider_analysis')
    expect(detailText).toContain('Max parallel')
    expect(detailText).toContain('File Output')
    expect(detailText).toContain('/tmp/provider-output.json')
    expect(detailText).toContain('Outcome')
    expect(detailText).toContain('Timeline')
    expect(detailText).toContain('Provider evidence')
    expect(detailText).toContain('COMPLETE_WITH_ERRORS')
    expect(detailText).toContain('partial')
    expect(detailText).toContain('hubspot-request-123')
    expect(detailText).toContain('Review two rejected rows.')
    expect(detailText).toContain('artifact_hubspot_export')
    expect(detailText).toContain('hubspot-export.csv')
    expect(detailText).toContain('30s')
    expect(detailText).toContain('[redacted]')
    expect(detailText).toContain('acct-managed')
    expect(detailText).not.toContain('sk-secret')
    expect(detailText).not.toContain('token-secret')

    await clickButton(wrapper, 'Failed')
    await vi.waitFor(() =>
      expect(requestedUrls.some((url) => url.includes('status=failed'))).toBe(true),
    )
    await vi.waitFor(() => expect(wrapper.text()).toContain('#2'))
    await emitRowClick(
      wrapper,
      actionCall({ id: 2, status: 'failed', error: 'provider rejected request' }),
    )
    await vi.waitFor(() =>
      expect(document.body.textContent ?? '').toContain('provider rejected request'),
    )
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
  if (url.includes('/api/v1/catalog')) throw new Error('unexpected aggregate catalog request')
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
  status: 'running' | 'success' | 'failed'
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
    provider_context_json: { acting_as_account: 'acct-managed', token: 'token-secret' },
    response_json:
      status === 'success'
        ? {
            status: 'partial',
            provider_status: 'COMPLETE_WITH_ERRORS',
            request_id: 'hubspot-request-123',
            failure_count: 2,
            result_available: true,
            response_complete: false,
            retryable: true,
            retry_after: 30,
            next_action: { label: 'Review two rejected rows.' },
            artifact_ref: 'artifact_hubspot_export',
            filename: 'hubspot-export.csv',
            mime_type: 'text/csv',
            size_bytes: 4096,
            asset_url: '/asset.webp',
            token: 'token-secret',
          }
        : null,
    metadata_json: {
      credential_ref: 'cred_safe',
      execution_context: {
        context_ref: 'ctx_provider_analysis',
        output_policy_json: { mode: 'file_if_large' },
        request_budget_json: { max_parallel: 3 },
        artifact_namespace: 'provider-analysis',
      },
      file_backed_output: {
        path: '/tmp/provider-output.json',
        content_type: 'application/json',
        schema_version: 'stackos.action-output.v1',
        schema_ref: 'stackos.action-output.v1',
        schema_operation: 'schema.get',
        bytes: 2048,
        sha256: 'sha256-output',
      },
    },
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
  const button = wrapper.findAll('button').find((candidate) => candidate.text().trim() === label)
  expect(button, `${label} button`).toBeDefined()
  await button?.trigger('click')
}

async function emitRowClick(
  wrapper: ReturnType<typeof mount>,
  row: ReturnType<typeof actionCall>,
): Promise<void> {
  const table = wrapper.findComponent({ name: 'DataTable' })
  expect(table.exists()).toBe(true)
  table.vm.$emit('row-click', row)
  await nextTick()
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}
