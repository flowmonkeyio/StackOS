<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiCard,
  UiCountBadge,
  UiPageShell,
  UiSectionHeader,
} from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import type { SchemaCapabilityOut, SchemaProviderOut } from '@/api'
import { useStackOsCatalogStore } from '@/stores/plugins'

const route = useRoute()
const catalogStore = useStackOsCatalogStore()
const { capabilities, providers, actions, loading, error } = storeToRefs(catalogStore)

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const operationsHref = computed(() => `/projects/${projectId.value}/operations`)

const capabilityColumns: DataTableColumn<SchemaCapabilityOut>[] = [
  { key: 'plugin_slug', label: 'Plugin', widthClass: 'w-32' },
  { key: 'key', label: 'Key' },
  { key: 'kind', label: 'Kind', widthClass: 'w-28' },
  { key: 'description', label: 'Description' },
]

const providerColumns: DataTableColumn<SchemaProviderOut>[] = [
  { key: 'plugin_slug', label: 'Plugin', widthClass: 'w-32' },
  { key: 'key', label: 'Provider' },
  { key: 'auth_type', label: 'Auth', widthClass: 'w-28' },
  { key: 'description', label: 'Description' },
]

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  await catalogStore.refresh(projectId.value)
}

onMounted(load)
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Capabilities"
      description="Capabilities, providers, and actions available to connected agents."
      :breadcrumbs="[{ label: 'Capabilities' }]"
    >
      <template #actions>
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="refresh"
          :loading="loading"
          @click="load"
        >
          Refresh
        </UiButton>
      </template>
    </ProjectPageHeader>

    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <section aria-label="Capabilities">
      <DataTable
        :items="capabilities"
        :columns="capabilityColumns"
        :loading="loading"
        aria-label="Capabilities"
        empty-message="No capabilities."
      >
        <template #cell:plugin_slug="{ value }">
          <UiBadge variant="outline">
            {{ value }}
          </UiBadge>
        </template>
      </DataTable>
    </section>

    <section aria-label="Providers">
      <UiSectionHeader
        title="Providers"
        as="h2"
      >
        <template #actions>
          <UiCountBadge :value="providers.length" />
        </template>
      </UiSectionHeader>
      <DataTable
        :items="providers"
        :columns="providerColumns"
        :loading="loading"
        aria-label="Providers"
        empty-message="No providers."
      >
        <template #cell:plugin_slug="{ value }">
          <UiBadge variant="outline">
            {{ value }}
          </UiBadge>
        </template>
      </DataTable>
    </section>

    <UiCard section>
      <UiSectionHeader
        title="Action contracts"
        description="Detailed input/output schemas live in Operations to keep this catalog page readable."
        as="h2"
      >
        <template #actions>
          <UiCountBadge :value="actions.length" />
          <UiButton
            variant="secondary"
            size="sm"
            :href="operationsHref"
          >
            Open Operations
          </UiButton>
        </template>
      </UiSectionHeader>
      <p class="text-sm text-fg-muted">
        {{ actions.length }} actions are registered. Operations provides the compact registry,
        surface policy, schemas, and connector readiness in one place.
      </p>
    </UiCard>
  </UiPageShell>
</template>
