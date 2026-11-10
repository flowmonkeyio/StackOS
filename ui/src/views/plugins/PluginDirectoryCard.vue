<script setup lang="ts">
import { computed } from 'vue'
import ProviderMark from '@/components/domain/ProviderMark.vue'
import { UiBadge, UiButton, UiCard, UiMedallion } from '@/components/ui'
import { resolveStatus } from '@/design/status'
import { pluginSectionIcon } from '@/lib/stackos/nav'

import type { PluginDirectoryItem } from './viewModel'

const props = defineProps<{
  item: PluginDirectoryItem
  availabilityLoading?: boolean
  availabilityError?: boolean
  enabling?: boolean
  enableDisabled?: boolean
}>()
defineEmits<{ showProviders: []; enable: [] }>()
const enablement = computed(() => resolveStatus(
  'plugin',
  props.item.plugin.enabled_for_project === true
    ? 'enabled'
    : props.item.plugin.enabled_for_project === false ? 'disabled' : 'available',
))
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
        :tone="enablement.tone"
        :dot="enablement.dot"
      >
        {{ enablement.label }}
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

    <div class="space-y-1 text-xs text-fg-muted" aria-label="Plugin availability">
      <p v-if="availabilityLoading">Checking availability…</p>
      <p v-else-if="availabilityError">Availability unavailable</p>
      <p v-else-if="item.integrations.length">
        {{ item.integrations.reduce((sum, entry) => sum + entry.executable_action_count, 0) }} of
        {{ item.integrations.reduce((sum, entry) => sum + entry.total_action_count, 0) }} actions executable
      </p>
      <p v-else>No integration availability reported.</p>
      <UiButton v-if="item.providers.length" variant="link" size="sm" @click="$emit('showProviders')">
        View provider availability
      </UiButton>
    </div>

    <UiButton
      v-if="item.plugin.enabled_for_project !== true"
      size="sm"
      :loading="enabling"
      :disabled="enableDisabled"
      :aria-label="`Enable ${item.plugin.name} for this project`"
      @click="$emit('enable')"
    >
      {{ enabling ? 'Enabling…' : 'Enable for this project' }}
    </UiButton>

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
  grid-template-rows: 4.5rem 7rem auto;
  gap: 1rem;
}

.plugin-directory-card__description {
  min-height: 4.5rem;
}

.plugin-directory-card__providers {
  min-height: 7rem;
}
</style>
