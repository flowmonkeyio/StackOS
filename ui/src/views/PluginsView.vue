<script setup lang="ts">
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'

import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import {
  UiButton,
  UiCallout,
  UiCard,
  UiEmptyState,
  UiFilterBar,
  UiPageShell,
  UiSegmentedControl,
  UiSelect,
  UiSkeleton,
} from '@/components/ui'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { useProjectScopedLoader } from '@/composables/useProjectScopedLoader'
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
import { useIntegrationAvailability } from './plugins/useIntegrationAvailability'

type CatalogView = 'plugins' | 'providers'

const catalogStore = useStackOsCatalogStore()
const { plugins, providers, actions, capabilities, loading, error } = storeToRefs(catalogStore)

const { projectId } = useProjectRouteScope()
const connectionsHref = computed(() => `/projects/${projectId.value}/connections?section=services`)
const inventory = useIntegrationAvailability()
const { items: integrations, loading: availabilityLoading, error: availabilityError } = inventory
const search = ref('')
const view = ref<CatalogView>('plugins')
const sort = ref<CatalogSort>('name')
const servicePluginSlug = ref<string | null>(null)

const viewOptions = [
  { key: 'plugins', label: 'Plugins' },
  { key: 'providers', label: 'Services' },
]
const sortOptions = [
  { value: 'name', label: 'Name' },
  { value: 'actions', label: 'Most actions' },
]

const pluginItems = computed(() =>
  buildPluginDirectory(plugins.value, providers.value, actions.value, capabilities.value, integrations.value),
)
const providerItems = computed(() => buildProviderDirectory(pluginItems.value))
const visiblePlugins = computed(() =>
  filterAndSortPlugins(pluginItems.value, search.value, sort.value),
)
const visibleProviders = computed(() =>
  filterAndSortProviders(
    providerItems.value.filter((item) => !servicePluginSlug.value || item.plugin.slug === servicePluginSlug.value),
    search.value,
    sort.value,
  ),
)
const activeFilters = computed(() => servicePluginSlug.value ? [{
  key: 'plugin',
  label: 'Plugin',
  value: pluginItems.value.find((item) => item.plugin.slug === servicePluginSlug.value)?.plugin.name ?? servicePluginSlug.value,
}] : [])
const resultCount = computed(() =>
  view.value === 'plugins' ? visiblePlugins.value.length : visibleProviders.value.length,
)

function setView(value: string | number): void {
  if (value === 'plugins' || value === 'providers') {
    view.value = value
    servicePluginSlug.value = null
  }
}

function setSort(value: string | number | null): void {
  if (value === 'name' || value === 'actions') sort.value = value
}

function showProviders(pluginSlug: string): void {
  view.value = 'providers'
  servicePluginSlug.value = pluginSlug
  search.value = ''
}

function clearFilters(): void {
  servicePluginSlug.value = null
  search.value = ''
}

const { refresh } = useProjectScopedLoader({
  projectId,
  load: ({ projectId }) => Promise.all([catalogStore.refresh(projectId), inventory.refresh(projectId)]),
  onScopeExit: inventory.reset,
})
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Plugins"
      description="Plugins and services available to this project."
      :breadcrumbs="[{ label: 'Plugins' }]"
    >
      <template #actions>
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="refresh"
          @click="refresh"
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

    <UiCallout
      v-if="availabilityError"
      tone="warning"
    >
      Availability unavailable. The catalog is still available; refresh to retry the integration check.
    </UiCallout>

    <UiFilterBar
      v-if="!loading && !error && plugins.length"
      v-model:search="search"
      search-placeholder="Search plugins or services"
      aria-label="Plugin catalog filters"
      :active="activeFilters"
      @remove="servicePluginSlug = null"
      @clear-all="clearFilters"
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
      class="grid grid-cols-1 gap-4 lg:grid-cols-2"
      aria-label="Loading plugins"
    >
      <UiCard
        v-for="n in 6"
        :key="n"
      >
        <UiSkeleton
          shape="line"
          :lines="4"
        />
      </UiCard>
    </div>

    <template v-else-if="!error">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <p class="text-xs text-fg-muted">
          <strong class="text-fg-strong">{{ resultCount }}</strong>
          {{ view === 'plugins' ? 'plugins' : 'services' }}
          <template v-if="search.trim()">
            for “{{ search.trim() }}”
          </template>
        </p>
        <UiButton
          v-if="search.trim()"
          variant="link"
          size="sm"
          @click="search = ''"
        >
          Clear search
        </UiButton>
      </div>

      <div
        v-if="view === 'plugins' && visiblePlugins.length"
        class="grid grid-cols-1 gap-4 lg:grid-cols-2"
      >
        <PluginDirectoryCard
          v-for="item in visiblePlugins"
          :key="item.plugin.slug"
          :item="item"
          :availability-loading="availabilityLoading"
          :availability-error="availabilityError"
          :connections-href="connectionsHref"
          @show-providers="showProviders(item.plugin.slug)"
        />
      </div>

      <div
        v-else-if="view === 'providers' && visibleProviders.length"
        class="grid grid-cols-1 items-start gap-4 lg:grid-cols-2"
      >
        <ProviderDirectoryCard
          v-for="item in visibleProviders"
          :key="`${item.provider.plugin_slug}.${item.provider.key}`"
          :item="item"
          :availability-loading="availabilityLoading"
          :connections-href="connectionsHref"
        />
      </div>

      <UiEmptyState
        v-else
        :title="search.trim() || servicePluginSlug ? 'No matching catalog items' : view === 'providers' ? 'No services available' : 'No plugins installed'"
        :description="
          search.trim() || servicePluginSlug
            ? 'Try a service name, action, capability, or type of work.'
            : view === 'providers'
              ? 'The installed plugins do not supply any services.'
              : 'Installed plugins appear here after the local catalog is synchronized.'
        "
        :icon="search.trim() ? 'search' : 'puzzle'"
        framed
      />
    </template>
  </UiPageShell>
</template>
