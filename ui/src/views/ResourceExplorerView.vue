<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import ArtifactRenderer from '@/components/renderers/ArtifactRenderer.vue'
import ResourceViewRenderer from '@/components/renderers/ResourceViewRenderer.vue'
import { UiBadge, UiCallout, UiFormField, UiPageShell, UiPanel, UiSectionHeader, UiSelect } from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import type { SchemaResourceOut, SchemaResourceRecordOut } from '@/api'
import { formatDateTime } from '@/lib/stackos/json'
import { useStackOsCatalogStore } from '@/stores/plugins'
import { useStackOsResourcesStore } from '@/stores/stackosResources'

const route = useRoute()
const catalogStore = useStackOsCatalogStore()
const resourcesStore = useStackOsResourcesStore()
const { enabledPlugins } = storeToRefs(catalogStore)
const { resources, records, artifacts, loading, error } = storeToRefs(resourcesStore)

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const pluginSlug = ref(String(route.query.plugin_slug ?? ''))
const resourceKey = ref(String(route.query.resource_key ?? ''))
const selectedRecord = ref<SchemaResourceRecordOut | null>(null)

const pluginOptions = computed(() => [
  { value: '', label: 'All plugins' },
  ...enabledPlugins.value.map((plugin) => ({ value: plugin.slug, label: plugin.name })),
])

const resourceOptions = computed(() => [
  { value: '', label: 'All resources' },
  ...resources.value.map((resource) => ({ value: resource.key, label: resource.name })),
])

const resourceColumns: DataTableColumn<SchemaResourceOut>[] = [
  { key: 'plugin_slug', label: 'Plugin' },
  { key: 'key', label: 'Key' },
  { key: 'name', label: 'Name' },
  { key: 'description', label: 'Description' },
]

const recordColumns: DataTableColumn<SchemaResourceRecordOut>[] = [
  { key: 'plugin_slug', label: 'Plugin' },
  { key: 'resource_key', label: 'Resource' },
  { key: 'title', label: 'Title', format: (value) => String(value ?? '-') },
  { key: 'updated_at', label: 'Updated', format: (value) => formatDateTime(String(value)) },
]

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  await Promise.all([
    catalogStore.refresh(projectId.value),
    resourcesStore.refresh(projectId.value, {
      pluginSlug: pluginSlug.value || null,
      resourceKey: resourceKey.value || null,
    }),
  ])
  selectedRecord.value = records.value[0] ?? null
}

function onPlugin(value: string | number | null): void {
  pluginSlug.value = String(value ?? '')
  resourceKey.value = ''
  void load()
}

function onResource(value: string | number | null): void {
  resourceKey.value = String(value ?? '')
  void load()
}

onMounted(load)
watch(projectId, load)
watch(records, (items) => {
  if (!selectedRecord.value || !items.some((record) => record.id === selectedRecord.value?.id)) {
    selectedRecord.value = items[0] ?? null
  }
})
watch(
  () => route.query,
  () => {
    pluginSlug.value = String(route.query.plugin_slug ?? '')
    resourceKey.value = String(route.query.resource_key ?? '')
    void load()
  },
)
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Resource Explorer"
      description="Plugin resource schemas, project records, and artifact references."
      :breadcrumbs="[{ label: 'Resources' }]"
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
        <UiFormField label="Resource">
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

    <div class="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(26rem,38rem)] xl:items-start">
      <div class="space-y-4">
        <UiPanel class="p-4">
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
            max-height="18rem"
            aria-label="Resource schemas"
            empty-message="No resource schemas."
          >
            <template #cell:plugin_slug="{ value }">
              <UiBadge tone="accent">{{ value }}</UiBadge>
            </template>
          </DataTable>
        </UiPanel>

        <UiPanel class="p-4">
          <UiSectionHeader
            title="Records"
            as="h3"
          >
            <template #actions>
              <UiBadge>{{ records.length }}</UiBadge>
            </template>
          </UiSectionHeader>
          <DataTable
            :items="records"
            :columns="recordColumns"
            :loading="loading"
            :selected-id="selectedRecord?.id"
            max-height="calc(100vh - 31rem)"
            aria-label="Resource records"
            empty-message="No resource records."
            interactive
            @row-click="(row) => (selectedRecord = row)"
          >
            <template #cell:plugin_slug="{ value }">
              <UiBadge tone="accent">{{ value }}</UiBadge>
            </template>
          </DataTable>
        </UiPanel>
      </div>

      <div class="space-y-4 xl:sticky xl:top-4 xl:max-h-[calc(100vh-2rem)] xl:overflow-y-auto">
        <section v-if="selectedRecord" class="space-y-3">
          <UiSectionHeader
            title="Record Details"
            :description="`Selected ${selectedRecord.resource_key} #${selectedRecord.id}`"
          >
            <template #actions>
              <UiBadge tone="accent">{{ selectedRecord.plugin_slug }}</UiBadge>
              <UiBadge>{{ selectedRecord.resource_key }}</UiBadge>
            </template>
          </UiSectionHeader>
          <ResourceViewRenderer :record="selectedRecord" />
        </section>

        <section v-if="artifacts.length > 0" class="space-y-3">
          <UiSectionHeader title="Artifacts">
            <template #actions>
              <UiBadge>{{ artifacts.length }}</UiBadge>
            </template>
          </UiSectionHeader>
          <div class="grid gap-3">
            <ArtifactRenderer
              v-for="artifact in artifacts"
              :key="artifact.id"
              :artifact="artifact"
            />
          </div>
        </section>
      </div>
    </div>
  </UiPageShell>
</template>
