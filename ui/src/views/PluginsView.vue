<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { onBeforeRouteUpdate, useRoute } from 'vue-router'

import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiCard,
  UiEmptyState,
  UiFilterBar,
  UiPageShell,
  UiPanel,
  UiSectionHeader,
  UiSegmentedControl,
  UiSelect,
  UiSkeleton,
} from '@/components/ui'
import { useStackOsCatalogStore } from '@/stores/plugins'

import PluginDirectoryCard from './plugins/PluginDirectoryCard.vue'
import ProviderDirectoryCard from './plugins/ProviderDirectoryCard.vue'
import {
  buildPluginDirectory,
  buildProviderDirectory,
  filterAndSortPlugins,
  filterAndSortProviders,
  type CatalogSort,
} from './plugins/viewModel'

type CatalogView = 'plugins' | 'providers'

const route = useRoute()
const catalogStore = useStackOsCatalogStore()
const { plugins, providers, actions, capabilities, loading, error } = storeToRefs(catalogStore)

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const operationsHref = computed(() => `/projects/${projectId.value}/operations`)
const search = ref('')
const view = ref<CatalogView>('plugins')
const sort = ref<CatalogSort>('name')

const viewOptions = [
  { key: 'plugins', label: 'Plugins' },
  { key: 'providers', label: 'Providers' },
]
const sortOptions = [
  { value: 'name', label: 'Name' },
  { value: 'actions', label: 'Most actions' },
]

const pluginItems = computed(() =>
  buildPluginDirectory(plugins.value, providers.value, actions.value, capabilities.value),
)
const providerItems = computed(() => buildProviderDirectory(pluginItems.value))
const visiblePlugins = computed(() =>
  filterAndSortPlugins(pluginItems.value, search.value, sort.value),
)
const visibleProviders = computed(() =>
  filterAndSortProviders(providerItems.value, search.value, sort.value),
)
const resultCount = computed(() =>
  view.value === 'plugins' ? visiblePlugins.value.length : visibleProviders.value.length,
)

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  await catalogStore.refresh(projectId.value)
}

function setView(value: string | number): void {
  if (value === 'plugins' || value === 'providers') view.value = value
}

function setSort(value: string | number | null): void {
  if (value === 'name' || value === 'actions') sort.value = value
}

onMounted(load)
onBeforeRouteUpdate((to) => {
  const nextProjectId = Number.parseInt(String(to.params.id), 10)
  if (nextProjectId !== projectId.value) setTimeout(() => void load(), 0)
})
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Plugins"
      description="Capability packs and providers available to this project. Search by tool, action, or type of work."
      :breadcrumbs="[{ label: 'Plugins' }]"
    />

    <UiCallout v-if="error" tone="danger">
      {{ error }}
    </UiCallout>

    <UiPanel
      v-if="!loading && !error"
      class="grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
    >
      <div>
        <p class="t-overline text-fg-subtle">Project catalog</p>
        <p class="mt-1 text-sm text-fg-muted">
          Plugins organize the work. Providers connect the tools agents can use when a plan reaches
          an approved action.
        </p>
      </div>
      <dl class="flex flex-wrap gap-x-6 gap-y-2 text-xs text-fg-muted sm:justify-end">
        <div>
          <dd class="text-lg font-semibold tabular-nums text-fg-strong">{{ plugins.length }}</dd>
          <dt>plugins</dt>
        </div>
        <div>
          <dd class="text-lg font-semibold tabular-nums text-fg-strong">{{ providers.length }}</dd>
          <dt>providers</dt>
        </div>
        <div>
          <dd class="text-lg font-semibold tabular-nums text-fg-strong">{{ actions.length }}</dd>
          <dt>actions</dt>
        </div>
      </dl>
    </UiPanel>

    <UiFilterBar
      v-if="!loading && !error && plugins.length"
      v-model:search="search"
      search-placeholder="Search plugins, providers, capabilities, or actions…"
      aria-label="Plugin catalog filters"
    >
      <UiSegmentedControl
        :model-value="view"
        :options="viewOptions"
        label="Browse catalog by"
        @update:model-value="setView"
      />
      <template #right>
        <UiSelect
          :model-value="sort"
          :options="sortOptions"
          size="sm"
          class="w-36"
          aria-label="Sort catalog"
          @update:model-value="setSort"
        />
      </template>
    </UiFilterBar>

    <div
      v-if="loading"
      class="grid gap-4 md:grid-cols-2 xl:grid-cols-3"
      aria-label="Loading plugins"
    >
      <UiCard v-for="n in 6" :key="n">
        <UiSkeleton shape="line" :lines="4" />
      </UiCard>
    </div>

    <template v-else-if="!error">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <p class="text-xs text-fg-muted">
          <strong class="text-fg-strong">{{ resultCount }}</strong>
          {{ view === 'plugins' ? 'plugins' : 'providers' }} found
          <template v-if="search.trim()"> for “{{ search.trim() }}”</template>
        </p>
        <UiButton v-if="search.trim()" variant="link" size="sm" @click="search = ''">
          Clear search
        </UiButton>
      </div>

      <div
        v-if="view === 'plugins' && visiblePlugins.length"
        class="grid gap-4 md:grid-cols-2 xl:grid-cols-3"
      >
        <PluginDirectoryCard v-for="item in visiblePlugins" :key="item.plugin.slug" :item="item" />
      </div>

      <div
        v-else-if="view === 'providers' && visibleProviders.length"
        class="grid gap-4 md:grid-cols-2 xl:grid-cols-3"
      >
        <ProviderDirectoryCard
          v-for="item in visibleProviders"
          :key="`${item.provider.plugin_slug}.${item.provider.key}`"
          :item="item"
        />
      </div>

      <UiEmptyState
        v-else
        :title="search.trim() ? 'No matching catalog items' : 'No plugins installed'"
        :description="
          search.trim()
            ? 'Try a provider name, action, capability, or type of work.'
            : 'Installed plugins appear here after the local catalog is synchronized.'
        "
        :icon="search.trim() ? 'search' : 'puzzle'"
        framed
      />
    </template>

    <UiCard v-if="!loading && !error" section>
      <UiSectionHeader
        title="Action contracts"
        description="Full provider actions are inspected in Operations so the plugin catalog stays scan-friendly."
        as="h3"
      >
        <template #actions>
          <UiBadge>{{ actions.length }}</UiBadge>
          <UiButton size="sm" :href="operationsHref"> Open Operations </UiButton>
        </template>
      </UiSectionHeader>
      <p class="text-sm text-fg-muted">
        {{ actions.length }} action contracts are available across {{ plugins.length }} installed
        plugins. Use Operations for schema detail, credential readiness, and entrypoint visibility.
      </p>
    </UiCard>
  </UiPageShell>
</template>
