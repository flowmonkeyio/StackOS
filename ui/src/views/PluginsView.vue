<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'

import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import { UiBadge, UiButton, UiCallout, UiPageShell, UiPanel, UiSectionHeader } from '@/components/ui'
import { useStackOsCatalogStore } from '@/stores/plugins'

const route = useRoute()
const catalogStore = useStackOsCatalogStore()
const { plugins, loading, error } = storeToRefs(catalogStore)

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const operationsHref = computed(() => `/projects/${projectId.value}/operations`)

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  await catalogStore.refresh(projectId.value)
}

onMounted(load)
watch(projectId, load)
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Plugins"
      description="Installed StackOS plugins, catalog objects, and project enablement."
      :breadcrumbs="[{ label: 'Plugins' }]"
    />

    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <p
      v-if="loading && plugins.length === 0"
      class="rounded-md border border-subtle bg-bg-surface-alt px-4 py-5 text-sm text-fg-muted"
    >
      Loading plugins.
    </p>

    <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      <UiPanel
        v-for="plugin in plugins"
        :key="plugin.slug"
        class="p-4"
      >
        <UiSectionHeader
          :title="plugin.name"
          :description="plugin.description"
          as="h3"
        >
          <template #actions>
            <UiBadge tone="accent">{{ plugin.slug }}</UiBadge>
            <UiBadge
              :tone="plugin.enabled_for_project === false ? 'neutral' : 'success'"
              :dot="plugin.enabled_for_project !== false"
            >
              {{ plugin.enabled_for_project === false ? 'available' : 'enabled' }}
            </UiBadge>
          </template>
        </UiSectionHeader>

        <dl class="grid gap-2 text-sm sm:grid-cols-2">
          <div>
            <dt class="text-xs text-fg-muted">Version</dt>
            <dd>{{ plugin.version }}</dd>
          </div>
          <div>
            <dt class="text-xs text-fg-muted">Source</dt>
            <dd>{{ plugin.source }}</dd>
          </div>
          <div>
            <dt class="text-xs text-fg-muted">Capabilities</dt>
            <dd>{{ catalogStore.capabilitiesFor(plugin.slug).length }}</dd>
          </div>
          <div>
            <dt class="text-xs text-fg-muted">Actions</dt>
            <dd>{{ catalogStore.actionsFor(plugin.slug).length }}</dd>
          </div>
        </dl>
      </UiPanel>
    </div>

    <UiPanel class="p-4">
      <UiSectionHeader
        title="Action Contracts"
        description="Full provider actions are inspected in Operations so the plugin catalog stays scan-friendly."
        as="h3"
      >
        <template #actions>
          <UiBadge>{{ catalogStore.actions.length }}</UiBadge>
          <UiButton size="sm" :href="operationsHref">Open Operations</UiButton>
        </template>
      </UiSectionHeader>
      <p class="text-sm text-fg-muted">
        {{ catalogStore.actions.length }} action contracts are available across
        {{ plugins.length }} installed plugins. Use Operations for schema detail, credential
        readiness, and entrypoint visibility.
      </p>
    </UiPanel>
  </UiPageShell>
</template>
