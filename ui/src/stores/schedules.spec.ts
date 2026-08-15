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

  it('does not expose schedule mutation methods to the UI store', () => {
    const store = useSchedulesStore()
    const exposed = store as unknown as Record<string, unknown>
    expect(exposed.set).toBeUndefined()
    expect(exposed.toggle).toBeUndefined()
    expect(exposed.disable).toBeUndefined()
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

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  const promise = new Promise<T>((next) => {
    resolve = next
  })
  return { promise, resolve }
}
