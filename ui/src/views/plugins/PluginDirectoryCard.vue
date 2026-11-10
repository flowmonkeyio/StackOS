<script setup lang="ts">
import ProviderMark from '@/components/domain/ProviderMark.vue'
import { UiBadge, UiButton, UiCard, UiIcon, UiMedallion } from '@/components/ui'
import { resolveStatus } from '@/design/status'
import { pluginSectionIcon } from '@/lib/stackos/nav'

import IntegrationAvailability from './IntegrationAvailability.vue'
import type { PluginDirectoryItem } from './viewModel'

defineProps<{
  item: PluginDirectoryItem
  availabilityLoading?: boolean
  availabilityError?: boolean
  connectionsHref: string
}>()
defineEmits<{ showProviders: [] }>()

const repair = resolveStatus('integration', 'repair_required')
</script>

<template>
  <UiCard
    section
    :aria-label="item.plugin.name"
    class="plugin-directory-card flex h-full min-w-0 flex-col"
    density="comfortable"
  >
    <div class="flex min-w-0 items-start justify-between gap-3">
      <div class="flex min-w-0 items-center gap-3">
        <UiMedallion
          :icon="pluginSectionIcon(item.plugin.slug)"
          shape="square"
          tone="info"
        />
        <h3
          class="min-w-0 line-clamp-2 break-words t-h1 text-fg-strong"
          :title="item.plugin.name"
        >
          {{ item.plugin.name }}
        </h3>
      </div>
      <UiBadge
        class="shrink-0"
        size="md"
        :tone="item.plugin.enabled_for_project === false ? 'neutral' : 'success'"
        :dot="item.plugin.enabled_for_project !== false"
      >
        {{ item.plugin.enabled_for_project === false ? 'Available' : 'Enabled' }}
      </UiBadge>
    </div>

    <p
      class="mt-3 line-clamp-2 break-words text-base text-fg-default"
      :title="item.plugin.description"
    >
      {{ item.plugin.description }}
    </p>

    <ul
      v-if="item.providers.length"
      class="mt-3 flex flex-wrap gap-2"
      :aria-label="`${item.plugin.name} services`"
    >
      <li
        v-for="provider in item.providers.slice(0, 4)"
        :key="provider.id"
        class="flex min-w-0 max-w-full items-center gap-2 rounded-md bg-bg-surface-alt px-2 py-1.5"
        :title="provider.name"
      >
        <ProviderMark
          :name="provider.name"
          :provider-key="provider.key"
          :plugin-slug="provider.plugin_slug"
          size="xs"
        />
        <span class="truncate text-sm font-medium text-fg-default">{{ provider.name }}</span>
      </li>
      <li
        v-if="item.providers.length > 4"
        class="rounded-md bg-bg-surface-alt px-2 py-1.5 text-xs text-fg-muted"
      >
        +{{ item.providers.length - 4 }} more
      </li>
    </ul>
    <p
      v-else
      class="mt-5 text-sm text-fg-muted"
    >
      No services required.
    </p>

    <UiButton
      v-if="item.providers.length"
      class="mt-2 self-start"
      variant="link"
      size="md"
      icon-right="arrow-right"
      @click="$emit('showProviders')"
    >
      View services
    </UiButton>

    <div
      v-if="!availabilityLoading && !availabilityError && item.integrations.some((entry) => entry.state === 'repair_required')"
      class="mt-2 flex flex-wrap items-center gap-2"
    >
      <UiBadge
        :tone="repair.tone"
        size="sm"
      >
        {{ repair.label }}
      </UiBadge>
      <UiButton
        :href="connectionsHref"
        variant="link"
        size="sm"
      >
        Review connections
      </UiButton>
    </div>

    <div class="mt-auto pt-4">
      <details class="group border-t border-default pt-2">
        <summary class="focus-ring flex cursor-pointer list-none items-center gap-2 rounded-sm py-1 text-sm text-fg-default [&::-webkit-details-marker]:hidden">
          <UiIcon
            name="chevron-down"
            class="h-4 w-4 transition-transform group-open:rotate-180"
          />
          Details
        </summary>
        <div class="mt-3 space-y-3 text-xs text-fg-muted">
          <p class="break-words">
            {{ item.plugin.description }}
          </p>
          <p>Version {{ item.plugin.version }} · {{ item.plugin.source }}</p>
          <p>{{ item.actions.length }} actions · {{ item.capabilities.length }} capabilities</p>
          <p v-if="availabilityLoading">
            Checking availability…
          </p>
          <p v-else-if="availabilityError">
            Availability unavailable
          </p>
          <ul
            v-else-if="item.providers.length"
            class="space-y-3"
            aria-label="Service availability"
          >
            <li
              v-for="provider in item.providers"
              :key="provider.id"
              class="break-words"
            >
              <p class="font-medium text-fg-default">
                {{ provider.name }}
              </p>
              <IntegrationAvailability
                :availability="item.integrations.find((entry) => entry.provider_key === provider.key)"
                :loading="false"
                :connections-href="connectionsHref"
              />
            </li>
          </ul>
        </div>
      </details>
    </div>
  </UiCard>
</template>

<style scoped>
.plugin-directory-card :deep(.ui-card__body) {
  display: flex;
  flex-direction: column;
}
</style>
