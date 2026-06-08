import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import AgentRequestsView from './AgentRequestsView.vue'

const ORIG_FETCH = globalThis.fetch

describe('AgentRequestsView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  it('renders agent request queue state through the generic operation endpoint', async () => {
    const calls: Array<{ url: string; body: unknown }> = []
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null })
      if (url.startsWith('/api/v1/projects?')) return json({ items: [], next_cursor: null })
      return json({
        items: [
          agentRequest({
            id: 7,
            request_key: 'telegram:update:7',
            title: 'Telegram message needs launch review',
            body_preview: 'Please check today',
            source_provider: 'telegram-bot',
            source_kind: 'telegram-message',
          }),
        ],
        next_cursor: null,
        total_estimate: 1,
      })
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/agent-requests', component: AgentRequestsView }],
    })
    await router.push('/projects/1/agent-requests')
    await router.isReady()

    const pinia = createPinia()
    const wrapper = mount(
      { template: '<RouterView />' },
      {
        global: {
          plugins: [router, pinia],
          stubs: { teleport: true },
        },
      },
    )

    await vi.waitFor(() => expect(wrapper.text()).toContain('Telegram message needs launch review'))
    expect(wrapper.text()).toContain('Agent Requests')
    expect(wrapper.text()).toContain('telegram-bot')
    await clickRow(wrapper, 'Telegram message needs launch review')
    expect(wrapper.text()).toContain('telegram:update:7')
    const operationCall = calls.find((call) =>
      call.url.includes('/api/v1/operations/agentRequest.list/call'),
    )
    expect(operationCall?.url).toBe('/api/v1/operations/agentRequest.list/call')
    expect(operationCall?.body).toMatchObject({
      arguments: {
        project_id: 1,
        claimable: true,
        limit: 50,
      },
    })
  })

  it('changes filters without leaving the operation registry path', async () => {
    const calls: Array<{ url: string; body: { arguments: Record<string, unknown> } }> = []
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (url.startsWith('/api/v1/projects?')) {
        return json({ items: [], next_cursor: null })
      }
      const body = init?.body ? JSON.parse(String(init.body)) : { arguments: {} }
      calls.push({ url, body })
      return json({
        items: [agentRequest({ id: calls.length, status: 'resolved', attention_status: 'archived' })],
        next_cursor: null,
        total_estimate: 1,
      })
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/agent-requests', component: AgentRequestsView }],
    })
    await router.push('/projects/3/agent-requests')
    await router.isReady()

    const pinia = createPinia()
    const wrapper = mount(
      { template: '<RouterView />' },
      {
        global: {
          plugins: [router, pinia],
          stubs: { teleport: true },
        },
      },
    )
    await vi.waitFor(() => expect(calls.length).toBe(1))

    await clickButton(wrapper, 'Terminal')
    await vi.waitFor(() =>
      expect(calls.at(-1)?.body.arguments).toMatchObject({
        project_id: 3,
        statuses: ['resolved', 'ignored', 'failed'],
      }),
    )

    await clickButton(wrapper, 'All attention states')
    await clickButton(wrapper, 'Archived')
    await vi.waitFor(() =>
      expect(calls.at(-1)?.body.arguments).toMatchObject({
        attention_status: 'archived',
      }),
    )
    expect(calls.every((call) => call.url === '/api/v1/operations/agentRequest.list/call')).toBe(true)
  })

  it('closes stale details when filters replace the selected request', async () => {
    const calls: Array<{ url: string; body: { arguments: Record<string, unknown> } }> = []
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      if (url.startsWith('/api/v1/projects?')) {
        return json({ items: [], next_cursor: null })
      }
      const body = init?.body ? JSON.parse(String(init.body)) : { arguments: {} }
      calls.push({ url, body })
      const firstLoad = calls.length === 1
      return json({
        items: [
          firstLoad
            ? agentRequest({
                id: 7,
                request_key: 'request:original',
                title: 'Original request',
              })
            : agentRequest({
                id: 9,
                request_key: 'request:terminal',
                title: 'Terminal request',
                status: 'resolved',
              }),
        ],
        next_cursor: null,
        total_estimate: 1,
      })
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/agent-requests', component: AgentRequestsView }],
    })
    await router.push('/projects/1/agent-requests')
    await router.isReady()

    const pinia = createPinia()
    const wrapper = mount(
      { template: '<RouterView />' },
      {
        global: {
          plugins: [router, pinia],
          stubs: { teleport: true },
        },
      },
    )

    await vi.waitFor(() => expect(wrapper.text()).toContain('Original request'))
    await clickRow(wrapper, 'Original request')
    expect(wrapper.text()).toContain('request:original')

    await clickButton(wrapper, 'Terminal')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Terminal request'))
    expect(wrapper.text()).not.toContain('request:original')
  })
})

function agentRequest(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: 1,
    project_id: 1,
    request_key: 'request:1',
    title: 'Request title',
    body_preview: '',
    source_provider: null,
    source_kind: null,
    source_resource_key: null,
    source_resource_record_id: null,
    source_message_ref: null,
    priority: 0,
    status: 'new',
    attention_status: 'unread',
    claimed_by: null,
    claimed_at: null,
    claim_expires_at: null,
    run_plan_id: null,
    completed_at: null,
    ignored_at: null,
    metadata_json: { safe: true },
    created_at: '2026-05-23T00:00:00',
    updated_at: '2026-05-23T00:00:00',
    ...overrides,
  }
}

async function clickButton(wrapper: ReturnType<typeof mount>, label: string): Promise<void> {
  const button = wrapper.findAll('button').find((candidate) => candidate.text().trim() === label)
  expect(button, `${label} button`).toBeDefined()
  await button?.trigger('click')
}

async function clickRow(wrapper: ReturnType<typeof mount>, label: string): Promise<void> {
  const row = wrapper.findAll('tr').find((candidate) => candidate.text().includes(label))
  expect(row, `${label} row`).toBeDefined()
  await row?.trigger('click')
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}
