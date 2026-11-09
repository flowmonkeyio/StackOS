import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import { formatAbsoluteDateTime } from '@/lib/stackos/time'
import RunDetail from './RunDetail.vue'
import { useStackOsResourcesStore } from '@/stores/stackosResources'

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

async function mountRun(overrides: { heartbeat_at?: string | null; status?: string } = {}, artifactRows: unknown[] = []) {
  globalThis.fetch = vi.fn(async (input) => {
    const url = String(input)
    if (url.includes('/api/v1/runs/7/children')) {
      return json([])
    }
    if (url.includes('/api/v1/runs/7')) {
      return json({ ...run, ...overrides })
    }
    if (url.includes('/artifacts?')) return json(page(artifactRows))
    return json(page())
  }) as typeof fetch

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/projects/:id/runs/:run_id', component: RunDetail }],
  })
  await router.push('/projects/1/runs/7')
  await router.isReady()

  const wrapper = mount(RunDetail, {
    props: { runId: 7, projectId: 1 },
    global: { plugins: [router] },
  })
  await vi.waitFor(() => expect(wrapper.text()).toContain('run-plan-step'))
  return wrapper
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
    const w = await mountRun()

    expect(w.text()).toContain('[redacted]')
    expect(w.text()).toContain('cred_123')
    expect(w.text()).not.toContain('metadata-secret')
    w.unmount()
  })

  it('uses the canonical artifact reader instead of owning another artifact inventory', async () => {
    const w = await mountRun()
    const resources = useStackOsResourcesStore()
    expect(resources.currentProjectId).toBe(1)
    expect(resources.artifactPage.loaded).toBe(true)
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/projects/1/artifacts?include_inactive=true&limit=50'),
      expect.anything(),
    )
    w.unmount()
  })

  it('links artifacts only through explicit same-project run fields', async () => {
    const base = { id: 1, project_id: 1, plugin_slug: 'engineering', kind: 'document',
      name: 'Recorded link', uri: 's3://example/ref', status: 'archived', mime_type: null,
      metadata_json: null, provenance_json: { run_id: 7 }, created_at: '2026-01-01', updated_at: '2026-01-01' }
    const w = await mountRun({}, [base,
      { ...base, id: 2, name: 'Unrelated numeric metadata', provenance_json: { size: 7 } },
      { ...base, id: 3, name: 'Other project reference', project_id: 2 },
    ])
    expect(w.text()).toContain('Recorded link')
    expect(w.text()).not.toContain('Unrelated numeric metadata')
    expect(w.text()).not.toContain('Other project reference')
    w.unmount()
  })

  it.each([
    { heartbeat_at: run.heartbeat_at, status: 'running' },
    { heartbeat_at: null, status: 'running' },
    { heartbeat_at: run.heartbeat_at, status: 'aborted' },
  ])('shows recorded audit activity for $status with heartbeat $heartbeat_at', async (audit) => {
    const w = await mountRun(audit)
    const summary = w.get('[aria-label="Run summary"]')
    const heartbeatLabel = summary.findAll('dt').find((dt) => dt.text() === 'Last audit heartbeat')

    expect(heartbeatLabel).toBeDefined()
    expect(heartbeatLabel?.element.nextElementSibling?.textContent?.trim()).toBe(
      audit.heartbeat_at ? formatAbsoluteDateTime(audit.heartbeat_at) : 'Not recorded',
    )
    expect(summary.text()).toContain('Recorded status')
    expect(w.text()).toContain(
      'Status and heartbeat record audit activity. They do not verify worker progress, success, termination, or settled effects.',
    )
    expect(summary.text()).not.toContain('Running…')
    w.unmount()
  })
})

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}
