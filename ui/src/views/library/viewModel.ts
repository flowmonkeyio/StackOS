import type { LocationQuery } from 'vue-router'
import type { LibrarySource } from '@/lib/librarySources'
import type { SourcePageState } from '@/lib/sourcePager'
import { sanitizeForDisplay } from '@/lib/stackos/json'

export interface LibraryQuery { tab: 'saved' | 'files'; plugin: string; type: string; item: string }
const first = (value: unknown) => String(Array.isArray(value) ? value[0] ?? '' : value ?? '')

export function parseLibraryQuery(query: LocationQuery): LibraryQuery {
  const item = first(query.item)
  return {
    tab: first(query.tab) === 'files' ? 'files' : 'saved',
    plugin: first(query.plugin), type: first(query.type),
    item: /^(resource_record|decision|learning|experiment|artifact):[1-9]\d*$/.test(item) ? item : '',
  }
}

/** Consumed by the final navigation owner; this feature does not register routes. */
export function libraryLocation(projectId: number, query: Partial<LibraryQuery> = {}) {
  return { path: `/projects/${projectId}/library`, query: Object.fromEntries(Object.entries(query).filter(([, value]) => value !== '')) }
}

export function sourceType(source: LibrarySource): string {
  return source.source_kind === 'resource_record' ? source.resource_key ?? 'resource_record'
    : source.source_kind === 'artifact' ? source.payload.kind : source.source_kind
}
export function humanLabel(value: string): string {
  return String(sanitizeForDisplay(value)).replace(/[_-]+/g, ' ').replace(/^./, x => x.toUpperCase())
}
export function sourceUpdated(source: LibrarySource): string | null {
  return ('updated_at' in source.payload ? source.payload.updated_at : null) ?? source.payload.created_at ?? null
}
export function filterSources(rows: LibrarySource[], filters: { search: string; plugin: string; type: string }) {
  const search = filters.search.trim().toLocaleLowerCase()
  return rows.filter(row => (!filters.plugin || row.plugin_slug === filters.plugin)
    && (!filters.type || sourceType(row) === filters.type)
    && (!search || [row.title, sourceType(row), row.plugin_slug ?? 'Project'].some(value => value.toLocaleLowerCase().includes(search))))
}

export interface LibraryReader {
  key?: string
  label?: string
  page: SourcePageState
  count: () => number
  refresh: () => Promise<void>
  more: () => Promise<void>
}

/** Restore reached depth through the canonical pager, without keeping a row cache. */
export async function refreshLoadedPages(reader: LibraryReader, isCurrent: () => boolean, targetCount = reader.count()) {
  await reader.refresh()
  while (isCurrent() && !reader.page.error && reader.page.next_cursor !== null && reader.count() < targetCount) {
    const cursor = reader.page.next_cursor
    await reader.more()
    if (reader.page.next_cursor === cursor) break
  }
}
