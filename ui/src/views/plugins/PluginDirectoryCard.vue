<script setup lang="ts">
import ProviderMark from '@/components/domain/ProviderMark.vue'
import { UiBadge, UiCard, UiMedallion } from '@/components/ui'
import { pluginSectionIcon } from '@/lib/stackos/nav'

import type { PluginDirectoryItem } from './viewModel'

defineProps<{ item: PluginDirectoryItem }>()
</script>

<template>
  <UiCard
    section
    :aria-label="item.plugin.name"
    class="plugin-directory-card flex h-full min-h-[21rem] flex-col"
  >
    <template #header>
      <div class="flex min-w-0 items-center gap-3">
        <UiMedallion :icon="pluginSectionIcon(item.plugin.slug)" shape="square" tone="info" />
        <div class="min-w-0">
          <p class="t-overline text-fg-subtle">StackOS plugin</p>
          <h3 class="truncate t-h3 text-fg-strong">
            {{ item.plugin.name }}
          </h3>
        </div>
      </div>
      <UiBadge
        :tone="item.plugin.enabled_for_project === false ? 'neutral' : 'success'"
        :dot="item.plugin.enabled_for_project !== false"
      >
        {{ item.plugin.enabled_for_project === false ? 'Available' : 'Enabled' }}
      </UiBadge>
    </template>

    <p class="plugin-directory-card__description line-clamp-3 text-sm leading-6 text-fg-muted">
      {{ item.plugin.description }}
    </p>

    <div class="plugin-directory-card__providers">
      <div class="flex items-center justify-between gap-3">
        <p class="t-overline text-fg-subtle">
          {{ item.providers.length }} {{ item.providers.length === 1 ? 'provider' : 'providers' }}
        </p>
        <span v-if="item.providers.length > 4" class="text-xs font-medium text-accent-fg">
          +{{ item.providers.length - 4 }} more
        </span>
      </div>
      <ul
        v-if="item.providers.length"
        class="mt-2 grid grid-cols-2 gap-2"
        :aria-label="`${item.plugin.name} providers`"
      >
        <li
          v-for="provider in item.providers.slice(0, 4)"
          :key="provider.id"
          class="flex min-w-0 items-center gap-2 rounded-md border border-subtle bg-bg-surface-alt px-2 py-1.5"
          :title="provider.name"
        >
          <ProviderMark
            :name="provider.name"
            :provider-key="provider.key"
            :plugin-slug="provider.plugin_slug"
            size="xs"
          />
          <span class="truncate text-xs font-medium text-fg-default">{{ provider.name }}</span>
        </li>
      </ul>
      <p v-else class="mt-2 text-xs text-fg-subtle">No provider setup required.</p>
    </div>

    <template #footer>
      <dl class="flex w-full flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-muted">
        <div class="flex items-baseline gap-1.5">
          <dd class="font-semibold tabular-nums text-fg-strong">
            {{ item.actions.length }}
          </dd>
          <dt>actions</dt>
        </div>
        <div class="flex items-baseline gap-1.5">
          <dd class="font-medium tabular-nums text-fg-default">
            {{ item.capabilities.length }}
          </dd>
          <dt>capabilities</dt>
        </div>
        <div class="ml-auto min-w-0">
          <dt class="sr-only">Version</dt>
          <dd class="truncate font-mono text-2xs text-fg-subtle">
            v{{ item.plugin.version }} · {{ item.plugin.source }}
          </dd>
        </div>
      </dl>
    </template>
  </UiCard>
</template>

<style scoped>
.plugin-directory-card :deep(.ui-card__body) {
  display: grid;
  grid-template-rows: 4.5rem 7rem;
  gap: 1rem;
}

.plugin-directory-card__description {
  min-height: 4.5rem;
}

.plugin-directory-card__providers {
  min-height: 7rem;
}
</style>
