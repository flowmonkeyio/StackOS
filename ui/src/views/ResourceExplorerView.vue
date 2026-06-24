<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import InspectableDetailDrawer from '@/components/InspectableDetailDrawer.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import ArtifactRenderer from '@/components/renderers/ArtifactRenderer.vue'
import ResourceViewRenderer from '@/components/renderers/ResourceViewRenderer.vue'
import {
  UiBadge,
  UiCallout,
  UiFormField,
  UiJsonBlock,
  UiPageShell,
  UiPanel,
  UiSectionHeader,
  UiSelect,
} from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import type { SchemaResourceOut, SchemaResourceRecordOut } from '@/api'
import { formatDateTime, sanitizeForDisplay } from '@/lib/stackos/json'
import { useStackOsCatalogStore } from '@/stores/plugins'
import { newestFirst } from '@/lib/stackos/time'
import { useStackOsResourcesStore } from '@/stores/stackosResources'

const route = useRoute()
const router = useRouter()
const catalogStore = useStackOsCatalogStore()
const resourcesStore = useStackOsResourcesStore()
const { enabledPlugins } = storeToRefs(catalogStore)
const { resources, records, artifacts, loading, error } = storeToRefs(resourcesStore)

// The API returns rows ascending; lead with the latest record activity.
const recordsNewest = computed(() =>
  newestFirst(records.value, (record) => record.updated_at ?? record.created_at),
)

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const pluginSlug = ref(String(route.query.plugin_slug ?? ''))
const resourceKey = ref(String(route.query.resource_key ?? ''))
const selectedResource = ref<SchemaResourceOut | null>(null)
const selectedRecord = ref<SchemaResourceRecordOut | null>(null)
const detailOpen = ref(false)
const detailKind = ref<'resource' | 'record'>('resource')

const selectedPlugin = computed(
  () => enabledPlugins.value.find((plugin) => plugin.slug === pluginSlug.value) ?? null,
)
const pageTitle = computed(() =>
  selectedPlugin.value ? `${selectedPlugin.value.name} data` : 'Data explorer',
)
const pageDescription = computed(() =>
  selectedPlugin.value
    ? `Schemas, records, and artifacts owned by ${selectedPlugin.value.name}.`
    : 'Plugin data schemas, project records, and artifact references.',
)
const breadcrumbLabel = computed(() => (selectedPlugin.value ? 'Data' : 'Data explorer'))
const selectedSchemaJson = computed(() =>
  selectedResource.value ? sanitizeForDisplay(selectedResource.value.schema_json ?? null) : null,
)
const selectedUiSchemaJson = computed(() =>
  selectedResource.value ? sanitizeForDisplay(selectedResource.value.ui_schema_json ?? null) : null,
)
const selectedConfigJson = computed(() =>
  selectedResource.value ? sanitizeForDisplay(selectedResource.value.config_json ?? null) : null,
)
const detailTitle = computed(() => {
  if (detailKind.value === 'record' && selectedRecord.value) {
    return selectedRecord.value.title || selectedRecord.value.external_id || `Record #${selectedRecord.value.id}`
  }
  return selectedResource.value?.name ?? 'Resource'
})
const detailDescription = computed(() => {
  if (detailKind.value === 'record' && selectedRecord.value) {
    return `Selected ${selectedRecord.value.resource_key} #${selectedRecord.value.id}`
  }
  return selectedResource.value?.description ?? undefined
})

const pluginOptions = computed(() => [
  { value: '', label: 'All plugins' },
  ...enabledPlugins.value.map((plugin) => ({ value: plugin.slug, label: plugin.name })),
])

const resourceOptions = computed(() => [
  { value: '', label: 'All schemas' },
  ...resources.value.map((resource) => ({ value: resource.key, label: resource.name })),
])

const resourceColumns: DataTableColumn<SchemaResourceOut>[] = [
  { key: 'plugin_slug', label: 'Plugin' },
  { key: 'key', label: 'Key', cellClass: 'font-mono text-xs' },
  { key: 'name', label: 'Name', cellClass: 'font-medium text-fg-strong' },
  { key: 'description', label: 'Description' },
]

const recordColumns: DataTableColumn<SchemaResourceRecordOut>[] = [
  { key: 'plugin_slug', label: 'Plugin' },
  { key: 'resource_key', label: 'Resource', cellClass: 'font-mono text-xs' },
  { key: 'title', label: 'Title', cellClass: 'font-medium text-fg-strong', format: (value) => String(value ?? '-') },
  { key: 'updated_at', label: 'Updated', format: (value) => formatDateTime(String(value)) },
]

