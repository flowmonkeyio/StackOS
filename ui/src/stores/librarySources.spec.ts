// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useStackOsResourcesStore } from './stackosResources'
import { useProjectDataStore } from './projectData'
import { toLibrarySource } from '@/lib/librarySources'

const client = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/client', () => ({
  apiFetch: client.apiFetch,
  formatApiError: (e: unknown) => String(e),
}))
const page = (items: unknown[], next_cursor: number | null = null, total_estimate = items.length) =>
  ({ items, next_cursor, total_estimate })
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}

describe('shared Library sources', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    client.apiFetch.mockReset().mockImplementation(async (url: string) =>
      url.startsWith('/api/v1/resources?') ? [] : page([]))
  })

  it('retains same-title identities, exact payload and canonical read refs across callers', () => {
    const resource = { id: 1, project_id: 1, plugin_slug: 'engineering', resource_key: 'engineering-decision', title: 'Same', data_json: { decision: 'old unlinked decision' } }
    const inputs = [
      ['resource_record', resource],
      ['decision', { id: 1, project_id: 1, title: 'Same', decision: 'core' }],
      ['resource_record', { ...resource, id: 2, resource_key: 'engineering-evidence' }],
      ['artifact', { id: 1, project_id: 1, name: 'Same', uri: 'missing://file' }],
    ] as const
    const rows = inputs.map(([kind, row]) => toLibrarySource(kind, row as never))
    expect(new Set(rows.map((r) => r.identity)).size).toBe(4)
    expect(rows[0]!.payload).toBe(resource)
    expect(rows[0]!.read_ref).toBe('/api/v1/resource-records/1')
    expect(rows[0]).toEqual(toLibrarySource('resource_record', resource as never))
    expect(toLibrarySource('learning', { id: 4, project_id: 1, statement: 'Historical' } as never).source_kind).toBe('learning')
    expect(toLibrarySource('experiment', { id: 5, project_id: 1, hypothesis: 'Meaningful hypothesis' } as never).source_kind).toBe('experiment')
  })

  it('pages beyond 50 per source without title dedup or competing artifact fetches', async () => {
    client.apiFetch.mockImplementation(async (url: string) => {
      if (url.startsWith('/api/v1/resources?')) return []
      return url.includes('after=50')
        ? page([{ id: 51, title: 'Same' }], null, 51)
        : page(Array.from({ length: 50 }, (_, i) => ({ id: i + 1, title: 'Same' })), 50, 51)
    })
    const resources = useStackOsResourcesStore()
    const data = useProjectDataStore()
    await resources.refresh(1)
    await Promise.all((['decisions', 'learnings', 'experiments'] as const)
      .map((kind) => data.refreshSource(1, kind)))
    // Both consumers read the canonical artifact owner; an already loaded scope is reused.
    expect(client.apiFetch.mock.calls.filter(([url]) => url.includes('/artifacts?'))).toHaveLength(1)
    await Promise.all([
      resources.loadMoreRecords(), resources.loadMoreArtifacts(),
      data.loadMore('decisions'), data.loadMore('learnings'), data.loadMore('experiments'),
    ])
    expect(resources.records).toHaveLength(51)
    expect(data.artifacts).toEqual(resources.artifacts)
    expect(data.artifacts).toHaveLength(51)
    expect(data.decisions).toHaveLength(51)
    expect(data.learnings).toHaveLength(51)
    expect(data.experiments).toHaveLength(51)
    expect(resources.artifactPage.next_cursor).toBeNull()
    expect(resources.recordPage.total_estimate).toBe(51)
    expect(data.pages.decisions.next_cursor).toBeNull()
    expect(client.apiFetch.mock.calls.some(([url]) => url.includes('include_inactive=true'))).toBe(true)
  })

  it('keeps successful sources and the failed cursor on partial failure, then retries only that read', async () => {
    const resources = useStackOsResourcesStore()
    client.apiFetch.mockImplementation(async (url: string) => {
      if (url.startsWith('/api/v1/resources?')) return []
      if (url.includes('/artifacts?')) throw new Error('artifact unavailable')
      return page([{ id: 1 }], 1, 2)
    })
    await resources.refresh(1)
    expect(resources.records).toHaveLength(1)
    expect(resources.artifactPage.error).toContain('artifact unavailable')
    expect(resources.artifactPage.loaded).toBe(false)
    client.apiFetch.mockResolvedValue(page([{ id: 2 }]))
    const before = client.apiFetch.mock.calls.length
    await resources.refreshArtifacts(1)
    expect(client.apiFetch.mock.calls.length - before).toBe(1)
    expect(resources.records).toHaveLength(1)
    expect(resources.artifactPage.error).toBeNull()
  })

  it('rejects late pages after plugin/project changes and clears incompatible rows immediately', async () => {
    const resources = useStackOsResourcesStore()
    client.apiFetch.mockResolvedValue(page([{ id: 1 }], 1, 2))
    await resources.refreshArtifacts(1, { pluginSlug: 'engineering' })
    const late = deferred<unknown>()
    client.apiFetch.mockReturnValueOnce(late.promise)
    const oldPage = resources.loadMoreArtifacts()
    client.apiFetch.mockResolvedValue(page([{ id: 2 }]))
    const next = resources.refreshArtifacts(2, { pluginSlug: 'seo' })
    expect(resources.artifacts).toEqual([])
    await next
    late.resolve(page([{ id: 99 }]))
    await oldPage
    expect(resources.artifacts.map((r) => r.id)).toEqual([2])
    expect(resources.artifactPage.loading).toBe(false)
  })

  it('invalidates same-project filter races and preserves loaded items on later-page failure', async () => {
    const resources = useStackOsResourcesStore()
    const slow = deferred<unknown>()
    client.apiFetch.mockReturnValueOnce(slow.promise)
    const old = resources.refreshRecords(1, { resourceKey: 'old' })
    client.apiFetch.mockResolvedValue(page([{ id: 3 }], 3, 4))
    await resources.refreshRecords(1, { resourceKey: 'new' })
    slow.resolve(page([{ id: 99 }]))
    await old
    client.apiFetch.mockRejectedValueOnce(new Error('late page failed'))
    await resources.loadMoreRecords()
    expect(resources.records.map((r) => r.id)).toEqual([3])
    expect(resources.recordPage.next_cursor).toBe(3)
    expect(resources.recordPage.error).toContain('late page failed')
    client.apiFetch.mockResolvedValueOnce(page([{ id: 4 }], null, 4))
    await resources.loadMoreRecords()
    expect(resources.records.map((r) => r.id)).toEqual([3, 4])
  })

  it('retains compatible shared artifacts while loading and never exposes another project artifact scope', async () => {
    const resources = useStackOsResourcesStore()
    const data = useProjectDataStore()
    client.apiFetch.mockResolvedValue(page([{ id: 1 }]))
    await data.refreshSource(1, 'decisions')
    await resources.refreshArtifacts(1)
    const slow = deferred<unknown>()
    client.apiFetch.mockImplementation((url: string) => url.includes('/artifacts?') ? slow.promise : Promise.resolve(page([])))
    const refresh = resources.refreshArtifacts(1)
    expect(data.artifacts.map((r) => r.id)).toEqual([1])
    slow.resolve(page([{ id: 2 }]))
    await refresh
    expect(data.artifacts.map((r) => r.id)).toEqual([2])
    client.apiFetch.mockResolvedValue(page([{ id: 3 }]))
    await resources.refreshArtifacts(2)
    expect(data.artifacts).toEqual([])
    expect(data.currentProjectId).toBe(1)
  })

  it('does not surface artifact errors from another project after an independent core read', async () => {
    client.apiFetch.mockImplementation(async (url: string) => {
      if (url.includes('/projects/1/artifacts?')) throw new Error('Project 1 artifact unavailable')
      return page([{ id: 2 }])
    })
    const data = useProjectDataStore()
    const resources = useStackOsResourcesStore()
    await data.refreshSource(1, 'decisions')
    await resources.refreshArtifacts(1)
    expect(data.error).toContain('Project 1 artifact unavailable')
    await data.refreshSource(2, 'decisions')
    expect(data.currentProjectId).toBe(2)
    expect(data.decisions.map((row) => row.id)).toEqual([2])
    expect(data.artifacts).toEqual([])
    expect(data.error).toBeNull()
    expect(resources.currentProjectId).toBe(1)
    expect(resources.artifactPage.error).toContain('Project 1 artifact unavailable')
  })

  it('ignores another project artifact loading state without resetting its in-flight read', async () => {
    const late = deferred<unknown>()
    const data = useProjectDataStore()
    const resources = useStackOsResourcesStore()
    await data.refreshSource(1, 'decisions')
    client.apiFetch.mockReturnValueOnce(late.promise)
    const read = resources.refreshArtifacts(1)
    expect(data.loading).toBe(true)
    await data.refreshSource(2, 'decisions')
    expect(data.loading).toBe(false)
    expect(resources.artifactPage.loading).toBe(true)
    expect(resources.currentProjectId).toBe(1)
    late.resolve(page([{ id: 1 }]))
    await read
    expect(data.loading).toBe(false)
    expect(data.artifacts).toEqual([])
    expect(resources.artifacts.map((row) => row.id)).toEqual([1])
  })
})
