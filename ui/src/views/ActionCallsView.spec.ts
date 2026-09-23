import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'

import UiConfirmDialog from '@/components/ui/UiConfirmDialog.vue'

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

  it('honors exact day and action drilldowns while ordinary ledger requests retain all dry-run modes', async () => {
    const requestedUrls: string[] = []
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      requestedUrls.push(url)
      if (url.includes('/action-calls'))
        return json(page([actionCall({ id: 2, status: 'success' })]))
      return catalogJson(url) ?? json({})
    }) as typeof fetch
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/action-calls', component: ActionCallsView }],
    })
    await router.push(
      '/projects/1/action-calls?created_from=2026-09-07T07:00:00Z&created_before=2026-09-07T23:00:00Z&dry_run=false&action_call_id=2&provider_key=stripe',
    )
    await router.isReady()
    const wrapper = mount(ActionCallsView, { global: { plugins: [router] } })
    await vi.waitFor(() =>
      expect(requestedUrls.some((url) => url.includes('/action-calls?'))).toBe(true),
    )
    const query = new URL(
      requestedUrls.find((url) => url.includes('/action-calls?'))!,
      'http://stackos.local',
    ).searchParams
    expect(query.get('created_from')).toBe('2026-09-07T07:00:00Z')
    expect(query.get('created_before')).toBe('2026-09-07T23:00:00Z')
    expect(query.get('dry_run')).toBe('false')
    expect(query.get('action_call_id')).toBe('2')
    expect(query.get('provider_key')).toBe('stripe')
    await vi.waitFor(() => expect(document.body.textContent).toContain('Action call #2'))
    wrapper.unmount()
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
    expect(new URL(requestedUrls.find((url) => url.includes('/action-calls?'))!, 'http://stackos.local').searchParams.get('dry_run')).toBeNull()
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

  it('renders and controls a durable action call through the registered lifecycle operations', async () => {
    const requested: Array<{ url: string; body: Record<string, unknown> | null }> = []
    let durableState = 'running'
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null
      requested.push({ url, body })
      const catalog = catalogJson(url)
      if (catalog) return catalog
      if (url.includes('/api/v1/projects/1/action-calls'))
        return json(page([actionCall({ id: 42, status: 'running' })]))
      if (url.endsWith('/api/v1/operations/actionCall.items/call'))
        return json(durableItems(durableState))
      if (url.endsWith('/api/v1/operations/actionCall.pause/call')) {
        durableState = 'paused'
        return json({ data: durableJob(durableState), project_id: 1, run_id: null })
      }
      if (url.endsWith('/api/v1/operations/actionCall.resume/call')) {
        durableState = 'running'
        return json({ data: durableJob(durableState), project_id: 1, run_id: null })
      }
      if (url.endsWith('/api/v1/operations/actionCall.retry/call')) {
        return json({ data: durableJob(durableState), project_id: 1, run_id: null })
      }
      if (url.endsWith('/api/v1/operations/actionCall.cancel/call')) {
        durableState = 'cancelled'
        return json({ data: durableJob(durableState), project_id: 1, run_id: null })
      }
      return json({})
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/action-calls', component: ActionCallsView }],
    })
    await router.push('/projects/1/action-calls')
    await router.isReady()
    const wrapper = mount(ActionCallsView, { global: { plugins: [router] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('#42'))

    await emitRowClick(wrapper, actionCall({ id: 42, status: 'running' }))
    await vi.waitFor(() => expect(document.body.textContent ?? '').toContain('1 / 4 settled'))
    const detailText = document.body.textContent ?? ''
    expect(detailText).toContain('Durable delivery')
    expect(detailText).toContain('1 / 4 settled')
    expect(detailText).toContain('Account pace')
    expect(detailText).toContain('2s')
    expect(detailText).toContain('Expires at')
    expect(detailText).toContain('Partial outcome')
    expect(detailText).toContain('Unknown receipt')
    expect(detailText).toContain('telegram-chat:100')
    expect(detailText).toContain('message:500')
    expect(detailText).not.toContain('token-secret')

    const pauseButton = documentButton('Pause')
    expect(pauseButton).toBeDefined()
    // A second click can arrive before Vue applies the disabled state. Only
    // the first control request may reach the registered operation.
    pauseButton?.click()
    pauseButton?.click()
    await nextTick()
    await vi.waitFor(() =>
      expect(requested.some((request) => request.url.endsWith('/actionCall.pause/call'))).toBe(true),
    )
    expect(requested.filter((request) => request.url.endsWith('/actionCall.pause/call'))).toHaveLength(1)
    const pause = requested.find((request) => request.url.endsWith('/actionCall.pause/call'))
    expect(pause?.body).toEqual({
      arguments: { response_mode: 'raw', project_id: 1, action_call_id: 42 },
    })
    await vi.waitFor(() => expect(document.body.textContent ?? '').toContain('Resume'))
    await vi.waitFor(() =>
      expect(requested.filter((request) => request.url.endsWith('/actionCall.items/call'))).toHaveLength(2),
    )
    expect(documentButton('Resume')?.disabled).toBe(false)

    await clickDocumentButton('Resume')
    const resumeConfirmation = wrapper.findComponent(UiConfirmDialog)
    expect(resumeConfirmation.props('modelValue')).toBe(true)
    expect(resumeConfirmation.text()).toContain('Resume delivery?')
    expect(documentButton('Resume delivery')).toBeDefined()
    await clickDocumentButton('Resume delivery')
    await vi.waitFor(() =>
      expect(requested.some((request) => request.url.endsWith('/actionCall.resume/call'))).toBe(true),
    )
    const resume = requested.find((request) => request.url.endsWith('/actionCall.resume/call'))
    expect(resume?.body).toEqual({
      arguments: {
        response_mode: 'raw',
        project_id: 1,
        action_call_id: 42,
        confirm_direct: true,
        intent_summary: 'Operator confirmed resuming durable Action Call #42 from the Action Calls view.',
      },
    })
    await vi.waitFor(() =>
      expect(requested.filter((request) => request.url.endsWith('/actionCall.items/call'))).toHaveLength(3),
    )
    await vi.waitFor(() => expect(document.body.textContent ?? '').toContain('Pause'))
    expect(documentButton('Pause')?.disabled).toBe(false)

    const retryCheckbox = document.querySelector<HTMLInputElement>('input[aria-label="Select row 3"]')
    expect(retryCheckbox).toBeTruthy()
    retryCheckbox?.click()
    await nextTick()
    expect(documentButton('Retry selected')?.disabled).toBe(false)
    await clickDocumentButton('Retry selected')
    const retryConfirmation = wrapper.findComponent(UiConfirmDialog)
    expect(retryConfirmation.props('modelValue')).toBe(true)
    expect(retryConfirmation.text()).toContain('Retry selected delivery?')
    await clickContainedButton(retryConfirmation, 'Retry selected')
    await vi.waitFor(() =>
      expect(requested.some((request) => request.url.endsWith('/actionCall.retry/call'))).toBe(true),
    )
    const retry = requested.find((request) => request.url.endsWith('/actionCall.retry/call'))
    expect(retry?.body).toEqual({
      arguments: {
        response_mode: 'raw',
        project_id: 1,
        action_call_id: 42,
        item_ids: [103],
        confirm_direct: true,
        intent_summary:
          'Operator confirmed retrying selected delivery item receipts for Action Call #42 from the Action Calls view.',
      },
    })

    await vi.waitFor(() =>
      expect(requested.filter((request) => request.url.endsWith('/actionCall.items/call'))).toHaveLength(4),
    )
    await vi.waitFor(() => expect(documentButton('Cancel delivery')?.disabled).toBe(false))
    await clickDocumentButton('Cancel delivery')
    const cancelConfirmation = wrapper.findComponent(UiConfirmDialog)
    expect(cancelConfirmation.props('modelValue')).toBe(true)
    expect(cancelConfirmation.text()).toContain('Cancel remaining delivery?')
    await clickContainedButton(cancelConfirmation, 'Cancel remaining delivery')
    await vi.waitFor(() =>
      expect(requested.some((request) => request.url.endsWith('/actionCall.cancel/call'))).toBe(true),
    )
    await vi.waitFor(() => expect(document.body.textContent ?? '').toContain('cancelled'))
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

function durableJob(state: string) {
  return {
    id: 8,
    project_id: 1,
    action_call_id: 42,
    credential_ref: 'cred_safe',
    action_ref: 'communications.telegram.message.broadcast',
    input_digest: 'digest-safe',
    state,
    due_at: '2026-01-02T00:00:00Z',
    expires_at: '2026-01-03T00:00:00Z',
    can_cancel: state !== 'cancelled',
    pacing_json: { account_interval_seconds: 2, destination_interval_seconds: 1 },
    item_count: 4,
    pending_count: state === 'cancelled' ? 0 : 1,
    leased_count: 0,
    completed_count: 1,
    failed_count: 0,
    cancelled_count: state === 'cancelled' ? 2 : 0,
    unknown_count: 1,
    next_eligible_at: state === 'running' ? '2026-01-02T00:00:02Z' : null,
  }
}

function durableItems(state: string) {
  return {
    action_call_id: 42,
    job: durableJob(state),
    count: 4,
    items: [
      {
        id: 101,
        project_id: 1,
        job_id: 8,
        ordinal: 0,
        destination_ref: 'telegram-chat:100',
        correlation_ref: 'delivery:101',
        input_json: { message: { text: 'Hello' }, token: 'token-secret' },
        state: 'succeeded',
        next_eligible_at: '2026-01-02T00:00:00Z',
        attempt_ref: 'attempt-1',
        lease_ref: null,
        lease_expires_at: null,
        attempt_count: 1,
        result_json: { message_ref: 'message:500' },
        error: null,
        progress_json: { provider_receipts: { '500': { status: 'sent' } } },
        temporary_message_ref: 'message:temporary:500',
        final_message_ref: 'message:500',
        provider_sending_id: '101',
        can_retry: false,
      },
      {
        id: 102,
        project_id: 1,
        job_id: 8,
        ordinal: 1,
        destination_ref: 'telegram-chat:101',
        correlation_ref: 'delivery:102',
        input_json: { message: { text: 'Hello' } },
        state: 'unknown-hold',
        next_eligible_at: '2026-01-02T00:00:00Z',
        attempt_ref: 'attempt-2',
        lease_ref: null,
        lease_expires_at: null,
        attempt_count: 1,
        result_json: null,
        error: 'Timed out after native acceptance',
        progress_json: { temporary_message_ids: [501] },
        temporary_message_ref: 'message:temporary:501',
        final_message_ref: null,
        provider_sending_id: '102',
        can_retry: false,
      },
      {
        id: 103,
        project_id: 1,
        job_id: 8,
        ordinal: 2,
        destination_ref: 'telegram-chat:102',
        correlation_ref: 'delivery:103',
        input_json: { message: { text: 'Hello' } },
        state: 'failed',
        next_eligible_at: '2026-01-02T00:00:00Z',
        attempt_ref: 'attempt-3',
        lease_ref: null,
        lease_expires_at: null,
        attempt_count: 1,
        result_json: { retry_safe: true },
        error: 'Peer rejected before provider acceptance',
        progress_json: null,
        temporary_message_ref: null,
        final_message_ref: null,
        provider_sending_id: null,
        can_retry: true,
      },
      {
        id: 104,
        project_id: 1,
        job_id: 8,
        ordinal: 3,
        destination_ref: 'telegram-chat:103',
        correlation_ref: 'delivery:104',
        input_json: { message: { text: 'Hello' } },
        state: 'pending',
        next_eligible_at: '2026-01-02T00:00:00Z',
        attempt_ref: null,
        lease_ref: null,
        lease_expires_at: null,
        attempt_count: 0,
        result_json: null,
        error: null,
        progress_json: null,
        temporary_message_ref: null,
        final_message_ref: null,
        provider_sending_id: null,
        can_retry: false,
      },
    ],
  }
}

async function clickButton(wrapper: ReturnType<typeof mount>, label: string): Promise<void> {
  await clickContainedButton(wrapper, label)
}

async function clickContainedButton(wrapper: VueWrapper, label: string): Promise<void> {
  const button = wrapper.findAll('button').find((candidate) => candidate.text().trim() === label)
  expect(button, `${label} button`).toBeDefined()
  await button?.trigger('click')
}

async function clickDocumentButton(label: string): Promise<void> {
  const button = documentButton(label)
  expect(button, `${label} button`).toBeDefined()
  button?.click()
  await nextTick()
}

function documentButton(label: string): HTMLButtonElement | undefined {
  return [...document.querySelectorAll('button')].find(
    (candidate) => candidate.textContent?.trim() === label,
  ) as HTMLButtonElement | undefined
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