function parseProjectId(raw: unknown): number {
  return Number.parseInt(String(Array.isArray(raw) ? raw[0] : raw), 10)
}

function chooseResource(nextResourceKey = resourceKey.value): SchemaResourceOut | null {
  if (selectedResource.value && resources.value.some((resource) => resource.id === selectedResource.value?.id)) {
    return selectedResource.value
  }
  return resources.value.find((resource) => resource.key === nextResourceKey) ?? resources.value[0] ?? null
}

function chooseRecord(): SchemaResourceRecordOut | null {
  if (selectedRecord.value && records.value.some((record) => record.id === selectedRecord.value?.id)) {
    return selectedRecord.value
  }
  return records.value[0] ?? null
}

async function loadFor(
  nextProjectId = projectId.value,
  nextPluginSlug = pluginSlug.value,
  nextResourceKey = resourceKey.value,
): Promise<void> {
  if (!nextProjectId || Number.isNaN(nextProjectId)) return
  pluginSlug.value = nextPluginSlug
  resourceKey.value = nextResourceKey
  await Promise.all([
    catalogStore.refresh(nextProjectId),
    resourcesStore.refresh(nextProjectId, {
      pluginSlug: nextPluginSlug || null,
      resourceKey: nextResourceKey || null,
    }),
  ])
  selectedResource.value = chooseResource(nextResourceKey)
  selectedRecord.value = chooseRecord()
  if (detailOpen.value) {
    if (detailKind.value === 'resource' && !selectedResource.value) detailOpen.value = false
    if (detailKind.value === 'record' && !selectedRecord.value) detailOpen.value = false
  }
}

function onPlugin(value: string | number | null): void {
  const nextPluginSlug = String(value ?? '')
  void router.replace({
    query: {
      ...route.query,
      plugin_slug: nextPluginSlug || undefined,
      resource_key: undefined,
    },
  })
}

function onResource(value: string | number | null): void {
  const nextResourceKey = String(value ?? '')
  void router.replace({
    query: {
      ...route.query,
      plugin_slug: pluginSlug.value || undefined,
      resource_key: nextResourceKey || undefined,
    },
  })
}

function openResource(row: SchemaResourceOut): void {
  selectedResource.value = row
  detailKind.value = 'resource'
  detailOpen.value = true
}

function openRecord(row: SchemaResourceRecordOut): void {
  selectedRecord.value = row
  detailKind.value = 'record'
  detailOpen.value = true
}

