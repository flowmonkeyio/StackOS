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
      order_index: 1,
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

  it('loads every page before admitting a move, then reloads authoritative order and preserves selection', async () => {
    const rows = Array.from({ length: 75 }, (_, i) => ({ ...PAGE.items[0], id: i + 1, order_index: i + 1 }))
    const requests: { url: string; body: unknown }[] = []
    let moved = false
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      requests.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null })
      if (url.includes('/operations/')) {
        moved = true
        return json({ data: { ...rows[74], order_index: 0 } })
      }
      const ordered = moved ? [{ ...rows[74], order_index: 0 }, ...rows.slice(0, 74)] : rows
      const offset = url.includes('after=') ? 50 : 0
      return json({ items: ordered.slice(offset, offset + 50), next_cursor: offset ? null : 50, total_estimate: 75 })
    })
    const store = useProjectsStore()
    await store.refresh()
    store.setActiveProjectId(60)
    expect(await store.moveProject({ itemKey: '1', anchorKey: '2', placement: 'after' })).toBe(false)
    await store.loadAll()
    expect(store.items).toHaveLength(75)
    expect(await store.moveProject({ itemKey: '75', anchorKey: '1', placement: 'before' })).toBe(true)
    expect(requests.find((request) => request.url.includes('/operations/'))).toEqual({
      url: '/api/v1/operations/project.move/call',
      body: { arguments: { response_mode: 'raw', project_id: 75, anchor_id: 1, placement: 'before', scope: 'catalog' } },
    })
    expect(store.items.map((p) => p.id)).toEqual([75, ...rows.slice(0, 74).map((p) => p.id)])
    expect(store.activeProjectId).toBe(60)
    expect(store.managementPending).toBe(false)
    expect(store.catalogComplete).toBe(true)
  })

  it('reconciles an applied move whose response was lost, without rollback or stuck pending state', async () => {
    const a = PAGE.items[0]
    const b = { ...a, id: 2, name: 'Beta', order_index: 2 }
    const store = useProjectsStore()
    globalThis.fetch = vi.fn(async (input) => {
      if (String(input).includes('/operations/')) throw new TypeError('connection lost')
      return json({ items: [{ ...b, order_index: 0 }, a], next_cursor: null, total_estimate: 2 })
    })
    store.items = [a, b]
    store.catalogComplete = true
    store.activeProjectId = 1
    expect(await store.moveProject({ itemKey: '2', anchorKey: '1', placement: 'before' })).toBe(false)
    expect(store.items.map((p) => p.id)).toEqual([2, 1])
    expect(store.error).toContain('connection lost')
    expect(store.activeProjectId).toBe(1)
    expect(store.managementPending).toBe(false)
    expect(store.loading).toBe(false)
  })

  it('rejects no-op, missing, inactive and concurrent move intents without a write', async () => {
    const store = useProjectsStore()
    store.items = [PAGE.items[0], { ...PAGE.items[0], id: 2, order_index: 2, is_active: false }]
    store.catalogComplete = true
    globalThis.fetch = vi.fn()
    for (const move of [
      { itemKey: '1', anchorKey: '1', placement: 'before' as const },
      { itemKey: '1', anchorKey: '999', placement: 'after' as const },
      { itemKey: '1', anchorKey: '2', placement: 'after' as const },
    ]) expect(await store.moveProject(move)).toBe(false)
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('releases failed reconciliation, retains the displayed catalog and blocks moves until a successful reload', async () => {
    const store = useProjectsStore()
    store.items = [PAGE.items[0], { ...PAGE.items[0], id: 2, order_index: 2 }]
    store.catalogComplete = true
    store.activeProjectId = 1
    globalThis.fetch = vi.fn(async (input) => {
      throw new Error(String(input).includes('/operations/') ? 'stale anchor' : 'read unavailable')
    })
    expect(await store.moveProject({ itemKey: '2', anchorKey: '1', placement: 'before' })).toBe(false)
    expect(store.error).toContain('stale anchor')
    expect(store.error).toContain('read unavailable')
    expect(store.items.map((p) => p.id)).toEqual([1, 2])
    expect(store.catalogComplete).toBe(false)
    expect(store.managementPending).toBe(false)
    expect(store.loading).toBe(false)
    expect(store.activeProjectId).toBe(1)
    expect(await store.moveProject({ itemKey: '2', anchorKey: '1', placement: 'before' })).toBe(false)
    expect(fetch).toHaveBeenCalledTimes(2)
    globalThis.fetch = vi.fn(async () => json(PAGE))
    expect(await store.loadAll()).toBe(true)
    expect(store.catalogComplete).toBe(true)
    expect(store.error).toBeNull()
  })

  it('allows one in-flight move and preserves a newer user selection when its late result arrives', async () => {
    const store = useProjectsStore()
    const rows = [PAGE.items[0], { ...PAGE.items[0], id: 2, order_index: 2 }]
    store.items = rows
    store.catalogComplete = true
    store.activeProjectId = 1
    let finish!: (response: Response) => void
    globalThis.fetch = vi.fn((input) => String(input).includes('/operations/')
      ? new Promise<Response>((resolve) => { finish = resolve })
      : Promise.resolve(json({ items: [{ ...rows[1], order_index: 0 }, rows[0]], next_cursor: null, total_estimate: 2 })))
    const first = store.moveProject({ itemKey: '2', anchorKey: '1', placement: 'before' })
    expect(store.managementPending).toBe(true)
    expect(await store.moveProject({ itemKey: '2', anchorKey: '1', placement: 'before' })).toBe(false)
    store.setActiveProjectId(2)
    finish(json({ data: rows[1] }))
    expect(await first).toBe(true)
    expect(store.activeProjectId).toBe(2)
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('deduplicates complete reads queued behind a page read and never publishes a failed partial catalog', async () => {
    const store = useProjectsStore()
    let firstPage!: (response: Response) => void
    let requests = 0
    globalThis.fetch = vi.fn(async (input) => {
      requests++
      if (requests === 1) return new Promise<Response>((resolve) => { firstPage = resolve })
      if (String(input).includes('after=')) throw new Error('page unavailable')
      return json({ ...PAGE, items: [{ ...PAGE.items[0], name: 'Partial name' }], next_cursor: 1 })
    })
    const initial = store.refresh()
    const first = store.loadAll()
    const second = store.loadAll()
    firstPage(json({ ...PAGE, next_cursor: 1 }))
    await initial
    expect(await first).toBe(false)
    expect(await second).toBe(false)
    expect(fetch).toHaveBeenCalledTimes(3)
    expect(store.items[0].name).toBe('Alpha')
    expect(store.catalogComplete).toBe(false)
    expect(store.loading).toBe(false)
  })

  it('renames through the bounded operation and updates shared labels without changing identity/selection', async () => {
    const store = useProjectsStore()
    store.items = PAGE.items
    store.activeProjectId = 1
    const renamed = { ...PAGE.items[0], name: 'Alpha Studio' }
    globalThis.fetch = vi.fn(async () => json({ data: renamed }))
    await store.renameProject(1, '  Alpha Studio  ')
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual({
      arguments: { response_mode: 'raw', project_id: 1, patch: { name: 'Alpha Studio' }, scope: 'catalog' },
    })
    expect(store.activeProject).toEqual(renamed)
    expect(store.getById(1)?.slug).toBe('alpha')
    expect(store.managementPending).toBe(false)
    for (const name of ['', '   ', 'x'.repeat(201)]) await expect(store.renameProject(1, name)).rejects.toThrow()
    await store.renameProject(1, 'Alpha Studio')
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('allows duplicate display names and exactly 200 Unicode characters', async () => {
    const store = useProjectsStore()
    const name = '🦊'.repeat(200)
    store.items = [PAGE.items[0], { ...PAGE.items[0], id: 2, name, order_index: 2 }]
    globalThis.fetch = vi.fn(async () => json({ data: { ...PAGE.items[0], name } }))
    await store.renameProject(1, name)
    expect(store.items.map((p) => p.name)).toEqual([name, name])
    await expect(store.renameProject(1, `${name}x`)).rejects.toThrow('200')
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('confirms an uncertain rename only when the authoritative target has the requested name', async () => {
    const store = useProjectsStore()
    store.items = PAGE.items
    store.activeProjectId = 1
    const renamed = { ...PAGE.items[0], name: 'Saved on server' }
    globalThis.fetch = vi.fn(async (input) => {
      if (String(input).includes('/operations/')) throw new TypeError('response lost')
      return json({ items: [renamed], next_cursor: null, total_estimate: 1 })
    })
    await expect(store.renameProject(1, renamed.name)).resolves.toEqual(renamed)
    expect(store.activeProject).toEqual(renamed)
    expect(store.managementPending).toBe(false)
  })

  it('appends new projects and retains persisted order/selection while loading unseen pages', async () => {
    const row = (id: number, order_index: number) => ({ ...PAGE.items[0], id, order_index })
    const first = Array.from({ length: 50 }, (_, i) => row(i + 1, i + 1))
    const unseen = Array.from({ length: 25 }, (_, i) => row(i + 51, i + 51))
    const created = row(76, 76)
    const responses = [
      { items: first, next_cursor: 50, total_estimate: 75 },
      { data: created, run_id: null, project_id: 76 },
      { items: [...unseen, created], next_cursor: null, total_estimate: 76 },
    ]
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(responses.shift()), {
      headers: { 'content-type': 'application/json' },
    }))
    const store = useProjectsStore()
    await store.refresh()
    await store.createProject({ slug: 'new', name: 'New', domain: 'new.test', locale: 'en-US' })
    expect(store.totalEstimate).toBe(76)
    expect(store.items.map((p) => p.id)).toEqual([...first.map((p) => p.id), 76])
    await store.loadMore()
    expect(store.items.map((p) => p.id)).toEqual(Array.from({ length: 76 }, (_, i) => i + 1))
    expect(store.activeProjectId).toBe(76)
    expect(store.activeProject?.id).toBe(76)
  })
})

function json(value: unknown): Response {
  return new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } })
}
