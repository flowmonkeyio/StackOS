<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DataTable from '@/components/DataTable.vue'
import TabBar from '@/components/TabBar.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import ArtifactRenderer from '@/components/renderers/ArtifactRenderer.vue'
import SourceDetailRenderer from '@/components/renderers/SourceDetailRenderer.vue'
import { UiBadge, UiButton, UiCallout, UiDescriptionList, UiIcon, UiIconButton, UiInput, UiPageShell, UiSelect } from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { toLibrarySource, type LibrarySource } from '@/lib/librarySources'
import { formatDateTime } from '@/lib/stackos/json'
import { useStackOsResourcesStore } from '@/stores/stackosResources'
import { useProjectDataStore } from '@/stores/projectData'
import { filterSources, humanLabel, libraryLocation, parseLibraryQuery, refreshLoadedPages, sourceType, sourceUpdated, type LibraryQuery, type LibraryReader } from './library/viewModel'

const route = useRoute()
const router = useRouter()
const { projectId, isValid } = useProjectRouteScope(route)
const resources = useStackOsResourcesStore()
const data = useProjectDataStore()
const query = ref(parseLibraryQuery(route.query))
const search = ref('')
const refreshing = ref(false)
const list = ref<HTMLElement | null>(null)
const root = ref<HTMLElement | null>(null)
let generation = 0
const lastRead = new Map<string, 'refresh' | 'more'>()
type ReadingState = { item: string; listScroll: number; detailScroll: number; detailsOpen: boolean }
// Only recovery metadata lives here; canonical readers retain all rows and cursors.
const recoveries = new Map<string, { targetCount: number; reading: ReadingState }>()
let recoveryScope = ''

function applyQuery(next: LibraryQuery) {
  const filtersChanged = next.plugin !== query.value.plugin || next.type !== query.value.type
  query.value = next
  synchronizeScope()
  if (filtersChanged && query.value.item && !selected.value) query.value = { ...query.value, item: '' }
}
function updateQuery(patch: Partial<LibraryQuery>) {
  applyQuery({ ...query.value, ...patch })
  void router.replace(libraryLocation(projectId.value, query.value))
}
function setSearch(value: string | number | null) {
  search.value = String(value ?? '')
  if (query.value.item && !selected.value) updateQuery({ item: '' })
}
function setTab(tab: string) {
  search.value = ''
  updateQuery({ tab: tab === 'files' ? 'files' : 'saved', type: '', item: '' })
}

const schemas = computed(() => query.value.tab === 'saved' && resources.currentProjectId === projectId.value ? resources.resources : [])
const sources = computed<LibrarySource[]>(() => {
  if (!isValid.value) return []
  if (query.value.tab === 'files') return resources.currentProjectId === projectId.value
    ? resources.artifacts.filter(row => row.project_id === projectId.value).map(row => toLibrarySource('artifact', row)) : []
  return [
    ...(resources.currentProjectId === projectId.value ? resources.records.filter(row => row.project_id === projectId.value).map(row => toLibrarySource('resource_record', row)) : []),
    ...(data.currentProjectId === projectId.value ? [
      ...data.decisions.map(row => toLibrarySource('decision', row)),
      ...data.learnings.map(row => toLibrarySource('learning', row)),
      ...data.experiments.map(row => toLibrarySource('experiment', row)),
    ].filter(row => row.project_id === projectId.value) : []),
  ]
})
const visible = computed(() => filterSources(sources.value, { ...query.value, search: search.value })
  .sort((a, b) => (sourceUpdated(b) ?? '').localeCompare(sourceUpdated(a) ?? '') || a.identity.localeCompare(b.identity)))
