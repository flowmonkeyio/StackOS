// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useSchedulesStore } from './schedules'

const ORIG_FETCH = globalThis.fetch

const JOB = {
  id: 1,
  project_id: 1,
  kind: 'weekly-review',
  cron_expr: '0 2 * * *',
  next_run_at: '2026-05-06T02:00:00Z',
  last_run_at: '2026-05-05T02:00:00Z',
  last_run_status: 'success',
  enabled: true,
}

describe('schedules store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })
  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('refresh() loads the list', async () => {
    globalThis.fetch = vi.fn(async () => {
      return new Response(JSON.stringify([JOB]), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    }) as typeof fetch
    const store = useSchedulesStore()
    await store.refresh(1)
    expect(store.items.length).toBe(1)
  })

  it('uses only registered project-scoped schedule setup operations', async () => {
    const store = useSchedulesStore()
    const calls: Array<{ url: string; args: unknown }> = []
    globalThis.fetch = vi.fn(async (input, init) => {
      calls.push({ url: String(input), args: init?.body ? JSON.parse(String(init.body)).arguments : null })
      return json({ data: JOB })
    }) as typeof fetch
    await store.toggle(7, 4, true)
    await store.remove(7, 4)
    expect(calls).toEqual([
      { url: '/api/v1/operations/schedule.toggle/call', args: { response_mode: 'raw', project_id: 7, job_id: 4, enabled: true } },
      { url: '/api/v1/operations/schedule.remove/call', args: { response_mode: 'raw', project_id: 7, job_id: 4 } },
    ])
  })

  it('pages actual executions and patches only the linked task usage', async () => {
    const requests: Record<string, unknown>[] = []
    globalThis.fetch = vi.fn(async (_input, init) => {
      const args = JSON.parse(String(init?.body)).arguments
      requests.push(args)
      return json({ items: [{ request_id: requests.length, task_key: `workflow-${requests.length}`, agent_stats: null, active_execution: null }], next_cursor: requests.length === 1 ? 1 : null })
    }) as typeof fetch
    const store = useSchedulesStore()
    await store.refreshRuns(1)
    await store.refreshRuns(1, true)
    expect(requests[1]).toMatchObject({ project_id: 1, after_id: 1 })
    expect(store.runs).toHaveLength(2)
    const stats = { reported_usage: { input: 123 } } as never
    store.applyUsage({ tasks: [{ key: 'workflow-1', agent_stats: stats }], tickets: [{ key: 'workflow-2', agent_stats: stats }] })
    expect(store.runs[0].agent_stats).toEqual(stats)
    expect(store.runs[1].agent_stats).toBeNull()
    store.reset()
    expect(store.runs).toEqual([])
  })

  it('ignores run results and failures after changing project', async () => {
    const old = deferred<void>()
    globalThis.fetch = vi.fn(async (_input, init) => {
      const args = JSON.parse(String(init?.body)).arguments
      if (args.project_id === 1) { await old.promise; throw new Error('old project failure') }
      return json({ items: [{ request_id: 2, project_id: 2 }], next_cursor: null })
    }) as typeof fetch
    const store = useSchedulesStore()
    const pending = store.refreshRuns(1)
    await store.refreshRuns(2)
    old.resolve()
    await pending
    expect(store.runs.map(row => row.project_id)).toEqual([2])
    expect(store.runsError).toBeNull()
    expect(store.runsLoading).toBe(false)
  })

  it('keeps schedules scoped to the newest project request', async () => {
    const projectOne = deferred<void>()
    globalThis.fetch = vi.fn(async (input) => {
      const projectId = String(input).includes('/projects/1/') ? 1 : 2
      if (projectId === 1) await projectOne.promise
      return new Response(JSON.stringify([{ ...JOB, id: projectId, project_id: projectId }]), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    }) as typeof fetch
    const store = useSchedulesStore()

    const older = store.refresh(1)
    await store.refresh(2)
    projectOne.resolve()
    await older

    expect(store.currentProjectId).toBe(2)
    expect(store.items.map((job) => job.project_id)).toEqual([2])
  })
})

function json(body: unknown) { return new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } }) }

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  const promise = new Promise<T>((next) => {
    resolve = next
  })
  return { promise, resolve }
}
