import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import RunDetail from './RunDetail.vue'

const ORIG_FETCH = globalThis.fetch

const run = {
  id: 7,
  project_id: 1,
  kind: 'skill-run',
  parent_run_id: null,
  client_session_id: null,
  started_at: '2026-05-05T00:00:00Z',
  ended_at: null,
  status: 'running',
  error: null,
  heartbeat_at: '2026-05-05T00:00:30Z',
  last_step: 'run-plan-step',
  last_step_at: '2026-05-05T00:00:25Z',
  metadata_json: { api_key: 'metadata-secret', credential_ref: 'cred_123' },
}

function page(items: unknown[] = []) {
  return { items, next_cursor: null, total_estimate: items.length }
}

describe('RunDetail', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('sanitizes raw run metadata panels', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      if (url.includes('/api/v1/runs/7/children')) {
        return json([])
      }
      if (url.includes('/api/v1/runs/7')) {
        return json(run)
      }
      return json(page())
    }) as typeof fetch

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/runs/:run_id', component: RunDetail }],
    })
    await router.push('/projects/1/runs/7')
    await router.isReady()

    const w = mount(RunDetail, {
      props: { runId: 7, projectId: 1 },
      global: { plugins: [router] },
    })
    await vi.waitFor(() => expect(w.text()).toContain('run-plan-step'))

    expect(w.text()).toContain('[redacted]')
    expect(w.text()).toContain('cred_123')
    expect(w.text()).not.toContain('metadata-secret')
  })
})

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}