const selected = computed(() => visible.value.find(row => row.identity === query.value.item) ?? null)
const selectedSchema = computed(() => {
  const source = selected.value
  return source?.source_kind === 'resource_record'
    ? schemas.value.find(schema => schema.id === source.payload.resource_id) : null
})
const selectedId = computed(() => selected.value?.identity ?? null)
function typeLabel(source: LibrarySource) {
  if (source.source_kind === 'resource_record') {
    const schema = schemas.value.find(schema => schema.id === source.payload.resource_id && schema.plugin_slug === source.plugin_slug && schema.key === source.resource_key)
    return schema ? humanLabel(schema.name) : humanLabel(sourceType(source))
  }
  return humanLabel(sourceType(source))
}
const plugins = computed(() => [
  { value: '', label: 'All plugins' },
  ...[...new Set([...schemas.value.map(row => row.plugin_slug), ...sources.value.map(row => row.plugin_slug), query.value.plugin].filter((value): value is string => Boolean(value)))].sort().map(value => ({ value, label: humanLabel(value) })),
])
const types = computed(() => {
  const values = new Map<string, string>()
  if (query.value.tab === 'saved') {
    for (const key of ['decision', 'learning', 'experiment']) values.set(key, humanLabel(key))
    for (const schema of schemas.value) if (!query.value.plugin || schema.plugin_slug === query.value.plugin) values.set(schema.key, humanLabel(schema.name))
  }
  for (const row of sources.value) if (!query.value.plugin || row.plugin_slug === query.value.plugin) values.set(sourceType(row), typeLabel(row))
  if (query.value.type && !values.has(query.value.type)) values.set(query.value.type, humanLabel(query.value.type))
  return [{ value: '', label: 'All types' }, ...[...values].map(([value, label]) => ({ value, label }))]
})
type ListRow = { id: string; source: LibrarySource; name: string; type: string; plugin: string; updated: string }
const rows = computed<ListRow[]>(() => visible.value.map(source => ({ id: source.identity, source, name: source.title, type: typeLabel(source), plugin: source.plugin_slug ? humanLabel(source.plugin_slug) : 'Project', updated: sourceUpdated(source) ? new Date(sourceUpdated(source)!).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—' })))
const columns = computed<DataTableColumn<ListRow>[]>(() => [
  { key: 'name', label: 'Name', widthClass: 'w-[40%]' },
  { key: 'type', label: query.value.tab === 'files' ? 'Kind' : 'Type' },
  { key: 'plugin', label: query.value.tab === 'files' ? 'Source' : 'Plugin' },
  { key: 'updated', label: 'Updated' },
])

const readers = computed((): (LibraryReader & { key: string; label: string })[] => query.value.tab === 'files' ? [{
  key: 'artifacts', label: 'Files', page: resources.artifactPage, count: () => resources.artifacts.length,
  refresh: () => resources.refreshArtifacts(projectId.value), more: () => resources.loadMoreArtifacts(),
}] : [
  { key: 'records', label: 'Saved records', page: resources.recordPage, count: () => resources.records.length,
    refresh: () => resources.refreshRecords(projectId.value), more: () => resources.loadMoreRecords() },
  ...(['decisions', 'learnings', 'experiments'] as const).map(key => ({
    key, label: humanLabel(key), page: data.pages[key], count: () => data[key].length,
    refresh: () => data.refreshSource(projectId.value, key), more: () => data.loadMore(key),
  })),
])
const loading = computed(() => refreshing.value || readers.value.some(reader => reader.page.loading))
const failed = computed(() => readers.value.some(reader => reader.page.error))
const emptyMessage = computed(() => failed.value ? 'Some saved content could not be read. Retry the failed sources below.'
  : search.value || query.value.plugin || query.value.type ? 'No matches in the loaded list.'
    : query.value.tab === 'files' ? 'No files saved for this project.' : 'No saved information for this project.')

function listViewport() {
  const desktop = list.value?.querySelector('table')?.parentElement
  return desktop?.clientHeight ? desktop : list.value
}

function readingState(): ReadingState {
  return {
    item: query.value.item,
    listScroll: listViewport()?.scrollTop ?? 0,
    detailScroll: root.value?.querySelector('[data-library-detail]')?.scrollTop ?? 0,
    detailsOpen: root.value?.querySelector<HTMLDetailsElement>('[data-library-detail] details')?.open ?? false,
  }
}
async function restoreReading(reading: ReadingState, current: number) {
  await nextTick()
  if (current !== generation || reading.item !== query.value.item) return
  const viewport = listViewport()
  if (viewport) viewport.scrollTop = reading.listScroll
  const detail = root.value?.querySelector('[data-library-detail]')
  if (detail) {
    const disclosure = detail.querySelector('details')
    if (disclosure) disclosure.open = reading.detailsOpen
    detail.scrollTop = reading.detailScroll
  }
}

async function load(initial = false) {
  const current = ++generation
  const id = projectId.value
  const scope = `${id}:${query.value.tab}`
  if (recoveryScope !== scope) {
    recoveries.clear()
    lastRead.clear()
    recoveryScope = scope
  }
  if (!isValid.value) return
  const reading = [...recoveries.values()].find(recovery => recovery.reading.item === query.value.item)?.reading ?? readingState()
  refreshing.value = true
  const active = readers.value
  await Promise.all([
    ...(query.value.tab === 'saved' ? [resources.refreshSchemas(id)] : []),
    ...active.map(async reader => {
      // Another consumer may have loaded a filtered query. Establish full Library scope before reuse.
      const recovery = { targetCount: Math.max(reader.count(), recoveries.get(reader.key)?.targetCount ?? 0), reading }
      recoveries.set(reader.key, recovery)
      lastRead.set(reader.key, 'refresh')
      await refreshLoadedPages(reader, () => current === generation, recovery.targetCount)
      if (current === generation && !reader.page.error) recoveries.delete(reader.key)
    }),
  ])
  if (current !== generation) return
  refreshing.value = false
  if (!initial && query.value.item && !selected.value && !failed.value) updateQuery({ item: '' })
  await restoreReading(reading, current)
}
async function read(reader: LibraryReader & { key: string }, retry = false) {
  const action = retry ? lastRead.get(reader.key) ?? 'refresh' : 'more'
  lastRead.set(reader.key, action)
  const current = generation
  const recovery = recoveries.get(reader.key)
  await (action === 'more' ? reader.more() : refreshLoadedPages(reader, () => current === generation, recovery?.targetCount))
  if (current !== generation || reader.page.error || !recovery) return
  recoveries.delete(reader.key)
  await restoreReading(recovery.reading, current)
}
function synchronizeScope() {
  if (recoveryScope === `${projectId.value}:${query.value.tab}`) return
  search.value = ''
  void load(true)
}
const libraryRoute = route.matched[route.matched.length - 1]
const removeRouteListener = router.afterEach((to, _from, failure) => {
  if (failure) return
  if (!libraryRoute || !to.matched.includes(libraryRoute)) { generation++; return }
  const next = parseLibraryQuery(to.query)
  applyQuery(next)
  if (query.value.item !== next.item) void router.replace(libraryLocation(projectId.value, query.value))
})
synchronizeScope()
onBeforeUnmount(() => { generation++; removeRouteListener() })
function select(row: ListRow) { updateQuery({ item: row.id }) }
async function closeDetails() {
  const identity = query.value.item
  updateQuery({ item: '' })
  await nextTick()
  const targets = [...(root.value?.querySelectorAll<HTMLElement>(`[data-source="${identity}"]`) ?? [])]
  ;(targets.find(target => target.getClientRects().length > 0) ?? targets[0])?.focus()
}
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Library"
      description="Saved information and files for this project."
    >
      <template #actions>
        <UiButton
          icon-left="refresh"
          :loading="loading"
          @click="load(false)"
        >
          Refresh
        </UiButton>
      </template>
      <template #tabs>
        <TabBar
          :tabs="[{ key: 'saved', label: 'Saved information' }, { key: 'files', label: 'Files' }]"
          :active-key="query.tab"
          aria-label="Library"
          @change="setTab"
        />
      </template>
    </ProjectPageHeader>
    <div
      ref="root"
      class="library-layout"
      :class="{ 'library-layout--selected': query.item }"
    >
      <section
        :id="`cs-tabpanel-${query.tab}`"
        role="tabpanel"
        :aria-labelledby="`cs-tab-${query.tab}`"
        class="min-w-0 space-y-4"
      >
        <div class="library-filters">
          <UiInput
            :model-value="search"
            type="search"
            placeholder="Search this list"
            aria-label="Search this list"
            clearable
            @update:model-value="setSearch"
          >
            <template #prefix>
              <UiIcon
                name="search"
                class="h-4 w-4"
              />
            </template>
          </UiInput>
          <UiSelect
            :model-value="query.plugin"
            :options="plugins"
            aria-label="Plugin"
            @update:model-value="updateQuery({ plugin: String($event ?? '') })"
          />
          <UiSelect
            :model-value="query.type"
            :options="types"
            aria-label="Type"
            @update:model-value="updateQuery({ type: String($event ?? '') })"
          />
        </div>
        <UiCallout
          v-if="resources.schemaError && query.tab === 'saved'"
          tone="warning"
        >
          Field descriptions unavailable. Saved sources remain readable in Details.
          <UiButton
            size="sm"
            @click="resources.refreshSchemas(projectId)"
          >
            Retry field descriptions
          </UiButton>
        </UiCallout>
        <div
          ref="list"
          data-library-list
          class="library-list"
        >
          <DataTable
            :items="rows"
            :columns="columns"
            :loading="loading"
            :selected-id="selectedId"
            :empty-message="emptyMessage"
            max-height="calc(100dvh - 320px)"
            :aria-label="query.tab === 'files' ? 'Files' : 'Saved information'"
            interactive
            @row-click="select"
          >
            <template #cell:name="{ row }">
              <button
                type="button"
                :data-source="row.id"
                class="focus-ring block w-full min-w-0 truncate rounded-xs text-left"
                :title="row.name"
                @click.stop="select(row)"
                @keydown.enter.stop="select(row)"
                @keydown.space.prevent.stop="select(row)"
              >
                {{ row.name }}
              </button>
            </template>
            <template #cell:type="{ row }">
              <UiBadge
                class="max-w-full truncate"
                :title="row.type"
              >
                {{ row.type }}
              </UiBadge>
            </template>
            <template #cell:plugin="{ row }">
              <span
                class="block truncate"
                :title="row.plugin"
              >{{ row.plugin }}</span>
            </template>
            <template #cell:updated="{ row }">
              <span
                class="whitespace-nowrap text-xs text-fg-muted"
                :title="sourceUpdated(row.source) ? formatDateTime(sourceUpdated(row.source)!) : undefined"
              >{{ row.updated }}</span>
            </template>
          </DataTable>
        </div>
        <div
          class="space-y-2 text-xs text-fg-muted"
          aria-live="polite"
        >
          <p>{{ visible.length }} shown · {{ sources.length }} loaded. Search and filters apply to loaded items.</p>
          <div
            v-for="reader in readers"
            :key="reader.key"
            class="flex flex-wrap items-center justify-between gap-2 border-t border-subtle pt-2"
          >
            <span>{{ reader.label }}: {{ reader.count() }} loaded · {{ reader.page.loading ? 'Loading…' : reader.page.error ? 'Read failed' : !reader.page.loaded ? 'Not loaded' : reader.page.next_cursor !== null ? 'More available' : 'All pages loaded' }}</span>
            <UiButton
              v-if="reader.page.error"
              size="sm"
              :loading="reader.page.loading"
              @click="read(reader, true)"
            >
              Retry {{ reader.label }}
            </UiButton>
            <UiButton
              v-else-if="reader.page.next_cursor !== null"
              size="sm"
              :loading="reader.page.loading"
              @click="read(reader)"
            >
              Load more {{ reader.label }}
            </UiButton>
            <p
              v-if="reader.page.error"
              role="alert"
              class="w-full break-words text-danger-fg"
            >
              {{ reader.page.error }}
            </p>
          </div>
        </div>
      </section>
      <aside
        v-if="query.item"
        data-library-detail
        class="library-detail"
        aria-label="Selected saved content"
        @keydown.esc.stop="closeDetails"
      >
        <template v-if="selected?.source_kind === 'artifact'">
          <ArtifactRenderer
            :key="selected.identity"
            :artifact="selected.payload"
            embedded
          >
            <template #actions>
              <UiIconButton
                aria-label="Close details"
                @click="closeDetails"
              >
                <UiIcon
                  name="close"
                  class="h-4 w-4"
                />
              </UiIconButton>
            </template>
          </ArtifactRenderer>
        </template>
        <template v-else>
          <div class="mb-4 flex min-w-0 items-start justify-between gap-3">
            <h2
              class="min-w-0 flex-1 line-clamp-3 text-xl font-semibold leading-snug text-fg-strong break-words"
              :title="selected?.title"
            >
              {{ selected?.title ?? (loading ? 'Loading selected content…' : 'Selected content unavailable') }}
            </h2>
            <UiIconButton
              aria-label="Close details"
              @click="closeDetails"
            >
              <UiIcon
                name="close"
                class="h-4 w-4"
              />
            </UiIconButton>
          </div>
          <template v-if="selected">
            <UiBadge
              class="mb-4 max-w-full truncate"
              :title="typeLabel(selected)"
            >
              {{ typeLabel(selected) }}
            </UiBadge>
            <SourceDetailRenderer
              :source="selected"
              :resource="selectedSchema"
              reading
            >
              <template #facts>
                <UiDescriptionList
                  :items="[{ label: 'Plugin', value: selected.plugin_slug ? humanLabel(selected.plugin_slug) : 'Project' }, { label: 'Updated', value: sourceUpdated(selected) ? formatDateTime(sourceUpdated(selected)!) : '—' }]"
                />
              </template>
            </SourceDetailRenderer>
          </template>
          <p
            v-else
            class="text-sm text-fg-muted"
          >
            {{ loading ? 'Reading saved sources.' : 'This item is outside the loaded list or current filters. Load more items or clear the selection.' }}
          </p>
        </template>
      </aside>
    </div>
  </UiPageShell>
</template>

<style scoped>
.library-layout { display: grid; gap: 16px; min-width: 0; }
.library-layout--selected { grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr); }
.library-filters { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr) minmax(0, 1fr); gap: 12px; }
.library-list { min-width: 0; }
.library-list :deep(table) { table-layout: fixed; width: 100%; }
.library-list :deep(td) { overflow: hidden; }
.library-list :deep(tr[aria-current="true"] td:first-child) { box-shadow: inset 3px 0 var(--color-accent-primary); }
.library-detail { min-width: 0; min-height: calc(100dvh - 172px); max-height: calc(100dvh - 172px); overflow: auto; overflow-wrap: anywhere; padding: 16px; border: 1px solid var(--color-border-default); border-radius: 8px; background: var(--color-bg-surface); }
@media (max-width: 1100px) { .library-layout--selected { grid-template-columns: minmax(0, 1fr); } .library-detail { min-height: 0; max-height: none; } }
@media (max-width: 767px) { .library-list { max-height: 50dvh; overflow: auto; } }
@media (max-width: 600px) {
  .library-filters { grid-template-columns: minmax(0, 1fr); gap: 8px; }
  .library-detail :deep(.ui-description-list__item) { grid-template-columns: minmax(0, 1fr); gap: 4px; }
}
</style>
