<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { onBeforeRouteUpdate, useRoute } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import InspectableDetailDrawer from '@/components/InspectableDetailDrawer.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import TemplateRenderer from '@/components/renderers/TemplateRenderer.vue'
import {
  UiBadge,
  UiCallout,
  UiCountBadge,
  UiFilterBar,
  UiPageShell,
  UiSectionHeader,
} from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import type { SchemaWorkflowTemplateSummaryOut } from '@/api'
import { useWorkflowTemplatesStore } from '@/stores/workflowTemplates'

type TemplateRow = SchemaWorkflowTemplateSummaryOut & { id: string }

const route = useRoute()
const templatesStore = useWorkflowTemplatesStore()
const { items, selected, loading, error } = storeToRefs(templatesStore)
const detailOpen = ref(false)

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const pluginSlug = computed(() => String(route.query.plugin_slug ?? ''))
const search = ref('')
const rows = computed<TemplateRow[]>(() => {
  const all = items.value.map((item) => ({
    ...item,
    id: `${item.source}:${item.key}:${item.version}`,
  }))
  const query = search.value.trim().toLowerCase()
  if (!query) return all
  return all.filter((row) =>
    [row.key, row.name, row.plugin_slug ?? '']
      .join(' ')
      .toLowerCase()
      .includes(query),
  )
})
const selectedRowId = computed(() =>
  selected.value
    ? `${selected.value.summary.source}:${selected.value.summary.key}:${selected.value.summary.version}`
    : null,
)

const columns: DataTableColumn<TemplateRow>[] = [
  { key: 'key', label: 'Key', cellClass: 'font-mono text-xs' },
  { key: 'name', label: 'Name', cellClass: 'font-medium text-fg-strong' },
  { key: 'source', label: 'Source', widthClass: 'w-28' },
  { key: 'plugin_slug', label: 'Plugin', widthClass: 'w-28', format: (value) => String(value ?? '-') },
  { key: 'version', label: 'Version', widthClass: 'w-24', cellClass: 'font-mono text-xs' },
]

function parseProjectId(raw: unknown): number {
  return Number.parseInt(String(Array.isArray(raw) ? raw[0] : raw), 10)
}

async function loadFor(nextProjectId = projectId.value, nextPluginSlug = pluginSlug.value): Promise<void> {
  if (!nextProjectId || Number.isNaN(nextProjectId)) return
  templatesStore.reset()
  await templatesStore.refresh(nextProjectId, nextPluginSlug || null)
}

async function selectTemplate(row: TemplateRow): Promise<void> {
  await templatesStore.describe(projectId.value, row.key, row.plugin_slug)
  detailOpen.value = true
}

onMounted(() => loadFor())
onBeforeRouteUpdate((to) => {
  void loadFor(parseProjectId(to.params.id), String(to.query.plugin_slug ?? ''))
})
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Workflow templates"
      description="Reusable contracts available to connected agents through MCP."
      :breadcrumbs="[{ label: 'Workflow library' }]"
    />

    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <section
      aria-label="Workflow templates"
      class="space-y-3"
    >
      <UiSectionHeader
        title="Templates"
        as="h3"
      >
        <template #actions>
          <UiCountBadge :value="rows.length" />
        </template>
      </UiSectionHeader>
      <UiFilterBar
        v-model:search="search"
        search-placeholder="Filter by key, name, or plugin…"
        aria-label="Workflow template filters"
      />
      <DataTable
        :items="rows"
        :columns="columns"
        :loading="loading"
        :selected-id="selectedRowId"
        aria-label="Workflow templates"
        empty-message="No workflow templates — plugins ship templates when enabled."
        interactive
        @row-click="selectTemplate"
      >
        <template #cell:source="{ value }">
          <UiBadge variant="outline">
            {{ value }}
          </UiBadge>
        </template>
      </DataTable>
    </section>

    <InspectableDetailDrawer
      v-model="detailOpen"
      :title="selected?.spec.name ?? 'Template'"
      :description="selected?.spec.description"
      size="2xl"
      :has-detail="Boolean(selected)"
      empty-title="No template selected"
      empty-description="Select a workflow template row to inspect setup, guidance, and grants."
    >
      <template #header="{ titleId, descriptionId }">
        <div class="min-w-0">
          <p class="t-overline text-accent-primary">
            Workflow guide
          </p>
          <h2
            :id="titleId"
            class="t-h2 mt-1 text-fg-strong"
          >
            {{ selected?.spec.name ?? 'Template' }}
          </h2>
          <p
            :id="descriptionId"
            class="mt-1 max-w-3xl text-sm text-fg-muted"
          >
            See how the work moves, what the agent needs, where people approve, and what the project receives.
          </p>
        </div>
      </template>
      <TemplateRenderer
        v-if="selected"
        :template="selected"
      />
    </InspectableDetailDrawer>
  </UiPageShell>
</template>
