<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import { UiBadge, UiCallout, UiJsonBlock, UiPageShell, UiPanel, UiSectionHeader } from '@/components/ui'
import type { SchemaCredentialConnectionOut } from '@/api'
import type { DataTableColumn } from '@/components/types'
import { sanitizeForDisplay } from '@/lib/stackos/json'
import { useStackOsCatalogStore } from '@/stores/plugins'

type ConnectionRow = SchemaCredentialConnectionOut & { id: string }

const route = useRoute()
const catalogStore = useStackOsCatalogStore()
const { authProviders, authStatus, loading, error } = storeToRefs(catalogStore)

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const connections = computed<ConnectionRow[]>(() =>
  (authStatus.value?.connections ?? []).map((connection) => ({
    ...connection,
    id: connection.credential_ref,
  })),
)

const columns: DataTableColumn<ConnectionRow>[] = [
  { key: 'provider_key', label: 'Provider' },
  { key: 'status', label: 'Status', widthClass: 'w-32' },
  { key: 'auth_type', label: 'Auth', widthClass: 'w-28' },
  { key: 'credential_ref', label: 'Credential ref' },
  { key: 'expires_at', label: 'Expires', format: (value) => String(value ?? '-') },
]

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  await catalogStore.refreshAuth(projectId.value)
}

onMounted(load)
watch(projectId, load)
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Connections"
      description="Sanitized provider auth state and opaque credential references."
      :breadcrumbs="[{ label: 'Connections' }]"
    />

    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <UiPanel class="p-4">
      <UiSectionHeader
        title="Credential Refs"
        as="h3"
      >
        <template #actions>
          <UiBadge>{{ connections.length }}</UiBadge>
        </template>
      </UiSectionHeader>
      <DataTable
        :items="connections"
        :columns="columns"
        :loading="loading"
        aria-label="Connections"
        empty-message="No credentials connected."
      >
        <template #cell:provider_key="{ value }">
          <UiBadge tone="accent">{{ value }}</UiBadge>
        </template>
        <template #cell:status="{ value, row }">
          <UiBadge :tone="(row as ConnectionRow).setup_required ? 'warning' : 'success'">
            {{ value }}
          </UiBadge>
        </template>
      </DataTable>
    </UiPanel>

    <UiPanel class="p-4">
      <UiSectionHeader
        title="Providers"
        as="h3"
      >
        <template #actions>
          <UiBadge>{{ authProviders.length }}</UiBadge>
        </template>
      </UiSectionHeader>
      <ul class="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
        <li
          v-for="provider in authProviders"
          :key="provider.key"
          class="rounded-md border border-subtle bg-bg-surface p-3"
        >
          <div class="mb-1 flex items-center justify-between gap-2">
            <span class="font-medium">{{ provider.name }}</span>
            <UiBadge>{{ provider.auth_type }}</UiBadge>
          </div>
          <p class="text-sm text-fg-muted">{{ provider.key }}</p>
        </li>
      </ul>
    </UiPanel>

    <details
      v-if="authStatus"
      class="rounded-md border border-default bg-bg-surface shadow-xs"
    >
      <summary class="cursor-pointer px-4 py-3 text-sm font-semibold text-fg-strong focus-ring">
        Diagnostics
      </summary>
      <div class="border-t border-subtle p-3">
        <UiJsonBlock
          :data="sanitizeForDisplay(authStatus)"
          density="compact"
          max-height="18rem"
          wrap
        />
      </div>
    </details>
  </UiPageShell>
</template>