onMounted(() => loadFor())
onBeforeRouteUpdate((to) => {
  void loadFor(
    parseProjectId(to.params.id),
    String(to.query.plugin_slug ?? ''),
    String(to.query.resource_key ?? ''),
  )
})
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      :title="pageTitle"
      :description="pageDescription"
      :breadcrumbs="[{ label: breadcrumbLabel }]"
    />

    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <UiPanel class="p-4">
      <div class="grid gap-3 md:grid-cols-2 lg:grid-cols-[260px_260px_1fr]">
        <UiFormField label="Plugin">
          <UiSelect
            :model-value="pluginSlug"
            :options="pluginOptions"
            @update:model-value="onPlugin"
          />
        </UiFormField>
        <UiFormField label="Schema">
          <UiSelect
            :model-value="resourceKey"
            :options="resourceOptions"
            @update:model-value="onResource"
          />
        </UiFormField>
        <div class="flex items-end gap-2">
          <UiBadge>{{ resources.length }} schemas</UiBadge>
          <UiBadge>{{ records.length }} records</UiBadge>
          <UiBadge>{{ artifacts.length }} artifacts</UiBadge>
        </div>
      </div>
    </UiPanel>

    <section aria-label="Resource schemas">
      <UiSectionHeader
        title="Schemas"
        as="h3"
      >
        <template #actions>
          <UiBadge>{{ resources.length }}</UiBadge>
        </template>
      </UiSectionHeader>
      <DataTable
        :items="resources"
        :columns="resourceColumns"
        :loading="loading"
        :selected-id="detailKind === 'resource' ? selectedResource?.id : null"
        aria-label="Resource schemas"
        empty-message="No resource schemas — plugins declare schemas when they are enabled for the project."
        interactive
        @row-click="openResource"
      >
        <template #cell:plugin_slug="{ value }">
          <UiBadge tone="accent">{{ value }}</UiBadge>
        </template>
      </DataTable>
    </section>

    <section aria-label="Resource records">
      <UiSectionHeader
        title="Records"
        as="h3"
      >
        <template #actions>
          <UiBadge>{{ records.length }}</UiBadge>
        </template>
      </UiSectionHeader>
      <DataTable
        :items="recordsNewest"
        :columns="recordColumns"
        :loading="loading"
        :selected-id="detailKind === 'record' ? selectedRecord?.id : null"
        aria-label="Resource records"
        empty-message="No records yet — agents and triggers write records through resource operations."
        interactive
        @row-click="openRecord"
      >
        <template #cell:plugin_slug="{ value }">
          <UiBadge tone="accent">{{ value }}</UiBadge>
        </template>
      </DataTable>
    </section>

    <section
      v-if="artifacts.length > 0"
      class="space-y-3"
      aria-label="Artifacts"
    >
      <UiSectionHeader
        title="Artifacts"
        as="h3"
      >
        <template #actions>
          <UiBadge>{{ artifacts.length }}</UiBadge>
        </template>
      </UiSectionHeader>
      <div class="grid gap-3 xl:grid-cols-2">
        <ArtifactRenderer
          v-for="artifact in artifacts"
          :key="artifact.id"
          :artifact="artifact"
        />
      </div>
    </section>

    <InspectableDetailDrawer
      v-model="detailOpen"
      :title="detailTitle"
      :description="detailDescription"
      size="xl"
      :has-detail="detailKind === 'resource' ? Boolean(selectedResource) : Boolean(selectedRecord)"
      empty-title="No resource selected"
      empty-description="Select a schema or record row to inspect its details."
    >
      <div
        v-if="detailKind === 'resource' && selectedResource"
        class="space-y-4"
      >
        <UiSectionHeader
          title="Schema details"
          :description="selectedResource.description"
        >
          <template #actions>
            <UiBadge tone="accent">{{ selectedResource.plugin_slug }}</UiBadge>
            <UiBadge>{{ selectedResource.key }}</UiBadge>
          </template>
        </UiSectionHeader>

        <dl class="grid gap-3 text-sm md:grid-cols-2">
          <div class="min-w-0">
            <dt class="text-xs text-fg-muted">Name</dt>
            <dd class="truncate font-medium text-fg-strong">{{ selectedResource.name }}</dd>
          </div>
          <div class="min-w-0">
            <dt class="text-xs text-fg-muted">Key</dt>
            <dd class="truncate font-mono text-xs">{{ selectedResource.key }}</dd>
          </div>
        </dl>

        <details class="rounded-md border border-subtle bg-bg-surface">
          <summary class="cursor-pointer px-3 py-2 text-sm font-medium text-fg-default focus-ring">
            Schema JSON
          </summary>
          <div class="border-t border-subtle p-3">
            <UiJsonBlock
              :data="selectedSchemaJson"
              density="compact"
              max-height="16rem"
              wrap
            />
          </div>
        </details>

        <details class="rounded-md border border-subtle bg-bg-surface">
          <summary class="cursor-pointer px-3 py-2 text-sm font-medium text-fg-default focus-ring">
            UI schema JSON
          </summary>
          <div class="border-t border-subtle p-3">
            <UiJsonBlock
              v-if="selectedUiSchemaJson !== null"
              :data="selectedUiSchemaJson"
              density="compact"
              max-height="14rem"
              wrap
            />
            <p
              v-else
              class="text-sm text-fg-muted"
            >
              No UI schema configured for this resource.
            </p>
          </div>
        </details>

        <details class="rounded-md border border-subtle bg-bg-surface">
          <summary class="cursor-pointer px-3 py-2 text-sm font-medium text-fg-default focus-ring">
            Config JSON
          </summary>
          <div class="border-t border-subtle p-3">
            <UiJsonBlock
              v-if="selectedConfigJson !== null"
              :data="selectedConfigJson"
              density="compact"
              max-height="14rem"
              wrap
            />
            <p
              v-else
              class="text-sm text-fg-muted"
            >
              No resource config configured for this resource.
            </p>
          </div>
        </details>
      </div>

      <ResourceViewRenderer
        v-else-if="detailKind === 'record' && selectedRecord"
        :record="selectedRecord"
      />
    </InspectableDetailDrawer>
  </UiPageShell>
</template>
