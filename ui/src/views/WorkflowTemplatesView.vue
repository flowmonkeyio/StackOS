<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import TemplateRenderer from '@/components/renderers/TemplateRenderer.vue'
import { UiBadge, UiCallout, UiPageShell, UiPanel, UiSectionHeader } from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import type { SchemaWorkflowTemplateSummaryOut } from '@/api'
import { useWorkflowTemplatesStore } from '@/stores/workflowTemplates'

type TemplateRow = SchemaWorkflowTemplateSummaryOut & { id: string }

const route = useRoute()
const templatesStore = useWorkflowTemplatesStore()
const { items, selected, loading, error } = storeToRefs(templatesStore)

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const rows = computed<TemplateRow[]>(() =>
  items.value.map((item) => ({ ...item, id: `${item.source}:${item.key}:${item.version}` })),
)

const columns: DataTableColumn<TemplateRow>[] = [
  { key: 'key', label: 'Key' },
  { key: 'name', label: 'Name' },
  { key: 'source', label: 'Source', widthClass: 'w-28' },
  { key: 'plugin_slug', label: 'Plugin', widthClass: 'w-28', format: (value) => String(value ?? '-') },
  { key: 'version', label: 'Version', widthClass: 'w-24' },
]

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  templatesStore.reset()
  await templatesStore.refresh(projectId.value)
}

async function selectTemplate(row: TemplateRow): Promise<void> {
  await templatesStore.describe(projectId.value, row.key, row.plugin_slug)
}

onMounted(load)
watch(projectId, load)
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Workflow Templates"
      description="Reusable workflow configuration and instruction contracts."
      :breadcrumbs="[{ label: 'Workflow Templates' }]"
    />

    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <div class="grid gap-4 xl:grid-cols-[420px_minmax(0,1fr)]">
      <UiPanel class="p-4">
        <UiSectionHeader
          title="Templates"
          as="h3"
        >
          <template #actions>
            <UiBadge>{{ rows.length }}</UiBadge>
          </template>
        </UiSectionHeader>
        <DataTable
          :items="rows"
          :columns="columns"
          :loading="loading"
          aria-label="Workflow templates"
          empty-message="No workflow templates."
          @row-click="selectTemplate"
        >
          <template #cell:source="{ value }">
            <UiBadge tone="accent">{{ value }}</UiBadge>
          </template>
        </DataTable>
      </UiPanel>

      <TemplateRenderer
        v-if="selected"
        :template="selected"
      />
      <UiPanel
        v-else
        class="p-4"
      >
        <UiSectionHeader
          title="Template"
          as="h3"
        />
        <p class="text-sm text-fg-muted">No template selected.</p>
      </UiPanel>
    </div>
  </UiPageShell>
</template>
