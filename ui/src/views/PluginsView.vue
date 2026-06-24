<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'

import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import { UiBadge, UiButton, UiCallout, UiCard, UiMedallion, UiPageShell, UiSectionHeader, UiSkeleton } from '@/components/ui'
import { pluginSectionIcon } from '@/lib/stackos/nav'
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

    <div
      v-if="loading && plugins.length === 0"
      class="grid gap-4 md:grid-cols-2 xl:grid-cols-3"
      aria-label="Loading plugins"
    >
      <UiCard
        v-for="n in 6"
        :key="n"
      >
        <UiSkeleton
          shape="line"
          :lines="3"
        />
      </UiCard>
    </div>

    <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      <UiCard
        v-for="plugin in plugins"
        :key="plugin.slug"
        section
        :aria-label="plugin.name"
        class="flex flex-col"
      >
        <template #header>
          <div class="flex min-w-0 items-center gap-3">
            <UiMedallion
              :icon="pluginSectionIcon(plugin.slug)"
              shape="square"
              tone="info"
            />
            <div class="min-w-0">
              <h3 class="truncate t-h3 text-fg-strong">
                {{ plugin.name }}
              </h3>
              <p class="truncate font-mono text-2xs text-fg-subtle">
                {{ plugin.slug }}
              </p>
            </div>
          </div>
          <UiBadge
            :tone="plugin.enabled_for_project === false ? 'neutral' : 'success'"
            :dot="plugin.enabled_for_project !== false"
          >
            {{ plugin.enabled_for_project === false ? 'Available' : 'Enabled' }}
          </UiBadge>
        </template>

        <div class="flex flex-1 flex-col">
          <p class="line-clamp-3 text-sm text-fg-muted">
            {{ plugin.description }}
          </p>
        </div>
        <dl class="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-subtle pt-3 text-xs text-fg-muted">
          <div class="flex items-baseline gap-1.5">
            <dd class="font-medium tabular-nums text-fg-default">
              {{ catalogStore.capabilitiesFor(plugin.slug).length }}
            </dd>
            <dt>capabilities</dt>
          </div>
          <div class="flex items-baseline gap-1.5">
            <dd class="font-medium tabular-nums text-fg-default">
              {{ catalogStore.actionsFor(plugin.slug).length }}
            </dd>
            <dt>actions</dt>
          </div>
          <div class="ml-auto flex items-baseline gap-1.5">
            <dt class="sr-only">
              Version
            </dt>
            <dd class="font-mono text-2xs text-fg-subtle">
              v{{ plugin.version }} · {{ plugin.source }}
            </dd>
          </div>
        </dl>
      </UiCard>
    </div>

    <UiCard section>
      <UiSectionHeader
        title="Action contracts"
        description="Full provider actions are inspected in Operations so the plugin catalog stays scan-friendly."
        as="h3"
      >
        <template #actions>
          <UiBadge>{{ catalogStore.actions.length }}</UiBadge>
          <UiButton
            size="sm"
            :href="operationsHref"
          >
            Open Operations
          </UiButton>
        </template>
      </UiSectionHeader>
      <p class="text-sm text-fg-muted">
        {{ catalogStore.actions.length }} action contracts are available across
        {{ plugins.length }} installed plugins. Use Operations for schema detail, credential
        readiness, and entrypoint visibility.
      </p>
    </UiCard>
  </UiPageShell>
</template>
