<script setup lang="ts">
import ProviderMark from '@/components/domain/ProviderMark.vue'
import { UiBadge, UiCard, UiIcon } from '@/components/ui'
import { formatAuthType } from '@/lib/stackos/providerPresentation'

import type { ProviderDirectoryItem } from './viewModel'
import IntegrationAvailability from './IntegrationAvailability.vue'

defineProps<{ item: ProviderDirectoryItem; availabilityLoading: boolean; connectionsHref: string }>()
</script>

<template>
  <UiCard
    section
    :aria-label="item.provider.name"
    class="min-w-0"
  >
    <div class="flex items-start justify-between gap-3">
      <div class="flex min-w-0 items-center gap-3">
        <ProviderMark
          :name="item.provider.name"
          :provider-key="item.provider.key"
          :plugin-slug="item.provider.plugin_slug"
        />
        <div class="min-w-0">
          <h3
            class="line-clamp-2 break-words t-h3 text-fg-strong"
            :title="item.provider.name"
          >
            {{ item.provider.name }}
          </h3>
          <p class="truncate text-xs text-fg-subtle">
            {{ item.plugin.name }}
          </p>
        </div>
      </div>
      <UiBadge variant="outline">
        {{ formatAuthType(item.provider.auth_type) }}
      </UiBadge>
    </div>

    <p
      class="mt-3 line-clamp-2 break-words text-sm text-fg-muted"
      :title="item.provider.description ?? undefined"
    >
      {{ item.provider.description || `Service supplied by ${item.plugin.name}.` }}
    </p>

    <div
      v-if="item.capabilityNames.length"
      class="mt-3 flex flex-wrap gap-1.5"
      aria-label="Capabilities"
    >
      <UiBadge
        v-for="capability in item.capabilityNames.slice(0, 3)"
        :key="capability"
        size="sm"
      >
        {{ capability }}
      </UiBadge>
    </div>

    <IntegrationAvailability
      :availability="item.availability"
      :loading="availabilityLoading"
      :connections-href="connectionsHref"
    />

    <details class="group mt-4 border-t border-default pt-2">
      <summary class="focus-ring flex cursor-pointer list-none items-center gap-2 rounded-sm py-1 text-xs text-fg-default [&::-webkit-details-marker]:hidden">
        <UiIcon
          name="chevron-down"
          class="h-4 w-4 transition-transform group-open:rotate-180"
        />
        Details
      </summary>
      <div class="mt-3 space-y-2 break-words text-xs text-fg-muted">
        <p>{{ item.provider.description }}</p>
        <p>{{ item.actions.length }} actions</p>
        <p class="font-mono">
          {{ item.provider.plugin_slug }}.{{ item.provider.key }}
        </p>
      </div>
    </details>
  </UiCard>
</template>
