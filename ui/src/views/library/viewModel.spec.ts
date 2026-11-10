// @vitest-environment node
import { describe, expect, it, vi } from 'vitest'
import { toLibrarySource } from '@/lib/librarySources'
import { filterSources, libraryLocation, parseLibraryQuery, refreshLoadedPages, sourceType } from './viewModel'

const source = (kind: 'decision' | 'resource_record' | 'artifact', row: object) => toLibrarySource(kind, { id: 1, project_id: 1, title: 'Same', ...row } as never)

describe('Library view model', () => {
  it('keeps duplicate titles and mixed IDs, and filters by explicit ownership only', () => {
    const rows = [source('decision', { decision: 'Core' }), source('resource_record', { plugin_slug: 'engineering', resource_key: 'engineering-evidence' }), source('resource_record', { id: 2, plugin_slug: 'engineering', resource_key: 'engineering-decision' })]
    expect(filterSources(rows, { search: 'same', plugin: '', type: '' })).toHaveLength(3)
    expect(filterSources(rows, { search: '', plugin: 'engineering', type: '' }).map(x => x.identity)).toEqual(['resource_record:1', 'resource_record:2'])
    expect(filterSources(rows, { search: '', plugin: '', type: 'decision' }).map(x => x.identity)).toEqual(['decision:1'])
    expect(sourceType(rows[1]!)).toBe('engineering-evidence')
  })

  it('exports the final destination and accepts only typed source selection', () => {
    expect(libraryLocation(7, { tab: 'files', plugin: 'old plugin', type: 'image', item: 'artifact:52' })).toEqual({ path: '/projects/7/library', query: { tab: 'files', plugin: 'old plugin', type: 'image', item: 'artifact:52' } })
    expect(parseLibraryQuery({ tab: 'bad', item: 'context_snapshot:1', plugin: ['engineering'] })).toEqual({ tab: 'saved', item: '', plugin: 'engineering', type: '' })
  })

  it('rehydrates previously loaded pages on refresh, stopping on error or changed project', async () => {
    let count = 100
    const page = { next_cursor: 50 as number | null, loading: false, loaded: true, error: null as string | null, total_estimate: 100 }
    const refresh = vi.fn(async () => { count = 50 })
    const more = vi.fn(async () => { count = 100; page.next_cursor = null })
    await refreshLoadedPages({ page, count: () => count, refresh, more }, () => true)
    expect(more).toHaveBeenCalledOnce()
    count = 100
    page.next_cursor = 50
    refresh.mockImplementationOnce(async () => { count = 50; page.error = 'unavailable' })
    await refreshLoadedPages({ page, count: () => count, refresh, more }, () => true)
    expect(more).toHaveBeenCalledOnce()
    page.error = null
    await refreshLoadedPages({ page, count: () => count, refresh, more }, () => false)
    expect(more).toHaveBeenCalledOnce()
  })
})
