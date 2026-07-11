<script setup lang="ts">
import { UiFormField, UiInput } from '@/components/ui'

defineProps<{
  profileValue: string
  labelValue: string
}>()

defineEmits<{
  (event: 'update:profile', value: string | number | null): void
  (event: 'update:label', value: string | number | null): void
}>()
</script>

<template>
  <UiFormField
    label="Connection name"
    help="Leave blank for the default account. Use a short name like client-a or sandbox when this service has more than one account."
  >
    <template #default="{ id, describedBy, invalid }">
      <UiInput
        :id="id"
        :model-value="profileValue"
        :aria-describedby="describedBy"
        :invalid="invalid"
        placeholder="default"
        @update:model-value="$emit('update:profile', $event)"
      />
    </template>
  </UiFormField>

  <UiFormField
    label="Display label"
    help="Shown to operators and agents as safe metadata."
  >
    <template #default="{ id, describedBy, invalid }">
      <UiInput
        :id="id"
        :model-value="labelValue"
        :aria-describedby="describedBy"
        :invalid="invalid"
        placeholder="Primary account"
        @update:model-value="$emit('update:label', $event)"
      />
    </template>
  </UiFormField>
</template>
