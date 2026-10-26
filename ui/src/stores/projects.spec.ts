// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useProjectsStore } from './projects'

const ORIG_FETCH = globalThis.fetch

const PAGE = {
  items: [
    {
      id: 1,
      slug: 'alpha',
      name: 'Alpha',
      domain: 'alpha.test',
      niche: 'a',
      locale: 'en-US',
      is_active: true,
      schedule_json: null,
      created_at: '2025-01-01T00:00:00Z',
      updated_at: '2025-01-01T00:00:00Z',
    },
  ],
  next_cursor: null,
  total_estimate: 1,
}

describe('projects store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })
  afterEach(() => {
    globalThis.fetch = ORIG_FETCH
    vi.restoreAllMocks()
  })

  it('fetches and stores the first page on refresh()', async () => {
    globalThis.fetch = vi.fn(async () => {
      return new Response(JSON.stringify(PAGE), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    }) as typeof fetch
    const store = useProjectsStore()
    await store.refresh()
    expect(store.items.length).toBe(1)
    expect(store.items[0].name).toBe('Alpha')
    expect(store.totalEstimate).toBe(1)
    expect(store.nextCursor).toBeNull()
    expect(store.activeProjectId).toBe(1)
  })

  it('sets error state when refresh() fails', async () => {
    globalThis.fetch = vi.fn(async () => {
      return new Response(JSON.stringify({ detail: 'nope' }), {
        status: 500,
        headers: { 'content-type': 'application/json' },
      })
    }) as typeof fetch
    const store = useProjectsStore()
    await store.refresh()
    expect(store.error).not.toBeNull()
    expect(store.items.length).toBe(0)
  })

  it('dedupes concurrent refresh() calls for the same page', async () => {
    let resolveFetch!: (response: Response) => void
    const fetchPromise = new Promise<Response>((resolve) => {
      resolveFetch = resolve
    })
    globalThis.fetch = vi.fn(() => fetchPromise) as typeof fetch

    const store = useProjectsStore()
    const first = store.refresh()
    const second = store.refresh()
    resolveFetch(
      new Response(JSON.stringify(PAGE), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
    await Promise.all([first, second])

    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
    expect(store.items).toHaveLength(1)
  })

  it('creates a project and marks it active', async () => {
    const postedBodies: unknown[] = []
    globalThis.fetch = vi.fn(async (_input, init) => {
      if (init?.body) postedBodies.push(JSON.parse(String(init.body)))
      return new Response(
        JSON.stringify({
          data: PAGE.items[0],
          run_id: null,
          project_id: PAGE.items[0].id,
        }),
        {
          status: 201,
          headers: { 'content-type': 'application/json' },
        },
      )
    }) as typeof fetch
    const store = useProjectsStore()
    const created = await store.createProject({
      slug: 'alpha',
      name: 'Alpha',
      domain: 'alpha.test',
      niche: null,
      locale: 'en-US',
      schedule_json: null,
    })

    expect(created.id).toBe(1)
    expect(store.items[0].name).toBe('Alpha')
    expect(store.activeProjectId).toBe(1)
    expect(postedBodies[0]).toMatchObject({ slug: 'alpha', domain: 'alpha.test' })
  })

  it('exposes activeProject via getter', () => {
    const store = useProjectsStore()
    store.items = PAGE.items as never
    store.activeProjectId = 1
    expect(store.activeProject?.name).toBe('Alpha')
  })
})
