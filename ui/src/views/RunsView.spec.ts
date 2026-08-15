// Smoke tests for RunsView (list + filter pills).

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import RunsView from './RunsView.vue'
import { RunKind, RunStatus } from '@/api'
import { useRunsStore, type Run } from '@/stores/runs'

const ORIG_FETCH = globalThis.fetch

function mountView() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:id/runs', name: 'project-runs', component: RunsView },
      { path: '/projects/:id/runs/:run_id', name: 'project-run-detail', component: RunsView },
    ],
  })
  void router.push('/projects/1/runs')
  return router
}

describe('RunsView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })
  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('renders heading + status pill bar + kind/status filters', async () => {
    globalThis.fetch = vi.fn(async () => {
      return new Response(
        JSON.stringify({ items: [], next_cursor: null, total_estimate: 0 }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      )
    }) as typeof fetch
    const router = mountView()
    await router.isReady()
    const w = mount(RunsView, { global: { plugins: [router] } })
    await new Promise((r) => setTimeout(r, 0))
    expect(w.text()).toContain('Runs')
    expect(w.text()).toContain('Running')
    expect(w.text()).toContain('Success')
    expect(w.text()).toContain('Failed')
    expect(w.text()).toContain('Aborted')
    expect(w.text()).toContain('Kind')
  })

  it('clears retained run rows before the next project response resolves', async () => {
    const projectTwo = deferred<Response>()
    const calls: string[] = []
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      calls.push(url)
      return projectTwo.promise
    }) as typeof fetch

    const store = useRunsStore()
    store.items = [runFixture(1, 'Alpha only run')]
    store.currentProjectId = 1

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id/runs', component: RunsView }],
    })
    await router.push('/projects/2/runs')
    await router.isReady()

    const wrapper = mount(RunsView, { global: { plugins: [router] } })

    expect(store.items).toEqual([])
    expect(wrapper.text()).not.toContain('Alpha only run')
    expect(calls.some((url) => url.startsWith('/api/v1/projects/2/runs?'))).toBe(true)

    projectTwo.resolve(
      new Response(
        JSON.stringify({
          items: [runFixture(2, 'Beta run')],
          next_cursor: null,
          total_estimate: 1,
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    )
    await vi.waitFor(() => expect(wrapper.text()).toContain('Beta run'))

    expect(wrapper.text()).not.toContain('Alpha only run')
    wrapper.unmount()
  })
})

function runFixture(projectId: number, lastStep: string): Run {
  return {
    id: projectId,
    project_id: projectId,
    kind: RunKind.skill_run,
    parent_run_id: null,
    client_session_id: null,
    started_at: '2026-05-05T00:00:00Z',
    ended_at: null,
    status: RunStatus.running,
    error: null,
    heartbeat_at: '2026-05-05T00:00:30Z',
    last_step: lastStep,
    last_step_at: '2026-05-05T00:00:25Z',
    metadata_json: {},
  }
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  const promise = new Promise<T>((next) => {
    resolve = next
  })
  return { promise, resolve }
}
