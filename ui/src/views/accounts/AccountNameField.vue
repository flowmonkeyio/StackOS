<script setup lang="ts">
import { UiFormField, UiInput } from '@/components/ui'

defineProps<{
  modelValue: string
  error?: string
}>()

defineEmits<{
  (event: 'update:modelValue', value: string | number | null): void
}>()
</script>

<template>
  <UiFormField
    label="Account name"
    help="Use a name that makes this provider account easy to recognize across projects."
    :error="error"
    input-id="account-display-name"
    required
  >
    <template #default="{ id, describedBy, invalid }">
      <UiInput
        :id="id"
        :model-value="modelValue"
        :aria-describedby="describedBy"
        :invalid="invalid"
        placeholder="Service - Default"
        @update:model-value="$emit('update:modelValue', $event)"
      />
    </template>
  </UiFormField>
</template>
