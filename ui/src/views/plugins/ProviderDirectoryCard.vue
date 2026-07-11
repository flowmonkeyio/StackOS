<script setup lang="ts">
import ProviderMark from '@/components/domain/ProviderMark.vue'
import { UiBadge, UiCard } from '@/components/ui'
import { formatAuthType } from '@/lib/stackos/providerPresentation'

import type { ProviderDirectoryItem } from './viewModel'

defineProps<{ item: ProviderDirectoryItem }>()
</script>

<template>
  <UiCard section :aria-label="item.provider.name" class="flex h-full min-h-[14rem] flex-col">
    <template #header>
      <div class="flex min-w-0 items-center gap-3">
        <ProviderMark
          :name="item.provider.name"
          :provider-key="item.provider.key"
          :plugin-slug="item.provider.plugin_slug"
        />
        <div class="min-w-0">
          <h3 class="truncate t-h3 text-fg-strong">
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
    </template>

    <p class="line-clamp-3 text-sm leading-6 text-fg-muted">
      {{ item.provider.description || `Provider supplied by ${item.plugin.name}.` }}
    </p>

    <div
      v-if="item.capabilityNames.length"
      class="mt-3 flex flex-wrap gap-1.5"
      aria-label="Capabilities"
    >
      <UiBadge v-for="capability in item.capabilityNames.slice(0, 3)" :key="capability" size="sm">
        {{ capability }}
      </UiBadge>
    </div>

    <template #footer>
      <div class="flex w-full items-center justify-between gap-3 text-xs text-fg-muted">
        <span
          ><strong class="text-fg-strong">{{ item.actions.length }}</strong> actions</span
        >
        <span class="truncate font-mono text-2xs text-fg-subtle">{{ item.provider.key }}</span>
      </div>
    </template>
  </UiCard>
</template>
