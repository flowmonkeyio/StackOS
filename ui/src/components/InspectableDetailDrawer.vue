<script setup lang="ts">
import { UiEmptyState, UiSidePanel } from '@/components/ui'

withDefaults(
  defineProps<{
    modelValue: boolean
    title: string
    description?: string
    size?: 'sm' | 'md' | 'lg' | 'xl' | '2xl'
    emptyTitle?: string
    emptyDescription?: string
    hasDetail?: boolean
  }>(),
  {
    description: undefined,
    size: 'lg',
    emptyTitle: 'No item selected',
    emptyDescription: 'Select a row to inspect its details.',
    hasDetail: true,
  },
)

defineEmits<{
  (event: 'update:modelValue', value: boolean): void
}>()
</script>

<template>
  <UiSidePanel
    :model-value="modelValue"
    :title="title"
    :description="description"
    :size="size"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <template
      v-if="$slots.header"
      #header="slotProps"
    >
      <slot
        name="header"
        v-bind="slotProps"
      />
    </template>

    <slot v-if="hasDetail" />
    <UiEmptyState
      v-else
      :title="emptyTitle"
      :description="emptyDescription"
      size="md"
    />
    <template
      v-if="$slots.footer"
      #footer="slotProps"
    >
      <slot
        name="footer"
        v-bind="slotProps"
      />
    </template>
  </UiSidePanel>
</template>
