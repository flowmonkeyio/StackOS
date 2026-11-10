<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import DataTable from '@/components/DataTable.vue'
import InspectableDetailDrawer from '@/components/InspectableDetailDrawer.vue'
import ArtifactRenderer from '@/components/renderers/ArtifactRenderer.vue'
import SourceDetailRenderer from '@/components/renderers/SourceDetailRenderer.vue'
import type { DataTableColumn } from '@/components/types'
import { UiBadge, UiButton, UiCallout, UiInput, UiSectionHeader, UiSelect } from '@/components/ui'
import { toLibrarySource, type LibrarySource } from '@/lib/librarySources'
import { useStackOsResourcesStore } from '@/stores/stackosResources'
import { filterSources, humanLabel, libraryLocation, refreshLoadedPages, sourceType, sourceUpdated } from '../library/viewModel'

const props = defineProps<{ projectId: number; pluginSlug: string; kind: 'saved' | 'files'; refreshKey: number }>()
const resources = useStackOsResourcesStore()
const search = ref('')
const type = ref('')
const selectedId = ref('')
const refreshing = ref(false)
const list = ref<HTMLElement | null>(null)
const detail = ref<HTMLElement | null>(null)
let reading: { identity: string; listScroll: number; detailScroll: number; detailsOpen: boolean } | null = null
let generation = 0
let retryRefresh = false
let targetCount = 0
const title = computed(() => props.kind === 'saved' ? 'Saved information' : 'Files')
const page = computed(() => props.kind === 'saved' ? resources.recordPage : resources.artifactPage)
const sources = computed<LibrarySource[]>(() => resources.currentProjectId !== props.projectId ? [] : props.kind === 'saved'
  ? resources.records.filter(row => row.project_id === props.projectId && row.plugin_slug === props.pluginSlug).map(row => toLibrarySource('resource_record', row))
  : resources.artifacts.filter(row => row.project_id === props.projectId && row.plugin_slug === props.pluginSlug).map(row => toLibrarySource('artifact', row)))
const schemas = computed(() => resources.currentProjectId === props.projectId ? resources.resources.filter(schema => schema.plugin_slug === props.pluginSlug) : [])
function typeLabel(source: LibrarySource) {
  const schema = source.source_kind === 'resource_record' ? schemas.value.find(schema => schema.id === source.payload.resource_id && schema.key === source.resource_key) : null
  return schema ? humanLabel(schema.name) : humanLabel(sourceType(source))
}
const types = computed(() => {
  const values = new Map(schemas.value.map(schema => [schema.key, humanLabel(schema.name)]))
  for (const source of sources.value) values.set(sourceType(source), typeLabel(source))
  if (type.value && !values.has(type.value)) values.set(type.value, humanLabel(type.value))
  return [{ value: '', label: 'All types' }, ...[...values].map(([value, label]) => ({ value, label }))]
})
const visible = computed(() => filterSources(sources.value, { plugin: props.pluginSlug, type: type.value, search: search.value })
  .sort((a, b) => (sourceUpdated(b) ?? '').localeCompare(sourceUpdated(a) ?? '') || a.identity.localeCompare(b.identity)))
