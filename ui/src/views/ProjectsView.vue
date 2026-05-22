<script setup lang="ts">
// ProjectsView — read-only project inventory.

import { computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { UiEmptyState, UiPageHeader, UiPageShell } from '@/components/ui'
import { useProjectsStore } from '@/stores/projects'
import type { DataTableColumn } from '@/components/types'
import type { Project } from '@/stores/projects'

const router = useRouter()
const projects = useProjectsStore()
const { items, loading, nextCursor } = storeToRefs(projects)

const columns: DataTableColumn<Project>[] = [
  { key: 'name', label: 'Name', sortable: false },
  { key: 'slug', label: 'Slug', sortable: false, cellClass: 'font-mono text-xs' },
  { key: 'domain', label: 'Domain', sortable: false },
  { key: 'niche', label: 'Niche', sortable: false },
  { key: 'locale', label: 'Locale', sortable: false },
  { key: 'is_active', label: 'Status', sortable: false },
  {
    key: 'updated_at',
    label: 'Updated',
    sortable: false,
    format: (value) => formatDate(value as string),
  },
]

const empty = computed<boolean>(() => !loading.value && items.value.length === 0)

function formatDate(iso: string): string {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

function openDetail(row: Project): void {
  void router.push(`/projects/${row.id}/overview`)
}

async function loadMore(): Promise<void> {
  await projects.loadMore()
}

onMounted(async () => {
  if (items.value.length === 0) await projects.refresh()
})
</script>

<template>
  <UiPageShell>
    <UiPageHeader
      title="Projects"
      description="Configured workspaces for plugins, runs, resources, and project memory."
    />

    <UiEmptyState
      v-if="empty"
      title="No projects yet"
      description="Projects appear here after agent setup."
      size="lg"
    />

    <DataTable
      v-else
      :items="items"
      :columns="columns"
      :loading="loading"
      :next-cursor="nextCursor"
      aria-label="Projects"
      empty-message="No projects yet"
      interactive
      @row-click="openDetail"
      @load-more="loadMore"
    >
      <template #cell:is_active="{ row }">
        <StatusBadge
          :status="(row as Project).is_active ? 'active' : 'inactive'"
          kind="project"
        />
      </template>
    </DataTable>
  </UiPageShell>
</template>
