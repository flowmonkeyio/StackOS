<script setup lang="ts">
import type { SchemaAuthProviderOut } from '@/api'
import { UiFormField, UiSelect } from '@/components/ui'
import ProviderMark from '@/components/domain/ProviderMark.vue'

import { formatAuthType } from './formatters'

const props = defineProps<{
  selectedProvider: SchemaAuthProviderOut
  providers: SchemaAuthProviderOut[]
  options: Array<{ value: string; label: string; group?: string }>
  disabled?: boolean
}>()

defineEmits<{
  (event: 'select', value: string | number | null): void
}>()

function providerForOption(value: string | number): SchemaAuthProviderOut | null {
  return props.providers.find((provider) => provider.key === String(value)) ?? null
}
</script>

<template>
  <UiFormField label="Service">
    <template #default="{ id, describedBy, invalid }">
      <UiSelect
        :id="id"
        :model-value="selectedProvider.key"
        :options="options"
        size="lg"
        :aria-describedby="describedBy"
        :invalid="invalid"
        :disabled="disabled"
        searchable
        search-placeholder="Search services"
        empty-label="No services found"
        @update:model-value="$emit('select', $event)"
      >
        <template #selected>
          <span class="flex min-w-0 items-center gap-2">
            <ProviderMark
              :name="selectedProvider.name"
              :provider-key="selectedProvider.key"
              :plugin-slug="selectedProvider.plugin_slug"
              size="xs"
            />
            <span class="min-w-0 truncate font-medium text-fg-strong">
              {{ selectedProvider.name }}
            </span>
            <span class="ml-auto shrink-0 text-2xs text-fg-muted">
              {{ formatAuthType(selectedProvider.auth_type) }}
            </span>
          </span>
        </template>
        <template #option="{ option }">
          <span v-if="providerForOption(option.value)" class="flex min-w-0 items-center gap-2.5">
            <ProviderMark
              :name="providerForOption(option.value)?.name ?? option.label"
              :provider-key="String(option.value)"
              :plugin-slug="providerForOption(option.value)?.plugin_slug"
              size="xs"
            />
            <span class="min-w-0 flex-1 truncate font-medium">
              {{ option.label }}
            </span>
            <span class="shrink-0 text-2xs text-fg-muted">
              {{ formatAuthType(providerForOption(option.value)?.auth_type) }}
            </span>
          </span>
          <span v-else>{{ option.label }}</span>
        </template>
      </UiSelect>
    </template>
  </UiFormField>
</template>