const selected = computed(() => sources.value.find(source => source.identity === selectedId.value) ?? null)
const selectedSchema = computed(() => {
  const source = selected.value
  return source?.source_kind === 'resource_record' ? schemas.value.find(schema => schema.id === source.payload.resource_id) : null
})
const detailOpen = computed({ get: () => Boolean(selectedId.value), set: value => { if (!value) selectedId.value = '' } })
type Row = { id: string; name: string; type: string; updated: string }
const rows = computed<Row[]>(() => visible.value.map(source => ({ id: source.identity, name: source.title, type: typeLabel(source), updated: sourceUpdated(source) ? new Date(sourceUpdated(source)!).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—' })))
const columns: DataTableColumn<Row>[] = [{ key: 'name', label: 'Name', widthClass: 'w-[52%]' }, { key: 'type', label: 'Type' }, { key: 'updated', label: 'Updated' }]
const loading = computed(() => refreshing.value || page.value.loading)
const emptyMessage = computed(() => page.value.error ? 'Content could not be read. Retry this section.' : search.value || type.value ? 'No matches in the loaded list.' : `No ${title.value.toLowerCase()} saved for this domain.`)

function reader() {
  const filters = { pluginSlug: props.pluginSlug }
  return {
    page: page.value, count: () => sources.value.length,
    refresh: () => props.kind === 'saved' ? resources.refreshRecords(props.projectId, filters) : resources.refreshArtifacts(props.projectId, filters),
    more: () => props.kind === 'saved' ? resources.loadMoreRecords() : resources.loadMoreArtifacts(),
  }
}
async function refresh() {
  const current = ++generation
  if (!reading || reading.identity !== selectedId.value) reading = {
    identity: selectedId.value,
    listScroll: list.value?.querySelector('table')?.parentElement?.scrollTop ?? 0,
    detailScroll: detail.value?.closest('.ui-sidepanel__body')?.scrollTop ?? 0,
    detailsOpen: detail.value?.querySelector('details')?.open ?? false,
  }
  targetCount = Math.max(targetCount, sources.value.length)
  refreshing.value = true
  retryRefresh = true
  await Promise.all([
    ...(props.kind === 'saved' ? [resources.refreshSchemas(props.projectId, { pluginSlug: props.pluginSlug })] : []),
    refreshLoadedPages(reader(), () => current === generation, targetCount),
  ])
  if (current !== generation) return
  refreshing.value = false
  if (!page.value.error) {
    retryRefresh = false
    targetCount = sources.value.length
    if (selectedId.value && !selected.value) selectedId.value = ''
  }
  await nextTick()
  const viewport = list.value?.querySelector('table')?.parentElement
  if (viewport) viewport.scrollTop = reading.listScroll
  if (reading.identity === selectedId.value) {
    const disclosure = detail.value?.querySelector('details')
    if (disclosure) disclosure.open = reading.detailsOpen
    const body = detail.value?.closest('.ui-sidepanel__body')
    if (body) body.scrollTop = reading.detailScroll
  }
  if (!page.value.error) reading = null
}
async function readMore() { retryRefresh = false; await reader().more() }
watch(() => props.refreshKey, () => { void refresh() }, { immediate: true })
watch([search, type], () => { if (!visible.value.some(source => source.identity === selectedId.value)) selectedId.value = '' })
onBeforeUnmount(() => { generation++ })
</script>

<template>
  <section
    class="domain-content"
    :aria-label="title"
  >
    <UiSectionHeader
      :title="title"
      as="h2"
    >
      <template #actions>
        <RouterLink
          class="focus-ring rounded-sm text-sm text-accent-primary underline"
          :to="libraryLocation(projectId, { tab: kind, plugin: pluginSlug, type })"
        >
          Open in Library<span class="sr-only">: {{ title }}</span>
        </RouterLink>
      </template>
    </UiSectionHeader>
    <div
      v-if="kind === 'saved'"
      class="domain-filters"
    >
      <UiInput
        v-model="search"
        type="search"
        placeholder="Search this list"
        aria-label="Search this list"
        clearable
      />
      <UiSelect
        v-model="type"
        :options="types"
        aria-label="Type"
      />
    </div>
    <UiCallout
      v-if="kind === 'saved' && resources.schemaError"
      tone="warning"
    >
      Field descriptions unavailable. Saved content remains readable in Details. <UiButton
        size="sm"
        @click="resources.refreshSchemas(projectId, { pluginSlug })"
      >
        Retry field descriptions
      </UiButton>
    </UiCallout>
    <div
      ref="list"
      class="domain-table"
    >
      <DataTable
        :items="rows"
        :columns="columns"
        :loading="loading"
        :empty-message="emptyMessage"
        :aria-label="title"
        :selected-id="selectedId"
        max-height="24rem"
        interactive
        @row-click="selectedId = $event.id"
      >
        <template #cell:name="{ row }">
          <button
            type="button"
            :data-source="row.id"
            :title="row.name"
            class="focus-ring block w-full truncate rounded-sm text-left"
            @click.stop="selectedId = row.id"
            @keydown.enter.stop="selectedId = row.id"
            @keydown.space.prevent.stop="selectedId = row.id"
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
      </DataTable>
    </div>
    <div
      v-if="page.next_cursor !== null || page.error || search || type"
      class="flex flex-wrap items-center justify-between gap-2 text-xs text-fg-muted"
      aria-live="polite"
    >
      <span>{{ rows.length }} shown · {{ sources.length }} loaded. {{ page.next_cursor !== null ? 'More available.' : page.loaded && !page.error ? 'All pages loaded.' : '' }} Search and filters apply to loaded items.</span>
      <UiButton
        v-if="page.error"
        size="sm"
        :loading="loading"
        @click="retryRefresh ? refresh() : readMore()"
      >
        Retry {{ title }}
      </UiButton>
      <UiButton
        v-else-if="page.next_cursor !== null"
        size="sm"
        :loading="loading"
        @click="readMore"
      >
        Load more {{ title }}
      </UiButton>
      <p
        v-if="page.error"
        role="alert"
        class="w-full break-words text-danger-fg"
      >
        {{ page.error }}
      </p>
    </div>
    <InspectableDetailDrawer
      v-model="detailOpen"
      :title="selected?.title ?? 'Selected content unavailable'"
      :has-detail="Boolean(selected)"
      size="xl"
    >
      <div ref="detail">
        <ArtifactRenderer
          v-if="selected?.source_kind === 'artifact'"
          :key="selected.identity"
          :artifact="selected.payload"
          embedded
        />
        <SourceDetailRenderer
          v-else-if="selected"
          :key="selected.identity"
          :source="selected"
          :resource="selectedSchema"
          reading
        />
      </div>
    </InspectableDetailDrawer>
  </section>
</template>

<style scoped>
.domain-content { display: grid; gap: 16px; min-width: 0; border-top: 1px solid var(--color-border-default); padding: 28px 0; }
.domain-content :deep(.ui-section-header) { margin-bottom: 0; }
.domain-filters { display: grid; grid-template-columns: minmax(0, 330px) minmax(0, 200px); gap: 12px; }
.domain-table { min-width: 0; }
.domain-table :deep(table) { width: 100%; table-layout: fixed; }
.domain-table :deep(td) { overflow: hidden; }
@media (max-width: 600px) { .domain-filters { grid-template-columns: minmax(0, 1fr); } }
</style>
